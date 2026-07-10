# -*- coding: utf-8 -*-
"""当前未走完的 15m K 线的成交量预估（现量 → 按已走时间比例外推整根全量）。

本项目 15m 时间戳为窗口「结束」时刻（END-label，如 10:45 覆盖 10:30–10:45）。
最后一根若其窗口仍在进行中（win_start ≤ now < ts），按已走分钟数外推整根预估量：
    预估全量 = 现量 / (已走分钟 / 15)
各 15m 窗口都不跨午休（边界对齐 11:30 / 13:15），故 elapsed 直接用钟表分钟即可。
"""
from __future__ import annotations

from datetime import datetime, timedelta

BAR_MINUTES = 15
# 至少按 1 分钟算，避免窗口刚开始那一两秒把预估放大到离谱倍数
_MIN_FRAC = 1.0 / BAR_MINUTES


def current_bar_projection(last_ts, actual_vol, base_avg=None, now=None) -> dict:
    """最后一根若在形成中，返回预估信息；否则 {'is_forming': False}。

    返回 {is_forming, elapsed_min, frac, actual_vol, projected_vol, projected_rvol}。
    now 可注入用于测试。
    """
    import pandas as pd
    try:
        now = now or datetime.now()
        ts = pd.Timestamp(last_ts).to_pydatetime()
        if ts.date() != now.date():
            return {'is_forming': False}
        win_start = ts - timedelta(minutes=BAR_MINUTES)
        if not (win_start <= now < ts):
            return {'is_forming': False}
        if actual_vol is None or actual_vol <= 0:
            return {'is_forming': False}

        elapsed_min = (now - win_start).total_seconds() / 60.0
        frac = max(_MIN_FRAC, min(1.0, elapsed_min / BAR_MINUTES))
        projected = actual_vol / frac
        return {
            'is_forming': True,
            'elapsed_min': round(elapsed_min, 1),
            'frac': round(frac, 3),
            'actual_vol': float(actual_vol),
            'projected_vol': float(projected),
            'projected_rvol': (round(projected / base_avg, 2) if base_avg else None),
        }
    except Exception:
        return {'is_forming': False}
