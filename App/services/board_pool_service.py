"""L1 候选池 —— 从 L0 全量记录里挑出"值得下功夫跟"的板块。

为什么要有这一层（见《资金流热点监控系统_设计说明书》§11.1）
-----------------------------------------------------------
L0 采集全量几乎零成本（几个 HTTP 请求），但**评分很贵**：概念板块没有本地日K，
要评分就得 拉成分 → 合成指数 → 合成 15m → v1-mtf 打分，504 个概念每天全跑不现实。
而且概念里一大半是僵尸和属性标签，评分纯浪费。所以 L1 = 动态候选池：
概念限额 POOL_CAP 个（+ 手工钉选）+ 全部一级行业，只有池内板块才做上面那条重管线。

三道闸（挡在 L1 之外，但 L0 的每日记录照留）
------------------------------------------
1. **属性标签黑名单**：融资融券/沪股通/机构重仓/预盈预增/高送转/次新股/昨日涨停…
   这些不是"热点主线"，是选股属性，成分动辄上千只、天天在榜却没有信息量。
   同理还有指数成分类（HS300_/上证50/中证500…）。
2. **过宽过滤**：成分股 > MAX_MEMBERS 或占全市场比例 > MAX_MEMBER_SHARE。
   成分数要拉过成分才知道，所以这道闸在 sync_members 之后补判。
3. **Jaccard 折叠**：与已在池板块成分重叠 > JACCARD_T 判为同一主线，
   保留**更窄更纯**（成分更少）的那个，另一个标 alias_of 指向它。

进出池规则（阈值都是初值，等 L0 样本够了按分布校准）
--------------------------------------------------
- 入池：连续 IN_CONSEC 个快照日热度排名 ≤ IN_RANK（防抖，避免一日游天天刷进刷出）
- 出池：连续 OUT_DAYS 个快照日没进过 TOP OUT_RANK → 降回 L0（**只降 tier，历史一行不删**）
- 手工钉选 is_pinned：恒在池内，不受出池规则约束，也不占用 POOL_CAP 之外的判断
- 冷启动：概念的 L0 历史刚起步（不可回补），样本不足 IN_CONSEC 天时允许"单日播种"
  （seed 模式），用当日 TOP IN_RANK 先把池子填起来，来源标 seed 以便事后复核

对外：
    evaluate(as_of=None)         -> dict 建议（dry-run，不写库）
    apply_changes(plan)          -> dict 实际写库结果
    sync_members(codes)          -> 拉成分入 industry_eastmoney + 回填 member_count/过宽闸
    fold_aliases()               -> Jaccard 折叠
    pool_status()                -> 池子概况
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import text

from App.exts import db
from App.models.evaluation.Board import (
    Board, TIER_L0, TIER_L1, EXCL_ATTRIBUTE_TAG, EXCL_TOO_BROAD, EXCL_ALIAS,
)
from App.models.strategy.SectorFlowDaily import SectorFlowDaily

logger = logging.getLogger(__name__)

# ============ 阈值（初值，待校准）============
POOL_CAP = 50        # 概念在池上限（不含手工钉选）
IN_RANK = 30         # 入池：热度排名 ≤ 该值
IN_CONSEC = 2        # 入池：需连续满足的快照日数（防抖）
OUT_RANK = 50        # 在榜：热度排名 ≤ 该值
OUT_DAYS = 15        # 出池：连续这么多个快照日没在榜
LOOKBACK = 30        # 评估回看的快照日数
MAX_MEMBERS = 300    # 过宽闸：成分股数上限
MAX_MEMBER_SHARE = 0.05   # 过宽闸：占全市场股票数比例上限
JACCARD_T = 0.7      # 成分重叠折叠阈值

# ============ 属性标签黑名单 ============
# 命中即永不进 L1。这些是"选股属性"不是"热点主线"：成分极多、天天在榜、没有信息量。
# 思路同新闻停用词——高频低信息量的词该进停用词，而不是进白名单。
ATTRIBUTE_TAG_KEYWORDS = (
    # 资金/持仓属性
    '融资融券', '沪股通', '深股通', '股通', '机构重仓', '基金重仓', '社保重仓',
    'QFII', '北向', '陆股通', '举牌', '股权转让',
    # 财务/事件属性
    '预盈', '预亏', '预减', '预增', '高送转', '送转', '业绩', '扭亏',
    # 交易属性
    '昨日涨停', '昨日连板', '昨日触板', '次新股', '新股', '破净', 'ST股', '壳资源',
    '超跌', '低价股', '高价股', '大盘股', '小盘股',
    # 指数成分/板块归属
    'HS300', '沪深300', '上证50', '中证500', '中证1000', '科创50', 'MSCI', '富时罗素',
    '标普道琼斯', '创业板综', '深成指', '上证180', '北交所概念',
    # 上市地/股本结构
    'AH股', 'AB股', 'B股', 'GDR', '转债标的', 'QDII', '含H股', '含B股',
    # 泛化归类
    '中字头', '央企', '国企改革', '专精特新', '注册制', '参股',
    # 风格/规模板块（东财 BK16xx 那一套）：大盘成长/小盘价值/微盘股/周期股/科技风格…
    # 这些是**选股风格分类**不是热点主线，成分按市值风格切分、天天在榜，同属"高频低信息量"
    '风格', '大盘', '中盘', '小盘', '微盘', '成长股', '价值股', '周期股', '绩优',
)


def is_attribute_tag(board_name: str) -> bool:
    """名称命中属性标签黑名单。"""
    n = (board_name or '').strip()
    if not n:
        return False
    return any(k in n for k in ATTRIBUTE_TAG_KEYWORDS)


# ============ 工具 ============
def _snapshot_dates(board_type='concept', limit=LOOKBACK):
    """取真正可用的快照日：必须有热度分/排名。

    早期遗留快照（分页被截断、无成交额）rank_heat 全为 NULL，把它算成"一天"会让
    "连续2日"之类的规则恒不成立，还会误判样本充足而不进 seed 模式。
    """
    rows = (db.session.query(SectorFlowDaily.date).distinct()
            .filter(SectorFlowDaily.board_type == board_type,
                    SectorFlowDaily.rank_heat.isnot(None))
            .order_by(SectorFlowDaily.date.desc()).limit(limit).all())
    return sorted(r[0] for r in rows)


def _market_stock_count():
    """全市场股票数（给"过宽"闸的相对口径）。取不到返回 0，调用方退化成绝对阈值。

    用 full_stock_hushen（沪深股票清单）；它为空再退到"近三个月有行情的股票数"。
    注意别用 data_stock_info —— 它的列名是 code 不是 stock_code，查 stock_code 会
    静默异常返回 0，相对闸就形同虚设了。
    """
    eng = db.engines['quanttradingsystem']
    for sql in ('SELECT COUNT(DISTINCT stock_code) FROM full_stock_hushen',
                "SELECT COUNT(DISTINCT stock_code) FROM data_stock_daily "
                "WHERE stock_code NOT LIKE 'BK%' "
                "AND date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)"):
        try:
            with eng.connect() as conn:
                n = int(conn.execute(text(sql)).scalar() or 0)
            if n > 1000:          # 明显不合理的小数字不采信
                return n
        except Exception as e:
            logger.debug(f'[pool] 全市场股票数查询失败: {e}')
    return 0


def member_limit():
    """成分数上限 = min(绝对上限, 全市场×比例)。两处闸门必须用同一个口径，
    否则 evaluate 说"可以留"、sync_members 说"太宽退回"，池子会来回抖。"""
    n = _market_stock_count()
    rel = int(n * MAX_MEMBER_SHARE) if n else 0
    return min([x for x in (MAX_MEMBERS, rel) if x] or [MAX_MEMBERS])


def _latest_members(codes):
    """取这些板块在 industry_eastmoney 里最新一份成分：{code: set(stock_code)}。"""
    if not codes:
        return {}
    eng = db.engines['quanttradingsystem']
    out = {}
    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT ie.board_code, ie.stock_code
              FROM industry_eastmoney ie
              JOIN (SELECT board_code, MAX(date) d FROM industry_eastmoney
                     WHERE board_code IN :codes GROUP BY board_code) m
                ON ie.board_code = m.board_code AND ie.date = m.d
             WHERE ie.board_code IN :codes
        """), {'codes': tuple(codes)}).fetchall()
    for bc, sc in rows:
        out.setdefault(bc, set()).add(sc)
    return out


# ============ 评估（dry-run）============
def evaluate(as_of=None, cap=POOL_CAP):
    """算出入池/出池建议，不写库。返回 dict。

    seed 模式：概念的 L0 历史刚起步时（快照日 < IN_CONSEC），"连续2日"根本无从谈起，
    这时按当日 TOP IN_RANK 播种，并在返回里标 mode='seed' —— 让调用方知道这批是
    冷启动填进去的，不是规则跑出来的。
    """
    Board.ensure_table()
    SectorFlowDaily.ensure_table()

    dates = _snapshot_dates('concept', LOOKBACK)
    if not dates:
        return {'ok': False, 'message': '概念板块尚无热度快照，先跑 L0 采集',
                'add': [], 'drop': [], 'blocked': [], 'dates': 0}
    as_of = as_of or dates[-1]
    mode = 'seed' if len(dates) < IN_CONSEC else 'rule'

    rows = (SectorFlowDaily.query
            .filter(SectorFlowDaily.board_type == 'concept',
                    SectorFlowDaily.date >= dates[0],
                    SectorFlowDaily.date <= as_of)
            .all())
    dix = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    series = {}
    for r in rows:
        i = dix.get(r.date)
        if i is None:
            continue
        s = series.setdefault(r.board_code, {
            'name': r.board_name, 'rank': [None] * n, 'heat': [None] * n})
        s['name'] = r.board_name or s['name']
        s['rank'][i] = r.rank_heat
        s['heat'][i] = r.heat_score

    existing = {b.board_code: b for b in Board.query.all()}
    add, drop, blocked, keep = [], [], [], []
    mlimit = member_limit()

    for code, s in series.items():
        name = s['name'] or code
        ranks = s['rank']
        in_top = [(rk is not None and rk <= OUT_RANK) for rk in ranks]
        last_top_i = max((i for i, f in enumerate(in_top) if f), default=None)
        last_top = dates[last_top_i] if last_top_i is not None else None
        # 入池条件：末尾连续 IN_CONSEC 天 rank<=IN_RANK（seed 模式退化成当日一天）
        need = 1 if mode == 'seed' else IN_CONSEC
        tail = [(rk is not None and rk <= IN_RANK) for rk in ranks[-need:]]
        hot_enough = len(tail) >= need and all(tail)
        heats = [h for h in s['heat'] if h is not None]
        avg_heat = round(sum(heats) / len(heats), 2) if heats else 0.0

        b = existing.get(code)
        item = {'board_code': code, 'board_name': name,
                'rank_now': ranks[-1], 'avg_heat': avg_heat,
                'last_top_date': last_top.isoformat() if last_top else None,
                'member_count': (b.member_count if b else None)}

        # —— 闸一：属性标签 ——
        # 已在池里的也要判：黑名单是逐步补出来的，早先靠 seed 混进来的必须能被清出去，
        # 否则改了黑名单也赶不走它们。钉选的除外（用户明确要跟就随他）。
        if is_attribute_tag(name):
            if b and b.tracking_tier == TIER_L1 and not b.is_pinned:
                drop.append({**item, 'reason': EXCL_ATTRIBUTE_TAG, 'gap_days': None})
            elif (not b or b.tracking_tier != TIER_L1) and hot_enough:
                blocked.append({**item, 'reason': EXCL_ATTRIBUTE_TAG})
            continue
        # —— 闸二：过宽（成分数已知时才判）——
        # 与闸一同理：已在池的超标板块也要清出去，不能只是跳过。
        if b and b.member_count and b.member_count > mlimit:
            if b.tracking_tier == TIER_L1 and not b.is_pinned:
                drop.append({**item, 'reason': EXCL_TOO_BROAD, 'gap_days': None})
            elif b.tracking_tier != TIER_L1 and hot_enough:
                blocked.append({**item, 'reason': EXCL_TOO_BROAD})
            continue
        # —— 闸三：已折叠到别的主线 ——
        if b and b.alias_of:
            if hot_enough:
                blocked.append({**item, 'reason': EXCL_ALIAS, 'alias_of': b.alias_of})
            continue

        in_pool = bool(b and b.tracking_tier == TIER_L1)
        pinned = bool(b and b.is_pinned)

        if in_pool:
            # 出池判定：连续 OUT_DAYS 个快照日没在榜（钉选的不出池）
            gap = (n - 1 - last_top_i) if last_top_i is not None else n
            if pinned:
                keep.append({**item, 'why': 'pinned'})
            elif gap >= OUT_DAYS:
                drop.append({**item, 'gap_days': gap})
            else:
                keep.append({**item, 'gap_days': gap})
        elif hot_enough:
            add.append({**item, 'mode': mode})

    # 限额：按近期平均热度排序，超出 cap 的这轮不进（下轮再看）
    add.sort(key=lambda x: -x['avg_heat'])
    pool_now = len([b for b in existing.values()
                    if b.tracking_tier == TIER_L1 and b.classification == '概念板块'
                    and not b.is_pinned])
    room = max(0, cap - pool_now + len(drop))
    deferred = add[room:]
    add = add[:room]

    return {
        'ok': True, 'mode': mode, 'as_of': str(as_of), 'dates': n,
        'pool_now': pool_now, 'cap': cap, 'room': room,
        'add': add, 'drop': drop, 'keep': keep,
        'blocked': blocked, 'deferred': deferred,
        'thresholds': {'in_rank': IN_RANK, 'in_consec': IN_CONSEC,
                       'out_rank': OUT_RANK, 'out_days': OUT_DAYS,
                       'max_members': mlimit, 'jaccard': JACCARD_T},
    }


# ============ 应用 ============
def ensure_industry_in_pool():
    """一级行业全部进 L1：它们只有 86 个、成分和日K都是现成的，没有限额问题。

    概念才需要动态进出（504 个、评分很贵）。返回被提升的板块数。
    """
    Board.ensure_table()
    rows = (Board.query
            .filter(Board.classification == '行业板块',
                    Board.enabled.isnot(False),
                    db.or_(Board.tracking_tier.is_(None),
                           Board.tracking_tier < TIER_L1))
            .all())
    today = date.today()
    for b in rows:
        Board.upsert(b.board_code, tracking_tier=TIER_L1, tier_since=today)
    if rows:
        logger.info(f'[pool] 一级行业入池 {len(rows)} 个')
    return len(rows)


def apply_changes(plan=None, as_of=None):
    """把 evaluate() 的建议写进 eval_board。plan 为空则先跑一次 evaluate。"""
    ensure_industry_in_pool()
    plan = plan or evaluate(as_of=as_of)
    if not plan.get('ok'):
        return plan
    today = date.today()
    added, dropped = [], []

    for it in plan.get('add', []):
        Board.upsert(it['board_code'],
                     board_name=it['board_name'],
                     classification='概念板块', source='concept',
                     tracking_tier=TIER_L1, tier_since=today,
                     enabled=True, exclude_reason=None,
                     last_top_date=(date.fromisoformat(it['last_top_date'])
                                    if it.get('last_top_date') else None),
                     notes=f"L1 入池({it.get('mode', 'rule')}) {today}")
        added.append(it['board_code'])

    for it in plan.get('drop', []):
        why = (f"命中闸门 {it['reason']}" if it.get('reason')
               else f"连续 {it.get('gap_days')} 个快照日未在榜")
        Board.upsert(it['board_code'], tracking_tier=TIER_L0, tier_since=today,
                     exclude_reason=it.get('reason'),
                     notes=f'L1 出池 {today}（{why}）')
        dropped.append(it['board_code'])

    # 在池的刷新 last_top_date，便于下次判定
    for it in plan.get('keep', []):
        if it.get('last_top_date'):
            Board.upsert(it['board_code'],
                         last_top_date=date.fromisoformat(it['last_top_date']))

    # 被闸门挡下的记一笔原因，避免下次重复评估时看不出为什么没进
    for it in plan.get('blocked', []):
        b = Board.query.filter_by(board_code=it['board_code']).first()
        if b is None:
            Board.upsert(it['board_code'], board_name=it['board_name'],
                         classification='概念板块', source='concept',
                         tracking_tier=TIER_L0, enabled=True,
                         exclude_reason=it['reason'],
                         notes=f"闸门拦截：{it['reason']}")
        elif b.exclude_reason != it['reason']:
            Board.upsert(it['board_code'], exclude_reason=it['reason'])

    logger.info(f'[pool] 入池 {len(added)} 个，出池 {len(dropped)} 个，'
                f'拦截 {len(plan.get("blocked", []))} 个')
    return {**plan, 'applied': True, 'added': added, 'dropped': dropped}


def pin(board_code, pinned=True, board_name=None):
    """手工钉选/取消钉选。钉选即进池且不受出池规则约束。"""
    Board.ensure_table()
    b = Board.query.filter_by(board_code=board_code).first()
    fields = {'is_pinned': bool(pinned)}
    if pinned:
        fields.update({'tracking_tier': TIER_L1, 'tier_since': date.today(),
                       'enabled': True, 'exclude_reason': None})
    if board_name and (not b or not b.board_name):
        fields['board_name'] = board_name
    if b is None:
        # 钉一个还没进过主表的板块：名称/分类从 L0 快照补
        sf = (SectorFlowDaily.query.filter_by(board_code=board_code)
              .order_by(SectorFlowDaily.date.desc()).first())
        fields.setdefault('board_name', (sf.board_name if sf else board_code))
        fields['classification'] = ('概念板块' if (sf and sf.board_type == 'concept')
                                    else '行业板块')
        fields['source'] = ('concept' if (sf and sf.board_type == 'concept') else 'industry')
    return Board.upsert(board_code, **fields).to_dict()


# ============ 成分同步 + 过宽闸 ============
def sync_members(codes=None, only_missing=True):
    """给 L1 板块拉成分股入 industry_eastmoney，并回填 member_count / 过宽闸。

    用 em_industry_cons_via_http（纯 requests，已翻页），不走 akshare —— 这个函数
    可能在请求线程里被调用，而 akshare 的 V8 只能主线程初始化。
    """
    from App.services.board_data_service import em_industry_cons_via_http
    Board.ensure_table()

    if codes:
        targets = Board.query.filter(Board.board_code.in_(list(codes))).all()
    else:
        targets = Board.list_by_tier(TIER_L1)
    if only_missing:
        targets = [b for b in targets if not b.member_count or not b.last_member_sync]
    if not targets:
        return {'ok': True, 'synced': 0, 'skipped': 0, 'too_broad': [], 'failed': []}

    limit = member_limit()

    eng = db.engines['quanttradingsystem']
    today = date.today()
    synced, too_broad, failed = 0, [], []

    for b in targets:
        try:
            members = em_industry_cons_via_http(b.board_code, retries=2)
        except Exception as e:
            failed.append({'board_code': b.board_code, 'error': str(e)[:150]})
            continue
        if not members:
            failed.append({'board_code': b.board_code, 'error': '成分为空'})
            continue

        with eng.begin() as conn:
            conn.execute(text('DELETE FROM industry_eastmoney WHERE board_code=:c'),
                         {'c': b.board_code})
            conn.execute(text(
                'INSERT INTO industry_eastmoney '
                '(board_name, board_code, stock_code, stock_name, date, total_cap, circ_cap) '
                'VALUES (:bn, :bc, :sc, :sn, :d, :tc, :cc)'),
                [{'bn': b.board_name or b.board_code, 'bc': b.board_code,
                  'sc': m['stock_code'], 'sn': m['stock_name'], 'd': today,
                  'tc': m.get('total_cap'), 'cc': m.get('circ_cap')} for m in members])

        fields = {'member_count': len(members), 'last_member_sync': datetime.utcnow()}
        # 闸二在这里才判得了：成分数要拉过才知道
        if len(members) > limit:
            fields.update({'tracking_tier': TIER_L0, 'exclude_reason': EXCL_TOO_BROAD,
                           'notes': f'成分 {len(members)} 只 > 上限 {limit}，覆盖过宽，退回 L0'})
            too_broad.append({'board_code': b.board_code, 'board_name': b.board_name,
                              'member_count': len(members), 'limit': limit})
        Board.upsert(b.board_code, **fields)
        synced += 1

    logger.info(f'[pool] 成分同步 {synced} 个，过宽退回 {len(too_broad)} 个，失败 {len(failed)} 个')
    return {'ok': True, 'synced': synced, 'too_broad': too_broad, 'failed': failed,
            'member_limit': limit}


# ============ Jaccard 折叠 ============
def fold_aliases(threshold=JACCARD_T, dry_run=True):
    """池内板块成分 Jaccard > threshold 判为同一主线：保留成分更少更纯的，另一个标 alias。

    只在**入池后**做一次（不是每天跑）——成分名单变化很慢，天天算纯属浪费。
    """
    pool = [b for b in Board.list_by_tier(TIER_L1) if not b.alias_of]
    members = _latest_members([b.board_code for b in pool])
    pool = [b for b in pool if members.get(b.board_code)]
    # 成分少的优先当"主线"（更窄更纯）
    pool.sort(key=lambda b: len(members[b.board_code]))

    folded = []
    kept = set()
    for b in pool:
        mb = members[b.board_code]
        hit = None
        for kc in kept:
            mk = members[kc]
            inter = len(mb & mk)
            union = len(mb | mk)
            j = inter / union if union else 0.0
            if j > threshold:
                hit = (kc, round(j, 3))
                break
        if hit:
            folded.append({'board_code': b.board_code, 'board_name': b.board_name,
                           'alias_of': hit[0], 'jaccard': hit[1],
                           'members': len(mb), 'main_members': len(members[hit[0]])})
            if not dry_run and not b.is_pinned:
                Board.upsert(b.board_code, alias_of=hit[0], tracking_tier=TIER_L0,
                             exclude_reason=EXCL_ALIAS,
                             notes=f'与 {hit[0]} 成分重叠 {hit[1]}，折叠为同一主线')
        else:
            kept.add(b.board_code)
    return {'ok': True, 'dry_run': dry_run, 'checked': len(pool), 'folded': folded}


# ============ 概况 ============
def pool_status():
    Board.ensure_table()
    rows = Board.query.all()
    def _cnt(pred):
        return sum(1 for b in rows if pred(b))
    l1 = [b for b in rows if b.tracking_tier == TIER_L1]
    return {
        'total_boards': len(rows),
        'l1_total': len(l1),
        'l1_concept': sum(1 for b in l1 if b.classification == '概念板块'),
        'l1_industry': sum(1 for b in l1 if b.classification == '行业板块'),
        'pinned': _cnt(lambda b: b.is_pinned),
        'blocked': {
            'attribute_tag': _cnt(lambda b: b.exclude_reason == EXCL_ATTRIBUTE_TAG),
            'too_broad': _cnt(lambda b: b.exclude_reason == EXCL_TOO_BROAD),
            'alias': _cnt(lambda b: b.exclude_reason == EXCL_ALIAS),
        },
        'members_synced': sum(1 for b in l1 if b.member_count),
        'has_daily': sum(1 for b in l1 if b.has_daily_data),
        'cap': POOL_CAP,
    }
