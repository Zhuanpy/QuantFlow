"""L1 候选池路由 /board_pool

职责：把 L0 全量记录里"值得下功夫跟"的板块挑进候选池，并把它们喂给已有的重管线
（成分同步 → 合成指数 → v1-mtf 趋势评分）。

与其他板块蓝图的分工：
  board_data     板块本身的数据管理与原始行情
  board_trend    趋势打分
  board_pref     个人偏好
  board_overview 整体看板
  **board_pool   候选池进出（本文件）**

规则与闸门都在 App/services/board_pool_service.py，这里只做 HTTP 编排。
"""
from flask import Blueprint, render_template, request, jsonify, current_app
from datetime import datetime
import logging
import threading

from App.exts import db
from App.models.evaluation.Board import Board, TIER_L0, TIER_L1
from App.services import board_pool_service as pool_svc

logger = logging.getLogger(__name__)

board_pool_bp = Blueprint('board_pool', __name__, url_prefix='/board_pool')

# 后台任务状态（进程内单实例；成分同步/合成指数都很慢，不能占着请求线程）
_TASK = {'running': False, 'kind': None, 'started_at': None,
         'progress': '', 'result': None, 'error': None}
_LOCK = threading.Lock()


def _task_snapshot():
    with _LOCK:
        return dict(_TASK)


def _start_task(app, kind, fn):
    """起一个后台任务；同一时刻只允许一个（这些活都在抢东财接口和数据库）。"""
    with _LOCK:
        if _TASK['running']:
            return False, f'已有任务在跑：{_TASK["kind"]}'
        _TASK.update({'running': True, 'kind': kind, 'progress': '启动中…',
                      'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                      'result': None, 'error': None})

    def _run():
        try:
            with app.app_context():
                res = fn()
            with _LOCK:
                _TASK['result'] = res
        except Exception as e:
            logger.exception(f'[pool] 后台任务 {kind} 失败')
            with _LOCK:
                _TASK['error'] = str(e)[:400]
        finally:
            with _LOCK:
                _TASK['running'] = False
                _TASK['progress'] = '完成'

    threading.Thread(target=_run, name=f'board-pool-{kind}', daemon=True).start()
    return True, '已启动'


# ===================== 页面 =====================
@board_pool_bp.route('/')
def page():
    return render_template('strategy/board_pool.html')


# ===================== 概况 / 清单 =====================
@board_pool_bp.route('/api/status')
def api_status():
    try:
        st = pool_svc.pool_status()
        st['task'] = _task_snapshot()
        return jsonify({'success': True, 'data': st})
    except Exception as e:
        logger.exception('读取候选池概况失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_pool_bp.route('/api/list')
def api_list():
    """池内清单。query: tier=1(默认) / classification=概念板块 / blocked=1 看被拦截的。"""
    try:
        Board.ensure_table()
        q = Board.query
        if request.args.get('blocked') == '1':
            q = q.filter(Board.exclude_reason.isnot(None))
        else:
            try:
                tier = int(request.args.get('tier', TIER_L1))
            except (TypeError, ValueError):
                tier = TIER_L1
            q = q.filter(Board.tracking_tier == tier)
        cls = (request.args.get('classification') or '').strip()
        if cls:
            q = q.filter(Board.classification == cls)
        rows = q.order_by(Board.classification.asc(), Board.board_code.asc()).all()

        # 附上最新热度，方便在池里直接看谁凉了
        from App.models.strategy.SectorFlowDaily import SectorFlowDaily
        codes = [b.board_code for b in rows]
        heat = {}
        if codes:
            sub = (db.session.query(SectorFlowDaily.board_code,
                                    db.func.max(SectorFlowDaily.date).label('d'))
                   .filter(SectorFlowDaily.board_code.in_(codes),
                           SectorFlowDaily.rank_heat.isnot(None))
                   .group_by(SectorFlowDaily.board_code).subquery())
            for r in (db.session.query(SectorFlowDaily)
                      .join(sub, db.and_(SectorFlowDaily.board_code == sub.c.board_code,
                                         SectorFlowDaily.date == sub.c.d)).all()):
                heat[r.board_code] = {'heat_score': r.heat_score, 'rank_heat': r.rank_heat,
                                      'change_pct': r.change_pct, 'date': str(r.date)}
        return jsonify({'success': True, 'data': [
            {**b.to_dict(), 'heat': heat.get(b.board_code)} for b in rows]})
    except Exception as e:
        logger.exception('读取候选池清单失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ===================== 进出池 =====================
@board_pool_bp.route('/api/evaluate')
def api_evaluate():
    """试算入池/出池建议（dry-run，不写库）。"""
    try:
        return jsonify({'success': True, 'data': pool_svc.evaluate()})
    except Exception as e:
        logger.exception('候选池试算失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_pool_bp.route('/api/apply', methods=['POST'])
def api_apply():
    """应用进出池建议（写 eval_board）。"""
    try:
        res = pool_svc.apply_changes()
        return jsonify({'success': bool(res.get('ok')), 'data': res})
    except Exception as e:
        db.session.rollback()
        logger.exception('候选池应用失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_pool_bp.route('/api/pin', methods=['POST'])
def api_pin():
    """手工钉选/取消。body: {board_code, pinned:true/false}"""
    data = request.get_json(silent=True) or {}
    code = (data.get('board_code') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '缺少 board_code'}), 400
    try:
        row = pool_svc.pin(code, pinned=bool(data.get('pinned', True)),
                           board_name=data.get('board_name'))
        return jsonify({'success': True, 'data': row})
    except Exception as e:
        db.session.rollback()
        logger.exception('钉选失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_pool_bp.route('/api/drop', methods=['POST'])
def api_drop():
    """手工把某板块退回 L0（只降层级，历史记录一行不删）。"""
    data = request.get_json(silent=True) or {}
    code = (data.get('board_code') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '缺少 board_code'}), 400
    try:
        Board.upsert(code, tracking_tier=TIER_L0, is_pinned=False,
                     exclude_reason=(data.get('reason') or 'manual'),
                     notes='手工退回 L0')
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


# ===================== 重管线（后台跑）=====================
@board_pool_bp.route('/api/sync_members', methods=['POST'])
def api_sync_members():
    """给池内板块拉成分股（后台）。body: {codes:[...], only_missing:true}"""
    data = request.get_json(silent=True) or {}
    codes = data.get('codes') or None
    only_missing = data.get('only_missing', True)
    app = current_app._get_current_object()
    ok, msg = _start_task(app, 'sync_members',
                          lambda: pool_svc.sync_members(codes, only_missing=only_missing))
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 409)


@board_pool_bp.route('/api/build_synth', methods=['POST'])
def api_build_synth():
    """给池内概念板块合成指数（后台）。概念没有东财日K，只能自己合成。

    body: {codes:[...], weighting:'circ'|'equal', rebuild:false}
    """
    data = request.get_json(silent=True) or {}
    codes = data.get('codes') or None
    weighting = data.get('weighting') or 'circ'
    rebuild = bool(data.get('rebuild'))
    app = current_app._get_current_object()

    def _job():
        from App.services.board_synth_service import build_synthetic_board, synth_exists
        targets = codes or [b.board_code for b in pool_svc.Board.list_by_tier(TIER_L1)
                            if b.classification == '概念板块' and b.member_count]
        done, skipped, failed = [], [], []
        for code in targets:
            with _LOCK:
                _TASK['progress'] = f'合成 {code}（{len(done)+len(skipped)+len(failed)+1}/{len(targets)}）'
            try:
                if not rebuild and (synth_exists(code) or {}).get('daily_rows'):
                    skipped.append(code)
                    continue
                res = build_synthetic_board(code, weighting=weighting,
                                            timeframes=('daily', '15m'))
                if (res or {}).get('daily_rows'):
                    Board.upsert(code, has_daily_data=True)
                    done.append(code)
                else:
                    failed.append({'board_code': code, 'error': '合成结果为空'})
            except Exception as e:
                failed.append({'board_code': code, 'error': str(e)[:150]})
        return {'built': done, 'skipped': skipped, 'failed': failed,
                'total': len(targets)}

    ok, msg = _start_task(app, 'build_synth', _job)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 409)


@board_pool_bp.route('/api/score', methods=['POST'])
def api_score():
    """给池内板块跑 v1-mtf 趋势评分（后台）。body: {record_date:'YYYY-MM-DD', codes:[...]}"""
    from datetime import date as _date
    data = request.get_json(silent=True) or {}
    codes = data.get('codes') or None
    rd = data.get('record_date')
    try:
        rd = datetime.strptime(rd, '%Y-%m-%d').date() if rd else _date.today()
    except ValueError:
        return jsonify({'success': False, 'message': 'record_date 格式应为 YYYY-MM-DD'}), 400
    app = current_app._get_current_object()

    def _job():
        from App.services.board_trend_score_service import compute_and_persist
        targets = codes or [b.board_code for b in Board.list_by_tier(TIER_L1)
                            if b.has_daily_data]
        with _LOCK:
            _TASK['progress'] = f'评分 {len(targets)} 个板块…'
        res = compute_and_persist(targets, rd)
        return {'record_date': rd.isoformat(), 'requested': len(targets),
                **{k: v for k, v in res.items() if k not in ('updated',)},
                'errors': res.get('errors', [])[:30]}

    ok, msg = _start_task(app, 'score', _job)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 409)


@board_pool_bp.route('/api/heat_ts', methods=['POST'])
def api_heat_ts():
    """给池内板块算时序热度分（后台）。body: {codes:[...], rebuild:false}

    时序热度不依赖全市场快照，只用板块自己的日K/合成指数算滚动分位，所以能整段回补 ——
    概念板块因此立刻拥有几年历史，不用等横截面快照慢慢攒。
    """
    data = request.get_json(silent=True) or {}
    codes = data.get('codes') or None
    rebuild = bool(data.get('rebuild'))
    app = current_app._get_current_object()

    def _job():
        from App.services.board_heat_ts_service import compute_pool
        with _LOCK:
            _TASK['progress'] = '计算时序热度…'
        return compute_pool(codes, incremental=not rebuild)

    ok, msg = _start_task(app, 'heat_ts', _job)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 409)


@board_pool_bp.route('/api/fold', methods=['POST'])
def api_fold():
    """Jaccard 折叠：成分高度重叠的池内板块归并成一条主线。body:{dry_run:true}"""
    data = request.get_json(silent=True) or {}
    try:
        res = pool_svc.fold_aliases(dry_run=bool(data.get('dry_run', True)))
        return jsonify({'success': True, 'data': res})
    except Exception as e:
        db.session.rollback()
        logger.exception('折叠失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_pool_bp.route('/api/task')
def api_task():
    return jsonify({'success': True, 'data': _task_snapshot()})
