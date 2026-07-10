# -*- coding: utf-8 -*-
"""每日收盘后盯盘小结：给每只监测股（持仓+关注）各发一封当日小结邮件（无论是否触发信号）。

由 holdings_1m_autofetch 后台线程在收盘后触发一次（见 _maybe_daily_summary），也可手动调
send_daily_summary(app) 或经 /api/trade/holdings/autofetch/daily_summary 触发。

每封邮件内嵌该股全部监测图（5m三均线 / 15m三均线 / 15m量能 / 板块15m三均线），
正文给出盯盘1/2/3 的当日结论。只走邮件渠道（only=('email',)），不骚扰微信/WhatsApp。
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_ACT = {'sell': '卖出（跌破三均线）', 'hold_buy': '持有/买入（站上三均线）', 'wait': '观望（均线纠缠）'}
_VERDICT = {'sync_down': '⚠ 与板块同步下跌 → 偏离场',
            'sync_up': '与板块同步上涨 → 偏持有',
            'mixed': '与板块分歧 → 以板块为主',
            'unknown': '方向未定'}


def _one_body(name, code, kind, sig, w1, w2) -> str:
    kind_txt = '持仓' if kind == 'holding' else '关注'
    lvl = ('底部放量（下跌分位≥90%+放量+止跌）' if sig.get('is_signal')
           else ('放量留意（放量未确认止跌）' if sig.get('level') == 'watch' else '无放量信号'))
    amp = sig.get('amp_dn_pct')
    amp_txt = f'{round(amp * 100)}%' if amp is not None else '-'
    streak = w1.get('streak')
    ma_txt = _ACT.get(w1.get('action'), '-') + (f'（连{streak}根）' if streak else '')
    board = w2.get('board_name') or w2.get('board_code') or '未知板块'
    lines = [
        f'{name}（{code}） · {kind_txt} · 收盘价 {sig.get("price", "-")}',
        '',
        f'· 盯盘3 放量：{lvl}',
        f'    下跌分位 {amp_txt} · RVOL {sig.get("rvol", "-")}',
        f'· 盯盘1 5m三均线：{ma_txt}',
        f'· 盯盘2 板块同步：{_VERDICT.get(w2.get("verdict"), "-")}（{board}）',
        '',
        '下附全部监测图：5m三均线 / 15m三均线 / 15m量能 / 板块15m三均线。',
        '（本邮件为每日收盘小结，非触发告警。）',
    ]
    return '\n'.join(lines)


def send_daily_summary(app) -> dict:
    """给每只监测股各发一封当日小结邮件。返回 {sent, total, reason?}。"""
    with app.app_context():
        from App.services.notifier import push, channels_configured
        if 'email' not in channels_configured():
            return {'sent': 0, 'total': 0, 'reason': '邮件渠道未配置'}

        from App.services.realtime_data_service import get_focus_stocks
        from App.services.bottom_volume_signal import scan_focus, get_signal
        from App.services.watch_detail_signal import evaluate_watch1, evaluate_watch2
        from App.services.chart_render import detection_charts

        # 先扫一遍确保放量信号是当日最新
        try:
            scan_focus(app)
        except Exception:
            logger.debug('[daily_summary] scan_focus 失败，用现有信号')

        focus = [x for x in get_focus_stocks() if x.get('kind') in ('holding', 'watching')]
        today = datetime.now().strftime('%Y-%m-%d')
        sent = 0
        for item in focus:
            code = item['stock_code']
            name = item.get('stock_name') or code
            kind = item.get('kind')
            try:
                sig = get_signal(code) or {}
                w1 = evaluate_watch1(code)
                w2 = evaluate_watch2(code)
                body = _one_body(name, code, kind, sig, w1, w2)
                # 触发过底部放量则量能图排第一，否则默认 5m三均线排第一
                primary = 'volume' if sig.get('is_signal') or sig.get('level') == 'watch' else 'ma5'
                images = detection_charts(code, name, primary=primary)
                title = f'【每日盯盘小结 {today}】{name}({code})'
                ok, _fail = push(title, body, images=images or None, only=('email',))
                if ok:
                    sent += 1
            except Exception:
                logger.exception(f'[daily_summary] {code} 小结发送失败')
        logger.info(f'[daily_summary] 已发 {sent}/{len(focus)} 封')
        return {'sent': sent, 'total': len(focus)}
