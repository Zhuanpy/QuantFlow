# -*- coding: utf-8 -*-
"""三正态分布快照计算服务

入口：compute_dist_snapshot(code)
  - 读 15m parquet → 拆 cycle → 已完成 cycle 做正态拟合
  - 当前未完成 cycle 算 z 分数 / 经验分位
  - upsert 写 stock_dist_snapshot
算法跟 /stock/api/<code>/cycles + 前端 renderDist 一致，但脱离 HTTP 层独立可批跑。

口径细节（保持与前端图表一致）：
  - 只用 completed=True 的周期做拟合，避免把未结算的 cycle 拉低均值
  - 最近 MAX_HIST=60 个完成周期（贴近当前行情，避免远古样本喧宾夺主）
  - 样本不足 MIN_FIT=8 不拟合，对应字段返回 None
  - 当前周期：取最新 cycle（无论是否完成），按其方向归到对应指标
    * 如果它在 inprogress_sidx 集合里（源数据里跨过 snapshot_date 才结束）
      → cycle_length_max 用 len(g)-1 重算（不引入未来）
  - amplitude_max 用极值口径：周期内 high.max(上涨)/low.min(下跌) 对比 flip 行起点价取绝对值
    与 stock_detail_route.api_cycles 的 amplitude_max(=amp_extreme) 保持一致，
    确保 /screener/ 和 /stock/ 详情页 renderDist 算出来的分位/z 是同一个序列。
    （注：合并口径 _build_cycles_merged 一直就是极值口径，此处统一原始口径）
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Dict, Optional

import pandas as pd

from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.exts import db
from App.models.strategy.StockDistSnapshot import StockDistSnapshot

logger = logging.getLogger(__name__)

MAX_HIST = 60       # 拟合最多用最近 60 个完成周期
MIN_FIT = 8         # 样本 < 8 不拟合（与前端 renderDist 一致）

# 多周期趋势 → 分位 P（与 stock_live.html 的 MTF_P_TABLE_AMP/LEN 完全一致）。
# 以 15m 方向为当前波基准，数更高周期(30m/60m)同向确认数 0/1/2 → 取表值。
MTF_P_AMP = {1: {0: 55, 1: 65, 2: 80}, -1: {0: 65, 1: 80, 2: 90}}   # 上涨 / 下跌
MTF_P_LEN = {1: {0: 50, 1: 65, 2: 75}, -1: {0: 50, 1: 65, 2: 75}}
# A 股 15m K 线每交易日 16 个收盘时点（与前端 BAR_TIMES 一致）
_BAR_TIMES = ['09:45', '10:00', '10:15', '10:30', '10:45', '11:00', '11:15', '11:30',
              '13:15', '13:30', '13:45', '14:00', '14:15', '14:30', '14:45', '15:00']


# -----------------------------------------------------------------
# 内部：把 15m DF 拆成 cycles，每个 cycle 算量化指标
# -----------------------------------------------------------------
def _build_cycles(df: pd.DataFrame, as_of_ts: Optional[pd.Timestamp]) -> list:
    """复用 stock_detail_route.api_cycles 的逻辑提取 cycles。

    Args:
        df:        已加载并按 date 排序的 15m DataFrame（含 SignalStartIndex 等）
        as_of_ts:  快照截止时刻；用于识别"在那天仍未结束"的周期，对它们重算
                   防未来泄漏的 length / amplitude

    Returns:
        list[dict]: 每个 cycle 一行，含 direction / cycle_length_max / amplitude_max /
                    cycle_1m_vol_max5 / completed
    """
    if df.empty or 'SignalStartIndex' not in df.columns:
        return []

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    # 识别"跨过 as_of 才结束"的周期 —— 它们的 CycleLengthMax/CycleAmplitudeMax
    # 是结束后回填的最终值，直接用会泄漏未来。
    inprogress_sidx = set()
    if as_of_ts is not None:
        sig_full = df[df['SignalStartIndex'].notna()]
        for sidx, gg in sig_full.groupby('SignalStartIndex'):
            if gg['date'].max() > as_of_ts:
                inprogress_sidx.add(sidx)
        df = df[df['date'] <= as_of_ts]

    df = df[df['SignalStartIndex'].notna()].sort_values('date')
    if df.empty:
        return []

    has_amp = 'CycleAmplitudeMax' in df.columns
    has_choice = 'SignalChoice' in df.columns
    has_signal = 'Signal' in df.columns
    has_v5 = 'Cycle1mVolMax5' in df.columns
    has_sp = 'StartPrice' in df.columns
    has_close = 'close' in df.columns
    has_high = 'high' in df.columns
    has_low = 'low' in df.columns

    cycles = []
    for sidx, g in df.groupby('SignalStartIndex', sort=True):
        is_inprogress = sidx in inprogress_sidx

        # 方向
        direction = 0
        if has_signal:
            sv = pd.to_numeric(g['Signal'], errors='coerce').dropna()
            if len(sv):
                direction = int(1 if sv.iloc[-1] > 0 else -1 if sv.iloc[-1] < 0 else 0)

        # 周期长度
        if is_inprogress:
            clen = max(0, len(g) - 1)
        else:
            clen = pd.to_numeric(g['CycleLengthMax'], errors='coerce').max()
            clen = None if pd.isna(clen) else int(clen)

        # 周期振幅（极值口径，绝对值）：周期内 high.max(上涨) / low.min(下跌) 对比起点价。
        # 与 stock_detail_route.api_cycles 的 amplitude_max(=amp_extreme) 完全一致，
        # 确保 /screener/ 历史页与 /stock/ 详情页分布图同口径（极值 ≥ 收盘）。
        # 起点价用 flip 行(组首)的 StartPrice 首个非空值——不要 iloc[-1]，周期边界处
        # StartPrice 会被相邻周期的 ffill 值污染，抓到下一周期起点导致振幅算错。
        amp = None
        sp_at_flip = None
        if has_sp:
            _sp = pd.to_numeric(g['StartPrice'], errors='coerce').dropna()
            if len(_sp):
                sp_at_flip = float(_sp.iloc[0])
        if sp_at_flip and sp_at_flip != 0 and has_high and has_low:
            hi = float(pd.to_numeric(g['high'], errors='coerce').max())
            lo = float(pd.to_numeric(g['low'], errors='coerce').min())
            if direction > 0:
                amp = abs(round((hi - sp_at_flip) / sp_at_flip, 4))
            elif direction < 0:
                amp = abs(round((lo - sp_at_flip) / sp_at_flip, 4))
            else:
                amp = round(max(abs((hi - sp_at_flip) / sp_at_flip),
                                abs((lo - sp_at_flip) / sp_at_flip)), 4)
        # 回退1：无 high/low 时退到收盘口径 (末根 close vs 起点)
        if amp is None and has_close and sp_at_flip and sp_at_flip != 0:
            close_series = pd.to_numeric(g['close'], errors='coerce').dropna()
            if len(close_series):
                amp = abs(round((float(close_series.iloc[-1]) - sp_at_flip) / sp_at_flip, 4))
        # 回退2：旧数据无 close/StartPrice 时退到 CycleAmplitudeMax 极值口径
        if amp is None and has_amp:
            a = pd.to_numeric(g['CycleAmplitudeMax'], errors='coerce').dropna()
            amp = float(a.abs().max()) if len(a) else None

        # 量能
        v5 = None
        if has_v5:
            vv = pd.to_numeric(g['Cycle1mVolMax5'], errors='coerce').dropna()
            v5 = float(vv.max()) if len(vv) else None

        # 完成标志：is_inprogress 强制 False
        if is_inprogress:
            completed = False
        elif has_choice:
            completed = bool(g['SignalChoice'].notna().any())
        else:
            completed = None

        # 起点价 / 最新价（供"预估目标价"用）：起点=flip行StartPrice；最新=末根 close
        last_price = None
        if has_close:
            _cl = pd.to_numeric(g['close'], errors='coerce').dropna()
            if len(_cl):
                last_price = float(_cl.iloc[-1])

        cycles.append({
            'direction': direction,
            'signal_start': sidx,          # 该周期的 SignalStartIndex（起始时间），用于拼趋势名
            'cycle_length_max': clen,
            'amplitude_max': amp,
            'cycle_1m_vol_max5': v5,
            'completed': completed,
            'start_price': sp_at_flip,
            'last_price': last_price,
        })
    return cycles


def _build_cycles_merged(df: pd.DataFrame, as_of_ts: Optional[pd.Timestamp],
                         merge_below: float) -> list:
    """合并 <merge_below% 的小周期后的 cycles —— 口径与 stock 详情页 api_cycles 的
    「合并视图」完全一致：连续 SSI 段切分 + 极值口径振幅 + 迭代三合一 + 合并后整段重算。

    规则：某"中间"周期振幅 < 阈值时，与前后两个相邻(同向)周期并成一个大周期，方向取相邻
    周期方向；迭代到无可并。合并后长度=整段根数、振幅=整段极值口径、能量=整段峰值。

    返回与 _build_cycles 相同结构的 dict 列表（direction / signal_start /
    cycle_length_max / amplitude_max / cycle_1m_vol_max5 / completed）。
    """
    if df.empty or 'SignalStartIndex' not in df.columns:
        return []
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df[df['SignalStartIndex'].notna()].sort_values('date')
    if as_of_ts is not None:           # 历史快照：截断到 as_of，杜绝未来泄漏
        df = df[df['date'] <= as_of_ts]
    df = df.reset_index(drop=True)
    if df.empty:
        return []

    has_choice = 'SignalChoice' in df.columns
    has_signal = 'Signal' in df.columns
    has_v5 = 'Cycle1mVolMax5' in df.columns
    has_sp = 'StartPrice' in df.columns
    has_high = 'high' in df.columns
    has_low = 'low' in df.columns

    # 连续同 SSI 段（按时间），不是 groupby(值)——SSI 会复用/非单调
    ssi_key = df['SignalStartIndex'].astype(str)
    seg_bounds = []
    s = 0
    for i in range(1, len(df) + 1):
        if i == len(df) or ssi_key.iloc[i] != ssi_key.iloc[s]:
            seg_bounds.append((s, i - 1))
            s = i

    def _amp_dir(a, b):
        g = df.iloc[a:b + 1]
        d = 0
        if has_signal:
            sv = pd.to_numeric(g['Signal'], errors='coerce').dropna()
            if len(sv):
                d = int(1 if sv.iloc[-1] > 0 else -1 if sv.iloc[-1] < 0 else 0)
        sp = None
        if has_sp:
            _sp = pd.to_numeric(g['StartPrice'], errors='coerce').dropna()
            if len(_sp):
                sp = float(_sp.iloc[0])
        amp = None
        if sp and sp != 0 and has_high and has_low:
            hi = float(pd.to_numeric(g['high'], errors='coerce').max())
            lo = float(pd.to_numeric(g['low'], errors='coerce').min())
            if d > 0:
                amp = abs((hi - sp) / sp)
            elif d < 0:
                amp = abs((lo - sp) / sp)
            else:
                amp = max(abs((hi - sp) / sp), abs((lo - sp) / sp))
        return d, amp

    thr = (merge_below or 0) / 100.0
    guard = 0
    while thr > 0 and len(seg_bounds) >= 3 and guard < 100000:
        guard += 1
        best = None
        for i in range(1, len(seg_bounds) - 1):
            _, amp = _amp_dir(*seg_bounds[i])
            if amp is not None and amp < thr and (best is None or amp < best[1]):
                best = (i, amp)
        if best is None:
            break
        i = best[0]
        seg_bounds = (seg_bounds[:i - 1]
                      + [(seg_bounds[i - 1][0], seg_bounds[i + 1][1])]
                      + seg_bounds[i + 2:])

    # 截断到 as_of 后，最后一段即"当前进行中"周期
    inprogress_idx = {len(seg_bounds) - 1} if seg_bounds else set()

    cycles = []
    for k, (a, b) in enumerate(seg_bounds):
        g = df.iloc[a:b + 1]
        is_inprogress = k in inprogress_idx
        direction = 0
        if has_signal:
            sv = pd.to_numeric(g['Signal'], errors='coerce').dropna()
            if len(sv):
                direction = int(1 if sv.iloc[-1] > 0 else -1 if sv.iloc[-1] < 0 else 0)
        clen = (len(g) - 1) if is_inprogress else len(g)
        # 振幅：极值口径（整段 high/low vs 起点价）
        amp = None
        if has_sp and has_high and has_low:
            _sp = pd.to_numeric(g['StartPrice'], errors='coerce').dropna()
            if len(_sp):
                sp = float(_sp.iloc[0])
                if sp != 0:
                    hi = float(pd.to_numeric(g['high'], errors='coerce').max())
                    lo = float(pd.to_numeric(g['low'], errors='coerce').min())
                    if direction > 0:
                        amp = abs(round((hi - sp) / sp, 4))
                    elif direction < 0:
                        amp = abs(round((lo - sp) / sp, 4))
                    else:
                        amp = round(max(abs((hi - sp) / sp), abs((lo - sp) / sp)), 4)
        v5 = None
        if has_v5:
            vv = pd.to_numeric(g['Cycle1mVolMax5'], errors='coerce').dropna()
            v5 = float(vv.max()) if len(vv) else None
        if is_inprogress:
            completed = False
        elif has_choice:
            completed = bool(g['SignalChoice'].notna().any())
        else:
            completed = None
        # 起点价 / 最新价（供"预估目标价"用）
        start_price = None
        if has_sp:
            _sp2 = pd.to_numeric(g['StartPrice'], errors='coerce').dropna()
            if len(_sp2):
                start_price = float(_sp2.iloc[0])
        last_price = None
        _cl = pd.to_numeric(g['close'], errors='coerce').dropna() if 'close' in g.columns else []
        if len(_cl):
            last_price = float(_cl.iloc[-1])
        cycles.append({
            'direction': direction,
            'signal_start': df['SignalStartIndex'].iloc[a],
            'cycle_length_max': int(clen),
            'amplitude_max': amp,
            'cycle_1m_vol_max5': v5,
            'completed': completed,
            'start_price': start_price,
            'last_price': last_price,
        })
    return cycles


# -----------------------------------------------------------------
# 内部：算单方向单指标的正态分布参数（保持与前端 renderDist 一致）
# -----------------------------------------------------------------
def _fit_direction(cycles: list, direction: int, value_getter, current_cycle: Optional[dict]) -> dict:
    """对指定方向 cycles 算 mean/std/current/z/pct/n。

    Args:
        cycles:        全部 cycle 列表（按时间序）
        direction:     1=上涨 / -1=下跌
        value_getter:  lambda c -> 数值 或 None
        current_cycle: 当前未完成 cycle（用最后一个）；方向不匹配则忽略

    Returns:
        {'mean': .., 'std': .., 'current': .., 'z': .., 'pct': .., 'n': ..}
    """
    out = {'mean': None, 'std': None, 'current': None, 'z': None, 'pct': None, 'n': 0}

    subset = [c for c in cycles if c['direction'] == direction]
    completed = [c for c in subset if c['completed'] is True]
    recent = completed[-MAX_HIST:]
    hist = [v for v in (value_getter(c) for c in recent) if v is not None]
    n = len(hist)
    out['n'] = n
    if n < MIN_FIT:
        # 仍把 current 算上（如果有），方便筛选"无历史样本但当前周期已开始"的标的
        if current_cycle and current_cycle['direction'] == direction:
            out['current'] = value_getter(current_cycle)
        return out

    mean = sum(hist) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in hist) / n)
    out['mean'] = round(mean, 4)
    out['std'] = round(std, 4)

    if current_cycle and current_cycle['direction'] == direction:
        cur = value_getter(current_cycle)
        if cur is not None:
            out['current'] = round(float(cur), 4)
            if std > 0:
                out['z'] = round((cur - mean) / std, 3)
            le = sum(1 for v in hist if v <= cur)
            out['pct'] = round(le / n, 3)
    return out


# -----------------------------------------------------------------
# 盘中预估：多周期方向 → 分位P → 预估目标价/结束时间（服务端复刻 stock_live.html）
# -----------------------------------------------------------------
def _tf_direction(df15: pd.DataFrame, period: str, as_of_ts) -> int:
    """某周期(15m/30m/60m)截至 as_of 的「当前波方向」：+1/-1/0。

    15m 直接用已有 Signal；30m/60m 由 15m 重采样后跑 v2 信号管线取末根 Signal 符号。
    """
    try:
        d = df15.copy()
        d['date'] = pd.to_datetime(d['date'])
        if as_of_ts is not None:
            d = d[d['date'] <= as_of_ts]
        if d.empty:
            return 0
        if period != '15m':
            from App.codes.utils.timeframe_resample import resample_15m
            from App.codes.Signals.MacdSignalV2 import compute_v2_signals_pipeline
            d = resample_15m(d, period)
            d = compute_v2_signals_pipeline(d, n=7)
        sv = pd.to_numeric(d.get('Signal'), errors='coerce').dropna() if 'Signal' in d.columns else []
        if len(sv) == 0:
            return 0
        v = sv.iloc[-1]
        return 1 if v > 0 else -1 if v < 0 else 0
    except Exception:
        logger.debug(f'[fc] {period} 方向计算失败', exc_info=True)
        return 0


def _invnorm(p: float) -> float:
    """标准正态分位（P/100 → z）。用标准库 NormalDist，避免引 scipy。"""
    from statistics import NormalDist
    p = min(0.999, max(0.001, float(p)))
    return NormalDist().inv_cdf(p)


def _add_trading_bars(start_ts, bars: int):
    """从某 15m 收盘时点按交易时段(跳午休/周末，不计节假日)顺推 N 根，返回 datetime。
    与 stock_live.html 的 addTradingBars 同口径。"""
    if start_ts is None:
        return None
    ts = pd.Timestamp(start_ts)
    hhmm = ts.strftime('%H:%M')
    idx = _BAR_TIMES.index(hhmm) if hhmm in _BAR_TIMES else next(
        (i for i, t in enumerate(_BAR_TIMES) if t >= hhmm), len(_BAR_TIMES) - 1)
    total = idx + max(0, int(round(bars)))
    day_adv, slot = divmod(total, 16)
    d = ts.normalize()
    advanced = 0
    while advanced < day_adv:
        d = d + pd.Timedelta(days=1)
        if d.weekday() < 5:      # 跳周末（不计节假日）
            advanced += 1
    eh, em = _BAR_TIMES[slot].split(':')
    return d.replace(hour=int(eh), minute=int(em), second=0)


def _compute_forecast(df, current_direction, current_cycle,
                      amp_stats, len_stats, last_bar_date, as_of_ts) -> dict:
    """按多周期趋势定 P，投影当前周期的整段振幅/目标价/长度/结束时间。"""
    fc = {'fc_dir_15m': None, 'fc_dir_30m': None, 'fc_dir_60m': None, 'fc_confirm': None,
          'fc_amp_p': None, 'fc_len_p': None, 'fc_proj_amp_pct': None,
          'fc_proj_target_price': None, 'fc_proj_len_bars': None, 'fc_proj_end_date': None}
    base = 1 if current_direction > 0 else -1 if current_direction < 0 else 0
    d15 = base                                     # 15m 方向即当前周期方向（无需重算）
    d30 = _tf_direction(df, '30m', as_of_ts)
    d60 = _tf_direction(df, '60m', as_of_ts)
    fc.update(fc_dir_15m=d15, fc_dir_30m=d30, fc_dir_60m=d60)
    if base == 0:
        return fc
    confirm = (1 if d30 == base else 0) + (1 if d60 == base else 0)
    p_amp = MTF_P_AMP[base][confirm]
    p_len = MTF_P_LEN[base][confirm]
    fc.update(fc_confirm=confirm, fc_amp_p=p_amp, fc_len_p=p_len)

    # 振幅投影：proj = mean + z(P)·std（fraction），目标价 = 起点×(1±proj)
    start = current_cycle.get('start_price')
    if amp_stats.get('mean') is not None and amp_stats.get('std') is not None and start:
        proj = max(0.0, amp_stats['mean'] + _invnorm(p_amp / 100) * amp_stats['std'])
        fc['fc_proj_amp_pct'] = round(proj * 100, 2)
        fc['fc_proj_target_price'] = round(start * (1 + proj if base > 0 else 1 - proj), 4)

    # 长度投影：proj 根数 = mean + z(P)·std；结束时间 = 从最新根顺推剩余根数
    if len_stats.get('mean') is not None and len_stats.get('std') is not None:
        proj_len = max(1, int(round(len_stats['mean'] + _invnorm(p_len / 100) * len_stats['std'])))
        fc['fc_proj_len_bars'] = proj_len
        elapsed = current_cycle.get('cycle_length_max') or 0
        remaining = max(0, proj_len - int(elapsed))
        end = _add_trading_bars(last_bar_date, remaining)
        fc['fc_proj_end_date'] = end.to_pydatetime() if end is not None else None
    return fc


# -----------------------------------------------------------------
# 公共入口
# -----------------------------------------------------------------
def compute_dist_snapshot(code: str,
                          snapshot_date: Optional[date] = None,
                          stock_name: Optional[str] = None,
                          commit: bool = True,
                          merge_below: float = 0) -> Optional[StockDistSnapshot]:
    """对单只股票算三正态分布快照，upsert 进 stock_dist_snapshot。

    Args:
        code:          股票代码
        snapshot_date: 快照日期，默认今天；同一日同口径重跑会覆盖（按唯一约束 upsert）
        stock_name:    股票名（不传会从 StockInfo 查；查不到留空）
        commit:        False 时只算不写库（测试用）
        merge_below:   周期合并阈值(%)。0=原始口径(每个信号周期独立)；>0=把振幅<该值的
                       中间小周期与前后同向周期合并成大周期后再统计（去噪口径，与 15m 详情页
                       「合并<X%小周期」开关一致）。与 0 口径分别存为独立快照行。

    Returns:
        StockDistSnapshot 实例；15m 数据不存在时返回 None
    """
    snapshot_date = snapshot_date or date.today()
    merge_below = float(merge_below or 0)
    as_of_ts = pd.Timestamp(snapshot_date) + pd.Timedelta(hours=15)  # 当日 15:00 收盘

    df = StockData15m.load_15m(code)
    if df.empty:
        logger.warning(f'[dist] {code} 无 15m 数据，跳过')
        return None

    if merge_below > 0:
        cycles = _build_cycles_merged(df, as_of_ts, merge_below)
    else:
        cycles = _build_cycles(df, as_of_ts=as_of_ts)
    if not cycles:
        logger.warning(f'[dist] {code} 截至 {snapshot_date} 无 cycles')
        return None

    # 当前周期：最新一个；通常进行中
    current_cycle = cycles[-1]
    current_direction = current_cycle['direction']

    # ---- 三指标 × 上/下跌 ----
    get_len = lambda c: c['cycle_length_max']
    get_amp = lambda c: c['amplitude_max']
    get_v5 = lambda c: c['cycle_1m_vol_max5']
    len_up = _fit_direction(cycles, 1, get_len, current_cycle)
    len_dn = _fit_direction(cycles, -1, get_len, current_cycle)
    amp_up = _fit_direction(cycles, 1, get_amp, current_cycle)
    amp_dn = _fit_direction(cycles, -1, get_amp, current_cycle)
    v5_up = _fit_direction(cycles, 1, get_v5, current_cycle)
    v5_dn = _fit_direction(cycles, -1, get_v5, current_cycle)

    last_bar_date = pd.to_datetime(df['date'].iloc[-1])
    # last_bar_date 可能比 snapshot_date 晚（如果用户拿历史日期跑快照） →
    # 真正的"基于 K 线"应该是 ≤ as_of_ts 的最后一根
    df_dates = pd.to_datetime(df['date'])
    in_window = df_dates[df_dates <= as_of_ts]
    if len(in_window):
        last_bar_date = in_window.max()

    # 取名字（如果没传）
    if not stock_name:
        try:
            from App.models.data.basic_info import StockInfo
            # find_stock 会处理 000001 这类同代码歧义（个股 vs 指数）
            info = StockInfo.find_stock(code)
            if info:
                stock_name = info.name
        except Exception:
            pass

    # upsert：先查再写（按 code + 日期 + 合并口径 三元组）
    row = (StockDistSnapshot.query
           .filter_by(stock_code=code, snapshot_date=snapshot_date, merge_below=merge_below)
           .first())
    if row is None:
        row = StockDistSnapshot(stock_code=code, snapshot_date=snapshot_date,
                                merge_below=merge_below)
        db.session.add(row)

    row.stock_name = stock_name
    row.last_bar_date = last_bar_date.to_pydatetime() if hasattr(last_bar_date, 'to_pydatetime') else last_bar_date
    row.current_direction = current_direction
    # 当前周期趋势名（与 15m 页「当前趋势」一致）：方向词 + #起始时间(YYMMDD-HHMM)
    row.current_signal_name = _signal_name_of(current_direction, current_cycle.get('signal_start'))
    # 当前周期起点价/最新价（供股票池"预估目标价"用）
    row.cur_start_price = current_cycle.get('start_price')
    row.cur_last_price = current_cycle.get('last_price')

    _assign(row, 'len_up', len_up)
    _assign(row, 'len_dn', len_dn)
    _assign(row, 'amp_up', amp_up)
    _assign(row, 'amp_dn', amp_dn)
    _assign(row, 'v5_up', v5_up)
    _assign(row, 'v5_dn', v5_dn)

    # ---- 盘中预估快照（多周期定P → 目标价/结束时间）----
    amp_cur = amp_up if current_direction > 0 else amp_dn
    len_cur = len_up if current_direction > 0 else len_dn
    fc = _compute_forecast(df, current_direction, current_cycle,
                           amp_cur, len_cur, last_bar_date, as_of_ts)
    for k, v in fc.items():
        setattr(row, k, v)

    if commit:
        db.session.commit()
    return row


def _signal_name_of(direction, signal_start):
    """当前周期趋势名：与 stock 详情页 _signal_name 同格式（↑涨/↓跌/·盘整 + #YYMMDD-HHMM）。"""
    arrow = '↑' if (direction or 0) > 0 else '↓' if (direction or 0) < 0 else '·'
    word = '涨' if (direction or 0) > 0 else '跌' if (direction or 0) < 0 else '盘整'
    tag = ''
    if signal_start is not None:
        try:
            tag = '#' + pd.Timestamp(signal_start).strftime('%y%m%d-%H%M')
        except Exception:
            tag = ''
    return f'{arrow}{word}{tag}'


def _assign(row: StockDistSnapshot, prefix: str, stats: Dict):
    """把 _fit_direction 输出的 dict 写到 row 上对应的字段。"""
    setattr(row, f'{prefix}_mean', stats['mean'])
    setattr(row, f'{prefix}_std', stats['std'])
    setattr(row, f'{prefix}_current', stats['current'])
    setattr(row, f'{prefix}_z', stats['z'])
    setattr(row, f'{prefix}_pct', stats['pct'])
    setattr(row, f'{prefix}_n', stats['n'])
