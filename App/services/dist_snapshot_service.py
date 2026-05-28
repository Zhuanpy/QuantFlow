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
  - amplitude_max 用 close 口径：(本周期最后一根 close − StartPrice)/StartPrice 取绝对值
    与 stock_detail_route.api_cycles 的 amp_close 字段保持一致，
    确保 /screener/ 和 /stock/ 详情页 renderDist 算出来的分位/z 是同一个序列。
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

        # 周期振幅（close 口径，绝对值）：(本周期最后一根 close − StartPrice) / StartPrice
        # 已完成周期 → 周期收盘价对比上一极值（实际兑现）
        # 进行中周期 → 最新 close 对比上一极值（当前 P&L）
        # 与 stock_detail_route.api_cycles 的 amp_close 一致，避免 screener 和详情页量纲不同
        amp = None
        if has_close and has_sp:
            sp_series = pd.to_numeric(g['StartPrice'], errors='coerce').dropna()
            close_series = pd.to_numeric(g['close'], errors='coerce').dropna()
            if len(sp_series) and len(close_series):
                sp = float(sp_series.iloc[-1])
                cl = float(close_series.iloc[-1])
                if sp != 0:
                    amp = abs(round((cl - sp) / sp, 4))
        # 回退：旧数据无 close/StartPrice 时退到 CycleAmplitudeMax 极值口径
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

        cycles.append({
            'direction': direction,
            'cycle_length_max': clen,
            'amplitude_max': amp,
            'cycle_1m_vol_max5': v5,
            'completed': completed,
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
# 公共入口
# -----------------------------------------------------------------
def compute_dist_snapshot(code: str,
                          snapshot_date: Optional[date] = None,
                          stock_name: Optional[str] = None,
                          commit: bool = True) -> Optional[StockDistSnapshot]:
    """对单只股票算三正态分布快照，upsert 进 stock_dist_snapshot。

    Args:
        code:          股票代码
        snapshot_date: 快照日期，默认今天；同一日重跑会覆盖（按唯一约束 upsert）
        stock_name:    股票名（不传会从 StockInfo 查；查不到留空）
        commit:        False 时只算不写库（测试用）

    Returns:
        StockDistSnapshot 实例；15m 数据不存在时返回 None
    """
    snapshot_date = snapshot_date or date.today()
    as_of_ts = pd.Timestamp(snapshot_date) + pd.Timedelta(hours=15)  # 当日 15:00 收盘

    df = StockData15m.load_15m(code)
    if df.empty:
        logger.warning(f'[dist] {code} 无 15m 数据，跳过')
        return None

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

    # upsert：先查再写
    row = (StockDistSnapshot.query
           .filter_by(stock_code=code, snapshot_date=snapshot_date)
           .first())
    if row is None:
        row = StockDistSnapshot(stock_code=code, snapshot_date=snapshot_date)
        db.session.add(row)

    row.stock_name = stock_name
    row.last_bar_date = last_bar_date.to_pydatetime() if hasattr(last_bar_date, 'to_pydatetime') else last_bar_date
    row.current_direction = current_direction

    _assign(row, 'len_up', len_up)
    _assign(row, 'len_dn', len_dn)
    _assign(row, 'amp_up', amp_up)
    _assign(row, 'amp_dn', amp_dn)
    _assign(row, 'v5_up', v5_up)
    _assign(row, 'v5_dn', v5_dn)

    if commit:
        db.session.commit()
    return row


def _assign(row: StockDistSnapshot, prefix: str, stats: Dict):
    """把 _fit_direction 输出的 dict 写到 row 上对应的字段。"""
    setattr(row, f'{prefix}_mean', stats['mean'])
    setattr(row, f'{prefix}_std', stats['std'])
    setattr(row, f'{prefix}_current', stats['current'])
    setattr(row, f'{prefix}_z', stats['z'])
    setattr(row, f'{prefix}_pct', stats['pct'])
    setattr(row, f'{prefix}_n', stats['n'])
