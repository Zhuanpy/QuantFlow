from flask import Blueprint, jsonify, render_template, current_app, request
from datetime import datetime
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
import threading
import os

from App.models.strategy import RnnTrainingRecords
from App.models.strategy.StockPool import StockPool
from App.exts import db
# from App.codes.RnnModel.DataProcessing import process_stock_data_for_year

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# 创建蓝图
RnnData = Blueprint('RnnData', __name__, url_prefix='/RnnData')

# 全局进度跟踪
class ProcessingProgress:
    def __init__(self):
        self.total = 0
        self.current = 0
        self.success = 0
        self.failed = 0
        self.lock = threading.Lock()
        self.processing_stocks = set()  # 记录正在处理的股票
    
    def update(self, stock_code, success=True):
        with self.lock:
            self.current += 1
            if success:
                self.success += 1
            else:
                self.failed += 1
            if stock_code in self.processing_stocks:
                self.processing_stocks.remove(stock_code)
    
    def add_stock(self, stock_code):
        with self.lock:
            self.processing_stocks.add(stock_code)
    
    def get_progress(self):
        with self.lock:
            return {
                'total': self.total,
                'current': self.current,
                'success': self.success,
                'failed': self.failed,
                'percentage': (self.current / self.total * 100) if self.total > 0 else 0,
                'processing_stocks': list(self.processing_stocks)
            }
    
    def reset(self):
        with self.lock:
            self.total = 0
            self.current = 0
            self.success = 0
            self.failed = 0
            self.processing_stocks.clear()

progress_tracker = ProcessingProgress()

def ensure_directory_exists(directory):
    """确保目录存在，如果不存在则创建"""
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
            logger.info(f"创建目录: {directory}")
            return True
        except Exception as e:
            logger.error(f"创建目录失败 {directory}: {str(e)}")
            return False
    return True

def get_base_directory():
    """获取基础目录的绝对路径"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def get_data_directory(year):
    """获取数据目录的绝对路径

    实际数据在项目根 data/data/years/<year>/，不是 App/static/...。
    历史路径写错过；这里指向真实位置。
    """
    from config import Config
    return os.path.join(Config.get_project_root(), 'data', 'data', 'years', str(year))

class ProcessContext:
    def __init__(self, app, stock_code, year):
        self.app = app
        self.stock_code = stock_code
        self.year = year

def process_stock_with_progress(process_context):
    """单个股票处理函数（用于多线程）"""
    stock_code = process_context.stock_code
    year = process_context.year
    app = process_context.app

    try:
        progress_tracker.add_stock(stock_code)
        
        # 获取并创建必要的目录
        data_dir = get_data_directory(year)
        input_dir = os.path.join(data_dir, '1m')
        output_dir = os.path.join(data_dir, '15m')
        
        # 确保目录存在
        if not ensure_directory_exists(input_dir) or not ensure_directory_exists(output_dir):
            raise Exception("无法创建必要的目录")
        
        # 检查输入目录是否存在数据
        if not os.path.exists(input_dir) or not os.listdir(input_dir):
            raise Exception(f"输入目录不存在或为空: {input_dir}")
            
        # 处理数据
        result = process_stock_data_for_year(year, stock_code)
        
        # 更新进度
        progress_tracker.update(stock_code, success=result)
        
        # 更新数据库状态
        with app.app_context():
            stock = RnnTrainingRecords.query.filter_by(code=stock_code).first()
            if stock:
                stock.original_15M_status = 'success' if result else 'failed'
                db.session.commit()
        
        return stock_code, result
        
    except Exception as e:
        logger.error(f"处理股票 {stock_code} 时出错: {str(e)}")
        logger.error(traceback.format_exc())
        progress_tracker.update(stock_code, success=False)
        
        # 更新数据库状态
        try:
            with app.app_context():
                stock = RnnTrainingRecords.query.filter_by(code=stock_code).first()
                if stock:
                    stock.original_15M_status = 'failed'
                    db.session.commit()
        except Exception as db_error:
            logger.error(f"更新数据库状态失败: {str(db_error)}")
        
        return stock_code, False

@RnnData.route('/processing_progress', methods=['GET'])
def get_processing_progress():
    """获取处理进度的API"""
    return jsonify(progress_tracker.get_progress())

def update_processing_status(stock_code, year, status, message=''):
    """更新股票处理状态"""
    try:
        record = RnnTrainingRecords.query.filter_by(code=stock_code).first()
        if record:
            record.set_process_status(year, status, message)
            db.session.commit()
            logging.info(f"Updated processing status for {stock_code}: {status}")
            return True
        else:
            logging.warning(f"Stock {stock_code} not found in database")
            return False
    except Exception as e:
        logging.error(f"Error updating status for {stock_code}: {str(e)}")
        db.session.rollback()
        return False

@RnnData.route('/rnn_data_page', methods=['GET', 'POST'])
def rnn_data_page():
    """RNN 模型数据状态监控页。

    可选传入个股：?code=<代码>&name=<名称>（GET 查询串或 POST 表单均可），
    传入后页面只针对该只股票（预填搜索框 + 顶部提示），不传则展示全部记录。
    """
    code = (request.values.get('code') or '').strip()
    name = (request.values.get('name') or '').strip()
    return render_template('strategy/rnn_model_data.html', stock_code=code, stock_name=name)

# 注：原 /data_statistics 页与 /quarter_stats 接口已移除——整页基于过时的"季度"概念，
# 数据源 quarter_stats 已失效（恒返回 0）、操作按钮全是未实现的桩。流水线状态统计（数据/
# 建模/检查 各月成功数）已由 /RnnStrategies/training 的记录表+按月汇总覆盖。


@RnnData.route('/api/stock_pool_stats')
def get_stock_pool_stats():
    """获取股票池统计信息用于RNN数据处理"""
    try:
        # 获取股票池统计
        pool_stats = StockPool.get_pool_statistics()
        
        # 获取训练就绪的股票数量
        training_ready_stocks = StockPool.get_training_ready_stocks()
        
        # 按类型统计
        type_distribution = {}
        for pool_type in ['general', 'training', 'testing', 'validation']:
            stocks = StockPool.get_by_pool_type(pool_type)
            type_distribution[pool_type] = len(stocks)
        
        return jsonify({
            'success': True,
            'data': {
                'total_stocks': pool_stats.get('total_stocks', 0),
                'active_stocks': pool_stats.get('active_stocks', 0),
                'training_ready': len(training_ready_stocks),
                'type_distribution': type_distribution,
                'training_ready_stocks': [stock.to_dict() for stock in training_ready_stocks[:10]]  # 只返回前10个
            }
        })
    except Exception as e:
        logger.error(f"获取股票池统计信息失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取股票池统计信息失败: {str(e)}'
        }), 500


_DEPRECATED_MSG = (
    '该功能已废弃。所有训练数据生成、模型训练、模型预测、模型健康度检查 '
    '已统一迁移到 /RnnStrategies/training 和 /RnnStrategies/prediction。'
)


@RnnData.route('/api/process_stock_pool_data', methods=['POST'])
def process_stock_pool_data():
    """已废弃：见 /RnnStrategies/training（按月份处理整个流水线）"""
    return jsonify({'success': False, 'message': _DEPRECATED_MSG}), 501

# 注：原 /statistics 与 /records 两个只读接口已随 rnn_data_page 瘦身为入口页而移除
# （记录查看/统计已统一到 /RnnStrategies/training 与其 api/training_records）。

# 初始化年份数据
@RnnData.route('/init_year/<year>', methods=['POST'])
def init_year_data(year):
    """已废弃：初始化年份处理状态。现在 RnnStrategies 训练流水线无需预先 init。"""
    return jsonify({'success': False, 'message': _DEPRECATED_MSG}), 501

# 处理标准化15分钟数据  ← 已废弃：原来只是把 status 标成 success 不做实事
@RnnData.route('/standard_15M/<year>', methods=['POST'])
def standard_15M(year):
    """已废弃：本函数原来只在数据库把状态标成 success，不做任何真处理。
    标准化已经合并到 RnnStrategies 训练流水线（按月份），见 /RnnStrategies/training。
    """
    return jsonify({
        'success': False,
        'message': '该功能已废弃。请使用 /RnnStrategies/training 页面的"生成训练数据"按钮，'
                   '它会自动跑完整管道（重采样 → MACD → 衍生特征 → 标准化 → .npy）。'
    }), 501


# 检查模型状态  ← 已废弃：原来只是把 processing 标成 success 不做实事
@RnnData.route('/check_models', methods=['POST'])
def check_models():
    """已废弃：原来只把 processing 标成 success，不做模型完整性、loss、健康度等真检查。
    模型健康度评估已迁移到 /RnnStrategies/api/model_health。
    """
    return jsonify({
        'success': False,
        'message': '该功能已废弃。请使用 /RnnStrategies/prediction 页面的"检查模型健康度"按钮，'
                   '它会基于训练样本评估每只股票每个模型是否退化（HEALTHY/WEAK/DEGENERATE）。'
    }), 501

# （原 check_models mock 实现已删除，新的 501 版本见上面）


# 处理季度数据
@RnnData.route('/process_quarter/<int:year>/<int:quarter>', methods=['POST'])
def process_quarter_data(year, quarter):
    """已废弃：按季度处理数据。改用 /RnnStrategies/training（按月份）。"""
    return jsonify({'success': False, 'message': _DEPRECATED_MSG}), 501


@RnnData.route('/process_quarters/<int:year>', methods=['POST'])
def process_multiple_quarters(year):
    """已废弃：按年/多季度批量处理数据。改用 /RnnStrategies/training。"""
    return jsonify({'success': False, 'message': _DEPRECATED_MSG}), 501

# 计算15分钟原始数据
@RnnData.route('/original_15M/<year>', methods=['POST'])
def original_15M(year):
    """已废弃：按年份多线程处理原始 15m 数据。改用 /RnnStrategies/training。"""
    return jsonify({'success': False, 'message': _DEPRECATED_MSG}), 501


@RnnData.route('/process_base_data/<int:year>', methods=['POST'])
def process_base_data(year):
    """已废弃：处理 15m 基础数据。改用 /RnnStrategies/training。"""
    return jsonify({'success': False, 'message': _DEPRECATED_MSG}), 501


@RnnData.route('/process_standard_data/<year>/<quarter>', methods=['POST'])
def process_standard_data(year, quarter):
    """已废弃：处理 15m 标准化数据。改用 /RnnStrategies/training。"""
    return jsonify({'success': False, 'message': _DEPRECATED_MSG}), 501

# 生成及保存模型训练数据