"""
RNN模型相关数据模型
用于记录RNN模型的运行记录
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class RnnRunningRecord(db.Model):
    """
    RNN运行记录模型
    
    用于记录RNN模型的运行结果和预测数据
    
    Attributes:
        id (int): 记录的唯一标识符
        name (str): 记录名称
        code (str): 记录代码
        parser_month (str): 解析月份
        trends (str): 趋势信息
        signal_start_time (datetime): 信号开始时间
        predict_cycle_length (int): 预测周期长度
        real_cycle_length (int): 实际周期长度
        predict_cycle_change (float): 预测周期变化
        predict_cycle_price (float): 预测周期价格
        real_cycle_change (float): 实际周期变化
        predict_bar_change (float): 预测条变化
        real_bar_change (float): 实际条变化
        predict_bar_volume (float): 预测条数量
        real_bar_volume (float): 实际条数量
        score_trends (float): 趋势分数
        trade_point (float): 交易点
        time_run_bar (datetime): 运行条时间
        time_15m (datetime): 15分钟时间
        renew_date (datetime): 更新日期
    """
    __tablename__ = 'rnn_running_records'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    # 主键
    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 基本信息
    name = db.Column(db.String(100), nullable=False, comment='记录名称')
    code = db.Column(db.String(10), nullable=False, comment='记录代码')
    parser_month = db.Column(db.String(10), nullable=False, comment='解析的月份')
    trends = db.Column(db.String(100), nullable=False, comment='趋势信息')
    
    # 时间信息
    signal_start_time = db.Column(db.DateTime, nullable=False, comment='信号开始时间')
    time_run_bar = db.Column(db.DateTime, nullable=False, comment='运行条的时间')
    time_15m = db.Column(db.DateTime, nullable=False, comment='15分钟的时间')
    renew_date = db.Column(db.DateTime, nullable=False, comment='更新日期')
    
    # 周期相关
    predict_cycle_length = db.Column(db.Integer, nullable=False, comment='预测的周期长度')
    real_cycle_length = db.Column(db.Integer, nullable=False, comment='实际的周期长度')
    predict_cycle_change = db.Column(db.Float, nullable=False, comment='预测周期的变化')
    predict_cycle_price = db.Column(db.Float, nullable=False, comment='预测周期的价格')
    real_cycle_change = db.Column(db.Float, nullable=False, comment='实际周期的变化')
    
    # 条相关
    predict_bar_change = db.Column(db.Float, nullable=False, comment='预测条的变化')
    real_bar_change = db.Column(db.Float, nullable=False, comment='实际条的变化')
    predict_bar_volume = db.Column(db.Float, nullable=False, comment='预测条的数量')
    real_bar_volume = db.Column(db.Float, nullable=False, comment='实际条的数量')
    
    # 评分和交易
    score_trends = db.Column(db.Float, nullable=False, comment='趋势分数')
    trade_point = db.Column(db.Float, nullable=False, comment='交易点')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        """
        返回对象的字符串表示
        """
        return f"<RnnRunningRecord(id={self.id}, code={self.code}, name={self.name}, trends={self.trends})>"

    @classmethod
    def get_records_by_stock(cls, stock_code: str):
        """
        获取指定股票的运行记录
        
        Args:
            stock_code: 股票代码
            
        Returns:
            List[RnnRunningRecord]: 运行记录列表
        """
        return cls.query.filter_by(code=stock_code).order_by(cls.created_at.desc()).all()

    @classmethod
    def get_records_by_trend(cls, trend: str):
        """
        获取指定趋势的运行记录
        
        Args:
            trend: 趋势字符串
            
        Returns:
            List[RnnRunningRecord]: 运行记录列表
        """
        return cls.query.filter_by(trends=trend).order_by(cls.created_at.desc()).all()

    @classmethod
    def get_records_by_month(cls, month: str):
        """
        获取指定月份的运行记录
        
        Args:
            month: 月份字符串
            
        Returns:
            List[RnnRunningRecord]: 运行记录列表
        """
        return cls.query.filter_by(parser_month=month).order_by(cls.created_at.desc()).all()

    @classmethod
    def get_latest_records(cls, limit: int = 10):
        """
        获取最新的运行记录
        
        Args:
            limit: 限制数量
            
        Returns:
            List[RnnRunningRecord]: 最新记录列表
        """
        return cls.query.order_by(cls.created_at.desc()).limit(limit).all()

    def calculate_accuracy(self):
        """
        计算预测准确度
        
        Returns:
            dict: 包含各种准确度指标的字典
        """
        try:
            # 周期长度准确度
            cycle_length_accuracy = 1 - abs(self.predict_cycle_length - self.real_cycle_length) / max(self.real_cycle_length, 1)
            
            # 周期变化准确度
            cycle_change_accuracy = 1 - abs(self.predict_cycle_change - self.real_cycle_change) / max(abs(self.real_cycle_change), 0.001)
            
            # 条变化准确度
            bar_change_accuracy = 1 - abs(self.predict_bar_change - self.real_bar_change) / max(abs(self.real_bar_change), 0.001)
            
            # 条数量准确度
            bar_volume_accuracy = 1 - abs(self.predict_bar_volume - self.real_bar_volume) / max(self.real_bar_volume, 1)
            
            return {
                'cycle_length_accuracy': max(0, min(1, cycle_length_accuracy)),
                'cycle_change_accuracy': max(0, min(1, cycle_change_accuracy)),
                'bar_change_accuracy': max(0, min(1, bar_change_accuracy)),
                'bar_volume_accuracy': max(0, min(1, bar_volume_accuracy)),
                'overall_accuracy': max(0, min(1, (cycle_length_accuracy + cycle_change_accuracy + bar_change_accuracy + bar_volume_accuracy) / 4))
            }
        except Exception as e:
            logger.error(f"计算准确度时出错: {str(e)}")
            return {
                'cycle_length_accuracy': 0,
                'cycle_change_accuracy': 0,
                'bar_change_accuracy': 0,
                'bar_volume_accuracy': 0,
                'overall_accuracy': 0
            }

    def to_dict(self):
        """
        转换为字典格式
        
        Returns:
            dict: 字典格式的数据
        """
        accuracy = self.calculate_accuracy()
        return {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'parser_month': self.parser_month,
            'trends': self.trends,
            'signal_start_time': self.signal_start_time.strftime('%Y-%m-%d %H:%M:%S') if self.signal_start_time else None,
            'time_run_bar': self.time_run_bar.strftime('%Y-%m-%d %H:%M:%S') if self.time_run_bar else None,
            'time_15m': self.time_15m.strftime('%Y-%m-%d %H:%M:%S') if self.time_15m else None,
            'renew_date': self.renew_date.strftime('%Y-%m-%d %H:%M:%S') if self.renew_date else None,
            'predict_cycle_length': self.predict_cycle_length,
            'real_cycle_length': self.real_cycle_length,
            'predict_cycle_change': self.predict_cycle_change,
            'predict_cycle_price': self.predict_cycle_price,
            'real_cycle_change': self.real_cycle_change,
            'predict_bar_change': self.predict_bar_change,
            'real_bar_change': self.real_bar_change,
            'predict_bar_volume': self.predict_bar_volume,
            'real_bar_volume': self.real_bar_volume,
            'score_trends': self.score_trends,
            'trade_point': self.trade_point,
            'accuracy': accuracy,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }

    def is_prediction_accurate(self, threshold: float = 0.8):
        """
        检查预测是否准确
        
        Args:
            threshold: 准确度阈值
            
        Returns:
            bool: 是否准确
        """
        accuracy = self.calculate_accuracy()
        return accuracy['overall_accuracy'] >= threshold
