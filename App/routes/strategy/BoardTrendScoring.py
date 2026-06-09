"""
板块趋势打分路由 /board_trend

职责：板块趋势阶段（上涨前期/上涨后期/下跌前期/下跌后期）的机器评分。
  - 评分列表 / 统计 / 日期 / 增删改 / 自动计算 / 评分历史 / 指标图解

板块清单与原始数据（K线/成分股/市值）见 board_data，个人偏好见 board_pref，
整体看板见 board_overview。共享逻辑（数据状态）见 services/board_data_service。
"""
from flask import Blueprint, render_template, request, jsonify
from datetime import datetime, date
import logging

from App.exts import db
from App.models.evaluation.BoardTrendScore import (
    BoardTrendScore, TREND_STAGES, TREND_STRENGTHS, SIGNALS,
)
from App.models.evaluation.BoardPreference import BoardPreference
from App.models.evaluation.Board import Board
from App.services.board_data_service import (
    latest_trading_date, query_data_status_batch, classify_data_status,
)

logger = logging.getLogger(__name__)

board_trend_bp = Blueprint('board_trend', __name__, url_prefix='/board_trend')


# ----------------- 页面 -----------------
@board_trend_bp.route('/')
def page():
    return render_template(
        'strategy/board_trend_scoring.html',
        trend_stages=TREND_STAGES,
        trend_strengths=TREND_STRENGTHS,
        signals=SIGNALS,
    )


@board_trend_bp.route('/api/indicator/<key>.png')
def api_indicator_chart(key):
    """子分指标的计算图解（matplotlib PNG）。key ∈ INDICATORS。"""
    from flask import Response, abort
    from App.services.indicator_explain_service import render_png, INDICATORS
    if key not in INDICATORS:
        abort(404)
    try:
        png = render_png(key)
    except Exception as e:
        logger.exception(f'生成指标图解失败: {key}')
        return jsonify({'success': False, 'message': str(e)}), 500
    return Response(png, mimetype='image/png',
                    headers={'Cache-Control': 'public, max-age=86400'})


# ----------------- 评分历史 -----------------
@board_trend_bp.route('/api/board/<code>/history')
def api_board_history(code):
    """返回该板块在打分表里的历史评分时间线"""
    try:
        rows = (BoardTrendScore.query
                .filter_by(board_code=code)
                .order_by(BoardTrendScore.record_date.desc())
                .limit(120).all())
        board_name = rows[0].board_name if rows else code
        return jsonify({
            'success': True,
            'data': {
                'board_code': code,
                'board_name': board_name,
                'count': len(rows),
                'rows': [r.to_dict() for r in rows],
            }
        })
    except Exception as e:
        logger.exception('板块历史评分获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 列表 / 查询 -----------------
@board_trend_bp.route('/api/list')
def api_list():
    """列表查询。

    query params:
      record_date: YYYY-MM-DD（可选；默认当前最新一日）
      board_code: 模糊匹配
      board_name: 模糊匹配
      trend_stage: 阶段精确匹配
      trend_direction: up / down（前期+后期方向快捷）
      signal: 信号精确匹配
      min_preference: 个人偏好分下限
      page, page_size
      sort_by: total_score_desc / total_score_asc / preference_desc / preference_asc / updated / board_code
    """
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 30, type=int)
        record_date = request.args.get('record_date', '').strip()
        board_code = request.args.get('board_code', '').strip()
        board_name = request.args.get('board_name', '').strip()
        trend_stage = request.args.get('trend_stage', '').strip()
        # 方向快捷：up = up_early + up_late, down = down_early + down_late
        trend_direction = request.args.get('trend_direction', '').strip().lower()
        signal = request.args.get('signal', '').strip()
        sort_by = request.args.get('sort_by', 'total_score_desc')
        min_preference = request.args.get('min_preference', type=int)

        BoardPreference.ensure_table()
        q = BoardTrendScore.query

        # 偏好筛选 / 偏好排序需要 LEFT JOIN 偏好表（偏好按 board_code 唯一）
        need_pref_join = (min_preference is not None
                          or sort_by in ('preference_desc', 'preference_asc'))
        if need_pref_join:
            q = q.outerjoin(
                BoardPreference,
                BoardPreference.board_code == BoardTrendScore.board_code)
            if min_preference is not None:
                q = q.filter(BoardPreference.preference_score >= min_preference)

        if record_date:
            q = q.filter(BoardTrendScore.record_date == record_date)
        else:
            latest = db.session.query(db.func.max(BoardTrendScore.record_date)).scalar()
            if latest:
                q = q.filter(BoardTrendScore.record_date == latest)
                record_date = latest.isoformat()

        if board_code:
            q = q.filter(BoardTrendScore.board_code.like(f'%{board_code}%'))
        if board_name:
            q = q.filter(BoardTrendScore.board_name.like(f'%{board_name}%'))
        if trend_stage and trend_stage in TREND_STAGES:
            q = q.filter(BoardTrendScore.trend_stage == trend_stage)
        elif trend_direction in ('up', 'down'):
            stages = ('up_early', 'up_late') if trend_direction == 'up' \
                     else ('down_early', 'down_late')
            q = q.filter(BoardTrendScore.trend_stage.in_(stages))
        if signal and signal in SIGNALS:
            q = q.filter(BoardTrendScore.signal == signal)

        # MySQL 不支持 NULLS LAST，用 (col IS NULL) 把 NULL 排到最后
        null_last = (BoardTrendScore.total_score.is_(None)).asc()
        if sort_by == 'total_score_asc':
            q = q.order_by(null_last, BoardTrendScore.total_score.asc())
        elif sort_by == 'updated':
            q = q.order_by(BoardTrendScore.updated_at.desc())
        elif sort_by == 'board_code':
            q = q.order_by(BoardTrendScore.board_code.asc())
        elif sort_by in ('preference_desc', 'preference_asc'):
            null_last_pref = (BoardPreference.preference_score.is_(None)).asc()
            if sort_by == 'preference_asc':
                q = q.order_by(null_last_pref, BoardPreference.preference_score.asc())
            else:
                q = q.order_by(null_last_pref, BoardPreference.preference_score.desc())
        else:
            q = q.order_by(null_last, BoardTrendScore.total_score.desc())

        pagination = q.paginate(page=page, per_page=page_size, error_out=False)
        items = [r.to_dict() for r in pagination.items]

        # 批量挂"个人偏好分"——按 board_code 取，一个板块共享一个偏好
        try:
            pref_codes = [it.get('board_code') for it in items if it.get('board_code')]
            pref_map = BoardPreference.map_for(pref_codes)
            for it in items:
                it['preference_score'] = pref_map.get(it.get('board_code'))
        except Exception as pf_err:
            logger.warning(f'附加 preference_score 失败: {pf_err}')
            for it in items:
                it.setdefault('preference_score', None)

        # 批量挂"数据状态"——一次查 DailyTaskStatus，按 board_code 拼回去
        try:
            codes = [it.get('board_code') for it in items if it.get('board_code')]
            status_map = query_data_status_batch(codes)
            ref_date = latest_trading_date()
            for it in items:
                code = it.get('board_code')
                hit = status_map.get(code, {})
                it['data_status'] = classify_data_status(
                    hit.get('latest_daily'),
                    hit.get('latest_1m'),
                    hit.get('latest_15m'),
                    ref_date,
                )
        except Exception as ds_err:
            logger.warning(f'附加 data_status 失败: {ds_err}')
            for it in items:
                it.setdefault('data_status', None)

        return jsonify({
            'success': True,
            'data': {
                'items': items,
                'record_date': record_date,
                'pagination': {
                    'page': pagination.page,
                    'pages': pagination.pages,
                    'per_page': pagination.per_page,
                    'total': pagination.total,
                    'has_prev': pagination.has_prev,
                    'has_next': pagination.has_next,
                }
            }
        })
    except Exception as e:
        logger.exception('板块趋势列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/stats')
def api_stats():
    """按阶段统计当日记录数量"""
    try:
        record_date = request.args.get('record_date', '').strip()
        if not record_date:
            latest = db.session.query(db.func.max(BoardTrendScore.record_date)).scalar()
            record_date = latest.isoformat() if latest else None

        stats = {s: 0 for s in TREND_STAGES}
        if record_date:
            rows = (db.session.query(BoardTrendScore.trend_stage, db.func.count())
                    .filter(BoardTrendScore.record_date == record_date)
                    .group_by(BoardTrendScore.trend_stage).all())
            for stage, cnt in rows:
                stats[stage or 'unknown'] = int(cnt)

        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date,
                'stage_counts': stats,
                'total': sum(stats.values()),
            }
        })
    except Exception as e:
        logger.exception('板块趋势统计失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/dates')
def api_dates():
    """已有打分记录的日期列表（最新在前）"""
    try:
        rows = (db.session.query(BoardTrendScore.record_date)
                .distinct()
                .order_by(BoardTrendScore.record_date.desc())
                .limit(60).all())
        return jsonify({
            'success': True,
            'data': [r[0].isoformat() for r in rows if r[0]],
        })
    except Exception as e:
        logger.exception('板块趋势日期列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 写入 -----------------
def _parse_payload(data):
    """从请求体取出可写字段（白名单），并做基本类型转换"""
    allowed = {
        'board_code', 'board_name', 'record_date',
        'trend_stage', 'trend_stage_confidence', 'trend_strength', 'signal',
        'price_structure_score', 'ma_score', 'macd_score',
        'volume_score', 'momentum_score', 'volatility_score', 'total_score',
        'close', 'change_pct', 'ma20', 'ma60',
        'macd_dif', 'macd_dea', 'macd_bar', 'atr',
        'formula_version', 'notes', 'is_manual',
    }
    out = {}
    for k, v in (data or {}).items():
        if k not in allowed:
            continue
        if v == '' or v is None:
            out[k] = None
            continue
        out[k] = v

    if 'record_date' in out and isinstance(out['record_date'], str):
        out['record_date'] = datetime.strptime(out['record_date'], '%Y-%m-%d').date()

    if out.get('trend_stage') and out['trend_stage'] not in TREND_STAGES:
        raise ValueError(f"非法 trend_stage: {out['trend_stage']}")
    if out.get('trend_strength') and out['trend_strength'] not in TREND_STRENGTHS:
        raise ValueError(f"非法 trend_strength: {out['trend_strength']}")
    if out.get('signal') and out['signal'] not in SIGNALS:
        raise ValueError(f"非法 signal: {out['signal']}")

    return out


@board_trend_bp.route('/api/upsert', methods=['POST'])
def api_upsert():
    """新增或更新一条板块趋势打分记录（按 board_code+record_date 唯一）"""
    try:
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)

        board_code = fields.pop('board_code', None)
        board_name = fields.pop('board_name', None)
        record_date = fields.pop('record_date', None) or date.today()

        if not board_code:
            return jsonify({'success': False, 'message': 'board_code 必填'}), 400

        if board_name is not None:
            fields['board_name'] = board_name
        if data.get('is_manual') is None:
            fields['is_manual'] = True

        row = BoardTrendScore.upsert(board_code=board_code,
                                     record_date=record_date,
                                     **fields)
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('upsert 板块趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/update/<int:row_id>', methods=['PUT'])
def api_update(row_id):
    """更新指定记录（不可改 board_code/record_date 唯一键）"""
    try:
        row = BoardTrendScore.query.get_or_404(row_id)
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)
        fields.pop('board_code', None)
        fields.pop('record_date', None)

        for k, v in fields.items():
            if hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('更新板块趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/delete/<int:row_id>', methods=['DELETE'])
def api_delete(row_id):
    try:
        ok = BoardTrendScore.delete_by_id(row_id)
        if not ok:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.exception('删除板块趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 计算入口 -----------------
@board_trend_bp.route('/api/compute', methods=['POST'])
def api_compute():
    """自动打分（v1）。板块清单优先取自板块主表 eval_board。

    body: {
        record_date: 'YYYY-MM-DD'（缺省取今天）,
        board_codes: ['BK0420', ...]（可选；缺省时按 scope 从主表解析）,
        scope: 'all' | 'unknown' | 'manual_skip'  仅在 board_codes 为空时生效
            all          → 主表所有启用且有数据的板块
            unknown      → 上述板块中，当日 stage=unknown / total_score 为空 / 当日无记录
            manual_skip  → 上述板块中，跳过当日 is_manual=True 的
    """
    try:
        from App.services.board_trend_score_service import compute_and_persist

        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        record_date = (datetime.strptime(record_date, '%Y-%m-%d').date()
                       if record_date else date.today())

        board_codes = data.get('board_codes') or []
        if not board_codes:
            scope = (data.get('scope') or 'unknown').lower()
            board_codes = _resolve_compute_codes(scope, record_date)

        if not board_codes:
            return jsonify({
                'success': False,
                'message': '没有需要计算的板块。先到「板块数据」页同步板块清单，或调整 scope。',
            }), 400

        result = compute_and_persist(board_codes, record_date)
        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date.isoformat(),
                'requested': len(board_codes),
                **result,
                'errors': result['errors'][:50],
                'updated': result['updated'][:50],
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('板块自动打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


def _resolve_compute_codes(scope, record_date):
    """按 scope 解析待计算板块清单。

    优先用板块主表 eval_board（enabled 且 has_daily_data）；主表为空时回退到
    现有 eval_board_trend_score 去重 board_code，保证旧流程不被打断。
    """
    Board.ensure_table()
    master_codes = [b.board_code for b in Board.list_enabled(require_data=True)]

    if master_codes:
        if scope == 'all':
            return master_codes
        existing = {r.board_code: r for r in
                    BoardTrendScore.query.filter(
                        BoardTrendScore.record_date == record_date).all()}
        if scope == 'manual_skip':
            return [c for c in master_codes
                    if not (existing.get(c) and existing[c].is_manual)]
        # unknown
        return [c for c in master_codes
                if (c not in existing)
                or existing[c].trend_stage == 'unknown'
                or existing[c].total_score is None]

    # 回退：主表尚未同步时，沿用旧的"基于评分行"解析
    q = BoardTrendScore.query.filter(BoardTrendScore.record_date == record_date)
    if scope == 'unknown':
        q = q.filter(db.or_(
            BoardTrendScore.trend_stage == 'unknown',
            BoardTrendScore.total_score.is_(None),
        ))
    elif scope == 'manual_skip':
        q = q.filter(BoardTrendScore.is_manual.isnot(True))
    return [r.board_code for r in q.all()]
