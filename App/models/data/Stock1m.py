"""
股票1分钟数据模型
提供动态创建股票1分钟数据表的功能
"""
import pandas as pd
from App.exts import db
from typing import Dict, Any, Tuple
import logging
from datetime import datetime
from .basic_info import StockCodes  # 导入StockCodes模型

logger = logging.getLogger(__name__)

# 缓存动态生成的模型类
model_cache: Dict[Tuple[str, int], Any] = {}


class RecordStockMinute(db.Model):
    """
    股票分钟数据下载记录表
    
    用于记录每只股票的分钟数据下载状态和进度，
    避免与 stock_codes 表重复，通过外键关联
    """
    __tablename__ = 'record_stock_minute'
    __bind_key__ = 'quanttradingsystem'  # 绑定到 quanttradingsystem 数据库

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 关联到 stock_market_data 表的外键 - 修复数据类型匹配
    stock_code_id = db.Column(db.BigInteger, db.ForeignKey('stock_market_data.id', ondelete='CASCADE'), nullable=False, comment='股票代码ID')
    
    # 下载状态和进度
    download_status = db.Column(db.String(20), default='pending', comment='下载状态：pending/processing/success/failed')
    download_progress = db.Column(db.Float, default=0.0, comment='下载进度(0-100)')
    error_message = db.Column(db.Text, comment='错误信息')
    
    # 数据时间范围
    start_date = db.Column(db.Date, comment='数据开始日期')
    end_date = db.Column(db.Date, comment='数据结束日期')
    record_date = db.Column(db.Date, comment='记录创建日期')
    
    # 数据统计
    total_records = db.Column(db.Integer, default=0, comment='总记录数')
    downloaded_records = db.Column(db.Integer, default=0, comment='已下载记录数')
    last_download_time = db.Column(db.DateTime, comment='最后下载时间')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    # 关联关系 - 暂时注释，避免初始化错误
    # stock_code = db.relationship('StockCodes', backref='minute_records', foreign_keys=[stock_code_id])

    def __repr__(self):
        return f'<RecordStockMinute {self.stock_code_id}:{self.download_status}>'

    @classmethod
    def get_by_stock_code(cls, code: str):
        """
        根据股票代码获取下载记录
        
        Args:
            code: 股票代码
            
        Returns:
            RecordStockMinute: 下载记录对象
        """
        # 暂时直接查询，不使用join
        return cls.query.filter_by(stock_code_id=code).first()

    @classmethod
    def get_pending_downloads(cls):
        """
        获取待下载的记录
        
        Returns:
            List[RecordStockMinute]: 待下载记录列表
        """
        return cls.query.filter_by(download_status='pending').all()

    @classmethod
    def get_failed_downloads(cls):
        """
        获取下载失败的记录
        
        Returns:
            List[RecordStockMinute]: 下载失败记录列表
        """
        return cls.query.filter_by(download_status='failed').all()

    def update_download_status(self, status: str, progress: float = None, error_msg: str = None):
        """
        更新下载状态
        
        Args:
            status: 下载状态
            progress: 下载进度
            error_msg: 错误信息
        """
        self.download_status = status
        if progress is not None:
            self.download_progress = progress
        if error_msg is not None:
            self.error_message = error_msg
        self.last_download_time = datetime.utcnow()
        self.updated_at = datetime.utcnow()


def create_1m_stock_model(stock_code: str, year: int):
    """
    动态生成股票分时数据模型，根据股票代码和年份创建表名。
    
    Args:
        stock_code: 股票代码
        year: 年份
        
    Returns:
        动态生成的模型类
    """
    # 缓存键：基于股票代码和年份
    cache_key = (stock_code, year)

    if cache_key in model_cache:
        return model_cache[cache_key]

    # 构造唯一的类名
    class_name = f"TimeSeriesData_{stock_code}_{year}"

    # 动态定义类
    TimeSeriesData = type(
        class_name,
        (db.Model,),
        {
            '__tablename__': f"{stock_code}",
            '__bind_key__': f"data1m{year}",
            'date': db.Column(db.DateTime, primary_key=True, nullable=False, comment='交易时间'),
            'open': db.Column(db.Float, nullable=False, comment='开盘价'),
            'close': db.Column(db.Float, nullable=False, comment='收盘价'),
            'high': db.Column(db.Float, nullable=False, comment='最高价'),
            'low': db.Column(db.Float, nullable=False, comment='最低价'),
            'volume': db.Column(db.Integer, nullable=False, comment='成交量'),
            'money': db.Column(db.Integer, nullable=False, comment='成交额'),
        },
    )

    # 缓存生成的类
    model_cache[cache_key] = TimeSeriesData

    return TimeSeriesData


def save_1m_stock_data_to_sql(stock_code: str, year: int, data: pd.DataFrame) -> bool:
    """
    将股票分时数据保存至按年份和股票代码划分的数据库表中。

    Args:
        stock_code: 股票代码
        year: 年份
        data: 股票分时数据的 DataFrame，每行包含 date、open、close 等字段
        
    Returns:
        bool: 保存是否成功
    """
    try:
        # 动态创建模型类
        StockModel = create_1m_stock_model(stock_code, year)

        # 将 DataFrame 转为字典列表
        records = data.to_dict(orient='records')

        # 获取已存在的日期
        existing_dates = {record.date for record in StockModel.query.with_entities(StockModel.date).all()}

        # 拆分插入与更新
        new_records = []
        updated_count = 0
        
        for record in records:
            if record['date'] in existing_dates:
                # 更新已有记录
                StockModel.query.filter_by(date=record['date']).update(record)
                updated_count += 1
            else:
                # 收集需要插入的新记录
                new_records.append(StockModel(**record))

        # 批量插入新记录
        if new_records:
            db.session.bulk_save_objects(new_records)

        # 提交事务
        db.session.commit()
        
        logger.info(f"成功保存股票 {stock_code} {year}年1分钟数据，新增 {len(new_records)} 条，更新 {updated_count} 条")
        return True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"保存股票 {stock_code} {year}年1分钟数据时发生错误: {e}")
        return False


def get_1m_stock_data(stock_code: str, year: int, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    获取指定时间范围的股票1分钟数据
    
    Args:
        stock_code: 股票代码
        year: 年份
        start_date: 开始日期时间 (YYYY-MM-DD HH:MM:SS)
        end_date: 结束日期时间 (YYYY-MM-DD HH:MM:SS)
        
    Returns:
        pd.DataFrame: 股票1分钟数据
    """
    try:
        StockModel = create_1m_stock_model(stock_code, year)
        
        query = StockModel.query
        
        if start_date:
            query = query.filter(StockModel.date >= start_date)
        if end_date:
            query = query.filter(StockModel.date <= end_date)
            
        records = query.order_by(StockModel.date).all()
        
        # 转换为DataFrame
        data = []
        for record in records:
            data.append({
                'date': record.date,
                'open': record.open,
                'close': record.close,
                'high': record.high,
                'low': record.low,
                'volume': record.volume,
                'money': record.money,
            })
        
        df = pd.DataFrame(data)
        
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            
        return df
        
    except Exception as e:
        logger.error(f"获取股票 {stock_code} {year}年1分钟数据时发生错误: {e}")
        return pd.DataFrame()