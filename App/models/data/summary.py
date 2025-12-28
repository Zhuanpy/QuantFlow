"""
股票数据汇总表模型
用于记录各股票数据类型的汇总信息
"""
from App.exts import db
from datetime import datetime
import enum


class DataTypeEnum(enum.Enum):
    """数据类型枚举"""
    MINUTE_1 = '1m'
    MINUTE_15 = '15m'
    MINUTE_120 = '120m'
    DAILY = 'Daily'


class DataSummary(db.Model):
    """股票数据汇总表模型"""
    __tablename__ = 'data_summary'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    # 定义列
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(100), nullable=True, comment='股票名称')
    data_type = db.Column(
        db.Enum(DataTypeEnum),
        nullable=False,
        comment='数据类型'
    )
    quarter = db.Column(db.String(7), nullable=False, comment='季度')
    is_complete = db.Column(db.Boolean, default=False, comment='是否完整')
    record_count = db.Column(db.Integer, default=0, comment='记录数量')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f"<DataSummary(stock_code='{self.stock_code}', data_type='{self.data_type.value}', quarter='{self.quarter}')>"

    @classmethod
    def get_summary_by_stock(cls, stock_code: str, data_type: DataTypeEnum = None):
        """
        获取指定股票的数据汇总
        
        Args:
            stock_code: 股票代码
            data_type: 数据类型（可选）
            
        Returns:
            DataSummary对象列表
        """
        query = cls.query.filter_by(stock_code=stock_code)
        if data_type:
            query = query.filter_by(data_type=data_type)
        return query.all()

    @classmethod
    def update_summary(cls, stock_code: str, data_type: DataTypeEnum, quarter: str, 
                      record_count: int, is_complete: bool = True):
        """
        更新或创建数据汇总记录
        
        Args:
            stock_code: 股票代码
            data_type: 数据类型
            quarter: 季度
            record_count: 记录数量
            is_complete: 是否完整
            
        Returns:
            DataSummary对象
        """
        summary = cls.query.filter_by(
            stock_code=stock_code,
            data_type=data_type,
            quarter=quarter
        ).first()
        
        if summary:
            # 更新现有记录
            summary.record_count = record_count
            summary.is_complete = is_complete
            summary.updated_at = datetime.utcnow()
        else:
            # 创建新记录
            summary = cls(
                stock_code=stock_code,
                data_type=data_type,
                quarter=quarter,
                record_count=record_count,
                is_complete=is_complete
            )
            db.session.add(summary)
        
        db.session.commit()
        return summary

    @classmethod
    def get_incomplete_data(cls):
        """
        获取所有不完整的数据记录
        
        Returns:
            不完整的数据汇总列表
        """
        return cls.query.filter_by(is_complete=False).all()

    @classmethod
    def get_summary_by_quarter(cls, quarter: str):
        """
        获取指定季度的所有数据汇总
        
        Args:
            quarter: 季度字符串
            
        Returns:
            该季度的数据汇总列表
        """
        return cls.query.filter_by(quarter=quarter).all() 