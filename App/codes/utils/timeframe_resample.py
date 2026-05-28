# -*- coding: utf-8 -*-
"""把已有的 15m K 线按 A 股交易时段感知地 resample 到更大周期。

为什么不直接 pandas.resample('60min')？
  A 股有 09:30–11:30 / 13:00–15:00 两个不连续的交易时段，中间空 1h45min。
  pandas 的标准 resample 会按"自然小时"切，结果是 09:00 / 10:00 / 11:00 …
  跨越午休的 bar 会变形（11:30 后的下午第一根从 13:00 还是 12:00 算起？）。
  这里按"每天 4 根 60m / 8 根 30m / 1 根 daily"显式映射 15m 时间点到目标 bar，
  保证每根 60m 都恰好对应 A 股惯例的小时收盘点（10:30 / 11:30 / 14:00 / 15:00）。

支持的目标周期：'15m'（透传）/ '30m' / '60m' / 'daily'
"""
from __future__ import annotations
import pandas as pd


# A 股 15m K 线在一个交易日内的 16 个时间点 → 对应 60m 桶的标签
_15M_TO_60M = {
    '09:45': '10:30', '10:00': '10:30', '10:15': '10:30', '10:30': '10:30',
    '10:45': '11:30', '11:00': '11:30', '11:15': '11:30', '11:30': '11:30',
    '13:15': '14:00', '13:30': '14:00', '13:45': '14:00', '14:00': '14:00',
    '14:15': '15:00', '14:30': '15:00', '14:45': '15:00', '15:00': '15:00',
}

# 30m 桶：每 2 根 15m 合并
_15M_TO_30M = {
    '09:45': '10:00', '10:00': '10:00',
    '10:15': '10:30', '10:30': '10:30',
    '10:45': '11:00', '11:00': '11:00',
    '11:15': '11:30', '11:30': '11:30',
    '13:15': '13:30', '13:30': '13:30',
    '13:45': '14:00', '14:00': '14:00',
    '14:15': '14:30', '14:30': '14:30',
    '14:45': '15:00', '15:00': '15:00',
}

SUPPORTED_PERIODS = ('15m', '30m', '60m', 'daily')


def resample_15m(df_15m: pd.DataFrame, period: str) -> pd.DataFrame:
    """把 15m DataFrame 重采样到指定周期。

    Args:
        df_15m: 必须包含 date / open / high / low / close / volume 列；其他列会被丢弃
        period: '15m'（透传，直接返回输入）/ '30m' / '60m' / 'daily'

    Returns:
        新的 DataFrame，每根 K 线的 date 是目标周期的标签时间：
          60m: 当日 10:30 / 11:30 / 14:00 / 15:00 四根之一
          30m: 当日 10:00/10:30/.../15:00 八根之一
          daily: 当日 15:00
    """
    if period not in SUPPORTED_PERIODS:
        raise ValueError(f"period 必须是 {SUPPORTED_PERIODS} 之一，传了: {period}")
    if period == '15m' or df_15m is None or df_15m.empty:
        return df_15m

    df = df_15m.copy()
    df['date'] = pd.to_datetime(df['date'])

    if period == 'daily':
        # 每天合并成一根，标签为 15:00（当日收盘）
        df['_grp'] = df['date'].dt.normalize() + pd.Timedelta(hours=15)
    else:
        mapping = _15M_TO_60M if period == '60m' else _15M_TO_30M
        hm = df['date'].dt.strftime('%H:%M')
        label_hm = hm.map(mapping)
        # 非标准时间点（盘中 09:31-09:44 这种碎片）→ 退化用 ffill；首根没值就丢
        label_hm = label_hm.ffill()
        df = df[label_hm.notna()].copy()
        label_hm = label_hm.dropna()
        df['_grp'] = pd.to_datetime(
            df['date'].dt.strftime('%Y-%m-%d') + ' ' + label_hm
        )

    agg_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }
    if 'money' in df.columns:
        agg_dict['money'] = 'sum'

    out = (df.groupby('_grp', sort=True)
             .agg(agg_dict)
             .reset_index()
             .rename(columns={'_grp': 'date'}))
    return out.sort_values('date').reset_index(drop=True)
