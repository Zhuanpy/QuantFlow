"""板块热点「持续性」分析 —— 读 mkt_sector_flow_daily 的纵向序列。

回答的问题：**今天上榜的热点，是一日游还是能持续？**
横截面（谁最热）看 rank_heat 就够了；这个模块干的是纵向的活：

    在榜(top-K) 的连续天数 / 累计天数 / 峰值与回撤 / 近5日热度斜率 → 贴生命周期标签

标签（窗口内，按优先级判定）：
    emerging  新兴   首次上榜在近 5 个交易日内，且当前已连续在榜 ≥2 天
    sustained 持续   当前连续在榜 ≥5 天，或窗口内在榜天数 ≥40%
    fading    退潮   当前已掉榜，但最后一次在榜就在近 5 日内，且曾连续 ≥2 天
    flash     一日游 窗口内最长连续 ≤1 天且累计 ≤2 天（点一下就没）
    intermittent 间歇 其余（反复上下榜，没形成趋势）

注意：这些阈值是**初值**，等 L0 攒够样本后按实际分布校准（设计说明书 §6 的回测才定权重）。
标签是监控看板的语义标签，不是买卖信号。

两种口径（basis），回答的是不同的问题，**不可混用**：
  cross 横截面（默认）：读 mkt_sector_flow_daily 的 heat_score/rank_heat。
        "今天 504 个概念里谁最热"。在榜 = 当日排名 ≤ top_k。
        缺点：依赖全市场同期快照，而快照不可回补 → 概念长期只有一两天样本。
  self  时序：读 mkt_board_heat_ts 的 heat_ts。
        "这个板块比它自己平时热多少"。在榜 = heat_ts ≥ hot_threshold。
        优点：只要有自己的日K就能整段回补（概念靠合成指数回到 2021 年），
        且不受"当天参与排名的板块数"变化影响。
        范围：只覆盖 L1 候选池（heat_ts 只给池内板块算）。

横截面口径下 board_type 就是横截面单位 —— concept(504) / industry(一级86) /
industry_sub(东财细分行业)。热度分与排名只在**同一个 board_type 内**算百分位，
不同粒度不可互相比较，也不可混进同一张图。

口径警告：heat_score 有三种版本（em/ak/hist，见 SectorFlowDaily docstring）。
本模块把窗口内出现的版本一并返回（`versions`），前端要提示"口径混合"，
避免把两段不同定义的分数当成一条连续曲线读。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from App.exts import db
from App.models.strategy.SectorFlowDaily import SectorFlowDaily

logger = logging.getLogger(__name__)

# 判定阈值（初值，待样本足够后校准）
NEW_DAYS = 5          # "首次上榜"算新的天数窗口
SUSTAIN_STREAK = 5    # 连续在榜多少天算"持续"
SUSTAIN_RATIO = 0.4   # 或窗口内在榜比例
FADE_RECENT = 5       # 掉榜后多少天内算"退潮"（再久就归间歇/沉寂）
SLOPE_N = 5           # 热度斜率的回归窗口

# —— 时序口径专用阈值 ——
# 不能套用横截面那套：cross 的"在榜"是 TOP20/504≈前4%，self 的门槛是自身历史分位，
# 两者严格程度差一个数量级；而且时序分位天然均值回归，"连续5天站在自身前20%"
# 几乎无人达到（实测 sustained 恒为 0）。
# 下面这套是按 2026-08-23 实测分布定的（60交易日窗口 × L1 池内 101 个板块）：
#   门槛70 → 98个板块曾在榜，最长连续中位3天/P75=4天，最长连续≥5天的 17个(17%)
#   门槛80 → 最长连续中位仅2天，≥5天的只有9个 —— 太苛刻，分不出层次
HOT_TS_THRESHOLD = 70      # 在榜 = heat_ts ≥ 70（处于自身历史前 30% 热度）
SUSTAIN_STREAK_TS = 5      # 连续≥5天算持续（实测约 17% 的板块能达到，是个像样的"少数"）
SUSTAIN_RATIO_TS = 0.35    # 或窗口内在榜占比 ≥35%（实测中位 18%，P75 约 25%）

LABELS = {
    'emerging': '新兴',
    'sustained': '持续',
    'fading': '退潮',
    'flash': '一日游',
    'intermittent': '间歇',
    'insufficient': '样本不足',
}

# 窗口里不足这么多个快照日，就不给生命周期标签 —— 只有2天数据时"最长连续≤1"
# 对每个板块都成立，会把全部板块打成"一日游"，那是样本不足的假象，不是判断。
MIN_LABEL_DAYS = 10


def _slope(ys: List[float]) -> Optional[float]:
    """最小二乘斜率（每交易日的热度分变化）。样本 <2 返回 None。"""
    pts = [(i, v) for i, v in enumerate(ys) if v is not None]
    n = len(pts)
    if n < 2:
        return None
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    denom = n * sxx - sx * sx
    if not denom:
        return None
    return (n * sxy - sx * sy) / denom


def _streaks(flags: List[bool]):
    """返回 (当前连续, 最长连续, 累计天数)。flags 按日期升序。"""
    total = sum(1 for f in flags if f)
    mx = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        mx = max(mx, cur)
    tail = 0
    for f in reversed(flags):
        if not f:
            break
        tail += 1
    return tail, mx, total


def _classify(cur_streak, max_streak, days_in, n_dates,
              first_top_idx, last_top_idx,
              sustain_streak=None, sustain_ratio=None):
    """贴生命周期标签。first/last_top_idx 是在日期序列里的下标（无则 None）。

    sustain_streak / sustain_ratio 可覆盖 —— 时序口径要用自己那套（见常量区注释）。
    """
    sustain_streak = SUSTAIN_STREAK if sustain_streak is None else sustain_streak
    sustain_ratio = SUSTAIN_RATIO if sustain_ratio is None else sustain_ratio
    if days_in == 0:
        return 'intermittent'
    from_end_first = (n_dates - 1 - first_top_idx) if first_top_idx is not None else 999
    from_end_last = (n_dates - 1 - last_top_idx) if last_top_idx is not None else 999

    if from_end_first < NEW_DAYS and cur_streak >= 2:
        return 'emerging'
    if cur_streak >= sustain_streak or days_in >= max(2, round(n_dates * sustain_ratio)):
        return 'sustained'
    if cur_streak == 0 and max_streak >= 2 and from_end_last < FADE_RECENT:
        return 'fading'
    if max_streak <= 1 and days_in <= 2:
        return 'flash'
    return 'intermittent'


def build_timeline(board_type: str = 'concept', days: int = 60, top_k: int = 20,
                   include_all: bool = False, basis: str = 'cross',
                   hot_threshold: float = HOT_TS_THRESHOLD):
    """构建热度时间序列 + 持续性指标。

    Args:
        board_type: industry / concept / industry_sub
        days: 回看多少个**已记录的**快照日（不是自然日）
        top_k: 横截面口径下，排名进前几算"在榜"
        include_all: True 则返回窗口内所有板块（否则只返回曾"在榜"过的）
        basis: 'cross' 横截面 / 'self' 时序（见模块 docstring）
        hot_threshold: 时序口径下 heat_ts 达到多少算"在榜"
    """
    if basis == 'self':
        return _build_timeline_self(board_type, days, hot_threshold, include_all)
    SectorFlowDaily.ensure_table()

    dates = [r[0] for r in (db.session.query(SectorFlowDaily.date).distinct()
                            .filter(SectorFlowDaily.board_type == board_type)
                            .order_by(SectorFlowDaily.date.desc())
                            .limit(days).all())]
    dates = sorted(dates)
    if not dates:
        return {'board_type': board_type, 'basis': 'cross', 'dates': [], 'boards': [],
                'summary': {}, 'top_k': top_k, 'versions': [], 'recorded_days': 0,
                'label_ready': False, 'min_label_days': MIN_LABEL_DAYS, 'labels': LABELS}

    rows = (SectorFlowDaily.query
            .filter(SectorFlowDaily.board_type == board_type,
                    SectorFlowDaily.date >= dates[0])
            .all())

    dix = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    boards = {}
    versions = set()
    for r in rows:
        i = dix.get(r.date)
        if i is None:
            continue
        # 只统计真正有热度分的行：早期遗留快照（分页被截断、无成交额）heat_score 为 NULL，
        # 它们不该点亮"口径混合"告警，也不该在曲线上留假点位。
        if r.heat_score is not None:
            versions.add(r.heat_version or '?')
        b = boards.setdefault(r.board_code, {
            'board_code': r.board_code,
            'board_name': r.board_name or r.board_code,
            'heat': [None] * n, 'rank': [None] * n, 'chg': [None] * n,
            'share': [None] * n,
        })
        b['board_name'] = r.board_name or b['board_name']
        b['heat'][i] = r.heat_score
        b['rank'][i] = r.rank_heat
        b['chg'][i] = r.change_pct
        b['share'][i] = r.amount_share

    out = []
    for b in boards.values():
        flags = [(rk is not None and rk <= top_k) for rk in b['rank']]
        cur_streak, max_streak, days_in = _streaks(flags)
        if days_in == 0 and not include_all:
            continue
        idxs = [i for i, f in enumerate(flags) if f]
        first_i = idxs[0] if idxs else None
        last_i = idxs[-1] if idxs else None

        heats = [h for h in b['heat'] if h is not None]
        peak_i, peak = None, None
        for i, h in enumerate(b['heat']):
            if h is not None and (peak is None or h > peak):
                peak, peak_i = h, i
        heat_now = b['heat'][-1]
        rank_now = b['rank'][-1]
        slope = _slope(b['heat'][-SLOPE_N:])

        out.append({
            'board_code': b['board_code'],
            'board_name': b['board_name'],
            'heat': b['heat'], 'rank': b['rank'], 'chg': b['chg'], 'share': b['share'],
            'in_top': flags,
            'heat_now': heat_now,
            'rank_now': rank_now,
            'chg_now': b['chg'][-1],
            'cur_streak': cur_streak,
            'max_streak': max_streak,
            'days_in_top': days_in,
            'in_top_ratio': round(days_in / n * 100, 1) if n else 0,
            'first_top_date': dates[first_i].isoformat() if first_i is not None else None,
            'last_top_date': dates[last_i].isoformat() if last_i is not None else None,
            'peak_heat': peak,
            'peak_date': dates[peak_i].isoformat() if peak_i is not None else None,
            'drawdown': (round(peak - heat_now, 2)
                         if (peak is not None and heat_now is not None) else None),
            'slope5': round(slope, 2) if slope is not None else None,
            'avg_heat': round(sum(heats) / len(heats), 2) if heats else None,
            'label': (_classify(cur_streak, max_streak, days_in, n, first_i, last_i)
                      if n >= MIN_LABEL_DAYS else 'insufficient'),
        })

    # 排序：当前在榜的按当前排名，掉榜的按最后在榜时间靠后优先
    out.sort(key=lambda x: (x['rank_now'] is None or x['rank_now'] > top_k,
                            x['rank_now'] if x['rank_now'] is not None else 9999))

    # 汇总：今天 TOP-K 里各标签几个（这就是"今天的热点有几个是真持续"）
    today_top = [b for b in out if b['rank_now'] is not None and b['rank_now'] <= top_k]
    summary = {k: 0 for k in LABELS}
    for b in today_top:
        summary[b['label']] = summary.get(b['label'], 0) + 1

    return {
        'board_type': board_type,
        'basis': 'cross',
        'top_k': top_k,
        'dates': [d.isoformat() for d in dates],
        'recorded_days': n,
        'boards': out,
        'today_top_count': len(today_top),
        'summary': summary,
        'labels': LABELS,
        'label_ready': n >= MIN_LABEL_DAYS,
        'min_label_days': MIN_LABEL_DAYS,
        'versions': sorted(versions),
        'mixed_version': len(versions) > 1,
        'thresholds': {'new_days': NEW_DAYS, 'sustain_streak': SUSTAIN_STREAK,
                       'sustain_ratio': SUSTAIN_RATIO, 'fade_recent': FADE_RECENT},
    }


# ============ 时序口径 ============
def _build_timeline_self(board_type: str, days: int, hot_threshold: float,
                         include_all: bool):
    """时序口径：读 mkt_board_heat_ts，在榜 = heat_ts ≥ hot_threshold。

    与横截面口径的关键差异：
      - 没有"排名"这个概念（rank 恒为 None）—— 排名必然依赖横截面
      - 覆盖范围是 L1 候选池，不是全市场（heat_ts 只给池内板块算）
      - 历史可以很长（概念靠合成指数回到 2021 年），所以标签通常直接可用
    """
    from App.models.strategy.BoardHeatTs import BoardHeatTs
    from App.models.evaluation.Board import Board, TIER_L1
    BoardHeatTs.ensure_table()
    Board.ensure_table()

    cls_map = {'concept': '概念板块', 'industry': '行业板块'}
    want_cls = cls_map.get(board_type)
    codes = [b.board_code for b in Board.list_by_tier(TIER_L1)
             if (not want_cls or b.classification == want_cls)]
    empty = {'board_type': board_type, 'basis': 'self', 'dates': [], 'boards': [],
             'summary': {}, 'top_k': hot_threshold, 'hot_threshold': hot_threshold,
             'versions': [], 'recorded_days': 0, 'labels': LABELS,
             'label_ready': False, 'min_label_days': MIN_LABEL_DAYS,
             'today_top_count': 0, 'mixed_version': False}
    if not codes:
        empty['message'] = f'{board_type} 在 L1 候选池里没有板块，时序热度只给池内板块算'
        return empty

    dates = [r[0] for r in (db.session.query(BoardHeatTs.date).distinct()
                            .filter(BoardHeatTs.board_code.in_(codes))
                            .order_by(BoardHeatTs.date.desc()).limit(days).all())]
    dates = sorted(dates)
    if not dates:
        empty['message'] = '尚未计算时序热度，先在候选池页跑一次「时序热度」'
        return empty

    rows = (BoardHeatTs.query
            .filter(BoardHeatTs.board_code.in_(codes),
                    BoardHeatTs.date >= dates[0], BoardHeatTs.date <= dates[-1])
            .all())
    dix = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    boards, versions = {}, set()
    for r in rows:
        i = dix.get(r.date)
        if i is None:
            continue
        versions.add(r.version or '?')
        b = boards.setdefault(r.board_code, {
            'board_code': r.board_code, 'board_name': r.board_name or r.board_code,
            'heat': [None] * n, 'chg': [None] * n, 'share': [None] * n,
            'source': r.data_source})
        b['heat'][i] = r.heat_ts
        b['chg'][i] = r.change_pct

    out = []
    for b in boards.values():
        flags = [(h is not None and h >= hot_threshold) for h in b['heat']]
        cur_streak, max_streak, days_in = _streaks(flags)
        if days_in == 0 and not include_all:
            continue
        idxs = [i for i, f in enumerate(flags) if f]
        first_i, last_i = (idxs[0], idxs[-1]) if idxs else (None, None)
        heats = [h for h in b['heat'] if h is not None]
        peak_i, peak = None, None
        for i, h in enumerate(b['heat']):
            if h is not None and (peak is None or h > peak):
                peak, peak_i = h, i
        heat_now = b['heat'][-1]
        slope = _slope(b['heat'][-SLOPE_N:])
        out.append({
            'board_code': b['board_code'], 'board_name': b['board_name'],
            'heat': b['heat'], 'rank': [None] * n, 'chg': b['chg'],
            'share': b['share'], 'in_top': flags,
            'heat_now': heat_now, 'rank_now': None, 'chg_now': b['chg'][-1],
            'cur_streak': cur_streak, 'max_streak': max_streak, 'days_in_top': days_in,
            'in_top_ratio': round(days_in / n * 100, 1) if n else 0,
            'first_top_date': dates[first_i].isoformat() if first_i is not None else None,
            'last_top_date': dates[last_i].isoformat() if last_i is not None else None,
            'peak_heat': peak,
            'peak_date': dates[peak_i].isoformat() if peak_i is not None else None,
            'drawdown': (round(peak - heat_now, 2)
                         if (peak is not None and heat_now is not None) else None),
            'slope5': round(slope, 2) if slope is not None else None,
            'avg_heat': round(sum(heats) / len(heats), 2) if heats else None,
            'data_source': b['source'],
            'label': (_classify(cur_streak, max_streak, days_in, n, first_i, last_i,
                                sustain_streak=SUSTAIN_STREAK_TS,
                                sustain_ratio=SUSTAIN_RATIO_TS)
                      if n >= MIN_LABEL_DAYS else 'insufficient'),
        })

    # 时序口径没有排名，按当前热度排序
    out.sort(key=lambda x: -(x['heat_now'] if x['heat_now'] is not None else -1))
    today_hot = [b for b in out if b['in_top'][-1]]
    summary = {k: 0 for k in LABELS}
    for b in out:
        summary[b['label']] = summary.get(b['label'], 0) + 1

    return {
        'board_type': board_type, 'basis': 'self',
        'top_k': hot_threshold, 'hot_threshold': hot_threshold,
        'dates': [d.isoformat() for d in dates], 'recorded_days': n,
        'boards': out, 'today_top_count': len(today_hot), 'summary': summary,
        'labels': LABELS, 'label_ready': n >= MIN_LABEL_DAYS,
        'min_label_days': MIN_LABEL_DAYS,
        'versions': sorted(versions), 'mixed_version': len(versions) > 1,
        'thresholds': {'hot_threshold': hot_threshold, 'new_days': NEW_DAYS,
                       'sustain_streak': SUSTAIN_STREAK_TS,
                       'sustain_ratio': SUSTAIN_RATIO_TS, 'fade_recent': FADE_RECENT},
    }


def board_series(board_code: str, days: int = 120):
    """单板块的热度序列（给详情小图用）。"""
    SectorFlowDaily.ensure_table()
    rows = (SectorFlowDaily.query
            .filter(SectorFlowDaily.board_code == board_code)
            .order_by(SectorFlowDaily.date.desc())
            .limit(days).all())
    rows = list(reversed(rows))
    if not rows:
        return {'board_code': board_code, 'points': []}
    return {
        'board_code': board_code,
        'board_name': rows[-1].board_name,
        'board_type': rows[-1].board_type,
        'points': [{
            'date': r.date.isoformat(),
            'heat': r.heat_score, 'rank': r.rank_heat,
            'chg': r.change_pct, 'share': r.amount_share,
            'turnover': r.turnover_rate, 'main_net': r.main_net,
            'up': r.up_count, 'down': r.down_count,
            'lead': r.lead_stock, 'version': r.heat_version,
        } for r in rows],
    }


def coverage():
    """L0 记录覆盖情况：各类型记录了多少天、最新到哪天、来源分布。给页面顶部做诚实提示。"""
    SectorFlowDaily.ensure_table()
    rows = (db.session.query(SectorFlowDaily.board_type,
                             db.func.count(db.distinct(SectorFlowDaily.date)),
                             db.func.min(SectorFlowDaily.date),
                             db.func.max(SectorFlowDaily.date))
            .group_by(SectorFlowDaily.board_type).all())
    src = (db.session.query(SectorFlowDaily.board_type, SectorFlowDaily.source,
                            db.func.count(db.distinct(SectorFlowDaily.date)))
           .group_by(SectorFlowDaily.board_type, SectorFlowDaily.source).all())
    out = {}
    for bt, ndays, dmin, dmax in rows:
        out[bt] = {'days': int(ndays or 0),
                   'first': dmin.isoformat() if dmin else None,
                   'last': dmax.isoformat() if dmax else None,
                   'sources': {}}
    for bt, s, ndays in src:
        if bt in out:
            out[bt]['sources'][s or 'unknown'] = int(ndays or 0)
    return out
