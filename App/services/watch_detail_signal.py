# -*- coding: utf-8 -*-
"""盯盘明细信号：为盯盘中的个股生成三条盯盘判断，供 /trade/watch 每只股票下的明细行展示。

盯盘1（5m 三均线，MA5/MA10/MA20）：
  - 连续 CONFIRM_BARS(7) 根 5m K 线「最高价」都低于三均线带下沿(min(MA)) → 跌破确认 → 卖出（防继续下跌）。
  - 连续 CONFIRM_BARS 根 5m K 线「最低价」都高于三均线带上沿(max(MA)) → 站稳确认 → 持有；空仓则买入（避免踏空）。
  - 否则均线纠缠 → 观望。
    （用「三均线带」而非单线：高点全在带下沿之下=彻底跌破均线组；低点全在带上沿之上=彻底站上均线组。
      周期是模块常量 MA_PERIODS，可按盘感改。）
盯盘2（板块 15m 同步）：个股 15m 方向 vs 所属东财行业板块 15m 方向。
  - 同步下跌 → 坚定止损/空仓观望；同步上涨 → 持有/坚定买入；方向不一致 → 分歧留意。
盯盘3（放量）：复用 bottom_volume_signal 的 RVOL/底部放量判断（同一数据源，避免重复计算）。

5m 数据来源：pytdx 拉近 LOAD_DAYS 天 1m 再重采样为 5m（早盘也有足够根数算 MA20+7）。
计算较重，故 5m 结果按 code 缓存 CACHE_TTL 秒、板块 15m 结果按 board_code 缓存 BOARD_TTL 秒。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ---- 可调参数 ----
MA_PERIODS = (5, 10, 20)   # 5m 三均线周期
CONFIRM_BARS = 7           # 连续确认根数
LOAD_DAYS = 5              # 拉取近 N 天 1m 以有足够 5m 根数
THUMB_BARS = 20            # 明细行缩略图展示的最近根数（图只需 20 根即可）
CACHE_TTL = 90             # 单只 5m 计算缓存秒数
BOARD_TTL = 300            # 板块 15m 方向缓存秒数

_lock = threading.Lock()
_w1_cache: dict = {}       # code -> (ts, result)
_board_cache: dict = {}    # board_code -> (ts, {dir, stage, name})


def _build_ma_series(df):
    """把 OHLC DataFrame 转成缩略图序列：最近 THUMB_BARS 根的 高/低/收 + 三均线。"""
    import pandas as pd
    if df is None or df.empty:
        return []
    df = df.sort_values('date').reset_index(drop=True)
    close = pd.to_numeric(df['close'], errors='coerce')
    high = pd.to_numeric(df['high'], errors='coerce') if 'high' in df.columns else close
    low = pd.to_numeric(df['low'], errors='coerce') if 'low' in df.columns else close
    ma = {p: close.rolling(p).mean() for p in MA_PERIODS}

    def _r(v):
        return round(float(v), 3) if pd.notna(v) else None

    start = max(0, len(df) - THUMB_BARS)
    out = []
    for i in range(start, len(df)):
        out.append({
            't': pd.to_datetime(df['date'].iloc[i]).strftime('%m-%d %H:%M'),
            'h': _r(high.iloc[i]), 'l': _r(low.iloc[i]), 'c': _r(close.iloc[i]),
            'ma5': _r(ma[5].iloc[i]), 'ma10': _r(ma[10].iloc[i]), 'ma20': _r(ma[20].iloc[i]),
        })
    return out


# ============== 盯盘1：5m 三均线 ==============
def _load_5m(code: str):
    """拉近 LOAD_DAYS 天 1m（pytdx）并重采样为 5m，返回按时间升序的 DataFrame（空则空表）。"""
    import pandas as pd

    from App.codes.downloads.DlPytdx import download_stock_1m_pytdx
    from App.codes.utils.data_processing import ResampleData

    df, _end = download_stock_1m_pytdx(code, days=LOAD_DAYS)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    if 'money' not in df.columns:      # resample_fun 依赖 money 列
        df['money'] = 0
    try:
        df5 = ResampleData.resample_fun(df, '5min')
    except Exception as e:
        logger.debug(f'[watch1] {code} 1m→5m 重采样失败: {e}')
        return pd.DataFrame()
    return df5.sort_values('date').reset_index(drop=True)


def evaluate_watch1(code: str) -> dict:
    """5m 三均线：连续 7 根高点跌破均线带→卖出；连续 7 根低点站上均线带→持有/买入。"""
    with _lock:
        hit = _w1_cache.get(code)
        if hit and (time.time() - hit[0]) < CACHE_TTL:
            return hit[1]

    out = {'ok': False, 'action': 'na', 'streak': 0,
           'price': None, 'ma': {}, 'last_bar_time': None, 'bars': 0}
    try:
        import pandas as pd
        df5 = _load_5m(code)
        need = max(MA_PERIODS) + CONFIRM_BARS
        out['bars'] = len(df5)
        if df5.empty or len(df5) < need:
            with _lock:
                _w1_cache[code] = (time.time(), out)
            return out

        close = pd.to_numeric(df5['close'], errors='coerce')
        high = pd.to_numeric(df5['high'], errors='coerce')
        low = pd.to_numeric(df5['low'], errors='coerce')
        ma = {p: close.rolling(p).mean() for p in MA_PERIODS}
        band_low = pd.concat([ma[p] for p in MA_PERIODS], axis=1).min(axis=1)
        band_high = pd.concat([ma[p] for p in MA_PERIODS], axis=1).max(axis=1)

        # 从最后一根往前数：连续「高点 < 均线带下沿」/「低点 > 均线带上沿」的根数
        below_streak = above_streak = 0
        for i in range(len(df5) - 1, -1, -1):
            if pd.notna(band_low.iloc[i]) and high.iloc[i] < band_low.iloc[i]:
                below_streak += 1
            else:
                break
        for i in range(len(df5) - 1, -1, -1):
            if pd.notna(band_high.iloc[i]) and low.iloc[i] > band_high.iloc[i]:
                above_streak += 1
            else:
                break

        if below_streak >= CONFIRM_BARS:
            out['action'], out['streak'] = 'sell', below_streak
        elif above_streak >= CONFIRM_BARS:
            out['action'], out['streak'] = 'hold_buy', above_streak
        else:
            out['action'] = 'wait'
            out['streak'] = max(below_streak, above_streak)
            out['bias'] = 'down' if below_streak >= above_streak else 'up'

        out['ok'] = True
        out['price'] = round(float(close.iloc[-1]), 3)
        out['ma'] = {f'ma{p}': (round(float(ma[p].iloc[-1]), 3)
                                if pd.notna(ma[p].iloc[-1]) else None)
                     for p in MA_PERIODS}
        out['last_bar_time'] = pd.to_datetime(df5['date'].iloc[-1]).strftime('%m-%d %H:%M')

        # 缩略图数据：最近 THUMB_BARS 根的 高/低/收 + 三均线（供明细行画 mini 图核对）
        def _r(v):
            return round(float(v), 3) if pd.notna(v) else None
        start = max(0, len(df5) - THUMB_BARS)
        series = []
        for i in range(start, len(df5)):
            series.append({
                't': pd.to_datetime(df5['date'].iloc[i]).strftime('%H:%M'),
                'h': _r(high.iloc[i]), 'l': _r(low.iloc[i]), 'c': _r(close.iloc[i]),
                'ma5': _r(ma[5].iloc[i]), 'ma10': _r(ma[10].iloc[i]), 'ma20': _r(ma[20].iloc[i]),
            })
        out['series'] = series

        # 个股 15m 缩略图（同款三均线，供与 5m 对照）
        try:
            from App.services.realtime_data_service import merge_history_and_today_15m
            res15 = merge_history_and_today_15m(code, refresh=False)
            out['series_15m'] = _build_ma_series(res15.get('df'))
        except Exception as e:
            logger.debug(f'[watch1] {code} 15m 序列失败: {e}')
            out['series_15m'] = []
    except Exception as e:
        logger.warning(f'[watch1] {code} 评估失败: {e}')

    with _lock:
        _w1_cache[code] = (time.time(), out)
    return out


# ============== 盯盘2：板块 15m 同步 ==============
def _stock_15m_direction(code: str):
    """个股 15m 方向：复用分布快照 current_direction（+1 涨 / -1 跌 / None 未知）。"""
    try:
        from App.services.bottom_volume_signal import _latest_snapshot
        snap = _latest_snapshot(code)
        if snap is not None and snap.current_direction in (1, -1):
            return int(snap.current_direction)
    except Exception as e:
        logger.debug(f'[watch2] {code} 取个股方向失败: {e}')
    return None


def _board_of(code: str):
    """该股最新东财行业板块 (board_code, board_name)。"""
    try:
        from sqlalchemy import text
        from App.exts import db
        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            r = conn.execute(text(
                'SELECT board_code, board_name FROM industry_eastmoney '
                'WHERE stock_code=:c ORDER BY date DESC LIMIT 1'), {'c': code}).fetchone()
        return (r.board_code, r.board_name) if r else (None, None)
    except Exception as e:
        logger.debug(f'[watch2] {code} 取所属板块失败: {e}')
        return (None, None)


def _board_15m_direction(board_code: str):
    """板块 15m 方向（缓存 BOARD_TTL 秒）：由多周期评分的 m15 阶段推出方向。"""
    with _lock:
        hit = _board_cache.get(board_code)
        if hit and (time.time() - hit[0]) < BOARD_TTL:
            return hit[1]
    res = {'dir': None, 'stage': None, 'series': []}
    try:
        from App.services.board_trend_score_service import (
            score_multi_timeframe, _stage_direction, _load_board_15m,
        )
        mtf = score_multi_timeframe(board_code, date.today())
        m15 = mtf.get('m15') if mtf else None
        if m15 and m15.get('trend_stage'):
            res['stage'] = m15['trend_stage']
            res['dir'] = _stage_direction(m15['trend_stage'])
        # 板块 15m 缩略图序列（收盘 + 三均线，供明细行核对趋势）
        res['series'] = _build_ma_series(_load_board_15m(board_code, date.today()))
    except Exception as e:
        logger.debug(f'[watch2] {board_code} 板块 15m 方向失败: {e}')
    with _lock:
        _board_cache[board_code] = (time.time(), res)
    return res


def evaluate_watch2(code: str) -> dict:
    """个股 15m 方向 vs 所属板块 15m 方向 → 同步下跌/同步上涨/分歧。"""
    stock_dir = _stock_15m_direction(code)
    board_code, board_name = _board_of(code)
    board = _board_15m_direction(board_code) if board_code else {'dir': None, 'stage': None}
    board_dir = board.get('dir')

    if stock_dir is None or board_dir is None:
        verdict = 'unknown'
    elif stock_dir == -1 and board_dir == -1:
        verdict = 'sync_down'
    elif stock_dir == 1 and board_dir == 1:
        verdict = 'sync_up'
    else:
        verdict = 'mixed'

    return {
        'stock_dir': stock_dir,
        'board_code': board_code,
        'board_name': board_name,
        'board_dir': board_dir,
        'board_stage': board.get('stage'),
        'board_series': board.get('series') or [],
        'verdict': verdict,
    }


# ============== 盯盘3：放量 ==============
def _volume_bars(code: str, n: int = 24) -> dict:
    """最近 n 根 15m 成交量 + 逐根 RVOL + 末根「进行中」预估，供盯盘3放量 mini 柱状图。

    与 /api/trade/<code>/volume_15m、量能图 modal 同口径。无数据返回空 bars。
    """
    out = {'bars': [], 'base_avg': None, 'rvol_min': None,
           'projection': {'is_forming': False}}
    try:
        import pandas as pd
        from App.services.realtime_data_service import merge_history_and_today_15m
        from App.services.bottom_volume_signal import RVOL_MIN, RVOL_N
        from App.services.volume_projection import current_bar_projection

        res = merge_history_and_today_15m(code, refresh=False)
        df = res.get('df') if res else None
        if df is None or df.empty:
            return out
        df = df.sort_values('date').reset_index(drop=True)
        vol = pd.to_numeric(df['volume'], errors='coerce')
        roll = vol.rolling(RVOL_N, min_periods=5).mean().shift(1)
        rvol = vol / roll
        base_avg = float(roll.iloc[-1]) if pd.notna(roll.iloc[-1]) else None

        sub = df.tail(n).reset_index(drop=True)
        sv = vol.tail(n).reset_index(drop=True)
        srv = rvol.tail(n).reset_index(drop=True)
        srl = roll.tail(n).reset_index(drop=True)
        bars = []
        for i in range(len(sub)):
            v, rv, rm = sv.iloc[i], srv.iloc[i], srl.iloc[i]
            hot = bool(pd.notna(rv) and rv >= RVOL_MIN)
            faint = bool((not hot) and pd.notna(rm) and pd.notna(v) and v < rm)
            bars.append({'t': pd.to_datetime(sub['date'].iloc[i]).strftime('%m-%d %H:%M'),
                         'volume': float(v) if pd.notna(v) else 0,
                         'rvol': round(float(rv), 2) if pd.notna(rv) else None,
                         'hot': hot, 'faint': faint})
        proj = current_bar_projection(
            sub['date'].iloc[-1],
            float(sv.iloc[-1]) if pd.notna(sv.iloc[-1]) else None, base_avg)
        out = {'bars': bars, 'base_avg': base_avg, 'rvol_min': RVOL_MIN, 'projection': proj}
    except Exception as e:
        logger.debug(f'[watch3] {code} 量柱系列失败: {e}')
    return out


def evaluate_watch3(code: str) -> dict:
    """复用底部放量信号：给出放量/底部放量/量能正常 + RVOL，并附 15m 量柱系列（含预估）。"""
    out = {'ok': False, 'state': 'unknown', 'rvol': None,
           'bottom_vol': False, 'level': None}
    out['vol_chart'] = _volume_bars(code)   # 无论有无信号都带量柱数据，供 mini 图
    try:
        from App.services.bottom_volume_signal import get_signal, RVOL_MIN, VR_MIN
        sig = get_signal(code)
        if not sig:
            return out
        out['ok'] = True
        out['rvol'] = sig.get('rvol')
        out['bottom_vol'] = bool(sig.get('is_signal'))
        out['level'] = sig.get('level')
        vol_up = bool(sig.get('vol_up'))
        if sig.get('is_signal'):
            out['state'] = 'bottom_vol'          # 底部放量（三条件齐）
        elif sig.get('level') == 'watch':
            out['state'] = 'watch'               # 放量但未确认止跌
        elif vol_up:
            out['state'] = 'vol_up'              # 放量（不在底部区）
        else:
            out['state'] = 'normal'              # 量能正常
    except Exception as e:
        logger.debug(f'[watch3] {code} 放量评估失败: {e}')
    return out


# ============== 汇总 ==============
def evaluate_all(code: str, name: str = None) -> dict:
    """一只股票的三条盯盘明细。任一子项失败不影响其余。"""
    return {
        'code': code,
        'name': name,
        'watch1': evaluate_watch1(code),
        'watch2': evaluate_watch2(code),
        'watch3': evaluate_watch3(code),
    }
