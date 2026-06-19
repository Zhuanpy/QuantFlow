"""
板块数据整体预览路由 /board_overview

只读看板：以板块主表 eval_board 为基础，横向汇总每个板块的
  - 数据覆盖状态（日K/1m/15m）
  - 成分股数量
  - 最新趋势打分（综合分 / 阶段 / 信号）
  - 个人偏好分
并在顶部给出分布统计卡片（趋势阶段分布 / 偏好分布 / 数据健康度）。
"""
from flask import Blueprint, render_template, request, jsonify
import logging

from App.exts import db
from App.models.evaluation.Board import Board, CLASSIFICATIONS
from App.models.evaluation.BoardTrendScore import BoardTrendScore, TREND_STAGES
from App.models.evaluation.BoardPreference import BoardPreference
from App.models.evaluation.BoardTrendDistribution import BoardTrendDistribution
from App.services.board_data_service import (
    latest_trading_date, query_data_status_batch, classify_data_status,
)

logger = logging.getLogger(__name__)

board_overview_bp = Blueprint('board_overview', __name__, url_prefix='/board_overview')


@board_overview_bp.route('/')
def page():
    return render_template('strategy/board_overview.html',
                           trend_stages=TREND_STAGES,
                           classifications=CLASSIFICATIONS)


def _latest_trend_map(codes):
    """codes -> 最新一条 BoardTrendScore（按 record_date）。"""
    if not codes:
        return {}
    sub = (db.session.query(
            BoardTrendScore.board_code,
            db.func.max(BoardTrendScore.record_date).label('mx'))
        .filter(BoardTrendScore.board_code.in_(codes))
        .group_by(BoardTrendScore.board_code).subquery())
    rows = (db.session.query(BoardTrendScore)
        .join(sub, db.and_(BoardTrendScore.board_code == sub.c.board_code,
                           BoardTrendScore.record_date == sub.c.mx))
        .all())
    return {r.board_code: r for r in rows}


def _compute_stage_distribution():
    """计算当前全量板块的趋势阶段分布。

    返回 (stage_counts:dict, total_boards:int, snapshot_date:date|None)。
    snapshot_date 取「最新评分日期」，无任何评分时回退到最新交易日。
    """
    all_boards = Board.query.all()
    all_codes = [b.board_code for b in all_boards]
    trend_map = _latest_trend_map(all_codes)

    stage_counts = {s: 0 for s in TREND_STAGES}
    stage_counts['none'] = 0
    latest_score_date = None
    for code in all_codes:
        t = trend_map.get(code)
        st = (t.trend_stage if t else None) or 'none'
        stage_counts[st] = stage_counts.get(st, 0) + 1
        if t and t.record_date and (latest_score_date is None or t.record_date > latest_score_date):
            latest_score_date = t.record_date

    snapshot_date = latest_score_date or latest_trading_date()
    return stage_counts, len(all_codes), snapshot_date


@board_overview_bp.route('/distribution')
def distribution_page():
    """趋势阶段分布历史展示页。"""
    return render_template('strategy/board_trend_distribution.html')


@board_overview_bp.route('/api/distribution/snapshot', methods=['POST'])
def api_distribution_snapshot():
    """把当前趋势阶段分布记录为一条快照（按快照日期 upsert）。"""
    try:
        Board.ensure_table()
        BoardTrendDistribution.ensure_table()
        stage_counts, total_boards, snapshot_date = _compute_stage_distribution()
        if snapshot_date is None:
            return jsonify({'success': False, 'message': '无法确定快照日期'}), 400
        row = BoardTrendDistribution.upsert(
            snapshot_date, stage_counts, total_boards, source='manual')
        return jsonify({'success': True, 'message': f'已记录 {snapshot_date.isoformat()} 分布',
                        'data': row.to_dict()})
    except Exception as e:
        logger.exception('记录趋势分布快照失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_overview_bp.route('/api/distribution/history')
def api_distribution_history():
    """趋势阶段分布历史（按日期升序）。query: limit（默认 60）。"""
    try:
        BoardTrendDistribution.ensure_table()
        limit = request.args.get('limit', 60, type=int)
        rows = BoardTrendDistribution.get_history(limit=limit)
        return jsonify({'success': True, 'data': {
            'items': [r.to_dict() for r in rows],
        }})
    except Exception as e:
        logger.exception('查询趋势分布历史失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_overview_bp.route('/api/overview')
def api_overview():
    """聚合预览。

    query: page, page_size, classification, trend_stage, min_preference,
           sort_by: trend_desc / preference_desc / member_desc / board_code
    返回 items（分页）+ summary（对全部主表板块的分布统计）。
    """
    try:
        Board.ensure_table()
        BoardPreference.ensure_table()

        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 50, type=int)
        classification = request.args.get('classification', '').strip()
        trend_stage = request.args.get('trend_stage', '').strip()
        min_preference = request.args.get('min_preference', type=int)
        sort_by = request.args.get('sort_by', 'trend_desc')

        # 全量主表板块（用于 summary），筛选只作用于明细分页
        all_boards = Board.query.order_by(Board.board_code.asc()).all()
        all_codes = [b.board_code for b in all_boards]
        trend_map = _latest_trend_map(all_codes)
        pref_map = BoardPreference.map_for(all_codes)

        # 数据状态（全量一次查）
        status_map = {}
        ref_date = latest_trading_date()
        try:
            raw = query_data_status_batch(all_codes)
            for c in all_codes:
                hit = raw.get(c, {})
                status_map[c] = classify_data_status(
                    hit.get('latest_daily'), hit.get('latest_1m'),
                    hit.get('latest_15m'), ref_date)
        except Exception as ds_err:
            logger.warning(f'overview data_status 失败: {ds_err}')

        def _row(b):
            t = trend_map.get(b.board_code)
            return {
                'board_code': b.board_code,
                'board_name': b.board_name,
                'classification': b.classification,
                'member_count': b.member_count,
                'enabled': b.enabled,
                'data_status': status_map.get(b.board_code),
                'trend_stage': t.trend_stage if t else None,
                'signal': t.signal if t else None,
                'total_score': t.total_score if t else None,
                'score_date': t.record_date.isoformat() if t and t.record_date else None,
                'preference_score': pref_map.get(b.board_code),
            }

        rows = [_row(b) for b in all_boards]

        # ---- summary（全量） ----
        stage_counts = {s: 0 for s in TREND_STAGES}
        stage_counts['none'] = 0
        pref_buckets = {'8-10': 0, '6-7': 0, '4-5': 0, '1-3': 0, 'unscored': 0}
        health = {'ok': 0, 'lag': 0, 'stale': 0, 'missing': 0, 'unknown': 0}
        for r in rows:
            st = r['trend_stage'] or 'none'
            stage_counts[st] = stage_counts.get(st, 0) + 1
            p = r['preference_score']
            if p is None:
                pref_buckets['unscored'] += 1
            elif p >= 8:
                pref_buckets['8-10'] += 1
            elif p >= 6:
                pref_buckets['6-7'] += 1
            elif p >= 4:
                pref_buckets['4-5'] += 1
            else:
                pref_buckets['1-3'] += 1
            ov = (r['data_status'] or {}).get('overall') if r['data_status'] else None
            health[ov if ov in health else 'unknown'] += 1
        # 最新评分日期（全量板块里最大的一条 record_date）
        latest_score_date = max(
            (r['score_date'] for r in rows if r['score_date']), default=None)
        summary = {
            'total_boards': len(rows),
            'stage_counts': stage_counts,
            'preference_buckets': pref_buckets,
            'data_health': health,
            'reference_date': ref_date.isoformat() if ref_date else None,
            'latest_score_date': latest_score_date,
        }

        # ---- 明细筛选 + 排序 + 分页（在内存里做，板块量级 ~ 百级别） ----
        filtered = rows
        if classification and classification in CLASSIFICATIONS:
            filtered = [r for r in filtered if r['classification'] == classification]
        if trend_stage:
            filtered = [r for r in filtered if (r['trend_stage'] or 'none') == trend_stage]
        if min_preference is not None:
            filtered = [r for r in filtered
                        if r['preference_score'] is not None
                        and r['preference_score'] >= min_preference]

        def _key_trend(r):
            return (r['total_score'] is None, -(r['total_score'] or 0))

        def _key_pref(r):
            return (r['preference_score'] is None, -(r['preference_score'] or 0))

        def _key_member(r):
            return (r['member_count'] is None, -(r['member_count'] or 0))

        if sort_by == 'preference_desc':
            filtered.sort(key=_key_pref)
        elif sort_by == 'member_desc':
            filtered.sort(key=_key_member)
        elif sort_by == 'board_code':
            filtered.sort(key=lambda r: r['board_code'])
        else:
            filtered.sort(key=_key_trend)

        total = len(filtered)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, page), pages)
        start = (page - 1) * page_size
        items = filtered[start:start + page_size]

        return jsonify({'success': True, 'data': {
            'items': items,
            'summary': summary,
            'pagination': {
                'page': page, 'pages': pages, 'per_page': page_size,
                'total': total, 'has_prev': page > 1, 'has_next': page < pages,
            }
        }})
    except Exception as e:
        logger.exception('板块整体预览失败')
        return jsonify({'success': False, 'message': str(e)}), 500
