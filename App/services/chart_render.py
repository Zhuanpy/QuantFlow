# -*- coding: utf-8 -*-
"""服务端把「检测情况」画成 PNG（用于邮件附件）。

matplotlib Agg 后端，无需显示器；用 Figure/FigureCanvasAgg 直接出图（不走 pyplot 全局态，
后台线程里更安全）。中文标题需 CJK 字体，Windows 用微软雅黑，缺字体时降级为方框但不报错。

目前提供：
  - volume_15m_png：最近 N 根 15m 量能图（放量橙 / 缩量浅灰 / 普通灰 + 前20根均量基准线），
    与盯盘页量能图 modal 同口径，最贴合「底部放量」检测。
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# 中文字体：Windows 优先微软雅黑；缺失则回退（英文正常，中文可能显示为方框）
try:
    import matplotlib
    matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False
except Exception:  # pragma: no cover
    pass


def volume_15m_png(code: str, name: str = None, bars: int = 48, now=None) -> bytes | None:
    """画某股最近 bars 根 15m 量能图，返回 PNG bytes；数据缺失/出错返回 None（邮件退化为纯文本）。

    末根若为「当前未走完」的 15m：实心柱=已累计现量，其上叠一段浅色斜纹虚柱=按已走时间
    外推到走完的预估增量，并标注预估 RVOL（避免半根量偏小被误判为不放量）。
    """
    try:
        import pandas as pd
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from App.services.realtime_data_service import merge_history_and_today_15m
        from App.services.bottom_volume_signal import RVOL_MIN, RVOL_N
        from App.services.volume_projection import current_bar_projection

        res = merge_history_and_today_15m(code, refresh=False)
        df = res.get('df') if res else None
        if df is None or df.empty:
            return None

        df = df.sort_values('date').reset_index(drop=True)
        vol = pd.to_numeric(df['volume'], errors='coerce')
        roll = vol.rolling(RVOL_N, min_periods=5).mean().shift(1)   # 前 N 根均量（不含当前）
        rvol = vol / roll

        sub = df.tail(bars).reset_index(drop=True)
        sv = vol.tail(bars).reset_index(drop=True)
        srv = rvol.tail(bars).reset_index(drop=True)
        srl = roll.tail(bars).reset_index(drop=True)

        base_avg = float(roll.iloc[-1]) if pd.notna(roll.iloc[-1]) else None
        last_rvol = float(rvol.iloc[-1]) if pd.notna(rvol.iloc[-1]) else None
        last_t = pd.to_datetime(sub['date'].iloc[-1]).strftime('%m-%d %H:%M')

        # 末根是否在形成中 → 预估全量
        proj = current_bar_projection(sub['date'].iloc[-1],
                                      float(sv.iloc[-1]) if pd.notna(sv.iloc[-1]) else None,
                                      base_avg, now=now)

        colors = []
        for i in range(len(sub)):
            rv, v, rm = srv.iloc[i], sv.iloc[i], srl.iloc[i]
            if pd.notna(rv) and rv >= RVOL_MIN:
                colors.append('#e0952f')      # 放量
            elif pd.notna(rm) and pd.notna(v) and v < rm:
                colors.append('#cbd5e1')      # 缩量
            else:
                colors.append('#94a3b8')      # 普通
        li = len(sub) - 1
        if proj.get('is_forming'):
            colors[li] = '#64748b'            # 现量部分：中性深灰，区别于"缩量浅灰"

        fig = Figure(figsize=(7.4, 3.2), dpi=110)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        ax.bar(range(len(sub)), sv.values, color=colors, width=0.72)
        if base_avg:
            ax.axhline(base_avg, color='#0f9d94', ls='--', lw=1.4,
                       label=f'前{RVOL_N}根均量 {base_avg:,.0f}')

        # 末根预估：现量之上叠浅色斜纹"预估增量"柱 + 标注
        if proj.get('is_forming'):
            inc = proj['projected_vol'] - proj['actual_vol']
            phot = proj.get('projected_rvol') is not None and proj['projected_rvol'] >= RVOL_MIN
            ax.bar(li, inc, bottom=proj['actual_vol'], width=0.72,
                   color=('#f4c77b' if phot else '#e2e8f0'), edgecolor='#e0952f',
                   hatch='////', linewidth=0.8,
                   label=f'预估增量(已走{proj["elapsed_min"]:.0f}/15分)')
            prv = proj.get('projected_rvol')
            ax.annotate(f'预估RVOL {prv}{" ★放量" if phot else ""}',
                        xy=(li, proj['projected_vol']),
                        xytext=(0, 4), textcoords='offset points',
                        ha='right', fontsize=8, fontweight='bold',
                        color='#b45309' if phot else '#475569')
        if base_avg or proj.get('is_forming'):
            ax.legend(loc='upper left', fontsize=8, frameon=False)

        rv_txt = f'{last_rvol:.2f}' if last_rvol is not None else '-'
        hot = last_rvol is not None and last_rvol >= RVOL_MIN
        if proj.get('is_forming'):
            prv = proj.get('projected_rvol')
            title = (f'{(name + " ") if name else ""}{code} · 15m量能  '
                     f'{last_t}(进行中)  现RVOL {rv_txt} → 预估 {prv}')
            tcol = '#b45309' if (prv is not None and prv >= RVOL_MIN) else '#1e293b'
        else:
            title = (f'{(name + " ") if name else ""}{code} · 15m量能  '
                     f'最新 {last_t}  RVOL {rv_txt}{" ★放量" if hot else ""}')
            tcol = '#b45309' if hot else '#1e293b'
        ax.set_title(title, fontsize=10, color=tcol)
        ax.set_xticks([])
        ax.tick_params(axis='y', labelsize=7)
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        return buf.getvalue()
    except Exception as e:
        logger.warning(f'[chart] {code} 量能图渲染失败: {e}')
        return None


def _draw_ma_png(series, title: str, title_color: str = '#1e293b') -> bytes | None:
    """把三均线 series 画成 PNG：高低价柱（红=跌破均线带/绿=站上/灰=纠缠）+ MA5/10/20 + 收盘线。

    series 每项形如 {t,h,l,c,ma5,ma10,ma20}（与盯盘页 mini 图同口径）。空/出错返回 None。
    """
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        s = series or []
        if not s:
            return None
        h = [b.get('h') for b in s]
        l = [b.get('l') for b in s]
        c = [b.get('c') for b in s]
        ma5 = [b.get('ma5') for b in s]
        ma10 = [b.get('ma10') for b in s]
        ma20 = [b.get('ma20') for b in s]
        n = len(s)

        fig = Figure(figsize=(7.4, 3.2), dpi=110)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        for i in range(n):
            if h[i] is None or l[i] is None:
                continue
            color = '#cbd5e1'
            if ma5[i] is not None and ma10[i] is not None and ma20[i] is not None:
                bl = min(ma5[i], ma10[i], ma20[i])
                bh = max(ma5[i], ma10[i], ma20[i])
                if h[i] < bl:
                    color = '#ef4444'
                elif l[i] > bh:
                    color = '#22c55e'
            ax.plot([i, i], [l[i], h[i]], color=color, lw=3, solid_capstyle='round')

        def _line(arr, color, lw, label):
            xs = [i for i in range(n) if arr[i] is not None]
            ys = [arr[i] for i in range(n) if arr[i] is not None]
            if xs:
                ax.plot(xs, ys, color=color, lw=lw, label=label)

        _line(ma20, '#a855f7', 1.3, 'MA20')
        _line(ma10, '#0f9d94', 1.3, 'MA10')
        _line(ma5, '#e0952f', 1.3, 'MA5')
        _line(c, '#1e293b', 1.1, '收盘')
        ax.legend(loc='upper left', fontsize=7, frameon=False, ncol=4)
        ax.set_title(title, fontsize=10, color=title_color)
        ax.set_xticks([])
        ax.tick_params(axis='y', labelsize=7)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        return buf.getvalue()
    except Exception as e:
        logger.warning(f'[chart] 三均线图渲染失败: {e}')
        return None


def ma_5m_png(code: str, name: str = None) -> bytes | None:
    """个股 5m 三均线（盯盘1），标题带卖出/持有买入/观望结论。"""
    try:
        from App.services.watch_detail_signal import evaluate_watch1
        w = evaluate_watch1(code)
        s = w.get('series') or []
        if not s:
            return None
        act = w.get('action')
        streak = w.get('streak') or 0
        act_txt = {'sell': f'卖出(连{streak}根跌破三均线)',
                   'hold_buy': f'持有/买入(连{streak}根站上三均线)'}.get(act, '观望(均线纠缠)')
        col = {'sell': '#dc2626', 'hold_buy': '#059669'}.get(act, '#1e293b')
        title = (f'{(name + " ") if name else ""}{code} · 5m三均线 · {act_txt}  '
                 f'{w.get("last_bar_time") or ""}')
        return _draw_ma_png(s, title, col)
    except Exception as e:
        logger.warning(f'[chart] {code} 5m三均线渲染失败: {e}')
        return None


def ma_15m_png(code: str, name: str = None) -> bytes | None:
    """个股 15m 三均线（盯盘1 的 15m 对照图）。"""
    try:
        from App.services.watch_detail_signal import evaluate_watch1
        w = evaluate_watch1(code)
        s = w.get('series_15m') or []
        if not s:
            return None
        title = f'{(name + " ") if name else ""}{code} · 15m三均线'
        return _draw_ma_png(s, title)
    except Exception as e:
        logger.warning(f'[chart] {code} 15m三均线渲染失败: {e}')
        return None


def board_15m_png(code: str, name: str = None) -> bytes | None:
    """所属板块 15m 三均线（盯盘2），标题带个股/板块同步下跌/上涨/分歧结论。"""
    try:
        from App.services.watch_detail_signal import evaluate_watch2
        w = evaluate_watch2(code)
        s = w.get('board_series') or []
        if not s:
            return None
        bn = w.get('board_name') or w.get('board_code') or '板块'
        vmap = {'sync_down': ('同步下跌', '#dc2626'),
                'sync_up': ('同步上涨', '#059669'),
                'mixed': ('个股/板块分歧', '#d97706')}
        vtxt, vcol = vmap.get(w.get('verdict'), ('方向未定', '#1e293b'))
        title = f'{bn} · 15m三均线 · {vtxt}'
        return _draw_ma_png(s, title, vcol)
    except Exception as e:
        logger.warning(f'[chart] {code} 板块15m三均线渲染失败: {e}')
        return None


# 触发信号 → 对应主图 key，组装邮件时排第一
_CHART_KEYS = ('volume', 'ma5', 'ma15', 'board')


def detection_charts(code: str, name: str = None, primary: str = 'volume') -> list:
    """该股全部监测图 [(filename, png_bytes)]，primary 对应的触发图排第一、其余依次。

    primary ∈ {'volume'(放量/盯盘3), 'ma5'(5m三均线/盯盘1), 'ma15'(15m三均线),
               'board'(板块15m/盯盘2)}。单张渲染失败自动跳过。
    """
    builders = {
        'volume': (f'{code}_15m_vol.png', lambda: volume_15m_png(code, name)),
        'ma5':    (f'{code}_5m_ma.png',   lambda: ma_5m_png(code, name)),
        'ma15':   (f'{code}_15m_ma.png',  lambda: ma_15m_png(code, name)),
        'board':  (f'{code}_board_15m.png', lambda: board_15m_png(code, name)),
    }
    if primary not in builders:
        primary = 'volume'
    order = [primary] + [k for k in _CHART_KEYS if k != primary]
    out = []
    for k in order:
        fname, fn = builders[k]
        png = fn()
        if png:
            out.append((fname, png))
    return out
