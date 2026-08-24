"""板块（行业+概念）每日热度快照下载 —— 热点追踪 L0 层。

一次调用拿到**全部**行业(t:2)/概念(t:3)板块的涨幅/成交额/换手/涨跌家数/领涨股/主力净额，
算出热度分与排名后写入 `mkt_sector_flow_daily`（每天每板块一行）。

设计要点（踩过的坑固化下来）：
1. **必须翻页**：东财 clist 单页实际上限 100 条（pz=2000 无效），老实现只落到 100 条，
   概念榜尾直接丢失。这里按 pn 翻页直到取满 data.total。
2. **单源必挂**：东财 push2 会整组 502/拒连（board_daily 已踩过；2026-08-23 实测 push2/48/45/82
   全挂，只有延时主机 push2delay 通）。所以按主机**逐个试通**（实时优先、延时兜底，
   收盘后两者等价），每台先 https 再 http，全挂再回退 akshare。**akshare 只在主线程调**——py_mini_racer(V8) 在请求/后台线程初始化会
   PartitionAlloc FATAL 整进程崩，所以后台线程路径一律 allow_akshare=False。
3. **L0 数据不可回补**：东财只给当日快照，今天没抓就永久缺这一天（行业可用本地板块日K
   回补，见 scripts/Others/backfill_sector_flow_hist.py；概念无日K，补不了）。
   所以宁可多跑几次覆盖，也不能漏跑。
4. **盘中不污染历史**：交易日 15:00 前抓到的是盘中值，写今天这行并标 is_intraday=1，
   收盘后再跑会覆盖成最终值；绝不把盘中值写进上一个交易日那行。
5. **行业要按级别拆**：东财 `t:2` 返回的 496 个"行业"里混着一级行业(86个，如"煤炭")和
   二级/三级细分(如"玉米""调味品""光通信设备")。热度分是**同一横截面内的百分位**，
   把两种粒度混在一个池子里算，排名含义就废了（而且回补的历史只有一级行业，
   86 个池 vs 496 个池的"第3名"根本不是一回事）。所以按级别拆成两个 board_type：
   `industry`(一级) / `industry_sub`(细分)，各自独立算百分位与排名。

对外：
  fetch_sector_flow(board_type, allow_akshare=...) -> (rows, source)
  compute_derived(rows) -> rows            # 就地补 amount_share/rank_*/heat_score
  sync_sector_flow(app, ...) -> dict       # 抓取 + 落库
  resolve_snapshot_date() -> (date, is_intraday)
"""
import logging
import threading
import time
import json as _json
from datetime import date as _date, datetime, time as _time, timedelta

import requests

from App.exts import db
from App.models.strategy.SectorFlowDaily import (
    SectorFlowDaily, HEAT_V_EM, HEAT_V_AK, SRC_EM, SRC_EM_DELAY, SRC_AK,
)

logger = logging.getLogger(__name__)

# 按优先级逐个试通（不是按重试次数轮换 —— 那样前几个主机全挂时永远轮不到后面的）。
# push2delay 是东财的**延时行情**主机：盘中数据延迟约15分钟，但收盘后的日度快照与实时源等价，
# 所以它作为最后兜底完全够用（2026-08-23 实测 push2* 全部 502/拒连，只有 push2delay 通）。
_EM_HOSTS = ('push2.eastmoney.com', '48.push2.eastmoney.com', '45.push2.eastmoney.com',
             '82.push2.eastmoney.com', 'push2delay.eastmoney.com')
_DELAY_HOSTS = ('push2delay.eastmoney.com',)
_EM_UAS = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
)
# 地域板块(t:1)不跟：几乎无 alpha，只增噪声
_TYPE_FS = {'industry': 'm:90+t:2', 'concept': 'm:90+t:3'}

# 抓取类型 -> 落库 board_type。抓 industry 一次，落库拆成两个横截面。
BT_INDUSTRY = 'industry'          # 一级行业（与本地 86 个行业板块对齐，历史可回补）
BT_INDUSTRY_SUB = 'industry_sub'  # 东财二级/三级细分行业
BT_CONCEPT = 'concept'


def _l1_industry_codes():
    """本地一级行业板块代码集合（data_stock_classification 里的"行业板块"，86 个）。

    取不到就返回空集 —— 调用方会退化成"全部按一级存"，即老行为，不至于把当天数据丢掉。
    """
    from sqlalchemy import text
    try:
        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT code FROM data_stock_classification "
                "WHERE Classification = '行业板块'")).fetchall()
        return {str(r[0]).strip() for r in rows if r[0]}
    except Exception as e:
        logger.warning(f'[sector_flow] 读一级行业清单失败，行业将不拆级别: {e}')
        return set()
_PAGE_SIZE = 100          # 东财单页实际上限
_MAX_PAGES = 20           # 翻页安全上限（2000 条，远超板块总数）

# f12代码 f14名称 f3涨幅 f6成交额 f62主力净额 f184主力净占比 f8换手 f20总市值
# f104涨家数 f105跌家数 f128领涨股 f136领涨股涨幅
_FIELDS = 'f12,f14,f3,f6,f62,f184,f8,f20,f104,f105,f128,f136'

# 收盘时刻：此后抓到的才算当日最终值
CLOSE_TIME = _time(15, 0)

# 整日替换的"防降级"阈值：新抓条数低于已存条数的这个比例，就认定是残缺结果、不覆盖
DOWNGRADE_RATIO = 0.6

# 单页超时：延时主机很慢（实测单页 40s+），15s 会稳定超时导致翻页半途而废
PAGE_TIMEOUT = 45
# 单页重试次数：一页偶发超时不该让整次翻页放弃（残缺结果比慢更糟）
PAGE_RETRIES = 3


def _num(v):
    if v is None or v in ('', '-'):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _in_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


# ==================== 抓取 ====================
def _em_page(board_type: str, pn: int, host: str, ua_idx: int = 0,
             scheme: str = 'https', timeout: int = PAGE_TIMEOUT):
    """取东财板块列表的第 pn 页。返回 (rows, total)；失败抛异常。"""
    ua = _EM_UAS[ua_idx % len(_EM_UAS)]
    url = f'{scheme}://{host}/api/qt/clist/get'
    params = {
        'pn': pn, 'pz': _PAGE_SIZE, 'po': 1, 'np': 1,
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': 2, 'invt': 2, 'fid': 'f3',   # 按涨幅降序（翻页顺序稳定即可）
        'fs': _TYPE_FS[board_type], 'fields': _FIELDS,
    }
    headers = {'User-Agent': ua, 'Referer': 'https://quote.eastmoney.com/', 'Accept': '*/*'}
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f'{scheme}://{host} HTTP {resp.status_code}')
    try:
        payload = _json.loads(resp.text)
    except _json.JSONDecodeError:
        raise RuntimeError(f'{scheme}://{host} non-JSON')
    data = (payload or {}).get('data') or {}
    diff = data.get('diff') or []
    total = int(data.get('total') or 0)
    rows = []
    for r in diff:
        if not r.get('f12'):
            continue
        rows.append({
            'board_type': board_type,
            'board_code': str(r.get('f12', '')).strip(),
            'board_name': str(r.get('f14', '')).strip(),
            'change_pct': _num(r.get('f3')),
            'amount': _num(r.get('f6')),
            'main_net': _num(r.get('f62')),
            'main_pct': _num(r.get('f184')),
            'turnover_rate': _num(r.get('f8')),
            'total_cap': _num(r.get('f20')),
            'up_count': int(_num(r.get('f104')) or 0),
            'down_count': int(_num(r.get('f105')) or 0),
            'lead_stock': str(r.get('f128', '')).strip() or None,
            'lead_pct': _num(r.get('f136')),
        })
    return rows, total


def _page_with_retry(board_type, pn, host, ua_idx, scheme, attempts):
    """取一页，失败重试 PAGE_RETRIES 次（每次换 UA、退避）。全败抛最后一个异常。"""
    last = None
    for k in range(PAGE_RETRIES):
        try:
            return _em_page(board_type, pn, host, ua_idx + k, scheme)
        except (requests.exceptions.RequestException, RuntimeError) as e:
            last = e
            attempts.append(f'{host} p{pn} try{k+1} {type(e).__name__}')
            if k + 1 < PAGE_RETRIES:
                time.sleep(1.5 * (k + 1))
    raise last


def _paginate(board_type: str, host: str, ua_idx: int, scheme: str, attempts: list):
    """在单台主机上翻页取全量。返回 (rows, total, complete)。

    complete=False 表示没取全（东财自报 total 与实际条数不符）—— 调用方**不能**拿它
    覆盖已有的完整快照，否则横截面缩水会直接扭曲百分位与排名。
    """
    first, total = _page_with_retry(board_type, 1, host, ua_idx, scheme, attempts)
    rows = list(first)
    seen = {r['board_code'] for r in first}
    pages = 1
    # total 为 0/缺失时按"取满一页就继续"翻，直到某页不足一页
    while (total and len(rows) < total) or (not total and len(first) == _PAGE_SIZE):
        pages += 1
        if pages > _MAX_PAGES:
            logger.warning(f'[sector_flow] {board_type} 翻页超过 {_MAX_PAGES} 页，提前收工')
            break
        try:
            page_rows, _t = _page_with_retry(board_type, pages, host, ua_idx, scheme, attempts)
        except (requests.exceptions.RequestException, RuntimeError) as e:
            # 翻页中途失败（已重试过）：已拿到的不丢，但结果标为不完整
            logger.warning(f'[sector_flow] {board_type} 第 {pages} 页重试 {PAGE_RETRIES} 次仍失败，'
                           f'已取 {len(rows)}/{total or 0} 条: {e}')
            break
        new = [r for r in page_rows if r['board_code'] not in seen]
        if not new:
            # 翻页失效（东财偶尔无视 pn 回同一页），别静默当成"取完了"
            logger.warning(f'[sector_flow] {board_type} 第 {pages} 页无新增（疑似翻页失效），'
                           f'已取 {len(rows)}/{total or 0} 条')
            break
        rows.extend(new)
        seen.update(r['board_code'] for r in new)
        if len(page_rows) < _PAGE_SIZE:
            break
        time.sleep(0.3)
    complete = (not total) or len(rows) >= total
    if not complete:
        logger.warning(f'[sector_flow] {board_type} 仅取到 {len(rows)}/{total} 条（分页未取全）')
    return rows, total, complete


def fetch_sector_flow_em(board_type: str, retries: int = 2):
    """东财：**逐主机试通**后翻页取全量。返回 (rows, host, complete)。全失败抛 RuntimeError。

    主机按 _EM_HOSTS 顺序试（实时源优先、延时源兜底），每台先 https 再 http。
    某台通了就用它翻完所有页，不中途换主机——换了分页游标不保证一致。
    某台只取到残缺结果时，继续试下一台，实在都不完整才返回最好的那份并标 complete=False。
    """
    attempts = []
    best = None      # (rows, host) —— 目前拿到的最完整的一份
    for rnd in range(retries):
        for hi, host in enumerate(_EM_HOSTS):
            for scheme in ('https', 'http'):
                try:
                    rows, total, complete = _paginate(board_type, host, rnd + hi,
                                                      scheme, attempts)
                except (requests.exceptions.RequestException, RuntimeError) as e:
                    attempts.append(f'{scheme}://{host} {type(e).__name__}: {e}')
                    continue
                if not rows:
                    attempts.append(f'{scheme}://{host} 返回空')
                    continue
                if host in _DELAY_HOSTS:
                    logger.info(f'[sector_flow] {board_type} 走延时主机 {host}（实时主机不可用）')
                if complete:
                    return rows, host, True
                if best is None or len(rows) > len(best[0]):
                    best = (rows, host)
                logger.warning(f'[sector_flow] {board_type} 在 {host} 只拿到残缺结果，换下一台再试')
        time.sleep(1.0 * (rnd + 1))
    if best:
        logger.warning(f'[sector_flow] {board_type} 所有主机都没取全，返回最完整的一份 '
                       f'({len(best[0])} 条)')
        return best[0], best[1], False
    raise RuntimeError(f'东财板块列表({board_type})抓取失败：' + ' | '.join(attempts[-8:]))


def fetch_sector_flow_ak(board_type: str):
    """akshare 兜底（**仅主线程**）。字段比东财少：无成交额、无主力净额。"""
    if not _in_main_thread():
        raise RuntimeError('akshare 只能在主线程调用（V8/PartitionAlloc 会 FATAL），当前是后台线程')
    import akshare as ak
    df = (ak.stock_board_industry_name_em() if board_type == 'industry'
          else ak.stock_board_concept_name_em())
    if df is None or df.empty:
        raise RuntimeError(f'akshare 板块列表({board_type}) 返回空')

    def col(*names):
        for n in names:
            if n in df.columns:
                return n
        return None

    c_code = col('板块代码')
    c_name = col('板块名称')
    c_chg = col('涨跌幅')
    c_turn = col('换手率')
    c_cap = col('总市值')
    c_up = col('上涨家数')
    c_dn = col('下跌家数')
    c_lead = col('领涨股票')
    c_leadpct = col('领涨股票-涨跌幅')
    rows = []
    for _, r in df.iterrows():
        code = str(r.get(c_code, '')).strip() if c_code else ''
        if not code:
            continue
        rows.append({
            'board_type': board_type,
            'board_code': code,
            'board_name': (str(r.get(c_name, '')).strip() if c_name else ''),
            'change_pct': _num(r.get(c_chg)) if c_chg else None,
            'amount': None,                      # akshare 该接口不给成交额
            'main_net': None,
            'main_pct': None,
            'turnover_rate': _num(r.get(c_turn)) if c_turn else None,
            'total_cap': _num(r.get(c_cap)) if c_cap else None,
            'up_count': int(_num(r.get(c_up)) or 0) if c_up else 0,
            'down_count': int(_num(r.get(c_dn)) or 0) if c_dn else 0,
            'lead_stock': (str(r.get(c_lead)).strip() if c_lead else None) or None,
            'lead_pct': _num(r.get(c_leadpct)) if c_leadpct else None,
        })
    return rows


def fetch_sector_flow(board_type: str, retries: int = 4, allow_akshare: bool = None):
    """取一类板块的全量快照。返回 (rows, source, complete)。

    allow_akshare 缺省=当前是主线程才允许（V8 只能主线程初始化）。
    """
    if allow_akshare is None:
        allow_akshare = _in_main_thread()
    try:
        rows, host, complete = fetch_sector_flow_em(board_type, retries=retries)
        return rows, (SRC_EM_DELAY if host in _DELAY_HOSTS else SRC_EM), complete
    except Exception as em_err:
        if not allow_akshare:
            raise
        logger.warning(f'[sector_flow] 东财失败，改用 akshare 兜底：{em_err}')
        try:
            return fetch_sector_flow_ak(board_type), SRC_AK, True
        except Exception as ak_err:
            raise RuntimeError(f'东财+akshare 双源均失败 | 东财: {em_err} | akshare: {ak_err}')


# ==================== 派生量 ====================
def _pct_rank(values):
    """降序百分位：最大值=100，最小值=0；None 视为最低。

    返回 (pct 列表, rank 列表)，rank 从 1 开始，1=最大。
    """
    n = len(values)
    idx = sorted(range(n), key=lambda i: (values[i] is None, -(values[i] or 0)))
    pct = [0.0] * n
    rank = [n] * n
    for pos, i in enumerate(idx):
        rank[i] = pos + 1
        pct[i] = 100.0 if n <= 1 else (n - 1 - pos) / (n - 1) * 100.0
    return pct, rank


def compute_derived(rows, heat_version=None):
    """就地补 amount_share / rank_chg / rank_amt / rank_heat / heat_score / heat_version。

    heat_score = 50×pct(涨跌幅) + 50×pct(成交额占比)；无成交额时(akshare)用换手率代理，
    并把 heat_version 标成 heat-v0-ak —— 口径不同的分数不可混画一条线。
    """
    if not rows:
        return rows

    chg = [r.get('change_pct') for r in rows]
    amt = [r.get('amount') for r in rows]
    has_amt = any(v for v in amt)

    # 成交额占比（占同类型当日合计）
    total_amt = sum(v for v in amt if v) or 0.0
    for r in rows:
        a = r.get('amount')
        r['amount_share'] = round(a / total_amt * 100, 4) if (a and total_amt) else None

    pct_chg, rank_chg = _pct_rank(chg)
    if has_amt:
        second = [r.get('amount_share') for r in rows]
        ver = heat_version or HEAT_V_EM
    else:
        second = [r.get('turnover_rate') for r in rows]
        ver = heat_version or HEAT_V_AK
    pct_2nd, rank_amt = _pct_rank(second)

    heat = [0.5 * pct_chg[i] + 0.5 * pct_2nd[i] for i in range(len(rows))]
    _hp, rank_heat = _pct_rank(heat)

    for i, r in enumerate(rows):
        r['rank_chg'] = rank_chg[i]
        r['rank_amt'] = rank_amt[i]
        r['rank_heat'] = rank_heat[i]
        r['heat_score'] = round(heat[i], 2)
        r['heat_version'] = ver
    return rows


# ==================== 快照日期 ====================
def resolve_snapshot_date(ref_date=None):
    """决定这次快照写到哪一天，以及是不是盘中值。

    - 今天是交易日 → 写今天；15:00 前标 is_intraday=True（收盘后再跑会覆盖成最终值）
    - 今天非交易日（周末/节假日）→ 写最近一个交易日，is_intraday=False
    绝不把盘中值写进上一个交易日那行，避免污染已收盘的历史。
    """
    if ref_date:
        return ref_date, False
    today = _date.today()
    try:
        from App.routes.data.download_data_route import get_trading_dates
        cal = get_trading_dates()
    except Exception:
        cal = None

    if cal:
        is_today_trading = today in cal
        last_td = today
        limit = today - timedelta(days=30)
        while last_td not in cal and last_td > limit:
            last_td -= timedelta(days=1)
    else:  # 无日历：回退到工作日判断
        is_today_trading = today.weekday() < 5
        last_td = today
        while last_td.weekday() >= 5:
            last_td -= timedelta(days=1)

    if is_today_trading:
        return today, datetime.now().time() < CLOSE_TIME
    return last_td, False


# ==================== 落库 ====================
def sync_sector_flow(app, snapshot_date=None, types=('industry', 'concept'),
                     allow_akshare=None):
    """抓取并整日替换写入 mkt_sector_flow_daily。返回统计 dict。

    整日替换 = 同 (date, board_type) 先删后插，所以重复跑安全、且收盘后能覆盖盘中值。
    """
    with app.app_context():
        SectorFlowDaily.ensure_table()
        d, intraday = resolve_snapshot_date(snapshot_date)
        result = {'date': str(d), 'is_intraday': intraday, 'per_type': {},
                  'sources': {}, 'errors': []}
        now = datetime.utcnow()
        l1 = _l1_industry_codes() if BT_INDUSTRY in types else set()
        for bt in types:
            try:
                rows, src, complete = fetch_sector_flow(bt, allow_akshare=allow_akshare)
            except Exception as e:
                result['errors'].append(f'{bt}: {e}')
                logger.error(f'[sector_flow] {bt} 抓取失败: {e}')
                continue

            # 行业按级别拆两个横截面；概念原样一份
            if bt == BT_INDUSTRY and l1:
                groups = {
                    BT_INDUSTRY: [r for r in rows if r['board_code'] in l1],
                    BT_INDUSTRY_SUB: [r for r in rows if r['board_code'] not in l1],
                }
                if not groups[BT_INDUSTRY]:   # 一级一个都没匹配上：清单坏了，别把数据丢进 sub
                    logger.warning('[sector_flow] 一级行业清单与东财零匹配，本次不拆级别')
                    groups = {BT_INDUSTRY: rows}
            else:
                groups = {bt: rows}

            for store_bt, grp in groups.items():
                if not grp:
                    continue
                for r in grp:
                    r['board_type'] = store_bt
                compute_derived(grp)          # 百分位/排名严格限定在本横截面内

                # 防降级：翻页中途失败时会拿到残缺的一半，绝不能拿它覆盖已存的快照。
                # 横截面变小会直接扭曲百分位与排名（"第3名/100" 与 "第3名/500" 不是一回事）。
                existing = (db.session.query(db.func.count(SectorFlowDaily.id))
                            .filter(SectorFlowDaily.date == d,
                                    SectorFlowDaily.board_type == store_bt).scalar() or 0)
                if not complete and existing:
                    msg = (f'{store_bt}: 本次抓取未取全（{len(grp)} 条），已存 {existing} 条，'
                           f'放弃覆盖，等下次重跑')
                    logger.warning(f'[sector_flow] {d} {msg}')
                    result['errors'].append(msg)
                    result.setdefault('skipped', {})[store_bt] = {
                        'fetched': len(grp), 'kept': existing, 'reason': 'incomplete'}
                    continue
                if not complete:
                    # 该横截面此前没有任何数据：残缺也先存下（有总比没有强），但要留痕待重跑
                    logger.warning(f'[sector_flow] {d} {store_bt} 抓取未取全（{len(grp)} 条），'
                                   f'但该日原本无数据，先落库待重跑覆盖')
                    result['errors'].append(f'{store_bt}: 未取全({len(grp)} 条)，需重跑')
                if existing and len(grp) < existing * DOWNGRADE_RATIO:
                    msg = (f'{store_bt}: 本次仅 {len(grp)} 条，少于已存 {existing} 条的 '
                           f'{DOWNGRADE_RATIO:.0%}，判定为残缺结果，放弃覆盖')
                    logger.warning(f'[sector_flow] {d} {msg}')
                    result['errors'].append(msg)
                    result.setdefault('skipped', {})[store_bt] = {
                        'fetched': len(grp), 'kept': existing, 'reason': 'downgrade'}
                    continue

                try:
                    (SectorFlowDaily.query
                     .filter(SectorFlowDaily.date == d,
                             SectorFlowDaily.board_type == store_bt)
                     .delete(synchronize_session=False))
                    db.session.bulk_insert_mappings(SectorFlowDaily, [
                        {**r, 'date': d, 'source': src, 'is_intraday': intraday,
                         'created_at': now} for r in grp])
                    db.session.commit()
                except Exception as e:
                    # 一组写失败就回滚这一组，别让它带塌其他组（同一天的其他横截面）
                    db.session.rollback()
                    result['errors'].append(f'{store_bt} 落库失败: {e}')
                    logger.exception(f'[sector_flow] {d} {store_bt} 落库失败')
                    continue

                result['per_type'][store_bt] = len(grp)
                result['sources'][store_bt] = src
                logger.info(f'[sector_flow] {d} {store_bt} 落库 {len(grp)} 条 '
                            f'(source={src}, intraday={intraday})')
        return result
