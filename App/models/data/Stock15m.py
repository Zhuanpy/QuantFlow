"""
股票15分钟数据模型
提供动态创建股票15分钟数据表的功能
"""
import pandas as pd
from App.exts import db
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

# 缓存动态生成的模型类
model_cache: Dict[str, Any] = {}


def create_15m_stock_model(stock_code: str):
    """
    动态生成股票15分钟数据模型，根据股票代码创建表名。
    
    Args:
        stock_code: 股票代码
        
    Returns:
        动态生成的模型类
    """
    # 缓存键：基于股票代码
    cache_key = stock_code

    if cache_key in model_cache:
        return model_cache[cache_key]

    # 构造唯一的类名
    class_name = f"Stock15mData_{stock_code}"

    # 动态定义类
    Stock15mData = type(
        class_name,
        (db.Model,),
        {
            '__tablename__': f"{stock_code}",
            '__bind_key__': "datadaily",  # 使用datadaily数据库
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
    model_cache[cache_key] = Stock15mData

    return Stock15mData


def save_15m_stock_data_to_sql(stock_code: str, data: pd.DataFrame) -> bool:
    """
    将股票15分钟数据保存至数据库表中。

    Args:
        stock_code: 股票代码
        data: 股票15分钟数据的 DataFrame，每行包含 date、open、close 等字段
        
    Returns:
        bool: 保存是否成功
    """
    try:
        # 动态创建模型类
        StockModel = create_15m_stock_model(stock_code)

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
        
        logger.info(f"成功保存股票 {stock_code} 15分钟数据，新增 {len(new_records)} 条，更新 {updated_count} 条")
        return True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"保存股票 {stock_code} 15分钟数据时发生错误: {e}")
        return False


def load_15m_stock_data_from_sql(stock_code: str) -> pd.DataFrame:
    """
    从数据库表中加载股票15分钟数据到Pandas DataFrame中。

    Args:
        stock_code: 股票代码

    Returns:
        pd.DataFrame: 包含股票15分钟数据的DataFrame
    """
    try:
        StockModel = create_15m_stock_model(stock_code)

        # 查询所有数据
        records = StockModel.query.order_by(StockModel.date).all()

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

        # 确保日期列是datetime类型
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])

        logger.info(f"成功加载股票 {stock_code} 15分钟数据，共 {len(df)} 条记录")
        return df
        
    except Exception as e:
        logger.error(f"加载股票 {stock_code} 15分钟数据时发生错误: {e}")
        return pd.DataFrame()


def get_15m_stock_data(stock_code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    获取指定时间范围的股票15分钟数据
    
    Args:
        stock_code: 股票代码
        start_date: 开始日期时间 (YYYY-MM-DD HH:MM:SS)
        end_date: 结束日期时间 (YYYY-MM-DD HH:MM:SS)
        
    Returns:
        pd.DataFrame: 股票15分钟数据
    """
    try:
        StockModel = create_15m_stock_model(stock_code)
        
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
        logger.error(f"获取股票 {stock_code} 15分钟数据时发生错误: {e}")
        return pd.DataFrame()
