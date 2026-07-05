# -*- coding: utf-8 -*-
"""把「当前持仓(模拟+真实)」双向同步为股票池的「交易中(trading)」标签。

持有判定复用 realtime_data_service.get_current_holdings()：汇总 trade_records 的
买卖净额>0（不分 simulate/real，即模拟+真实合并），就是"当前持有"。

双向同步：
  · 持有 → 确保在池(不在则自动入池) + 打「交易中」标签；
  · 已清仓（原来标了交易中但现在没持有）→ 摘掉「交易中」标签（仍留在池，其它标签不动）。
即 股票池的「交易中」恒等于 当前实际持仓。
"""
from __future__ import annotations

import logging

from App.exts import db
from App.models.strategy.StockPool import StockPool

logger = logging.getLogger(__name__)


def sync_trading_from_holdings() -> dict:
    """执行一次双向同步，返回 {held, created, tagged, untagged}。失败抛异常由调用方兜。"""
    from App.services.realtime_data_service import get_current_holdings

    held = {}
    for h in (get_current_holdings() or []):
        code = (h.get('stock_code') or '').strip()
        if code:
            held[code] = h
    held_codes = set(held.keys())

    created, tagged, untagged = [], [], []

    # 1) 持有 → 确保在池且带 trading 标签
    existing = ({p.stock_code: p for p in
                 StockPool.query.filter(StockPool.stock_code.in_(held_codes)).all()}
                if held_codes else {})
    for code in held_codes:
        p = existing.get(code)
        if p is None:
            p = StockPool(stock_code=code, stock_name=held[code].get('stock_name'), is_active=True)
            p.set_states(['trading'])
            db.session.add(p)
            created.append(code)
        else:
            touched = False
            if not p.is_active:
                p.is_active = True
                touched = True
            if p.is_excluded:
                p.is_excluded = False
                touched = True
            if 'trading' not in p.get_states():
                p.add_state('trading')
                touched = True
            if touched:
                tagged.append(code)

    # 2) 标了 trading 但已清仓 → 摘掉 trading（保留其它标签，仍留在池）
    for p in StockPool.query.filter(StockPool.is_active.is_(True)).all():
        if 'trading' in p.get_states() and p.stock_code not in held_codes:
            p.remove_state('trading')
            untagged.append(p.stock_code)

    if created or tagged or untagged:
        db.session.commit()
    return {'held': len(held_codes), 'created': created, 'tagged': tagged, 'untagged': untagged}
