# -*- coding: utf-8 -*-
"""个股筛选器 (screener) —— 基于 stock_dist_snapshot 表

路由：
    GET  /screener/                                页面（分布快照筛选）
    GET  /screener/api/snapshots/dates             有快照的日期列表（下拉用）
    GET  /screener/api/snapshots/filter            筛选 API（核心）
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime

from flask import Blueprint, jsonify, render_template, request, current_app
from sqlalchemy import and_, func, or_, text, bindparam

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


# ---------------- 后台刷新快照（生成今日 snapshot_date）----------------
_compute_state = {'running': False, 'done': 0, 'total': 0, 'ok': 0,
                  'empty': 0, 'fail': 0, 'date': None, 'error': None}
_compute_lock = threading.Lock()


def _run_compute_all(app, target_date):
    """后台线程：对 data/15m 下全部个股(排除 BK)算当日分布快照，逐只 upsert。"""
    from pathlib import Path
    from config import Config
    from App.services.dist_snapshot_service import compute_dist_snapshot
    try:
        with app.app_context():
            d15 = Path(Config.get_project_root()) / 'data' / '15m'
            # 仅取 6 位数字个股代码：排除 BK 板块，以及 *.v1.bak 之类备份文件
            # （它们的 stem 形如 000063.v1.bak，会撑爆 stock_code 列且非真实代码）
            codes = sorted(p.stem for p in d15.glob('*.parquet')
                           if re.fullmatch(r'\d{6}', p.stem))
            with _compute_lock:
                _compute_state['total'] = len(codes)
            for code in codes:
                try:
                    row = compute_dist_snapshot(code, snapshot_date=target_date, commit=True)
                    with _compute_lock:
                        _compute_state['empty' if row is None else 'ok'] += 1
                except Exception:
                    logger.exception(f'[screener] 计算 {code} 快照失败')
                    with _compute_lock:
                        _compute_state['fail'] += 1
                finally:
                    with _compute_lock:
                        _compute_state['done'] += 1
    except Exception as e:
        logger.exception('[screener] 后台快照计算异常')
        with _compute_lock:
            _compute_state['error'] = str(e)
    finally:
        with _compute_lock:
            _compute_state['running'] = False


@screener_bp.route('/api/snapshots/compute', methods=['POST'])
def api_compute():
    """启动后台计算：对全部收集个股生成今日分布快照（同日重跑覆盖）。"""
    with _compute_lock:
        if _compute_state['running']:
            return jsonify({'success': False, 'message': '已有快照计算在进行中'}), 409
        _compute_state.update({'running': True, 'done': 0, 'total': 0, 'ok': 0,
                               'empty': 0, 'fail': 0, 'date': str(date.today()),
                               'error': None})
    app = current_app._get_current_object()
    threading.Thread(target=_run_compute_all, args=(app, date.today()),
                     daemon=True).start()
    return jsonify({'success': True, 'message': '已开始后台计算今日快照'})


@screener_bp.route('/api/snapshots/compute_status', methods=['GET'])
def api_compute_status():
    with _compute_lock:
        return jsonify({'success': True, 'data': dict(_compute_state)})


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

    # 4.5) 板块趋势过滤：只看「所属板块最新趋势阶段」匹配的股票
    #   board_stage = up_early/up_late/down_early/down_late（精确），或 up/down（前缀，含初/末期）
    board_stage = (request.args.get('board_stage') or '').strip()
    if board_stage:
        bs_codes = _codes_by_board_stage(board_stage)
        if bs_codes:
            q = q.filter(StockDistSnapshot.stock_code.in_(bs_codes))
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
    data = [r.to_dict() for r in rows]
    _enrich_boards(data)
    return jsonify({
        'success': True,
        'date': target.isoformat(),
        'total': total,
        'count': len(rows),
        'conditions': applied_conditions,
        'data': data,
    })


def _codes_by_board_stage(stage):
    """返回「所属板块最新趋势阶段匹配 stage」的全部成分股代码集合。

    stage 取 up_early/up_late/down_early/down_late（精确），或 up/down（前缀，含初/末期）。
    流程：每个 board_code 取最新一条 → 命中 stage 的板块 → industry_eastmoney 取其成分股。
    查询失败/无命中返回空集（调用方据此返回空结果）。
    """
    try:
        from App.models.evaluation.BoardTrendScore import BoardTrendScore
        rows = (BoardTrendScore.query
                .order_by(BoardTrendScore.board_code, BoardTrendScore.record_date.desc())
                .all())
        latest = {}
        for r in rows:
            latest.setdefault(r.board_code, r.trend_stage)
        if stage in ('up', 'down'):
            board_codes = [bc for bc, st in latest.items()
                           if st and st.startswith(stage)]
        else:
            board_codes = [bc for bc, st in latest.items() if st == stage]
        if not board_codes:
            return set()
        eng = db.engines['quanttradingsystem']
        stmt = text('SELECT DISTINCT stock_code FROM industry_eastmoney '
                    'WHERE board_code IN :bcs').bindparams(
                        bindparam('bcs', expanding=True))
        with eng.connect() as conn:
            return {r.stock_code for r in conn.execute(stmt, {'bcs': board_codes}).fetchall()
                    if r.stock_code}
    except Exception:
        logger.exception('[screener] 按板块趋势筛选失败')
        return set()


def _enrich_boards(data):
    """给结果行附加 板块（东财行业）+ 板块趋势（eval_board_trend_score 最新一条）。

    每只股票取一个东财行业板块（按 industry_eastmoney 最新日期）；再取该板块最新
    record_date 的趋势阶段/综合分/信号。查询失败时对应字段留空，不影响主筛选。
    """
    codes = [d['stock_code'] for d in data if d.get('stock_code')]
    if not codes:
        return
    # 1) 每只股票 → 一个东财行业板块（按日期取最新那条）
    code_board = {}
    try:
        eng = db.engines['quanttradingsystem']
        stmt = text('''
            SELECT t.stock_code, t.board_code, t.board_name
              FROM industry_eastmoney t
              JOIN (SELECT stock_code, MAX(date) mx FROM industry_eastmoney
                     WHERE stock_code IN :codes GROUP BY stock_code) m
                ON t.stock_code = m.stock_code AND t.date = m.mx
             WHERE t.stock_code IN :codes
        ''').bindparams(bindparam('codes', expanding=True))
        with eng.connect() as conn:
            for r in conn.execute(stmt, {'codes': codes}).fetchall():
                code_board.setdefault(r.stock_code, (r.board_code, r.board_name))
    except Exception:
        logger.exception('[screener] 取个股板块失败')
    # 2) 板块 → 最新趋势
    board_trend = {}
    board_codes = list({bc for bc, _ in code_board.values() if bc})
    if board_codes:
        try:
            from App.models.evaluation.BoardTrendScore import BoardTrendScore
            trows = (BoardTrendScore.query
                     .filter(BoardTrendScore.board_code.in_(board_codes))
                     .order_by(BoardTrendScore.board_code,
                               BoardTrendScore.record_date.desc())
                     .all())
            for r in trows:
                board_trend.setdefault(r.board_code, {
                    'stage': r.trend_stage,
                    'score': r.total_score,
                    'signal': r.signal,
                    'date': r.record_date.isoformat() if r.record_date else None,
                })
        except Exception:
            logger.exception('[screener] 取板块趋势失败')
    # 3) 附加
    for d in data:
        bc, bn = code_board.get(d['stock_code'], (None, None))
        bt = board_trend.get(bc) if bc else None
        d['board_code'] = bc
        d['board_name'] = bn
        d['board_trend_stage'] = bt['stage'] if bt else None
        d['board_trend_score'] = bt['score'] if bt else None
        d['board_signal'] = bt['signal'] if bt else None
