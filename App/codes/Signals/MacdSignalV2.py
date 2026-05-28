# -*- coding: utf-8 -*-
"""
v2 趋势信号：直接价格 vs 长均线判定，比 v1 的"三均线对齐"滞后小很多。

规则：
  上涨 flip：MACD > 0  AND  连续 n 根 low  > MA30
  下跌 flip：MACD < 0  AND  连续 n 根 high < MA30

确认时间 = 连续段的"末根 K 线"（i） —— 系统只有到这一根才能下结论。
起点时间 = 从确认点 i 往回走到"MACD 变色那一根"（上涨=MACD 转正的第一根、
          下跌=MACD 转负的第一根）。即趋势真正的起点是 MACD 变红/变绿处，
          而不是连续 7 根确认窗口的首根 —— 确认晚不代表趋势晚开始。
          这样统计到的周期根数/振幅更完整。回溯不会越过上一段 flip 的起点。

设计要点：
  - 同方向连续 flip 自动合并（一段趋势中途即使再次满足条件也不重复标）
  - flip 触发后会覆盖此前的 Signal/SignalChoice（hindsight 视角，趋势真的从首根开始）
  - 中间过渡区（既不满足上涨也不满足下跌）保留上一段的 Signal（ffill）

不要写 1m 相关字段，那些由外层批处理函数另行处理。
"""
from __future__ import annotations
import pandas as pd
from App.codes.Signals.MacdSignal import calculate_MACD
from App.codes.parsers.MacdParser import (
    Signal, SignalId, SignalChoice, SignalStartIndex,
    up, down, upInt, downInt,
)


def compute_v2_signals(data: pd.DataFrame, n: int = 7) -> pd.DataFrame:
    """对 15m 数据应用 v2 趋势判定。

    Args:
        data: DataFrame，必须有 date / open / high / low / close。
              MACD 列（EmaShort/EmaMid/EmaLong/Dif/Dea/MACD/DifSm/DifMl）若不存在
              会先用 calculate_MACD 补上；长均线 MA30 == EmaLong（30 周期 SMA）。
        n:    确认所需的连续根数（用户默认 7）

    Returns:
        添加/覆盖了 Signal / SignalChoice / SignalId / SignalStartIndex 四列的 DataFrame。
    """
    data = data.copy().reset_index(drop=True)

    # 补齐 MACD 三件套（如果调用方已经算过会被原样覆盖，数学结果一致）
    if 'MACD' not in data.columns or 'EmaLong' not in data.columns:
        data = calculate_MACD(data)

    macd_col = data['MACD']
    long_ma = data['EmaLong']   # 30 周期 SMA，"长均线"

    up_cond = (macd_col > 0) & (data['low']  > long_ma)
    dn_cond = (macd_col < 0) & (data['high'] < long_ma)

    # 连续 n 根都满足：rolling 求和 == n
    up_consec_end = up_cond.rolling(n).sum() == n   # 这一根是连续段的"末根"
    dn_consec_end = dn_cond.rolling(n).sum() == n

    # 重置信号列（避免历史污染）
    for col in [Signal, SignalChoice, SignalId, SignalStartIndex]:
        data[col] = pd.NA

    macd_vals = macd_col.to_numpy()

    def _mark(start_idx, sig_int, choice):
        ts = data['date'].iloc[start_idx]
        data.at[start_idx, Signal] = sig_int
        data.at[start_idx, SignalChoice] = choice
        data.at[start_idx, SignalId] = pd.Timestamp(ts).strftime("%Y%m%d%H%M")
        data.at[start_idx, SignalStartIndex] = ts

    last_side = None   # 上一个确认的方向
    prev_start = -1    # 上一段 flip 的起点 idx（防止回溯越过上一段）
    for i in range(len(data)):
        if up_consec_end.iloc[i] and last_side != 'up':
            # 确认点是 i（连续 7 根满足），但趋势真正的起点是"MACD 变红那一根"——
            # 从 i 往回走到当前 MACD>0 连续段的第一根，把起点提前。
            start_idx = i
            while start_idx > prev_start + 1 and macd_vals[start_idx - 1] > 0:
                start_idx -= 1
            _mark(start_idx, upInt, up)
            last_side = 'up'
            prev_start = start_idx
        elif dn_consec_end.iloc[i] and last_side != 'down':
            # 对称：回溯到 MACD 变绿（<0）那一根
            start_idx = i
            while start_idx > prev_start + 1 and macd_vals[start_idx - 1] < 0:
                start_idx -= 1
            _mark(start_idx, downInt, down)
            last_side = 'down'
            prev_start = start_idx

    # ffill：每根 K 线继承上一个 flip 的 Signal / SignalStartIndex / SignalId
    # SignalChoice 不 ffill（只在 flip 那根标记，统计代码靠它定位 flip）
    data[Signal] = data[Signal].ffill()
    data[SignalStartIndex] = data[SignalStartIndex].ffill()
    data[SignalId] = data[SignalId].ffill()

    return data


def compute_v2_signals_pipeline(data: pd.DataFrame, n: int = 7) -> pd.DataFrame:
    """v2 版的完整信号流水线，对齐 v1 的 compute_macd_signals_pipeline 接口。

    流程：
      1. calculate_MACD：MA12/20/30 + DIF/DEA/MACD（沿用 v1，SMA）
      2. compute_v2_signals：用价格 vs MA30 + MACD 符号判定上涨/下跌
    """
    data = calculate_MACD(data)
    data = compute_v2_signals(data, n=n)
    return data
