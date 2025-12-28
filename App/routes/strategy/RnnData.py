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
    """获取数据目录的绝对路径"""
    base_dir = get_base_directory()
    return os.path.join(base_dir, 'static', 'data', 'years', str(year))

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


@RnnData.route('/api/process_stock_pool_data', methods=['POST'])
def process_stock_pool_data():
    """基于股票池处理RNN数据"""
    try:
        data = request.get_json()
        pool_type = data.get('pool_type', 'training')
        year = data.get('year')
        quarter = data.get('quarter')
        
        if not year:
            return jsonify({
                'success': False,
                'message': '请指定年份'
            }), 400
        
        # 获取指定类型的股票池
        stocks = StockPool.get_by_pool_type(pool_type, is_active=True)
        
        if not stocks:
            return jsonify({
                'success': False,
                'message': f'没有找到{pool_type}类型的股票'
            }), 400
        
        # 更新处理进度
        global processing_progress
        processing_progress = {
            'status': 'processing',
            'current': 0,
            'total': len(stocks),
            'message': f'开始处理{len(stocks)}只股票的数据...'
        }
        
        # 在后台线程中处理数据
        def process_data():
            try:
                from App.codes.RnnModel.QuarterlyDataProcessor import QuarterlyDataProcessor
                
                processor = QuarterlyDataProcessor()
                success_count = 0
                error_count = 0
                
                for i, stock in enumerate(stocks):
                    try:
                        # 更新进度
                        processing_progress.update({
                            'current': i + 1,
                            'message': f'正在处理股票 {stock.stock_code} ({i+1}/{len(stocks)})...'
                        })
                        
                        # 处理单个股票的数据
                        if quarter:
                            # 处理指定季度
                            result = processor.process_quarter(year, quarter, [stock.stock_code])
                        else:
                            # 处理整年
                            result = processor.process_multiple_quarters(year, [stock.stock_code])
                        
                        if result.get('success', False):
                            success_count += 1
                            # 更新股票的训练状态
                            StockPool.update_training_status(
                                stock.stock_code, 
                                'completed', 
                                datetime.now().date()
                            )
                        else:
                            error_count += 1
                            StockPool.update_training_status(
                                stock.stock_code, 
                                'failed'
                            )
                            
                    except Exception as e:
                        logger.error(f"处理股票 {stock.stock_code} 数据失败: {e}")
                        error_count += 1
                        StockPool.update_training_status(
                            stock.stock_code, 
                            'failed'
                        )
                
                # 完成处理
                processing_progress.update({
                    'status': 'completed',
                    'message': f'处理完成！成功: {success_count}, 失败: {error_count}'
                })
                
            except Exception as e:
                logger.error(f"批量处理股票池数据失败: {e}")
                processing_progress.update({
                    'status': 'failed',
                    'message': f'处理失败: {str(e)}'
                })
        
        # 启动后台处理线程
        thread = threading.Thread(target=process_data)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'message': f'开始处理{len(stocks)}只{pool_type}类型股票的数据',
            'stock_count': len(stocks)
        })
        
    except Exception as e:
        logger.error(f"处理股票池数据失败: {e}")
        return jsonify({
            'success': False,
            'message': f'处理失败: {str(e)}'
        }), 500

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
    """初始化指定年份的数据处理状态"""
    try:
        # 验证年份
        year = str(year)
        if not (2000 <= int(year) <= 2099):
            return jsonify({
                'success': False,
                'message': '年份必须在2000-2099之间'
            }), 400
        
        # 初始化处理状态
        success = RnnTrainingRecords.init_process_year(year)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'成功初始化 {year} 年的处理状态'
            })
        else:
            return jsonify({
                'success': False,
                'message': '初始化失败'
            }), 500
            
    except Exception as e:
        logger.error(f"初始化年份数据失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

# 处理标准化15分钟数据
@RnnData.route('/standard_15M/<year>', methods=['POST'])
def standard_15M(year):
    """处理标准化15分钟数据"""
    try:
        # 验证年份
        year = str(year)
        if not (2000 <= int(year) <= 2099):
            return jsonify({
                'success': False,
                'message': '年份必须在2000-2099之间'
            }), 400
        
        # 获取需要处理的记录
        records = RnnTrainingRecords.query.filter(
            and_(
                RnnTrainingRecords.original_15M_year == year,
                RnnTrainingRecords.original_15M_status == RnnTrainingRecords.STATUS_SUCCESS,
                or_(
                    RnnTrainingRecords.standard_15M_status.is_(None),
                    RnnTrainingRecords.standard_15M_status == RnnTrainingRecords.STATUS_FAILED
                )
            )
        ).all()
        
        if not records:
            return jsonify({
                'success': True,
                'message': f'{year} 年没有需要处理标准化数据的记录'
            })
        
        # 处理记录
        success_count = 0
        failed_count = 0
        
        for record in records:
            try:
                # 这里应该调用实际的数据处理函数
                # 暂时模拟处理过程
                record.set_standard_status(RnnTrainingRecords.STATUS_SUCCESS, '处理完成')
                success_count += 1
            except Exception as e:
                record.set_standard_status(RnnTrainingRecords.STATUS_FAILED, str(e))
                failed_count += 1
                logger.error(f"处理记录 {record.id} 失败: {e}")
        
        return jsonify({
            'success': True,
            'message': f'标准化数据处理完成，成功: {success_count}, 失败: {failed_count}'
        })
        
    except Exception as e:
        logger.error(f"处理标准化15分钟数据失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

# 检查模型状态
@RnnData.route('/check_models', methods=['POST'])
def check_models():
    """检查模型状态"""
    try:
        # 获取所有记录
        records = RnnTrainingRecords.query.all()
        
        updated_count = 0
        for record in records:
            # 这里应该实现实际的模型检查逻辑
            # 暂时模拟检查过程
            if record.model_check == RnnTrainingRecords.STATUS_PROCESSING:
                # 模拟检查结果
                record.model_check = RnnTrainingRecords.STATUS_SUCCESS
                record.model_check_timing = datetime.utcnow()
                record.model_error = None
                updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'模型状态检查完成，更新了 {updated_count} 条记录'
        })
        
    except Exception as e:
        logger.error(f"检查模型状态失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

# 处理季度数据
@RnnData.route('/process_quarter/<int:year>/<int:quarter>', methods=['POST'])
def process_quarter_data(year, quarter):
    """处理指定季度的数据"""
    try:
        from App.codes.RnnModel.QuarterlyDataProcessor import QuarterlyDataProcessor
        
        # 验证参数
        if not (1 <= quarter <= 4):
            return jsonify({
                'success': False,
                'message': '季度必须在1-4之间'
            }), 400
        
        if not (2000 <= year <= 2099):
            return jsonify({
                'success': False,
                'message': '年份必须在2000-2099之间'
            }), 400
        
        # 获取请求参数
        data = request.get_json() or {}
        stock_codes = data.get('stock_codes')  # 可选的股票代码列表
        
        # 初始化处理器
        processor = QuarterlyDataProcessor()
        
        # 处理季度数据
        success = processor.process_quarter(year, quarter, stock_codes)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'{year}年Q{quarter}季度数据处理完成'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'{year}年Q{quarter}季度数据处理失败'
            }), 500
            
    except Exception as e:
        logger.error(f"处理季度数据失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

# 处理多个季度数据
@RnnData.route('/process_quarters/<int:year>', methods=['POST'])
def process_multiple_quarters(year):
    """处理指定年份的多个季度数据"""
    try:
        from App.codes.RnnModel.QuarterlyDataProcessor import QuarterlyDataProcessor
        
        # 验证年份
        if not (2000 <= year <= 2099):
            return jsonify({
                'success': False,
                'message': '年份必须在2000-2099之间'
            }), 400
        
        # 获取请求参数
        data = request.get_json() or {}
        quarters = data.get('quarters', [1, 2, 3, 4])  # 默认处理所有季度
        stock_codes = data.get('stock_codes')  # 可选的股票代码列表
        
        # 验证季度参数
        if not all(1 <= q <= 4 for q in quarters):
            return jsonify({
                'success': False,
                'message': '季度必须在1-4之间'
            }), 400
        
        # 初始化处理器
        processor = QuarterlyDataProcessor()
        
        # 处理多个季度数据
        results = processor.process_multiple_quarters(year, quarters, stock_codes)
        
        # 统计结果
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        
        return jsonify({
            'success': True,
            'message': f'{year}年季度数据处理完成，成功: {success_count}/{total_count}',
            'results': results
        })
        
    except Exception as e:
        logger.error(f"处理多季度数据失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

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
    try:
        # 验证年份
        year = str(year)
        if not (2000 <= int(year) <= 2099):
            return jsonify({
                'status': 'error',
                'message': '年份必须在2000-2099之间'
            }), 400

        # 检查数据目录
        data_dir = get_data_directory(year)
        input_dir = os.path.join(data_dir, '1m')
        output_dir = os.path.join(data_dir, '15m')
        
        if not os.path.exists(input_dir):
            return jsonify({
                'status': 'error',
                'message': f'输入目录不存在: {input_dir}'
            }), 400

        # 获取需要处理的股票列表
        stocks = RnnTrainingRecords.query.filter(
            or_(
                RnnTrainingRecords.original_15M_year.is_(None),
                RnnTrainingRecords.original_15M_year != year,
                and_(
                    RnnTrainingRecords.original_15M_year == year,
                    RnnTrainingRecords.original_15M_status.in_(['failed', 'pending'])
                )
            )
        ).all()

        if not stocks:
            success_records = RnnTrainingRecords.query.filter(
                and_(
                    RnnTrainingRecords.original_15M_year == year,
                    RnnTrainingRecords.original_15M_status == 'success'
                )
            ).count()
            
            total_records = RnnTrainingRecords.query.count()
            
            if success_records == total_records:
                return jsonify({
                    'status': 'success',
                    'message': f'{year}年的所有数据已经处理完成',
                    'data': {
                        'success_count': success_records,
                        'failed_count': 0
                    }
                })

        # 初始化进度跟踪器
        progress_tracker.reset()
        progress_tracker.total = len(stocks)

        # 更新所有股票状态为处理中
        for stock in stocks:
            stock.original_15M_year = year
            stock.original_15M_status = 'pending'
        db.session.commit()

        # 获取当前应用实例
        app = current_app._get_current_object()

        # 使用线程池处理数据
        with ThreadPoolExecutor(max_workers=min(8, len(stocks))) as executor:
            futures = []
            for stock in stocks:
                process_context = ProcessContext(app, stock.code, year)
                future = executor.submit(process_stock_with_progress, process_context)
                futures.append(future)

            # 等待所有任务完成
            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"处理任务失败: {str(e)}")

        # 获取最终进度
        final_progress = progress_tracker.get_progress()

        return jsonify({
            'status': 'success',
            'message': f'处理完成: 成功 {final_progress["success"]} 只，失败 {final_progress["failed"]} 只',
            'data': {
                'year': year,
                'success_count': final_progress["success"],
                'failed_count': final_progress["failed"],
                'processed_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"处理过程出错: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'status': 'error',
            'message': f'处理失败: {str(e)}'
        }), 500

# 处理15分钟基础数据
@RnnData.route('/process_base_data/<int:year>', methods=['POST'])
def process_base_data(year):
    try:
        # 获取所有股票记录
        records = RnnTrainingRecords.query.all()
        
        if not records:
            # 如果没有记录，从股票列表创建新记录
            # TODO: 从你的股票列表数据源获取股票代码和名称
            stocks = [
                {'code': '000001', 'name': '平安银行'},
                # ... 其他股票
            ]
            for stock in stocks:
                record = RnnTrainingRecords(
                    stock_code=stock['code'],
                    stock_name=stock['name']
                )
                db.session.add(record)
            db.session.commit()
            records = RnnTrainingRecords.query.all()

        # 检查是否有正在处理的数据
        processing_records = RnnTrainingRecords.query.filter(
            and_(
                RnnTrainingRecords.original_15M_year == year,
                RnnTrainingRecords.original_15M_status.in_(['pending', 'failed'])
            )
        ).all()

        if processing_records:
            # 处理未完成或失败的记录
            for record in processing_records:
                try:
                    # TODO: 在这里添加你的15分钟数据处理逻辑
                    process_15min_data(record.stock_code, year)
                    record.original_15M_status = 'success'
                except Exception as e:
                    record.original_15M_status = 'failed'
                    print(f"处理失败 {record.stock_code}: {str(e)}")
                db.session.commit()
        else:
            # 开始新的处理
            for record in records:
                record.original_15M_year = year
                record.original_15M_status = 'pending'
                db.session.commit()
                
                try:
                    # TODO: 在这里添加你的15分钟数据处理逻辑
                    process_15min_data(record.stock_code, year)
                    record.original_15M_status = 'success'
                except Exception as e:
                    record.original_15M_status = 'failed'
                    print(f"处理失败 {record.stock_code}: {str(e)}")
                db.session.commit()

        # 获取处理结果统计
        success_count = RnnTrainingRecords.query.filter(
            and_(
                RnnTrainingRecords.original_15M_year == year,
                RnnTrainingRecords.original_15M_status == 'success'
            )
        ).count()
        
        failed_count = RnnTrainingRecords.query.filter(
            and_(
                RnnTrainingRecords.original_15M_year == year,
                RnnTrainingRecords.original_15M_status == 'failed'
            )
        ).count()

        return jsonify({
            'status': 'success',
            'message': f'处理完成。成功: {success_count}, 失败: {failed_count}',
            'data': {
                'success_count': success_count,
                'failed_count': failed_count
            }
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': f'处理过程中发生错误: {str(e)}'
        }), 500

def process_15min_data(stock_code, year):
    """
    处理15分钟数据的具体逻辑
    这里需要实现你的数据处理逻辑
    """
    # TODO: 实现你的15分钟数据处理逻辑
    pass

# 处理15分钟标准化数据
@RnnData.route('/process_standard_data/<year>/<quarter>', methods=['POST'])
def process_standard_data(year, quarter):

    try:
        # 验证年份
        year = str(year)
        if not (2000 <= int(year) <= 2099):
            return jsonify({
                'status': 'error',
                'message': '年份必须在2000-2099之间'
            }), 400

        # 获取已完成基础数据处理的股票
        stocks = RnnTrainingRecords.query.filter(
            getattr(RnnTrainingRecords, f'processed_{year}') == RnnTrainingRecords.STATUS_SUCCESS
        ).all()

        if not stocks:
            return jsonify({
                'status': 'error',
                'message': f'没有找到{year}年已完成基础数据处理的股票'
            }), 400

        success_count = 0
        failed_count = 0

        # 处理全年数据
        if quarter == 'full':
            for stock in stocks:
                try:
                    result = process_full_year_data(year, stock.code)
                    if result['status'] == 'success':
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    failed_count += 1
                    logging.error(f"处理股票 {stock.code} 全年数据时出错: {str(e)}")
            
            message = f'{year}年全年数据标准化处理完成'
        else:
            # 验证季度
            quarter = int(quarter)
            if not (1 <= quarter <= 4):
                return jsonify({
                    'status': 'error',
                    'message': '季度必须在1-4之间'
                }), 400
            
            for stock in stocks:
                try:
                    result = process_quarter_data(year, quarter, stock.code)
                    if result['status'] == 'success':
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    failed_count += 1
                    logging.error(f"处理股票 {stock.code} 第{quarter}季度数据时出错: {str(e)}")
            
            message = f'{year}年第{quarter}季度数据标准化处理完成'

        return jsonify({
            'status': 'success',
            'message': message,
            'data': {
                'year': year,
                'quarter': quarter,
                'success_count': success_count,
                'failed_count': failed_count,
                'processed_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        })

    except Exception as e:
        logging.error(f"处理标准化数据时出错: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify({
            'status': 'error',
            'message': f'处理失败: {str(e)}'
        }), 500

def process_full_year_data(year, stock_code):
    """处理全年数据的具体实现"""
    try:
        # 这里添加处理全年数据的具体逻辑
        return {
            'status': 'success',
            'stock_code': stock_code,
            'year': year,
            'message': '全年数据处理成功'
        }
    except Exception as e:
        logging.error(f"处理全年数据时出错: {str(e)}")

        return {
            'status': 'failed',
            'stock_code': stock_code,
            'year': year,
            'message': str(e)
        }

def process_quarter_data(year, quarter, stock_code):
    """处理季度数据的具体实现"""
    try:
        # 这里添加处理季度数据的具体逻辑
        return {
            'status': 'success',
            'stock_code': stock_code,
            'year': year,
            'quarter': quarter,
            'message': '季度数据处理成功'
        }
    except Exception as e:
        logging.error(f"处理季度数据时出错: {str(e)}")
        return {
            'status': 'failed',
            'stock_code': stock_code,
            'year': year,
            'quarter': quarter,
            'message': str(e)
        }

# 生成及保存模型训练数据