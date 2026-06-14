# -*- coding: utf-8 -*-
"""个股统计列表 (stock_stats) —— 整合「分布快照(screener)」+「RNN 预测」两套数据

路由：
    GET  /stock_stats/                       页面
    GET  /stock_stats/api/list               合并列表（分布快照 ⨝ RNN 最新预测 ⨝ 板块/板块趋势）

数据口径：以某快照日 stock_dist_snapshot 为基础全集，左连每只股票的「最新一条」RNN 运行
记录（strategy_rnn_runs）和东财行业板块 + 板块最新趋势。筛选合并两页：方向 / 长度·振幅·
量能分位 / 板块趋势（来自 screener）+ RNN 买卖信号 / 趋势分数区间（来自预测页）+ 代码名称 / 股票池。
"""
from __future__ import annotations

import logging
from datetime import date

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import func, or_

from App.exts import db
from App.models.strategy.StockDistSnapshot import StockDistSnapshot
from App.models.strategy.StockPool import StockPool
from App.models.strategy.RnnRunningRecords import RnnRunningRecord
# 复用 screener 已有的板块辅助，避免重复实现
from App.routes.strategy.dist_filter import (
    _exclude_boards, _enrich_boards, _codes_by_board_stage,
)

logger = logging.getLogger(__name__)

stock_stats_bp = Blueprint('stock_stats', __name__, url_prefix='/stock_stats')


@stock_stats_bp.route('/')
def page():
    return render_template('strategy/stock_stats.html')


def _latest_rnn_by_codes(codes):
    """每只股票的最新一条 RNN 运行记录（按 id 最大），返回 {code: to_dict()}。"""
    if not codes:
        return {}
    sub = (db.session.query(RnnRunningRecord.code,
                            func.max(RnnRunningRecord.id).label('mid'))
           .filter(RnnRunningRecord.code.in_(list(codes)))
           .group_by(RnnRunningRecord.code).subquery())
    q = (db.session.query(RnnRunningRecord)
         .join(sub, RnnRunningRecord.id == sub.c.mid))
    return {r.code: r.to_dict() for r in q.all()}


def _rnn_filter_codes(signal, score_min, score_max):
    """按 RNN 最新记录的 买卖信号 / 趋势分数区间，返回满足的股票代码集合；无条件返回 None。"""
    if not signal and score_min is None and score_max is None:
        return None
    sub = (db.session.query(RnnRunningRecord.code,
                            func.max(RnnRunningRecord.id).label('mid'))
           .group_by(RnnRunningRecord.code).subquery())
    q = (db.session.query(RnnRunningRecord.code)
         .join(sub, RnnRunningRecord.id == sub.c.mid))
    if signal == 'buy':
        q = q.filter(RnnRunningRecord.trade_point > 0)
    elif signal == 'sell':
        q = q.filter(RnnRunningRecord.trade_point < 0)
    elif signal == 'none':
        q = q.filter(RnnRunningRecord.trade_point == 0)
    if score_min is not None:
        q = q.filter(RnnRunningRecord.score_trends >= score_min)
    if score_max is not None:
        q = q.filter(RnnRunningRecord.score_trends <= score_max)
    return {r.code for r in q.all()}


@stock_stats_bp.route('/api/add_to_pool', methods=['POST'])
def api_add_to_pool():
    """把选中的股票批量加入指定股票池（已在该池的活跃条目跳过去重）。"""
    data = request.get_json(silent=True) or {}
    codes = [str(c).strip() for c in (data.get('codes') or []) if str(c).strip()]
    pool_type = (data.get('pool_type') or 'watching').strip()
    if not codes:
        return jsonify({'success': False, 'message': '未选择股票'}), 400
    try:
        existing = {r.stock_code for r in
                    StockPool.query.filter_by(pool_type=pool_type, is_active=True).all()}
        name_map = {}
        try:
            from App.models.data.basic_info import StockInfo
            for r in (StockInfo.query.filter(StockInfo.code.in_(codes))
                      .with_entities(StockInfo.code, StockInfo.name).all()):
                name_map.setdefault(r.code, r.name)
        except Exception:
            pass
        added = skipped = 0
        for code in codes:
            if code in existing:
                skipped += 1
                continue
            try:
                StockPool.create_manual(code, name_map.get(code), pool_type=pool_type)
                existing.add(code)
                added += 1
            except Exception:
                logger.exception(f'加入股票池失败 {code}')
                skipped += 1
        return jsonify({'success': True, 'added': added, 'skipped': skipped, 'pool_type': pool_type})
    except Exception as e:
        logger.exception('批量加入股票池失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_stats_bp.route('/api/snapshot_dates', methods=['GET'])
def api_dates():
    try:
        merge_below = float(request.args.get('merge_below') or 0)
    except ValueError:
        merge_below = 0.0
    rows = (db.session.query(StockDistSnapshot.snapshot_date,
                             func.count(StockDistSnapshot.id).label('cnt'))
            .filter(StockDistSnapshot.merge_below == merge_below)
            .group_by(StockDistSnapshot.snapshot_date)
            .order_by(StockDistSnapshot.snapshot_date.desc()).all())
    return jsonify({'success': True,
                    'dates': [{'date': r[0].isoformat(), 'count': r[1]} for r in rows]})


@stock_stats_bp.route('/api/list', methods=['GET'])
def api_list():
    # ---- 口径：0=原始；>0=合并<X%小周期(去噪)，与 screener / 15m 详情页一致 ----
    try:
        merge_below = float(request.args.get('merge_below') or 0)
    except ValueError:
        merge_below = 0.0
    # ---- 快照日期：默认最新一天 ----
    date_str = (request.args.get('date') or '').strip()
    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            target = None
    else:
        target = None
    if target is None:
        target = (db.session.query(func.max(StockDistSnapshot.snapshot_date))
                  .filter(StockDistSnapshot.merge_below == merge_below).scalar())
    if target is None:
        return jsonify({'success': True, 'date': None, 'total': 0, 'count': 0, 'data': [],
                        'merge_below': merge_below,
                        'pagination': {'page': 1, 'total_pages': 1, 'total_items': 0}})

    q = _exclude_boards(StockDistSnapshot.query.filter(
        StockDistSnapshot.snapshot_date == target,
        StockDistSnapshot.merge_below == merge_below))

    # ---- 代码/名称 ----
    kw = (request.args.get('q') or '').strip()
    if kw:
        like = f'%{kw}%'
        q = q.filter(or_(StockDistSnapshot.stock_code.like(like),
                         StockDistSnapshot.stock_name.like(like)))

    # ---- 方向 ----
    direction = (request.args.get('direction') or '').lower()
    is_up = direction in ('up', '1')
    is_dn = direction in ('down', '-1', 'dn')
    if is_up:
        q = q.filter(StockDistSnapshot.current_direction == 1)
    elif is_dn:
        q = q.filter(StockDistSnapshot.current_direction == -1)

    # ---- 长度/振幅/能量分位（需方向；geX=≥ / leX=≤）----
    def _apply_metric(param, prefix):
        v = (request.args.get(param) or '').strip()
        if not v or not (is_up or is_dn):
            return q
        d = 'up' if is_up else 'dn'
        col = getattr(StockDistSnapshot, f'{prefix}_{d}_pct')
        ncol = getattr(StockDistSnapshot, f'{prefix}_{d}_n')
        try:
            num = float(v[2:])
        except ValueError:
            return q
        cond = col >= num if v.startswith('ge') else col <= num
        return q.filter(col != None).filter(cond).filter(ncol >= 8)
    q = _apply_metric('len', 'len')
    q = _apply_metric('amp', 'amp')
    q = _apply_metric('vol', 'v5')

    # ---- 股票池 ----
    pool_type = (request.args.get('pool_type') or '').strip()
    if pool_type:
        active = {r.stock_code for r in StockPool.query.filter_by(is_active=1).all()}
        if pool_type == 'not_in_pool':
            if active:
                q = q.filter(~StockDistSnapshot.stock_code.in_(active))
        elif pool_type == 'in_any_pool':
            q = q.filter(StockDistSnapshot.stock_code.in_(active or {'__none__'}))
        else:
            codes = {r.stock_code for r in
                     StockPool.query.filter_by(pool_type=pool_type, is_active=1).all()}
            q = q.filter(StockDistSnapshot.stock_code.in_(codes or {'__none__'}))

    # ---- 板块趋势（来自 screener 辅助）----
    board_stage = (request.args.get('board_stage') or '').strip()
    if board_stage:
        bs_codes = _codes_by_board_stage(board_stage)
        q = q.filter(StockDistSnapshot.stock_code.in_(bs_codes or {'__none__'}))

    # ---- RNN 信号 / 趋势分数区间 ----
    def _f(name):
        v = (request.args.get(name) or '').strip()
        try:
            return float(v) if v != '' else None
        except ValueError:
            return None
    rnn_signal = (request.args.get('rnn_signal') or '').strip()
    score_min, score_max = _f('score_min'), _f('score_max')
    rnn_codes = _rnn_filter_codes(rnn_signal, score_min, score_max)
    if rnn_codes is not None:
        q = q.filter(StockDistSnapshot.stock_code.in_(rnn_codes or {'__none__'}))

    # ---- 排序 + 分页 ----
    if is_up:
        q = q.order_by(StockDistSnapshot.len_up_pct.is_(None),
                       StockDistSnapshot.len_up_pct.desc())
    elif is_dn:
        q = q.order_by(StockDistSnapshot.len_dn_pct.is_(None),
                       StockDistSnapshot.len_dn_pct.desc())
    else:
        q = q.order_by(StockDistSnapshot.stock_code.asc())

    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    try:
        page_size = max(1, min(int(request.args.get('page_size', 30)), 200))
    except ValueError:
        page_size = 30

    pagination = q.paginate(page=page, per_page=page_size, error_out=False)
    data = [r.to_dict() for r in pagination.items]

    # ---- 富集：板块/板块趋势 + RNN 最新预测 ----
    _enrich_boards(data)
    codes = [d['stock_code'] for d in data]
    rnn_map = _latest_rnn_by_codes(codes)
    for d in data:
        d['rnn'] = rnn_map.get(d['stock_code'])

    return jsonify({
        'success': True,
        'date': target.isoformat(),
        'merge_below': merge_below,
        'count': len(data),
        'pagination': {
            'page': pagination.page,
            'total_pages': pagination.pages or 1,
            'total_items': pagination.total,
        },
        'data': data,
    })
