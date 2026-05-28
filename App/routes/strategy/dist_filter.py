# -*- coding: utf-8 -*-
"""个股筛选器 (screener) —— 基于 stock_dist_snapshot 表

路由：
    GET  /screener/                                页面（分布快照筛选）
    GET  /screener/api/snapshots/dates             有快照的日期列表（下拉用）
    GET  /screener/api/snapshots/filter            筛选 API（核心）
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import and_, func, or_

from App.exts import db
from App.models.strategy.StockDistSnapshot import StockDistSnapshot
from App.models.strategy.StockPool import StockPool

logger = logging.getLogger(__name__)

screener_bp = Blueprint('screener', __name__, url_prefix='/screener')

def _exclude_boards(q):
    """所有 screener 查询都加这条：不要板块代码（BK*）。

    screener 服务的是"个股"筛选；板块虽然也有 15m K 线，但语义不同，
    出现在结果里会噪声化。这里在 API 层兜底过滤，不依赖批跑脚本自觉。
    """
    return q.filter(~func.upper(StockDistSnapshot.stock_code).like('BK%'))


# 允许的筛选字段（白名单，防注入）
_FILTERABLE_NUMERIC = {
    'len_up_mean', 'len_up_std', 'len_up_current', 'len_up_z', 'len_up_pct', 'len_up_n',
    'len_dn_mean', 'len_dn_std', 'len_dn_current', 'len_dn_z', 'len_dn_pct', 'len_dn_n',
    'amp_up_mean', 'amp_up_std', 'amp_up_current', 'amp_up_z', 'amp_up_pct', 'amp_up_n',
    'amp_dn_mean', 'amp_dn_std', 'amp_dn_current', 'amp_dn_z', 'amp_dn_pct', 'amp_dn_n',
    'v5_up_mean', 'v5_up_std', 'v5_up_current', 'v5_up_z', 'v5_up_pct', 'v5_up_n',
    'v5_dn_mean', 'v5_dn_std', 'v5_dn_current', 'v5_dn_z', 'v5_dn_pct', 'v5_dn_n',
}


# ---------------- 页面 ----------------
@screener_bp.route('/')
def page():
    return render_template('strategy/dist_filter.html',
                           filterable_fields=sorted(_FILTERABLE_NUMERIC))


# ---------------- API ----------------
@screener_bp.route('/api/snapshots/dates', methods=['GET'])
def api_dates():
    """有快照数据的日期列表（降序），前端下拉用。计数已排除板块。"""
    q = (db.session.query(StockDistSnapshot.snapshot_date,
                          func.count(StockDistSnapshot.id).label('cnt')))
    q = _exclude_boards(q)
    rows = (q.group_by(StockDistSnapshot.snapshot_date)
             .order_by(StockDistSnapshot.snapshot_date.desc())
             .limit(60).all())
    return jsonify({
        'success': True,
        'dates': [{'date': r.snapshot_date.isoformat(), 'count': int(r.cnt)} for r in rows],
    })


@screener_bp.route('/api/snapshots/filter', methods=['GET'])
def api_filter():
    """筛选 API。

    query params:
      date=YYYY-MM-DD       默认最新有数据的一天
      direction=up|down     当前未完成周期方向限定（'up'=1, 'down'=-1）
      pool_type=...         可选：限定 strategy_stock_pool.pool_type
      <field>_min=X         数值字段下限（>=）
      <field>_max=X         数值字段上限（<=）
      limit=N               最多返回多少条（默认 200）

    例：找下跌已走够长 + 振幅还不大的：
      ?direction=down&len_dn_pct_min=0.7&amp_dn_z_max=0&limit=50
    """
    # 1) 日期：缺省挑"样本数最多"的那天（同 count 取最新）
    #    Why: 单独 --code 调试会在最新一天写出仅 1 条快照，按 max(date) 会让列表只剩 1 只
    date_str = (request.args.get('date') or '').strip()
    if date_str:
        try:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': f'invalid date: {date_str}'}), 400
    else:
        cnt_col = func.count(StockDistSnapshot.id)
        row = (_exclude_boards(db.session.query(StockDistSnapshot.snapshot_date,
                                                cnt_col.label('cnt')))
               .group_by(StockDistSnapshot.snapshot_date)
               .order_by(cnt_col.desc(), StockDistSnapshot.snapshot_date.desc())
               .first())
        target = row.snapshot_date if row else date.today()

    q = StockDistSnapshot.query.filter(StockDistSnapshot.snapshot_date == target)
    q = _exclude_boards(q)

    # 1.5) 代码 / 名称模糊搜索（可选）
    keyword = (request.args.get('q') or '').strip()
    if keyword:
        like = f'%{keyword}%'
        q = q.filter(or_(StockDistSnapshot.stock_code.like(like),
                         StockDistSnapshot.stock_name.like(like)))

    # 2) 方向
    direction = (request.args.get('direction') or '').lower()
    if direction in ('up', '1'):
        q = q.filter(StockDistSnapshot.current_direction == 1)
    elif direction in ('down', '-1', 'dn'):
        q = q.filter(StockDistSnapshot.current_direction == -1)

    # 3) 数值字段范围（白名单严格校验）
    applied_conditions = []   # 把生效的条件回吐给前端，便于显示"按 X 筛选出 N 条"
    for key, val in request.args.items():
        if val in ('', None):
            continue
        if key.endswith('_min'):
            field, op = key[:-4], '>='
        elif key.endswith('_max'):
            field, op = key[:-4], '<='
        else:
            continue
        if field not in _FILTERABLE_NUMERIC:
            continue
        try:
            num = float(val)
        except ValueError:
            continue
        col = getattr(StockDistSnapshot, field)
        q = q.filter(col != None)   # 排除 null
        q = q.filter(col >= num) if op == '>=' else q.filter(col <= num)
        applied_conditions.append({'field': field, 'op': op, 'value': num})

    # 4) 限定股票池 pool_type（可选）
    #    特殊值：not_in_pool = 只看"还没在我任何活跃池子里"的（新发现候选）
    #            in_any_pool = 在任意活跃池子里
    pool_type = (request.args.get('pool_type') or '').strip()
    if pool_type:
        if pool_type == 'not_in_pool':
            in_pool_codes = {r.stock_code for r in
                             StockPool.query.filter_by(is_active=1).all()}
            if in_pool_codes:
                q = q.filter(~StockDistSnapshot.stock_code.in_(in_pool_codes))
        elif pool_type == 'in_any_pool':
            in_pool_codes = {r.stock_code for r in
                             StockPool.query.filter_by(is_active=1).all()}
            if in_pool_codes:
                q = q.filter(StockDistSnapshot.stock_code.in_(in_pool_codes))
            else:
                return jsonify({'success': True, 'date': target.isoformat(),
                                'total': 0, 'count': 0, 'conditions': applied_conditions,
                                'data': []})
        else:
            pool_codes = {r.stock_code for r in
                          StockPool.query.filter_by(pool_type=pool_type, is_active=1).all()}
            if pool_codes:
                q = q.filter(StockDistSnapshot.stock_code.in_(pool_codes))
            else:
                return jsonify({'success': True, 'date': target.isoformat(),
                                'total': 0, 'count': 0, 'conditions': applied_conditions,
                                'data': []})

    # 5) limit + 排序
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 1000))
    except ValueError:
        limit = 200

    total = q.count()
    # 排序：上涨方向按 len_up_pct desc；下跌按 len_dn_pct desc；混合 / 未指定按 stock_code
    # MySQL 不支持 SQL 标准的 NULLS LAST，用 `col IS NULL` 当主排序键变相实现：
    # IS NULL=False(0) 先，True(1) 后，于是非空排在前面，null 在后。
    if direction in ('up', '1'):
        q = q.order_by(StockDistSnapshot.len_up_pct.is_(None),
                       StockDistSnapshot.len_up_pct.desc())
    elif direction in ('down', '-1', 'dn'):
        q = q.order_by(StockDistSnapshot.len_dn_pct.is_(None),
                       StockDistSnapshot.len_dn_pct.desc())
    else:
        q = q.order_by(StockDistSnapshot.stock_code.asc())

    rows = q.limit(limit).all()
    return jsonify({
        'success': True,
        'date': target.isoformat(),
        'total': total,
        'count': len(rows),
        'conditions': applied_conditions,
        'data': [r.to_dict() for r in rows],
    })
