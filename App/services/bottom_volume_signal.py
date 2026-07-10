# -*- coding: utf-8 -*-
"""底部放量信号：下跌趋势末端 + 放量 + 止跌确认 → 低吸提示。

判定三条件（合取，缺一不可）：
  ① 底部区：当前 15m 周期方向=下跌，且下跌振幅经验分位 amp_dn_pct ≥ AMP_DN_MIN
             （复用 stock_dist_snapshot，即 /screener 的「下跌超过90%」同一字段）。
  ② 放量：  最新一根 15m 的相对量 RVOL = 本根量 / 前 N 根均量 ≥ RVOL_MIN，
             或 实时量比(f50) ≥ VR_MIN（量比按当日分钟均量归一，抗未收满的当前根偏差）。
  ③ 止跌：  最新一根 15m 收阳(close≥open) 或 收盘高于前一根（价格收上来 / 不再创新低）。

数据来源：realtime_data_service.merge_history_and_today_15m（历史+当日实时 15m，
含 volume 列）；由 holdings_1m_autofetch 的 5 分钟循环在交易时段驱动扫描，命中即：
  - 记入 _state 供 /api/trade/signals/bottom_volume 与盯盘面板读取；
  - 对「新命中」（换了新的一根 15m 才算新）调用 notifier 做服务端推送。

阈值都是模块常量，可按盘感微调。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ---- 可调阈值 ----
AMP_DN_MIN = 0.90     # 下跌振幅经验分位下限（“超过90%”）
RVOL_N = 20           # 相对量基准：前 N 根 15m 均量
RVOL_MIN = 2.0        # 相对量放大倍数阈值
VR_MIN = 1.8          # 实时量比阈值

_state = {
    'as_of': None,
    'results': {},          # {code: signal_dict}  最近一轮全部评估结果
    'recent_alerts': [],    # 最近命中（新触发）列表，最多 50 条
    'last_scan_codes': 0,
    'last_hit_count': 0,
}
_lock = threading.Lock()


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _latest_snapshot(code: str):
    """取该股最新一天的原始口径(merge_below=0)分布快照。"""
    try:
        from App.models.strategy.StockDistSnapshot import StockDistSnapshot
        return (StockDistSnapshot.query
                .filter_by(stock_code=code, merge_below=0)
                .order_by(StockDistSnapshot.snapshot_date.desc())
                .first())
    except Exception as e:
        logger.debug(f'[bvsig] 取快照失败 {code}: {e}')
        return None


def evaluate_code(code: str, name: str = None, quote: dict = None) -> dict:
    """评估单只股票的底部放量信号，返回结构化 dict（不抛异常）。"""
    import pandas as pd
    from App.services.realtime_data_service import merge_history_and_today_15m

    out = {
        'code': code, 'name': name,
        'price': None, 'change_pct': None,
        'rvol': None, 'vol_ratio': None,
        'amp_dn_pct': None, 'direction': None,
        'last_bar_time': None,
        'bottom': False, 'vol_up': False, 'stabilize': False,
        'is_signal': False, 'level': None, 'reasons': [],
    }
    try:
        res = merge_history_and_today_15m(code, refresh=False)
        df = res.get('df')
        if df is None or df.empty or len(df) < 3:
            return out
        df = df.sort_values('date').reset_index(drop=True)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        out['last_bar_time'] = pd.to_datetime(last['date']).strftime('%Y-%m-%d %H:%M')
        out['price'] = round(float(last['close']), 3) if pd.notna(last['close']) else None

        # ① 底部：方向 + 振幅分位
        snap = _latest_snapshot(code)
        if snap is not None:
            out['amp_dn_pct'] = snap.amp_dn_pct
            out['direction'] = snap.current_direction
        bottom = (out['direction'] == -1
                  and out['amp_dn_pct'] is not None
                  and out['amp_dn_pct'] >= AMP_DN_MIN)
        out['bottom'] = bool(bottom)

        # ② 放量：15m RVOL（+ 实时量比）
        vol = pd.to_numeric(df['volume'], errors='coerce')
        if len(vol) >= RVOL_N + 1:
            base = vol.iloc[-(RVOL_N + 1):-1].mean()
            cur = vol.iloc[-1]
            if base and base > 0 and pd.notna(cur):
                out['rvol'] = round(float(cur) / float(base), 2)
        if quote and quote.get('volume_ratio') is not None:
            out['vol_ratio'] = quote.get('volume_ratio')
        if quote and quote.get('change_pct') is not None:
            out['change_pct'] = quote.get('change_pct')
        vol_up = ((out['rvol'] is not None and out['rvol'] >= RVOL_MIN)
                  or (out['vol_ratio'] is not None and out['vol_ratio'] >= VR_MIN))
        out['vol_up'] = bool(vol_up)

        # ③ 止跌确认
        try:
            stabilize = bool(last['close'] >= last['open'] or last['close'] > prev['close'])
        except Exception:
            stabilize = False
        out['stabilize'] = stabilize

        # 汇总
        reasons = []
        if bottom:
            reasons.append(f'下跌分位{round(out["amp_dn_pct"] * 100)}%')
        if out['rvol'] is not None:
            reasons.append(f'RVOL {out["rvol"]}')
        if out['vol_ratio'] is not None:
            reasons.append(f'量比 {out["vol_ratio"]}')
        if stabilize:
            reasons.append('止跌收上')
        out['reasons'] = reasons

        out['is_signal'] = bool(bottom and vol_up and stabilize)
        if out['is_signal']:
            out['level'] = 'strong'
        elif bottom and vol_up:
            out['level'] = 'watch'   # 放量但未确认止跌，留意
        return out
    except Exception as e:
        logger.warning(f'[bvsig] 评估失败 {code}: {e}')
        return out


def scan_focus(app) -> tuple:
    """扫描 持仓+关注 股票，更新状态并对新命中推送。返回 (results, new_hits)。"""
    from App.services.realtime_data_service import get_focus_stocks
    try:
        from App.services.stock_quote_service import fetch_stock_quote
    except Exception:
        fetch_stock_quote = None

    with app.app_context():
        focus = [x for x in get_focus_stocks() if x.get('kind') in ('holding', 'watching')]

        results, new_hits = [], []
        with _lock:
            prev_map = dict(_state['results'])

        for item in focus:
            code = item['stock_code']
            quote = None
            if fetch_stock_quote:
                try:
                    quote = fetch_stock_quote(code)
                except Exception:
                    quote = None
            r = evaluate_code(code, name=item.get('stock_name'), quote=quote)
            r['kind'] = item.get('kind')
            results.append(r)
            # 新命中 = 本轮 is_signal 且（上轮未命中 或 换了新的一根 15m）
            if r['is_signal']:
                p = prev_map.get(code)
                same = (p and p.get('is_signal')
                        and p.get('last_bar_time') == r['last_bar_time'])
                if not same:
                    new_hits.append(r)

    with _lock:
        _state['as_of'] = _now_str()
        _state['results'] = {r['code']: r for r in results}
        _state['last_scan_codes'] = len(results)
        _state['last_hit_count'] = sum(1 for r in results if r['is_signal'])
        if new_hits:
            stamped = [{**h, 'fired_at': _state['as_of']} for h in new_hits]
            _state['recent_alerts'] = (stamped + _state['recent_alerts'])[:50]

    if new_hits:
        _dispatch_push(new_hits)
    return results, new_hits


def _dispatch_push(hits: list):
    """对新命中做服务端推送（未配置渠道则 no-op）。"""
    try:
        from App.services.notifier import push
        title = f'底部放量提醒 · {len(hits)} 只'
        lines = []
        for h in hits:
            lines.append(
                f"{h.get('name') or h['code']}({h['code']}) "
                f"价{h.get('price')} · {' · '.join(h.get('reasons') or [])}"
            )
        # 每只命中股附上全部监测图（内嵌邮件正文）：触发信号=放量，故量能图排第一，
        # 其后依次 5m三均线 / 15m三均线 / 板块15m三均线。限前 3 只（每只 4 张）避免邮件过大；
        # 单张渲染失败自动跳过，全失败则退化为纯文本。
        images = []
        try:
            from App.services.chart_render import detection_charts
            for h in hits[:3]:
                images.extend(detection_charts(h['code'], h.get('name'), primary='volume'))
        except Exception as e:
            logger.debug(f'[bvsig] 检测图渲染跳过: {e}')
        push(title, '\n'.join(lines), images=images or None)
    except Exception as e:
        logger.warning(f'[bvsig] 推送调度失败: {e}')


def get_signals() -> dict:
    with _lock:
        return {
            'as_of': _state['as_of'],
            'count': _state['last_scan_codes'],
            'hit_count': _state['last_hit_count'],
            'signals': list(_state['results'].values()),
            'recent_alerts': list(_state['recent_alerts']),
            'thresholds': {'amp_dn_min': AMP_DN_MIN, 'rvol_min': RVOL_MIN, 'vr_min': VR_MIN},
        }


def get_signal(code: str) -> dict:
    with _lock:
        return _state['results'].get(code)
