"""
RNN模型训练记录模型
用于记录RNN模型的训练过程和状态
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class RnnTrainingRecords(db.Model):
    """RNN模型训练记录表"""
    __tablename__ = 'rnn_training_records'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    # 状态常量
    STATUS_PENDING = 'pending'    # 待处理
    STATUS_PROCESSING = 'processing'  # 处理中
    STATUS_SUCCESS = 'success'    # 处理成功
    STATUS_FAILED = 'failed'      # 处理失败
    
    # 主键ID
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 股票基本信息
    name = db.Column(db.String(100), nullable=True, comment='股票名称')
    code = db.Column(db.String(10), nullable=True, comment='股票代码')
    
    # 解析信息
    parser_month = db.Column(db.String(10), nullable=True, comment='解析月份')
    starting_date = db.Column(db.DateTime, nullable=True, comment='开始日期')
    
    # 模型数据相关
    model_data = db.Column(db.Text, nullable=True, comment='模型数据状态')
    model_data_timing = db.Column(db.DateTime, nullable=True, comment='数据处理时间')
    
    # 模型创建相关
    model_create = db.Column(db.String(20), nullable=True, comment='模型创建状态')
    model_create_timing = db.Column(db.DateTime, nullable=True, comment='模型创建时间')
    
    # 模型检查相关
    model_check = db.Column(db.String(20), nullable=True, comment='模型检查状态')
    model_error = db.Column(db.Text, nullable=True, comment='错误信息')
    model_check_timing = db.Column(db.DateTime, nullable=True, comment='检查时间')

    # 15分钟原始数据处理相关
    original_15M_year = db.Column(db.String(4), nullable=True, comment='原始数据处理年份')
    original_15M_status = db.Column(db.String(10), nullable=True, comment='原始数据处理状态')
    original_15M_time = db.Column(db.DateTime, nullable=True, comment='原始数据处理时间')
    original_15M_message = db.Column(db.Text, nullable=True, comment='原始数据处理消息')

    # 15分钟标准化数据处理相关
    standard_15M_year = db.Column(db.String(4), nullable=True, comment='标准化数据处理年份')
    standard_15M_status = db.Column(db.String(10), nullable=True, comment='标准化数据处理状态')
    standard_15M_time = db.Column(db.DateTime, nullable=True, comment='标准化数据处理时间')
    standard_15M_message = db.Column(db.Text, nullable=True, comment='标准化数据处理消息')
    
    # 时间戳字段
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'parser_month': self.parser_month,
            'starting_date': self.starting_date.strftime('%Y-%m-%d %H:%M:%S') if self.starting_date else None,
            'model_data': self.model_data,
            'model_data_timing': self.model_data_timing.strftime('%Y-%m-%d %H:%M:%S') if self.model_data_timing else None,
            'model_create': self.model_create,
            'model_create_timing': self.model_create_timing.strftime('%Y-%m-%d %H:%M:%S') if self.model_create_timing else None,
            'model_check': self.model_check,
            'model_error': self.model_error,
            'model_check_timing': self.model_check_timing.strftime('%Y-%m-%d %H:%M:%S') if self.model_check_timing else None,
            'original_15M': {
                'year': self.original_15M_year,
                'status': self.original_15M_status,
                'time': self.original_15M_time.strftime('%Y-%m-%d %H:%M:%S') if self.original_15M_time else None,
                'message': self.original_15M_message
            },
            'standard_15M': {
                'year': self.standard_15M_year,
                'status': self.standard_15M_status,
                'time': self.standard_15M_time.strftime('%Y-%m-%d %H:%M:%S') if self.standard_15M_time else None,
                'message': self.standard_15M_message
            }
        }

    def __repr__(self):
        return f"<RnnTrainingRecords(id={self.id}, code={self.code}, name={self.name})>"

    @classmethod
    def get_records_by_stock(cls, stock_code: str):
        """
        获取指定股票的训练记录
        
        Args:
            stock_code: 股票代码
            
        Returns:
            List[RnnTrainingRecords]: 训练记录列表
        """
        return cls.query.filter_by(code=stock_code).order_by(cls.created_at.desc()).all()

    @classmethod
    def get_records_by_status(cls, status: str):
        """
        获取指定状态的训练记录
        
        Args:
            status: 状态值
            
        Returns:
            List[RnnTrainingRecords]: 训练记录列表
        """
        return cls.query.filter_by(model_check=status).all()

    @classmethod
    def get_pending_records(cls):
        """
        获取所有待处理的记录
        
        Returns:
            List[RnnTrainingRecords]: 待处理记录列表
        """
        return cls.query.filter_by(model_check=cls.STATUS_PENDING).all()

    @classmethod
    def init_process_year(cls, year: str):
        """
        初始化指定年份的处理状态
        
        Args:
            year: 年份
            
        Returns:
            bool: 初始化是否成功
        """
        try:
            # 更新所有记录的年份和状态
            cls.query.update({
                'original_15M_year': str(year),
                'original_15M_status': cls.STATUS_PENDING,
                'original_15M_time': None,
                'original_15M_message': None,
                'standard_15M_year': str(year),
                'standard_15M_status': cls.STATUS_PENDING,
                'standard_15M_time': None,
                'standard_15M_message': None
            })
            db.session.commit()
            logger.info(f"成功初始化 {year} 年的处理状态")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"初始化处理状态时出错: {str(e)}")
            return False

    def set_original_status(self, status: str, message: str = None):
        """
        设置原始数据处理状态
        
        Args:
            status: 状态值
            message: 消息内容
            
        Returns:
            bool: 设置是否成功
        """
        try:
            self.original_15M_status = status
            self.original_15M_time = datetime.utcnow()
            if message:
                self.original_15M_message = message
            self.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"成功更新股票 {self.code} 原始数据处理状态为: {status}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新原始数据处理状态时出错: {str(e)}")
            return False

    def set_standard_status(self, status: str, message: str = None):
        """
        设置标准化数据处理状态
        
        Args:
            status: 状态值
            message: 消息内容
            
        Returns:
            bool: 设置是否成功
        """
        try:
            self.standard_15M_status = status
            self.standard_15M_time = datetime.utcnow()
            if message:
                self.standard_15M_message = message
            self.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"成功更新股票 {self.code} 标准化数据处理状态为: {status}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新标准化数据处理状态时出错: {str(e)}")
            return False

    def is_original_processed(self):
        """
        检查原始数据是否处理成功
        
        Returns:
            bool: 是否处理成功
        """
        return self.original_15M_status == self.STATUS_SUCCESS

    def is_standard_processed(self):
        """
        检查标准化数据是否处理成功
        
        Returns:
            bool: 是否处理成功
        """
        return self.standard_15M_status == self.STATUS_SUCCESS

    def is_fully_processed(self):
        """
        检查是否完全处理成功
        
        Returns:
            bool: 是否完全处理成功
        """
        return self.is_original_processed() and self.is_standard_processed() 