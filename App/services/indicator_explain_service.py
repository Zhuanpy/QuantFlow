# -*- coding: utf-8 -*-
"""
趋势打分——指标计算图解服务

为 6 个子分各生成一张 matplotlib 说明图（PNG），口径严格对齐
board_trend_score_service 里的真实打分函数（_score_*、_linmap）。

图是「静态教学图」，与具体板块数据无关，因此首次生成后按 key 缓存到进程内存。
用对象式 Figure API（不走 pyplot 全局态），配进程锁，规避多线程渲染问题。
"""
from __future__ import annotations

import threading
from io import BytesIO

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False
from matplotlib.figure import Figure

from App.services.board_trend_score_service import (
    _linmap, _ema, _score_ma, _score_price_structure, _score_macd,
)

# 指标元信息（也用于路由校验 key 合法性）
INDICATORS = {
    'price_structure': {'name': '价格结构', 'weight': 15},
    'ma':              {'name': '均线',     'weight': 20},
    'macd':            {'name': 'MACD',     'weight': 25},
    'volume':          {'name': '量能',     'weight': 15},
    'momentum':        {'name': '动量',     'weight': 15},
    'volatility':      {'name': '波动率',   'weight': 10},
}

_CACHE: dict[str, bytes] = {}
_LOCK = threading.Lock()


def _df(close: np.ndarray) -> pd.DataFrame:
    s = np.asarray(close, dtype=float)
    o = np.ones_like(s)
    return pd.DataFrame({'open': s, 'high': s, 'low': s, 'close': s, 'volume': o, 'money': o})


# ---------------- 各指标图 ----------------
def _build_price_structure() -> Figure:
    rng = np.random.default_rng(11)
    N = 80
    t = np.arange(N)
    close = 100 + 12 * np.sin(t / 13) + 0.18 * t + rng.normal(0, 1.1, N)
    df = _df(close)
    sc, d = _score_price_structure(df)
    cs = pd.Series(close)
    high60 = float(cs.rolling(60, min_periods=20).max().iloc[-1])
    low60 = float(cs.rolling(60, min_periods=20).min().iloc[-1])
    cur = float(close[-1])
    pos = d.get('pos_60d')

    fig = Figure(figsize=(9, 5), dpi=110)
    ax = fig.subplots()
    ax.plot(t, close, color='#334155', lw=1.5, label='收盘价')
    ax.axhline(high60, color='#dc2626', ls='--', lw=1, label=f'60日高 {high60:.1f}')
    ax.axhline(low60, color='#16a34a', ls='--', lw=1, label=f'60日低 {low60:.1f}')
    ax.fill_between([t[0], t[-1]], low60, high60, color='#3b82f6', alpha=0.06)
    ax.scatter([t[-1]], [cur], color='#2563eb', s=50, zorder=5)
    ax.annotate(f'当前 pos≈{pos:.0f}', (t[-1], cur), textcoords='offset points',
                xytext=(-78, 4), fontsize=11, color='#2563eb', fontweight='bold')
    ax.set_title(f'价格结构 price_structure（权重 15）   得分 = {sc:.0f}',
                 fontsize=13, fontweight='bold')
    ax.text(0.012, 0.03,
            'pos = (close − low60) / (high60 − low60) × 100\n'
            '创 20 日新高 +5 / 新低 −5，最后裁剪到 0–100\n'
            '贴近区间顶 → 高分（偏强/偏顶），贴近底 → 低分',
            transform=ax.transAxes, fontsize=9.5, va='bottom',
            bbox=dict(boxstyle='round', fc='#f8fafc', ec='#cbd5e1'))
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(alpha=.25)
    ax.set_xticks([])
    fig.tight_layout()
    return fig


def _build_ma() -> Figure:
    N = 90
    t = np.arange(N)

    def noisy(a, s=0.35):
        return a + np.random.default_rng(7).normal(0, s, len(a))

    def sc_of(close):
        return _score_ma(_df(close))[0]

    def search(target, maker, tries=8000):
        rng = np.random.default_rng(123)
        first = None
        for _ in range(tries):
            s = maker(rng)
            if first is None:
                first = s
            if sc_of(s) == target:
                return s
        return first

    def make_top(rng):
        base = 100 + 0.6 * t.astype(float)
        k = int(rng.integers(55, 66))
        s = base.copy()
        tail = np.arange(N - k)
        s[k:] = (base[k] + rng.uniform(1.5, 3.5) * np.sin(tail / rng.uniform(2, 4) + rng.uniform(0, 6))
                 - rng.uniform(0.1, 0.4) * tail)
        return s + rng.normal(0, 0.25, N)

    def make_base(rng):
        base = 160 - 0.9 * t.astype(float)
        k = int(rng.integers(48, 56))
        s = base.copy()
        s[k:] = base[k]
        return s + rng.normal(0, 0.7, N)

    c = 100 + 0.55 * t
    c[72:] = c[71] - 1.5 * np.arange(N - 72)
    d = 160 - 0.7 * t
    d[72:] = d[71] + 1.6 * np.arange(N - 72)
    scenarios = [
        ('多头排列 + MA20 上行', noisy(100 + 0.55 * t)),
        ('多头排列但 MA20 走平/转头（顶部钝化）', search(80.0, make_top)),
        ('均线多头，价回踩 MA20 下方', noisy(c, 0.3)),
        ('空头排列中反弹，价上穿 MA20', noisy(d, 0.3)),
        ('空头排列 + MA20 下行', noisy(160 - 0.6 * t)),
        ('空头排列但 MA20 企稳（筑底）', search(20.0, make_base)),
    ]

    fig = Figure(figsize=(15, 8.5), dpi=104)
    axes = fig.subplots(2, 3)
    for ax, (title, close) in zip(axes.flat, scenarios):
        s = pd.Series(close)
        ma20 = s.rolling(20).mean()
        ma60 = s.rolling(60).mean()
        sc = _score_ma(_df(close))[0]
        slope = (ma20.iloc[-1] - ma20.iloc[-6]) / max(abs(ma20.iloc[-6]), 1e-9) * 100
        ax.plot(t, close, color='#94a3b8', lw=1.1, label='收盘价')
        ax.plot(t, ma20, color='#f59e0b', lw=1.7, label='MA20')
        ax.plot(t, ma60, color='#8b5cf6', lw=1.7, label='MA60')
        ax.scatter([t[-1]], [close[-1]], color='#dc2626', zorder=5, s=26)
        ax.set_title(f'{title}\nma 子分 = {sc:.0f}   (MA20斜率={slope:+.1f}%)',
                     fontsize=11, fontweight='bold')
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(alpha=.25)
        ax.set_xticks([])
    fig.suptitle('均线 ma（权重 20）：close vs MA20 vs MA60 的排列 + MA20 近 5 日斜率',
                 fontsize=14, fontweight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return fig


def _build_macd() -> Figure:
    rng = np.random.default_rng(5)
    N = 120
    t = np.arange(N)
    close = 100 + 0.12 * t + 8 * np.sin(t / 15) + rng.normal(0, 0.8, N)
    s = pd.Series(close)
    ema12 = _ema(s, 12)
    ema26 = _ema(s, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    bar = (dif - dea) * 2
    sc, _ = _score_macd(_df(close))

    fig = Figure(figsize=(10, 6.6), dpi=110)
    ax1, ax2 = fig.subplots(2, 1, gridspec_kw={'height_ratios': [2, 1.5]})
    ax1.plot(t, close, color='#334155', lw=1.4, label='收盘')
    ax1.plot(t, ema12, color='#f59e0b', lw=1, label='EMA12')
    ax1.plot(t, ema26, color='#8b5cf6', lw=1, label='EMA26')
    ax1.set_title(f'MACD macd（权重 25）   得分 = {sc:.0f}', fontsize=13, fontweight='bold')
    ax1.legend(fontsize=8, loc='upper left')
    ax1.grid(alpha=.25)
    ax1.set_xticks([])

    colors = ['#dc2626' if v >= 0 else '#16a34a' for v in bar]
    ax2.bar(t, bar, color=colors, width=.85)
    ax2.plot(t, dif, color='#2563eb', lw=1.3, label='DIF')
    ax2.plot(t, dea, color='#f59e0b', lw=1.3, label='DEA')
    ax2.axhline(0, color='#64748b', lw=.9)
    ax2.legend(fontsize=8, loc='upper left')
    ax2.grid(alpha=.25)
    ax2.set_xticks([])
    fig.text(0.012, 0.01,
             'DIF = EMA12 − EMA26    DEA = EMA9(DIF)    BAR = 2(DIF − DEA)\n'
             'score = 0.55 × linmap(DIF/close×100, −2→0, +2→100) + 0.45 × 柱体分；'
             '柱体上行=65/下行=35，DIF&DEA 同正 +15 / 同负 −15',
             fontsize=8.6, va='bottom',
             bbox=dict(boxstyle='round', fc='#f8fafc', ec='#cbd5e1'))
    fig.tight_layout(rect=[0, 0.08, 1, 1])
    return fig


def _build_volume() -> Figure:
    x = np.linspace(0.3, 2.2, 200)
    y = [_linmap(v, 0.5, 2.0) for v in x]
    fig = Figure(figsize=(9, 5), dpi=110)
    ax = fig.subplots()
    ax.plot(x, y, color='#2563eb', lw=2.2)
    for vx in [0.5, 1.0, 1.5, 2.0]:
        vy = _linmap(vx, 0.5, 2.0)
        ax.scatter([vx], [vy], color='#dc2626', zorder=5, s=35)
        ax.annotate(f'{vx:g} → {vy:.0f}', (vx, vy), textcoords='offset points',
                    xytext=(8, -4), fontsize=9.5, color='#dc2626')
    ax.axvline(1.0, color='#94a3b8', ls=':', lw=1)
    ax.set_xlabel('量比 ratio = 5 日均量 / 60 日均量', fontsize=10)
    ax.set_ylabel('量能得分', fontsize=10)
    ax.set_title('量能 volume（权重 15）   score = linmap(ratio, 0.5→0, 2.0→100)',
                 fontsize=12, fontweight='bold')
    ax.text(0.02, 0.96, '← 缩量（弱）            放量（强）→',
            transform=ax.transAxes, fontsize=9.5, color='#6b7280', va='top')
    ax.set_ylim(-3, 105)
    ax.grid(alpha=.25)
    fig.tight_layout()
    return fig


def _build_momentum() -> Figure:
    def score(rsi):
        if 50 <= rsi <= 65:
            return _linmap(rsi, 50, 65, 80, 95)
        if rsi > 65:
            return _linmap(rsi, 65, 85, 80, 50)
        if 40 <= rsi < 50:
            return _linmap(rsi, 40, 50, 50, 75)
        return _linmap(rsi, 20, 40, 30, 50)

    x = np.linspace(0, 100, 400)
    y = [score(v) for v in x]
    fig = Figure(figsize=(9, 5), dpi=110)
    ax = fig.subplots()
    ax.axvspan(50, 65, color='#16a34a', alpha=0.10)
    ax.axvspan(65, 100, color='#f59e0b', alpha=0.08)
    ax.axvspan(0, 40, color='#94a3b8', alpha=0.08)
    ax.plot(x, y, color='#2563eb', lw=2.2)
    ax.scatter([65], [score(65)], color='#dc2626', zorder=5, s=40)
    ax.annotate(f'峰值 65 → {score(65):.0f}', (65, score(65)),
                textcoords='offset points', xytext=(8, 2), fontsize=10, color='#dc2626', fontweight='bold')
    ax.text(57, 20, '健康看多\n50–65', ha='center', fontsize=9, color='#166534')
    ax.text(82, 20, '超买衰减\n>65', ha='center', fontsize=9, color='#9a3412')
    ax.set_xlabel('RSI(14)', fontsize=10)
    ax.set_ylabel('动量得分', fontsize=10)
    ax.set_title('动量 momentum（权重 15）   RSI(14) → 得分', fontsize=12, fontweight='bold')
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 105)
    ax.grid(alpha=.25)
    fig.tight_layout()
    return fig


def _build_volatility() -> Figure:
    def score(p):
        if p < 1:
            return _linmap(p, 0, 1, 30, 60)
        if p <= 3:
            return _linmap(p, 1, 3, 60, 90)
        if p <= 5:
            return _linmap(p, 3, 5, 90, 60)
        return _linmap(p, 5, 10, 60, 25)

    x = np.linspace(0, 10, 400)
    y = [score(v) for v in x]
    fig = Figure(figsize=(9, 5), dpi=110)
    ax = fig.subplots()
    ax.axvspan(1, 3, color='#16a34a', alpha=0.10)
    ax.plot(x, y, color='#2563eb', lw=2.2)
    ax.scatter([3], [score(3)], color='#dc2626', zorder=5, s=40)
    ax.annotate(f'峰值 3% → {score(3):.0f}', (3, score(3)),
                textcoords='offset points', xytext=(8, 2), fontsize=10, color='#dc2626', fontweight='bold')
    ax.text(2, 20, '理想区\n1–3%', ha='center', fontsize=9, color='#166534')
    ax.text(0.5, 20, '太静\n<1%', ha='center', fontsize=8.5, color='#6b7280')
    ax.text(7.5, 20, '风险高\n>5%', ha='center', fontsize=9, color='#9a3412')
    ax.set_xlabel('ATR(14) / close × 100  (%)', fontsize=10)
    ax.set_ylabel('波动率得分', fontsize=10)
    ax.set_title('波动率 volatility（权重 10）   ATR% → 得分', fontsize=12, fontweight='bold')
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 105)
    ax.grid(alpha=.25)
    fig.tight_layout()
    return fig


_BUILDERS = {
    'price_structure': _build_price_structure,
    'ma': _build_ma,
    'macd': _build_macd,
    'volume': _build_volume,
    'momentum': _build_momentum,
    'volatility': _build_volatility,
}


def render_png(key: str) -> bytes:
    """返回指标说明图的 PNG 字节（按 key 缓存）。key 必须在 INDICATORS 中。"""
    if key in _CACHE:
        return _CACHE[key]
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        fig = _BUILDERS[key]()
        buf = BytesIO()
        fig.savefig(buf, format='png')
        data = buf.getvalue()
        _CACHE[key] = data
        return data
