# -*- coding: utf-8 -*-
"""历史「底部放量」扫描（回测）。

在个股 15m 历史(data/15m/<code>.parquet)上找出满足三条件的放量点：
  ① 下跌趋势中：Signal == -1（处于下跌段）
  ② 大概是底部：段内跌幅已 ≥ MIN_DROP，且本根 low 贴近「段内目前最低点」（做/贴近新低）
  ③ 放量：RVOL_slot ≥ RVOL_MIN

关键：放量用**时段对齐相对量 RVOL_slot** = 本根量 ÷ 过去 SLOT_LOOKBACK 天同一时段(HH:MM)均量，
从根上排除「早盘第一根天生天量」造成的假放量（09:45 常态量约为 midday 的 5 倍）。
`13:00` 边界占位根直接排除。全部因果计算（滚动均值 shift(1)、段内 cummin），无未来函数。

输出每个命中点 + 汇总（放量倍数/原始量分位、命中后 1/2 天涨跌胜率与中位），用于校准阈值。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---- 默认参数（可由接口覆盖）----
SLOT_LOOKBACK = 20      # 同时段基准回看天数
RVOL_MIN = 1.8          # 放量阈值（时段归一口径，通常比混合口径低）
MIN_DROP = 0.05         # 段内已跌幅下限（“跌到位”）
NEAR_EPS = 0.015        # 距段内最低点 ≤1.5% 视为底部
FWD_BARS_1D = 8         # 约 1 个交易日（8 根 15m）
FWD_BARS_2D = 16        # 约 2 个交易日
EXCLUDE_SLOTS = {'13:00'}   # 边界占位根


def _rate(s: dict, sc: dict) -> str:
    """适配评级：样本≥5 用确认口径的 2 日胜率，否则用当根口径。"""
    n = s.get('count') or 0
    if n < 5:
        return '样本不足'
    if (sc.get('count') or 0) >= 5 and sc.get('fwd2_win') is not None:
        w = sc['fwd2_win']
    else:
        w = s.get('fwd2_win')
    if w is None:
        return '样本不足'
    if w >= 55:
        return '适合'
    if w >= 45:
        return '一般'
    return '不适合'


def scan_watchlist(app, days: int = 240, rvol_min: float = RVOL_MIN,
                   min_drop: float = MIN_DROP, require_stabilize: bool = False) -> dict:
    """对 持仓+关注 逐只跑 scan_history，返回按适配度排序的每股汇总。"""
    from App.services.realtime_data_service import get_focus_stocks

    with app.app_context():
        focus = [x for x in get_focus_stocks() if x.get('kind') in ('holding', 'watching')]

    rows = []
    for item in focus:
        code = item['stock_code']
        try:
            r = scan_history(code, days=days, rvol_min=rvol_min,
                             min_drop=min_drop, require_stabilize=require_stabilize)
        except Exception as e:
            r = {'ok': False, 'message': str(e)}
        if not r.get('ok'):
            rows.append({'code': code, 'name': item.get('stock_name'),
                         'kind': item.get('kind'), 'ok': False, 'message': r.get('message')})
            continue
        s, sc = r['summary'], r['summary_confirmed']
        rows.append({
            'code': code, 'name': item.get('stock_name'), 'kind': item.get('kind'), 'ok': True,
            'count': s['count'], 'rvol_p50': s['rvol_p50'],
            'naive_fwd2_win': s['fwd2_win'], 'naive_fwd2_med': s['fwd2_med'],
            'conf_count': sc['count'], 'confirm_rate': sc['confirm_rate'],
            'conf_fwd2_win': sc['fwd2_win'], 'conf_fwd2_med': sc['fwd2_med'],
            'rating': _rate(s, sc),
        })

    order = {'适合': 3, '一般': 2, '不适合': 1, '样本不足': 0}

    def keyf(x):
        if not x.get('ok'):
            return (-1, 0, 0)
        w = x.get('conf_fwd2_win') if (x.get('conf_count') or 0) >= 5 else None
        if w is None:
            w = x.get('naive_fwd2_win') or 0
        return (order.get(x.get('rating'), 0), w, x.get('count') or 0)

    rows.sort(key=keyf, reverse=True)
    return {
        'rows': rows, 'count': len(rows),
        'params': {'days': days, 'rvol_min': rvol_min,
                   'min_drop': min_drop, 'require_stabilize': require_stabilize},
    }


def _load_15m(code: str):
    import pandas as pd
    from App.utils.path_manager import get_path_manager
    p = get_path_manager().data_base / '15m' / f'{code}.parquet'
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if df is None or df.empty or 'volume' not in df.columns:
        return None
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)


def _q(series, p):
    import pandas as pd
    s = pd.to_numeric(series, errors='coerce').dropna()
    return round(float(s.quantile(p)), 2) if len(s) else None


def scan_history(code: str, days: int = 240, rvol_min: float = RVOL_MIN,
                 min_drop: float = MIN_DROP, near_eps: float = NEAR_EPS,
                 require_stabilize: bool = False, slot_lookback: int = SLOT_LOOKBACK) -> dict:
    """扫描历史底部放量点，返回 {events, summary, params, slot_profile}。"""
    import pandas as pd
    import numpy as np

    df = _load_15m(code)
    if df is None or len(df) < 60:
        return {'ok': False, 'message': f'{code} 无足够 15m 历史'}

    df['slot'] = df['date'].dt.strftime('%H:%M')
    df['dir'] = pd.to_numeric(df['Signal'], errors='coerce') if 'Signal' in df.columns else np.nan
    vol = pd.to_numeric(df['volume'], errors='coerce')

    # 时段对齐基准：同一 HH:MM 过去 slot_lookback 根的均量（shift(1) 排除当根，因果）
    df['slot_base'] = (vol.groupby(df['slot'])
                       .transform(lambda s: s.rolling(slot_lookback, min_periods=5).mean().shift(1)))
    df['rvol_slot'] = vol / df['slot_base']

    # 段内：以 SignalStartIndex 分组；段内 low 的 cummin + 段起点价
    seg = df['SignalStartIndex'] if 'SignalStartIndex' in df.columns else (df['dir'] != df['dir'].shift()).cumsum()
    df['seg'] = seg
    df['seg_low'] = df.groupby('seg')['low'].cummin()
    df['seg_start_close'] = df.groupby('seg')['close'].transform('first')
    df['drop_so_far'] = (df['seg_start_close'] - df['low']) / df['seg_start_close']
    df['near_bottom'] = df['low'] <= df['seg_low'] * (1 + near_eps)

    # 前瞻收益（评估用；无未来数据的尾部为 NaN）
    close = df['close']
    high = pd.to_numeric(df['high'], errors='coerce')
    # A) 当根买入：以本根收盘为入场
    df['fwd_1d'] = close.shift(-FWD_BARS_1D) / close - 1
    df['fwd_2d'] = close.shift(-FWD_BARS_2D) / close - 1
    # B) 次根确认买入：下一根收盘 > 本根最高（站上放量柱）才入场，以次根收盘为入场
    entry_next = close.shift(-1)
    df['confirmed'] = close.shift(-1) > high
    df['cfwd_1d'] = close.shift(-1 - FWD_BARS_1D) / entry_next - 1
    df['cfwd_2d'] = close.shift(-1 - FWD_BARS_2D) / entry_next - 1

    # 时间窗过滤
    if days and days > 0:
        cutoff = df['date'].max() - pd.Timedelta(days=days)
        win = df[df['date'] >= cutoff].copy()
    else:
        win = df.copy()

    cond = (
        (win['dir'] == -1)
        & (~win['slot'].isin(EXCLUDE_SLOTS))
        & (win['drop_so_far'] >= min_drop)
        & (win['near_bottom'])
        & (win['rvol_slot'] >= rvol_min)
    )
    if require_stabilize:
        cond = cond & (win['close'] >= win['open'])

    hits = win[cond].copy()

    events = []
    for _, r in hits.sort_values('date', ascending=False).head(300).iterrows():
        conf = bool(r['confirmed']) if pd.notna(r['confirmed']) else False
        events.append({
            'time': r['date'].strftime('%Y-%m-%d %H:%M'),
            'price': round(float(r['close']), 2),
            'volume': int(r['volume']) if pd.notna(r['volume']) else 0,
            'rvol_slot': round(float(r['rvol_slot']), 2) if pd.notna(r['rvol_slot']) else None,
            'drop_pct': round(float(r['drop_so_far']) * 100, 1) if pd.notna(r['drop_so_far']) else None,
            'fwd_1d': round(float(r['fwd_1d']) * 100, 2) if pd.notna(r['fwd_1d']) else None,
            'fwd_2d': round(float(r['fwd_2d']) * 100, 2) if pd.notna(r['fwd_2d']) else None,
            'confirmed': conf,
            'cfwd_1d': round(float(r['cfwd_1d']) * 100, 2) if (conf and pd.notna(r['cfwd_1d'])) else None,
            'cfwd_2d': round(float(r['cfwd_2d']) * 100, 2) if (conf and pd.notna(r['cfwd_2d'])) else None,
            'stabilized': bool(r['close'] >= r['open']),
        })

    # 汇总
    n = len(hits)
    fwd1 = pd.to_numeric(hits['fwd_1d'], errors='coerce').dropna()
    fwd2 = pd.to_numeric(hits['fwd_2d'], errors='coerce').dropna()
    summary = {
        'count': int(n),
        'rvol_p50': _q(hits['rvol_slot'], .5),
        'rvol_p25': _q(hits['rvol_slot'], .25),
        'rvol_p75': _q(hits['rvol_slot'], .75),
        'rvol_max': _q(hits['rvol_slot'], 1.0),
        'vol_p50': int(pd.to_numeric(hits['volume'], errors='coerce').median()) if n else None,
        'vol_p75': int(pd.to_numeric(hits['volume'], errors='coerce').quantile(.75)) if n else None,
        'fwd1_win': round(float((fwd1 > 0).mean()) * 100, 1) if len(fwd1) else None,
        'fwd1_med': round(float(fwd1.median()) * 100, 2) if len(fwd1) else None,
        'fwd2_win': round(float((fwd2 > 0).mean()) * 100, 1) if len(fwd2) else None,
        'fwd2_med': round(float(fwd2.median()) * 100, 2) if len(fwd2) else None,
    }

    # 次根确认买入（下一根收盘站上放量柱最高价才入场）
    conf_hits = hits[hits['confirmed'] == True]
    cf1 = pd.to_numeric(conf_hits['cfwd_1d'], errors='coerce').dropna()
    cf2 = pd.to_numeric(conf_hits['cfwd_2d'], errors='coerce').dropna()
    summary_confirmed = {
        'count': int(len(conf_hits)),
        'confirm_rate': round(len(conf_hits) / n * 100, 1) if n else None,
        'fwd1_win': round(float((cf1 > 0).mean()) * 100, 1) if len(cf1) else None,
        'fwd1_med': round(float(cf1.median()) * 100, 2) if len(cf1) else None,
        'fwd2_win': round(float((cf2 > 0).mean()) * 100, 1) if len(cf2) else None,
        'fwd2_med': round(float(cf2.median()) * 100, 2) if len(cf2) else None,
    }

    # 时段量能画像（给前端展示“早盘天量”背景）
    prof = (df.groupby('slot')['volume'].mean().round(0)
            .astype('int64').to_dict())
    slot_profile = [{'slot': k, 'avg_vol': int(v)} for k, v in sorted(prof.items())]

    return {
        'ok': True,
        'code': code,
        'events': events,
        'summary': summary,
        'summary_confirmed': summary_confirmed,
        'slot_profile': slot_profile,
        'params': {
            'days': days, 'rvol_min': rvol_min, 'min_drop': min_drop,
            'near_eps': near_eps, 'require_stabilize': require_stabilize,
            'slot_lookback': slot_lookback,
        },
        'data_range': [df['date'].min().strftime('%Y-%m-%d'),
                       df['date'].max().strftime('%Y-%m-%d')],
    }
