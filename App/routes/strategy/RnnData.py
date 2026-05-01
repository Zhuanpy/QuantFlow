from flask import Blueprint, jsonify, render_template, current_app, request
from datetime import datetime
import logging
import traceback
from sqlalchemy import and_, or_
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
    return render_template('strategy/rnn_model_data.html')

# 数据统计页面
@RnnData.route('/data_statistics')
def data_statistics_page():
    """数据统计页面"""
    return render_template('strategy/data_statistics.html')


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

# 获取统计数据
@RnnData.route('/statistics', methods=['GET'])
def get_statistics():
    """获取RNN训练记录的统计数据"""
    try:
        total = RnnTrainingRecords.query.count()
        success = RnnTrainingRecords.query.filter(
            RnnTrainingRecords.model_check == RnnTrainingRecords.STATUS_SUCCESS
        ).count()
        pending = RnnTrainingRecords.query.filter(
            RnnTrainingRecords.model_check == RnnTrainingRecords.STATUS_PENDING
        ).count()
        failed = RnnTrainingRecords.query.filter(
            RnnTrainingRecords.model_check == RnnTrainingRecords.STATUS_FAILED
        ).count()
        
        return jsonify({
            'success': True,
            'total': total,
            'success': success,
            'pending': pending,
            'failed': failed
        })
    except Exception as e:
        logger.error(f"获取统计数据失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

# 获取记录列表
@RnnData.route('/records', methods=['GET'])
def get_records():
    """获取RNN训练记录列表"""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        search = request.args.get('search', '')
        status = request.args.get('status', '')
        
        # 构建查询
        query = RnnTrainingRecords.query
        
        # 搜索条件
        if search:
            query = query.filter(
                or_(
                    RnnTrainingRecords.code.like(f'%{search}%'),
                    RnnTrainingRecords.name.like(f'%{search}%')
                )
            )
        
        # 状态筛选
        if status:
            query = query.filter(RnnTrainingRecords.model_check == status)
        
        # 分页
        pagination = query.paginate(
            page=page,
            per_page=page_size,
            error_out=False
        )
        
        # 转换为字典格式
        records = []
        for record in pagination.items:
            records.append(record.to_dict())
        
        return jsonify({
            'success': True,
            'records': records,
            'pagination': {
                'current_page': pagination.page,
                'total_pages': pagination.pages,
                'total_items': pagination.total,
                'per_page': pagination.per_page
            }
        })
    except Exception as e:
        logger.error(f"获取记录列表失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

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

# 获取季度数据统计
@RnnData.route('/quarter_stats/<int:year>', methods=['GET'])
def get_quarter_stats(year):
    """获取指定年份的季度数据统计"""
    try:
        from App.codes.RnnModel.QuarterlyDataProcessor import QuarterlyDataProcessor
        import os
        from datetime import datetime
        
        processor = QuarterlyDataProcessor()
        stats = {}
        
        for quarter in range(1, 5):
            quarter_key = f"{year}Q{quarter}"
            
            # 检查季度数据目录
            quarter_path = processor.base_data_path / "quarters" / f"{year}Q{quarter}"
            csv_files = list(quarter_path.glob("*.csv")) if quarter_path.exists() else []
            
            # 检查训练数据目录
            train_path = processor.base_data_path / "RnnData" / f"{year}-{quarter:02d}" / "train_data"
            train_files = list(train_path.glob("*_training.csv")) if train_path.exists() else []
            
            # 检查15分钟数据目录
            data_15m_path = processor.base_data_path / "RnnData" / f"{year}-{quarter:02d}" / "15m"
            data_15m_files = list(data_15m_path.glob("*.csv")) if data_15m_path.exists() else []
            
            # 获取文件大小信息
            raw_data_size = 0
            if quarter_path.exists():
                for file in csv_files:
                    try:
                        raw_data_size += os.path.getsize(file)
                    except:
                        pass
            
            training_data_size = 0
            if train_path.exists():
                for file in train_files:
                    try:
                        training_data_size += os.path.getsize(file)
                    except:
                        pass
            
            # 获取最后修改时间
            last_raw_update = None
            if csv_files:
                try:
                    last_raw_update = datetime.fromtimestamp(
                        max(os.path.getmtime(f) for f in csv_files)
                    ).strftime('%Y-%m-%d %H:%M:%S')
                except:
                    pass
            
            last_training_update = None
            if train_files:
                try:
                    last_training_update = datetime.fromtimestamp(
                        max(os.path.getmtime(f) for f in train_files)
                    ).strftime('%Y-%m-%d %H:%M:%S')
                except:
                    pass
            
            # 确定状态
            raw_status = "有数据" if len(csv_files) > 0 else "无数据"
            training_status = "已整理" if len(train_files) > 0 else "未整理"
            data_15m_status = "已处理" if len(data_15m_files) > 0 else "未处理"
            
            # 计算完成度
            completion_rate = 0
            if len(csv_files) > 0:
                completion_rate += 25  # 有原始数据
            if len(data_15m_files) > 0:
                completion_rate += 25  # 有15分钟数据
            if len(train_files) > 0:
                completion_rate += 50  # 有训练数据
            
            stats[quarter_key] = {
                'quarter': quarter,
                'year': year,
                'raw_data_files': len(csv_files),
                'training_files': len(train_files),
                'data_15m_files': len(data_15m_files),
                'raw_data_size': raw_data_size,
                'training_data_size': training_data_size,
                'raw_data_size_mb': round(raw_data_size / (1024 * 1024), 2),
                'training_data_size_mb': round(training_data_size / (1024 * 1024), 2),
                'has_raw_data': len(csv_files) > 0,
                'has_training_data': len(train_files) > 0,
                'has_15m_data': len(data_15m_files) > 0,
                'raw_status': raw_status,
                'training_status': training_status,
                'data_15m_status': data_15m_status,
                'last_raw_update': last_raw_update,
                'last_training_update': last_training_update,
                'completion_rate': completion_rate,
                'status_color': 'success' if completion_rate == 100 else ('warning' if completion_rate >= 50 else 'danger')
            }
        
        return jsonify({
            'success': True,
            'year': year,
            'quarter_stats': stats
        })
        
    except Exception as e:
        logger.error(f"获取季度统计失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

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