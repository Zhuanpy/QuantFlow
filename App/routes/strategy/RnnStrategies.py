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

from App.exts import db
from App.models.strategy.RnnRunningRecords import RnnRunningRecord
from App.models.strategy.RnnTrainingRecords import RnnTrainingRecords

logger = logging.getLogger(__name__)

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
    # 池子模式：循环单股（精确控制范围）
    if pool_type:
        from App.models.strategy.StockPool import StockPool
        stocks = StockPool.get_by_pool_type(pool_type)
        if not stocks:
            logger.warning(f'[TRAIN] 池 {pool_type} 为空，跳过')
            return
        codes = [sp.stock_code for sp in stocks]
        logger.info(f'[TRAIN] 池模式 {pool_type}：{len(codes)} 只股票')

        if kind == 'data':
            from App.codes.RnnModel.RnnCreationData import TrainingDataCalculate
            for code in codes:
                try:
                    TrainingDataCalculate(code, month, start_date).calculation_single()
                except Exception as e:
                    logger.warning(f'[TRAIN] {code} 数据生成失败: {e}')
        elif kind == 'train':
            from App.codes.RnnModel.RnnCreationModel import BuiltModel
            for code in codes:
                try:
                    BuiltModel(code, month).model_all()
                except Exception as e:
                    logger.warning(f'[TRAIN] {code} 训练失败: {e}')
            try:
                from App.codes.RnnModel.model_predictor import clear_model_cache
                clear_model_cache()
            except Exception:
                pass
        elif kind == 'check':
            from App.codes.RnnModel.CheckModel import RMHistoryCheck
            rc = RMHistoryCheck(month_parsers=month)
            for code in codes:
                try:
                    rc.check1stock(code)
                except Exception as e:
                    logger.warning(f'[TRAIN] {code} 历史检查失败: {e}')
        else:
            raise ValueError(f'未知 kind: {kind}')
        return

    # 全部模式（原逻辑）
    if kind == 'data':
        from App.codes.RnnModel.RnnCreationTrainingData import RMTrainingData
        rt = RMTrainingData(months=month, start_=start_date)
        rt.all_stock()
    elif kind == 'train':
        from App.codes.RnnModel.RnnCreationModel import RMBuiltModel
        rb = RMBuiltModel(month)
        rb.train_remaining_models()
        try:
            from App.codes.RnnModel.model_predictor import clear_model_cache
            clear_model_cache()
        except Exception:
            pass
    elif kind == 'check':
        from App.codes.RnnModel.CheckModel import RMHistoryCheck
        rc = RMHistoryCheck(month_parsers=month)
        rc.loop_by_date()
    else:
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
    """RnnTrainingRecords 表的内容。可按 month / status 过滤。"""
    month = request.args.get('month')
    status = request.args.get('status')
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 1000))
    except ValueError:
        limit = 200

    q = RnnTrainingRecords.query
    if month:
        q = q.filter_by(parser_month=month)
    if status:
        q = q.filter_by(model_check=status)
    records = q.order_by(RnnTrainingRecords.id.desc()).limit(limit).all()
    return jsonify({'success': True, 'data': [r.to_dict() for r in records], 'count': len(records)})


@rnn_strategies_bp.route('/api/available_months', methods=['GET'])
def api_available_months():
    """训练记录里出现过的所有月份，倒序。"""
    rows = db.session.query(RnnTrainingRecords.parser_month).distinct().all()
    months = sorted({r[0] for r in rows if r and r[0]}, reverse=True)
    return jsonify({'success': True, 'data': months})


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
    """RnnRunningRecord 表的最新记录。可按 month / code 过滤。"""
    month = request.args.get('month')
    code = request.args.get('code')
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 500))
    except ValueError:
        limit = 100

    q = RnnRunningRecord.query
    if month:
        q = q.filter_by(parser_month=month)
    if code:
        q = q.filter_by(code=code)
    records = q.order_by(RnnRunningRecord.created_at.desc()).limit(limit).all()
    return jsonify({'success': True, 'data': [r.to_dict() for r in records], 'count': len(records)})
