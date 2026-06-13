# -*- coding: utf-8 -*-
"""
RNN 训练 / 预测路由

页面：
    GET  /RnnStrategies/training        训练管理页
    GET  /RnnStrategies/prediction      预测页

训练 API：
    POST /RnnStrategies/api/start_train         启动训练（kind=data|train|check）
    POST /RnnStrategies/api/stop_train          请求停止
    GET  /RnnStrategies/api/train_status        当前任务状态/进度
    GET  /RnnStrategies/api/training_records    训练记录表
    GET  /RnnStrategies/api/available_months    可用月份列表

预测 API：
    POST /RnnStrategies/api/predict_one         单只预测
    POST /RnnStrategies/api/predict_batch       股票池批量预测（后台）
    GET  /RnnStrategies/api/predict_status      批量预测状态/进度
    POST /RnnStrategies/api/stop_predict        停止批量预测
    GET  /RnnStrategies/api/prediction_records  预测记录表
"""
import logging
import threading
from datetime import datetime
from typing import Optional

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import case, text, bindparam, false

from App.exts import db
from App.models.strategy.RnnRunningRecords import RnnRunningRecord
from App.models.strategy.RnnTrainingRecords import RnnTrainingRecords

logger = logging.getLogger(__name__)


def _boards_for_codes(codes):
    """批量查一组股票代码所属的东财行业板块名，返回 {code: '板块A / 板块B'}。

    数据源 industry_eastmoney（每个 board_code 取该股最新日期那条）；一股可能属多个板块，
    名称用 ' / ' 连接。表与 RnnRunningRecord 同在 quanttradingsystem 库（独立 bind）。
    查询失败/无数据时该 code 不出现在结果里（前端显示 '-'）。
    """
    codes = [str(c) for c in {c for c in codes if c}]
    if not codes:
        return {}
    try:
        eng = db.engines['quanttradingsystem']
        stmt = text(
            '''
            SELECT t.stock_code AS code, t.board_name AS board_name
              FROM industry_eastmoney t
              JOIN (SELECT stock_code, board_code, MAX(date) AS mx
                      FROM industry_eastmoney
                     WHERE stock_code IN :codes
                     GROUP BY stock_code, board_code) m
                ON t.stock_code = m.stock_code
               AND t.board_code = m.board_code
               AND t.date = m.mx
             WHERE t.stock_code IN :codes
             ORDER BY t.date DESC, t.board_name
            '''
        ).bindparams(bindparam('codes', expanding=True))
        out = {}
        with eng.connect() as conn:
            for r in conn.execute(stmt, {'codes': codes}).fetchall():
                if r.board_name:
                    out.setdefault(r.code, []).append(r.board_name)
        # 去重保序后拼接
        return {c: ' / '.join(dict.fromkeys(names)) for c, names in out.items()}
    except Exception:
        logger.exception('查 industry_eastmoney 板块失败')
        return {}


def _codes_in_board(board_name):
    """某东财行业板块下的全部股票代码（去重）。查询失败返回 []。"""
    if not board_name:
        return []
    try:
        eng = db.engines['quanttradingsystem']
        stmt = text(
            'SELECT DISTINCT stock_code FROM industry_eastmoney WHERE board_name = :b')
        with eng.connect() as conn:
            return [r.stock_code for r in conn.execute(stmt, {'b': board_name}).fetchall()
                    if r.stock_code]
    except Exception:
        logger.exception('查板块成分股失败')
        return []

rnn_strategies_bp = Blueprint(
    'rnn_strategies_bp', __name__, url_prefix='/RnnStrategies'
)


# ==================== 任务状态 ====================
class TaskState:
    """通用后台任务状态。训练和批量预测各持有一份。"""

    def __init__(self):
        self.kind: Optional[str] = None
        self.status: str = '未开始'  # 未开始 / 进行中 / 已完成 / 已停止 / 失败
        self.progress: float = 0
        self.message: str = ''
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.stop: bool = False
        self.total: int = 0
        self.processed: int = 0
        self.month: Optional[str] = None

    def reset(self, kind: str, month: Optional[str] = None):
        self.kind = kind
        self.status = '进行中'
        self.progress = 0
        self.message = ''
        self.started_at = datetime.now()
        self.finished_at = None
        self.stop = False
        self.total = 0
        self.processed = 0
        self.month = month

    def to_dict(self):
        def fmt(t):
            return t.strftime('%Y-%m-%d %H:%M:%S') if t else None
        return {
            'kind': self.kind,
            'status': self.status,
            'progress': self.progress,
            'message': self.message,
            'started_at': fmt(self.started_at),
            'finished_at': fmt(self.finished_at),
            'total': self.total,
            'processed': self.processed,
            'month': self.month,
        }


train_state = TaskState()
train_lock = threading.Lock()
train_thread: Optional[threading.Thread] = None

predict_state = TaskState()
predict_lock = threading.Lock()
predict_thread: Optional[threading.Thread] = None


# ==================== 页面 ====================
@rnn_strategies_bp.route('/')
@rnn_strategies_bp.route('/training')
def training_page():
    return render_template('strategy/rnn_training.html')


@rnn_strategies_bp.route('/prediction')
def prediction_page():
    return render_template('strategy/rnn_prediction.html')


# ==================== 训练 ====================
def _run_train_kind(kind: str, month: str, start_date: Optional[str],
                     pool_type: Optional[str] = None):
    """在后台线程内执行实际的训练步骤。

    pool_type:
      - None: 走原有"全部"逻辑（all RnnTrainingRecords）
      - 'watching' / 'candidate' / 'trading' / 'archived':
          只对该 StockPool 池里的股票循环执行单股操作
    """
    # 单股循环模式：精确控制范围，逐只汇报进度、响应停止、回写状态。
    #   - pool_type 指定 → 只处理该 StockPool 池
    #   - 否则 kind == 'check'（「全部」历史检查）→ 处理本月 RnnTrainingRecords 全部记录。
    #     旧的 rc.loop_by_date() 依赖已废弃的 rnn_model MySQL 库（本环境不存在该库，
    #     会报 Unknown database 'rnn_model'），故改走与此处一致的 SQLAlchemy 单只路径，
    #     并与 data/train「全部」模式（均按 parser_month 取本月批次）保持一致。
    codes = None
    names = {}
    if pool_type:
        from App.models.strategy.StockPool import StockPool
        stocks = StockPool.get_by_pool_type(pool_type)
        if not stocks:
            logger.warning(f'[TRAIN] 池 {pool_type} 为空，跳过')
            with train_lock:
                train_state.message = f'股票池 {pool_type} 为空，无可处理股票'
            return
        codes = [sp.stock_code for sp in stocks]
        names = {sp.stock_code: sp.stock_name for sp in stocks}
        logger.info(f'[TRAIN] 池模式 {pool_type}：{len(codes)} 只股票')
    elif kind == 'check':
        recs = RnnTrainingRecords.query.filter_by(parser_month=month).all()
        codes = [r.code for r in recs]
        names = {r.code: r.name for r in recs}
        if not codes:
            logger.warning(f'[TRAIN] 本月 {month} 无训练记录，历史检查跳过')
            with train_lock:
                train_state.message = f'本月 {month} 无训练记录可检查'
            return
        logger.info(f'[TRAIN] 全部历史检查 {month}：{len(codes)} 只股票')

    if codes is not None:
        total = len(codes)
        with train_lock:
            train_state.total = total
            train_state.processed = 0

        # 每个 kind 对应一个"处理单只股票"的闭包
        if kind == 'data':
            from App.codes.RnnModel.RnnCreationData import TrainingDataCalculate
            def _do(code):
                TrainingDataCalculate(code, month, start_date).calculation_single()
        elif kind == 'train':
            from App.codes.RnnModel.RnnCreationModel import BuiltModel
            def _do(code):
                BuiltModel(code, month).model_all()
        elif kind == 'check':
            from App.codes.RnnModel.CheckModel import RMHistoryCheck
            rc = RMHistoryCheck(month_parsers=month)
            def _do(code):
                rc.check1stock(code)
        else:
            raise ValueError(f'未知 kind: {kind}')

        # 池模式也回写 RnnTrainingRecords 状态，让记录表实时反映进度。
        # 按 kind 写对应字段：data→model_data / train→model_create / check→model_check。
        # 按 code 取/建行并绑定到本月，与全部模式"单行按月复用"语义一致。
        _FIELD_BY_KIND = {
            'data': ('model_data', 'model_data_timing'),
            'train': ('model_create', 'model_create_timing'),
            'check': ('model_check', 'model_check_timing'),
        }

        def _pool_set_status(code, status):
            field, timing = _FIELD_BY_KIND[kind]
            try:
                db.session.rollback()
                rec = (RnnTrainingRecords.query.filter_by(code=str(code))
                       .order_by(RnnTrainingRecords.id.desc()).first())
                if rec is None:
                    rec = RnnTrainingRecords(code=str(code), name=names.get(code),
                                             parser_month=month)
                    db.session.add(rec)
                rec.parser_month = month
                setattr(rec, field, status)
                setattr(rec, timing, datetime.now() if status == 'success' else None)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.warning(f'[TRAIN] 状态写回失败 {code}/{kind}: {e}')

        # 一次取 BATCH_SIZE 只标成 processing，整批处理完再取下一批
        BATCH_SIZE = 10
        failed = 0
        done = 0
        stopped = False
        for start in range(0, total, BATCH_SIZE):
            with train_lock:
                if train_state.stop:
                    logger.info('[TRAIN] 收到停止请求，提前结束池循环')
                    break
            chunk = codes[start:start + BATCH_SIZE]
            # 整批先标 processing（列表里这一批一起显示"处理中"）
            for code in chunk:
                _pool_set_status(code, 'processing')

            for code in chunk:
                with train_lock:
                    if train_state.stop:
                        logger.info('[TRAIN] 收到停止请求，提前结束池循环')
                        stopped = True
                        break
                    train_state.message = f'正在处理 {code}（{done + 1}/{total}）'
                try:
                    _do(code)
                    _pool_set_status(code, 'success')
                except Exception as e:
                    failed += 1
                    logger.warning(f'[TRAIN] {code} {kind} 失败: {e}')
                    _pool_set_status(code, 'error')
                with train_lock:
                    done += 1
                    train_state.processed = done
                    train_state.progress = round(done / total * 100, 1)
                    if failed:
                        train_state.message = f'已处理 {done}/{total}，其中失败 {failed} 只'
            if stopped:
                break

        if kind == 'train':
            try:
                from App.codes.RnnModel.model_predictor import clear_model_cache
                clear_model_cache()
            except Exception:
                pass
        return

    # 全部模式（原逻辑）
    # 进度写进 train_state，前端轮询 /api/train_status 即可实时看到处理到哪只；
    # data / train 都按 10 只一批、逐只回写状态（见各自的 all_stock）。
    def _report(processed, total, message):
        with train_lock:
            train_state.total = total
            train_state.processed = processed
            train_state.progress = round(processed / total * 100, 1) if total else 0
            train_state.message = message

    def _should_stop():
        with train_lock:
            return train_state.stop

    if kind == 'data':
        from App.codes.RnnModel.RnnCreationTrainingData import RMTrainingData
        RMTrainingData(months=month, start_=start_date).all_stock(
            progress_cb=_report, should_stop=_should_stop)
    elif kind == 'train':
        from App.codes.RnnModel.RnnCreationModel import RMBuiltModel
        RMBuiltModel(month).all_stock(progress_cb=_report, should_stop=_should_stop)
        try:
            from App.codes.RnnModel.model_predictor import clear_model_cache
            clear_model_cache()
        except Exception:
            pass
    else:
        # kind == 'check' 已在上方「单股循环模式」处理并 return，不会走到这里。
        raise ValueError(f'未知 kind: {kind}')


@rnn_strategies_bp.route('/api/start_train', methods=['POST'])
def api_start_train():
    """启动训练任务（分步）。
    body: { kind: 'data'|'train'|'check', month: '2022-02',
            start_date: '2018-01-01' (kind=data 必填),
            pool_type: 'watching' (可选，限定股票池) }
    """
    global train_thread
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    month = data.get('month')
    start_date = data.get('start_date')
    pool_type = (data.get('pool_type') or '').strip() or None

    if kind not in ('data', 'train', 'check'):
        return jsonify({'success': False, 'message': "kind 必须是 'data' / 'train' / 'check'"}), 400
    if not month:
        return jsonify({'success': False, 'message': '请提供 month 参数（如 2022-02）'}), 400
    if kind == 'data' and not start_date:
        return jsonify({'success': False, 'message': "kind='data' 时需要 start_date"}), 400
    if pool_type and pool_type not in ('trading', 'watching', 'candidate', 'archived'):
        return jsonify({'success': False, 'message': f"pool_type 无效: {pool_type}"}), 400

    if train_thread is not None and train_thread.is_alive():
        return jsonify({'success': False, 'message': '已有训练任务在运行中', 'data': train_state.to_dict()}), 409

    with train_lock:
        train_state.reset(kind, month)

    app = current_app._get_current_object()

    def run():
        try:
            with app.app_context():
                _run_train_kind(kind, month, start_date, pool_type=pool_type)
            with train_lock:
                if train_state.stop:
                    train_state.status = '已停止'
                else:
                    train_state.status = '已完成'
                    train_state.progress = 100
                train_state.finished_at = datetime.now()
            logger.info(f'[TRAIN] 任务 {kind}/{month} 完成')
        except Exception as e:
            logger.exception(f'[TRAIN] 任务执行失败: {e}')
            with train_lock:
                train_state.status = '失败'
                train_state.message = str(e)
                train_state.finished_at = datetime.now()

    train_thread = threading.Thread(target=run, daemon=True)
    train_thread.start()
    return jsonify({'success': True, 'message': f'{kind} 任务已启动', 'data': train_state.to_dict()})


@rnn_strategies_bp.route('/api/stop_train', methods=['POST'])
def api_stop_train():
    with train_lock:
        train_state.stop = True
        train_state.message = '已请求停止（Keras 训练循环不可中断，仅在 step 间生效）'
    return jsonify({'success': True, 'message': train_state.message})


@rnn_strategies_bp.route('/api/train_status', methods=['GET'])
def api_train_status():
    with train_lock:
        return jsonify({'success': True, 'data': train_state.to_dict()})


@rnn_strategies_bp.route('/api/training_records', methods=['GET'])
def api_training_records():
    """RnnTrainingRecords 表的内容。可按 month / status 过滤，分页返回。"""
    month = request.args.get('month')
    status = request.args.get('status')
    # 状态针对哪一步骤列：数据/建模/检查 是三个独立状态列，必须指明过滤哪一列，
    # 否则（旧实现写死 model_check）会出现"按 success 筛选却滤掉数据/建模已成功但
    # 未检查的行""跑数据步时按 processing 筛选一条都没有"等不符合直觉的结果。
    _STEP_COLUMNS = {'model_data', 'model_create', 'model_check'}
    step = request.args.get('step') or 'model_check'
    if step not in _STEP_COLUMNS:
        step = 'model_check'
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    try:
        page_size = max(1, min(int(request.args.get('page_size', 20)), 200))
    except ValueError:
        page_size = 20

    q = RnnTrainingRecords.query
    if month:
        q = q.filter_by(parser_month=month)
    if status:
        q = q.filter(getattr(RnnTrainingRecords, step) == status)

    # 排序：让处理进度在列表里实时可见。第 1 页优先看到：
    #   0 正在处理(processing) → 1 处理好的(success，按完成时间倒序) →
    #   2 即将处理(pending/未开始) → 3 其余(error 等)
    # 排序字段跟随当前正在运行的步骤：train→建模(model_create) / check→检查(model_check)
    # / 否则→数据(model_data)，这样跑哪一步，哪一步的"正在处理"就浮到最上。
    with train_lock:
        running_kind = train_state.kind if train_state.status == '进行中' else None
    sort_state, sort_timing = {
        'train': (RnnTrainingRecords.model_create, RnnTrainingRecords.model_create_timing),
        'check': (RnnTrainingRecords.model_check, RnnTrainingRecords.model_check_timing),
    }.get(running_kind,
          (RnnTrainingRecords.model_data, RnnTrainingRecords.model_data_timing))
    order_priority = case(
        (sort_state == 'processing', 0),
        (sort_state == 'success', 1),
        (sort_state == 'pending', 2),
        (sort_state.is_(None), 2),
        else_=3,
    )
    pagination = q.order_by(
        order_priority,
        sort_timing.desc(),
        RnnTrainingRecords.id.desc(),
    ).paginate(page=page, per_page=page_size, error_out=False)
    records = pagination.items

    # 三步骤成功数在"整个过滤集"上统计（不只是当前页），分页时仍准确
    def _success(field):
        return q.filter(getattr(RnnTrainingRecords, field) == 'success').count()

    return jsonify({
        'success': True,
        'data': [r.to_dict() for r in records],
        'count': len(records),
        'pagination': {
            'page': pagination.page,
            'page_size': page_size,
            'total_pages': pagination.pages or 1,
            'total_items': pagination.total,
        },
        'summary': {
            'total': pagination.total,
            'data_success': _success('model_data'),
            'create_success': _success('model_create'),
            'check_success': _success('model_check'),
        },
    })


@rnn_strategies_bp.route('/api/available_months', methods=['GET'])
def api_available_months():
    """训练记录里出现过的所有月份，倒序。"""
    rows = db.session.query(RnnTrainingRecords.parser_month).distinct().all()
    months = sorted({r[0] for r in rows if r and r[0]}, reverse=True)
    return jsonify({'success': True, 'data': months})


@rnn_strategies_bp.route('/api/board_names', methods=['GET'])
def api_board_names():
    """东财行业板块名列表（去重、排序），供预测记录的板块筛选下拉用。"""
    try:
        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            rows = conn.execute(text(
                'SELECT DISTINCT board_name FROM industry_eastmoney '
                'WHERE board_name IS NOT NULL AND board_name <> "" ORDER BY board_name'
            )).fetchall()
        return jsonify({'success': True, 'data': [r.board_name for r in rows]})
    except Exception as e:
        logger.exception('查板块名列表失败')
        return jsonify({'success': False, 'message': str(e), 'data': []})


# 允许打开的子文件夹白名单：键 -> 相对 RnnData/<month> 的子目录
_OPEN_SUBDIRS = {
    'root': '',            # data/RnnData/<month>
    'train_data': 'train_data',
    'model': 'model',
    'weight': 'weight',
    'json': 'json',
    '1m': '1m',
}


@rnn_strategies_bp.route('/api/data_dir', methods=['GET'])
def api_data_dir():
    """返回某月份各数据子目录的绝对路径与是否存在（供 UI 展示/复制）。"""
    import os
    from App.codes.RnnDataFile.stock_path import StockDataPath

    month = (request.args.get('month') or '').strip()
    root = StockDataPath.rnnData_folder_path()
    base = os.path.join(root, month) if month else root
    dirs = {}
    for key, sub in _OPEN_SUBDIRS.items():
        p = os.path.join(base, sub) if sub else base
        dirs[key] = {'path': os.path.normpath(p), 'exists': os.path.isdir(p)}
    return jsonify({'success': True, 'month': month, 'rnn_root': os.path.normpath(root), 'dirs': dirs})


@rnn_strategies_bp.route('/api/open_folder', methods=['POST'])
def api_open_folder():
    """在服务器（本机）资源管理器里打开生成的数据文件夹。

    仅限本机访问（localhost），且路径必须落在 data/RnnData 下，防越权/穿越。
    body: { month: '2022-02' (可选), sub: 'train_data'|'model'|... (默认 root) }
    """
    import os
    import sys
    import subprocess
    from App.codes.RnnDataFile.stock_path import StockDataPath

    # 仅允许本机触发（避免远程浏览器在服务器上弹窗口）
    if request.remote_addr not in ('127.0.0.1', '::1', 'localhost'):
        return jsonify({'success': False, 'message': '仅允许本机（localhost）打开文件夹'}), 403

    data = request.get_json(silent=True) or {}
    month = (data.get('month') or '').strip()
    sub_key = (data.get('sub') or 'root').strip()
    if sub_key not in _OPEN_SUBDIRS:
        return jsonify({'success': False, 'message': f'未知子目录: {sub_key}'}), 400

    root = os.path.normpath(StockDataPath.rnnData_folder_path())
    sub = _OPEN_SUBDIRS[sub_key]
    if month:
        target = os.path.join(root, month, sub) if sub else os.path.join(root, month)
    else:
        target = os.path.join(root, sub) if sub else root
    target = os.path.normpath(target)

    # 安全校验：目标必须在 RnnData 根目录内
    if os.path.commonpath([root, target]) != root:
        return jsonify({'success': False, 'message': '非法路径'}), 400

    if not os.path.isdir(target):
        return jsonify({'success': False, 'message': f'文件夹不存在（可能尚未生成）：{target}',
                        'path': target}), 404

    try:
        if sys.platform.startswith('win'):
            os.startfile(target)  # noqa: only available on Windows
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', target])
        else:
            subprocess.Popen(['xdg-open', target])
        return jsonify({'success': True, 'message': '已打开文件夹', 'path': target})
    except Exception as e:
        logger.exception(f'[OPEN_FOLDER] 打开失败: {e}')
        return jsonify({'success': False, 'message': f'打开失败: {e}', 'path': target}), 500


# ==================== 预测 ====================
@rnn_strategies_bp.route('/api/predict_one', methods=['POST'])
def api_predict_one():
    """单只股票同步预测。
    body: { stock: '002475', month: '2022-02', check_date: '2024-01-15' (可选，缺省=今天) }
    """
    data = request.get_json(silent=True) or {}
    stock = data.get('stock')
    month = data.get('month')
    check_date = data.get('check_date') or datetime.now().strftime('%Y-%m-%d')

    if not stock or not month:
        return jsonify({'success': False, 'message': '需要 stock 和 month 参数'}), 400

    try:
        from App.codes.RnnModel.prediction_engine import PredictionCommon
        pc = PredictionCommon(
            stock=stock, month_parsers=month, check_date=check_date, monitor=False
        )
        pc.single_stock()

        latest = (RnnRunningRecord.query
                  .filter_by(code=str(pc.stock_code), parser_month=month)
                  .order_by(RnnRunningRecord.created_at.desc()).first())
        return jsonify({
            'success': True,
            'message': '预测完成',
            'stock_code': pc.stock_code,
            'stock_name': pc.stock_name,
            'check_date': check_date,
            'latest_record': latest.to_dict() if latest else None,
        })
    except Exception as e:
        logger.exception(f'[PREDICT] 单只预测失败: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500


@rnn_strategies_bp.route('/api/predict_batch', methods=['POST'])
def api_predict_batch():
    """股票池批量预测（后台）。
    body: { month: '2022-02', check_date: '2024-01-15' (可选), pool_type: 'watching' (可选) }
    """
    global predict_thread
    data = request.get_json(silent=True) or {}
    month = data.get('month')
    check_date = data.get('check_date') or datetime.now().strftime('%Y-%m-%d')
    pool_type = data.get('pool_type', 'watching')

    if not month:
        return jsonify({'success': False, 'message': '需要 month 参数'}), 400

    if predict_thread is not None and predict_thread.is_alive():
        return jsonify({'success': False, 'message': '已有批量预测任务在运行中', 'data': predict_state.to_dict()}), 409

    with predict_lock:
        predict_state.reset('predict_batch', month)

    app = current_app._get_current_object()

    def run():
        try:
            with app.app_context():
                from App.codes.RnnModel.prediction_engine import PredictionCommon
                from App.models.strategy.StockPool import StockPool

                stocks = StockPool.get_by_pool_type(pool_type)
                with predict_lock:
                    predict_state.total = len(stocks)

                if not stocks:
                    with predict_lock:
                        predict_state.status = '已完成'
                        predict_state.progress = 100
                        predict_state.message = f'股票池 {pool_type} 为空'
                        predict_state.finished_at = datetime.now()
                    return

                for sp in stocks:
                    with predict_lock:
                        if predict_state.stop:
                            predict_state.status = '已停止'
                            predict_state.finished_at = datetime.now()
                            return
                    try:
                        pc = PredictionCommon(
                            stock=sp.stock_code, month_parsers=month,
                            check_date=check_date, monitor=False,
                        )
                        pc.single_stock()
                    except Exception as e:
                        logger.warning(f'[PREDICT] {sp.stock_code} 失败: {e}')
                    with predict_lock:
                        predict_state.processed += 1
                        predict_state.progress = round(
                            predict_state.processed * 100 / max(predict_state.total, 1), 1
                        )

                with predict_lock:
                    predict_state.status = '已完成'
                    predict_state.progress = 100
                    predict_state.finished_at = datetime.now()
            logger.info(f'[PREDICT] 批量预测完成: {predict_state.processed}/{predict_state.total}')
        except Exception as e:
            logger.exception(f'[PREDICT] 批量预测失败: {e}')
            with predict_lock:
                predict_state.status = '失败'
                predict_state.message = str(e)
                predict_state.finished_at = datetime.now()

    predict_thread = threading.Thread(target=run, daemon=True)
    predict_thread.start()
    return jsonify({'success': True, 'message': '批量预测已启动', 'data': predict_state.to_dict()})


@rnn_strategies_bp.route('/api/stop_predict', methods=['POST'])
def api_stop_predict():
    with predict_lock:
        predict_state.stop = True
    return jsonify({'success': True, 'message': '已请求停止批量预测'})


@rnn_strategies_bp.route('/api/predict_status', methods=['GET'])
def api_predict_status():
    with predict_lock:
        return jsonify({'success': True, 'data': predict_state.to_dict()})


@rnn_strategies_bp.route('/api/model_health', methods=['POST'])
def api_model_health():
    """
    评估模型是否退化。
    body: { stock: '002475', month: '2022-02', sample_size: 50 (可选) }

    思路：
      在训练样本上跑预测，比较"预测方差"与"标签方差"。
      如果模型退化为常数（比如总是输出 Y 的均值），预测方差会接近 0；
      健康模型的预测方差应当与标签方差同量级。

    判定（per-model）：
      ratio = pred_std / y_std
      ratio < 0.05  → DEGENERATE  （几乎是常数）
      ratio < 0.30  → WEAK        （响应弱）
      ratio >= 0.30 → HEALTHY     （正常）
    整体健康度取 4 个模型的最低评级。
    """
    import os
    import numpy as np
    from App.codes.RnnDataFile.stock_path import StockDataPath
    from App.codes.RnnModel.model_predictor import _get_cached_model
    from App.codes.parsers.RnnParser import ModelName

    data = request.get_json(silent=True) or {}
    stock_code = (data.get('stock') or '').strip()
    month = (data.get('month') or '').strip()
    try:
        sample_size = max(5, min(int(data.get('sample_size', 50)), 500))
    except (TypeError, ValueError):
        sample_size = 50

    if not stock_code or not month:
        return jsonify({'success': False, 'message': '需要 stock 和 month 参数'}), 400

    # 简单解析股票代码（支持传名称或代码）
    if not stock_code.isdigit():
        try:
            from App.codes.MySql.sql_utils import Stocks
            _, resolved_code, _ = Stocks(stock_code)
            if not resolved_code or not str(resolved_code).isdigit():
                return jsonify({'success': False, 'message': f'股票名称无法解析为代码: {stock_code}'}), 400
            stock_code = str(resolved_code)
        except Exception as e:
            return jsonify({'success': False, 'message': f'解析股票失败: {e}'}), 400

    rank_order = {'HEALTHY': 3, 'WEAK': 2, 'DEGENERATE': 1, 'UNTRAINED': 0}
    per_model = []

    for name in ModelName:
        x_file = f'{name}_{stock_code}_x.npy'
        y_file = f'{name}_{stock_code}_y.npy'
        m_file = f'{name}_{stock_code}.h5'
        x_path = StockDataPath.train_data_path(month, x_file)
        y_path = StockDataPath.train_data_path(month, y_file)
        m_path = StockDataPath.model_path(month, m_file)

        if not (os.path.exists(x_path) and os.path.exists(y_path) and os.path.exists(m_path)):
            per_model.append({
                'model': name, 'status': 'UNTRAINED', 'detail': '训练样本或模型文件缺失',
                'sample_count': 0,
            })
            continue

        try:
            x = np.load(x_path)
            y = np.load(y_path)
            n = min(sample_size, x.shape[0])
            x_sample, y_sample = x[:n], y[:n]
            model = _get_cached_model(month, m_file)
            pred = model.predict(x_sample, verbose=0).flatten()
            y_flat = y_sample.flatten()

            pred_std = float(np.std(pred))
            y_std = float(np.std(y_flat))
            pred_range = float(pred.max() - pred.min())
            mae = float(np.mean(np.abs(pred - y_flat)))
            mae_baseline = float(np.mean(np.abs(y_flat - y_flat.mean())))  # 预测=均值的MAE

            ratio = (pred_std / y_std) if y_std > 1e-9 else 0.0
            if ratio < 0.05:
                status = 'DEGENERATE'
            elif ratio < 0.30:
                status = 'WEAK'
            else:
                status = 'HEALTHY'

            per_model.append({
                'model': name, 'status': status,
                'sample_count': n,
                'pred_std': round(pred_std, 4),
                'y_std': round(y_std, 4),
                'pred_y_std_ratio': round(ratio, 4),
                'pred_range': round(pred_range, 4),
                'mae': round(mae, 4),
                'mae_vs_baseline': round(mae / mae_baseline, 4) if mae_baseline > 1e-9 else None,
                'pred_min': round(float(pred.min()), 4),
                'pred_max': round(float(pred.max()), 4),
                'pred_mean': round(float(pred.mean()), 4),
            })
        except Exception as e:
            logger.exception(f'[HEALTH] {name}/{stock_code} 评估失败')
            per_model.append({'model': name, 'status': 'ERROR', 'detail': str(e)})

    # 综合评级 = 最差的那一个
    statuses = [m['status'] for m in per_model if m['status'] in rank_order]
    overall = min(statuses, key=lambda s: rank_order[s]) if statuses else 'UNTRAINED'

    return jsonify({
        'success': True,
        'stock_code': stock_code, 'month': month,
        'overall': overall,
        'per_model': per_model,
        'legend': {
            'HEALTHY': '预测方差 ≥ 30% 标签方差，响应正常',
            'WEAK': '响应较弱，模型可能欠拟合',
            'DEGENERATE': '预测几乎是常数，模型已退化',
            'UNTRAINED': '未找到训练数据/模型',
            'ERROR': '评估失败',
        },
    })


@rnn_strategies_bp.route('/api/prediction_records', methods=['GET'])
def api_prediction_records():
    """RnnRunningRecord 表的记录，按 created_at 倒序分页。
    可按 month / code / trend(趋势) / signal(买卖信号) 过滤。"""
    month = request.args.get('month')
    code = request.args.get('code')
    trend = request.args.get('trend')          # 上涨 / 下跌
    signal = request.args.get('signal')        # buy / sell / none（按 trade_point 符号）
    board = request.args.get('board')          # 东财行业板块名 → 该板块成分股
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    try:
        page_size = max(1, min(int(request.args.get('page_size', 20)), 200))
    except ValueError:
        page_size = 20

    q = RnnRunningRecord.query
    if month:
        q = q.filter_by(parser_month=month)
    if code:
        q = q.filter_by(code=code)
    if trend:
        q = q.filter_by(trends=trend)
    if signal == 'buy':
        q = q.filter(RnnRunningRecord.trade_point > 0)
    elif signal == 'sell':
        q = q.filter(RnnRunningRecord.trade_point < 0)
    elif signal == 'none':
        q = q.filter(RnnRunningRecord.trade_point == 0)
    if board:
        board_codes = _codes_in_board(board)
        # 板块无成分股（或查询失败）时返回空集，而不是忽略筛选
        q = q.filter(RnnRunningRecord.code.in_(board_codes)) if board_codes \
            else q.filter(false())
    pagination = q.order_by(RnnRunningRecord.created_at.desc()).paginate(
        page=page, per_page=page_size, error_out=False)
    records = pagination.items
    data = [r.to_dict() for r in records]
    # 附加每只股票所属板块（东财行业），整页一次批量查询
    boards = _boards_for_codes([d.get('code') for d in data])
    for d in data:
        d['board'] = boards.get(str(d.get('code')), '')
    return jsonify({
        'success': True,
        'data': data,
        'count': len(records),
        'pagination': {
            'page': pagination.page,
            'page_size': page_size,
            'total_pages': pagination.pages or 1,
            'total_items': pagination.total,
        },
    })
