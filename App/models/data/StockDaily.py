"""
股票日线数据模型
使用单表多股票结构，便于跨股票查询和统计
"""
from App.exts import db
import pandas as pd
from sqlalchemy.exc import IntegrityError
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

class StockDaily(db.Model):
    """
    股票日线数据模型
    使用单表多股票结构，每只股票的数据存储在同一个表中
    """
    __tablename__ = 'daily_stock_data'
    __bind_key__ = 'quanttradingsystem'
    
    # 复合主键：股票代码 + 日期
    stock_code = db.Column(db.String(10), primary_key=True, nullable=False, comment='股票代码')
    date = db.Column(db.Date, primary_key=True, nullable=False, comment='交易日期')
    
    # 基础OHLCV数据
    open = db.Column(db.Float, nullable=False, comment='开盘价')
    close = db.Column(db.Float, nullable=False, comment='收盘价')
    high = db.Column(db.Float, nullable=False, comment='最高价')
    low = db.Column(db.Float, nullable=False, comment='最低价')
    volume = db.Column(db.Integer, nullable=False, comment='成交量')
    money = db.Column(db.Integer, nullable=False, comment='成交额')
    
    # 技术分析字段 - 趋势信息
    trends = db.Column(db.Integer, nullable=True, default=1, comment='趋势')
    signal_id = db.Column(db.String(20), nullable=True, comment='信号ID')
    signal_start_time = db.Column(db.Text, nullable=True, comment='信号开始时间')
    re_trend = db.Column(db.Integer, nullable=True, default=1, comment='重新趋势')
    
    # 技术分析字段 - 周期信息
    cycle_amplitude_per_bar = db.Column(db.Float, nullable=True, default=0, comment='每根K线周期振幅')
    cycle_amplitude_max = db.Column(db.Float, nullable=True, default=0, comment='最大周期振幅')
    cycle_1m_vol_max1 = db.Column(db.Integer, nullable=True, default=0, comment='1分钟最大成交量1')
    cycle_1m_vol_max5 = db.Column(db.Integer, nullable=True, default=0, comment='1分钟最大成交量5')
    cycle_length_per_bar = db.Column(db.Integer, nullable=True, default=0, comment='每根K线周期长度')
    
    # 技术分析字段 - 预测信息
    predict_cycle_length = db.Column(db.Integer, nullable=True, default=0, comment='预测周期长度')
    predict_cycle_change = db.Column(db.Float, nullable=True, default=0, comment='预测周期变化')
    predict_cycle_price = db.Column(db.Float, nullable=True, default=0, comment='预测周期价格')
    predict_bar_change = db.Column(db.Float, nullable=True, default=0, comment='预测K线变化')
    predict_bar_price = db.Column(db.Float, nullable=True, default=0, comment='预测K线价格')
    predict_bar_volume = db.Column(db.Integer, nullable=True, default=0, comment='预测K线成交量')
    
    # 技术分析字段 - 评分信息
    score_rnn_model = db.Column(db.Float, nullable=True, default=0, comment='RNN模型得分')
    score_board_boll = db.Column(db.Float, nullable=True, default=0, comment='布林带得分')
    score_board_money = db.Column(db.Float, nullable=True, default=0, comment='资金得分')
    score_board_hot = db.Column(db.Float, nullable=True, default=0, comment='热度得分')
    score_funds_awkward = db.Column(db.Float, nullable=True, default=0, comment='基金重仓得分')
    trend_probability = db.Column(db.Float, nullable=True, default=0, comment='趋势概率')
    rnn_model_score = db.Column(db.Float, nullable=True, default=0, comment='RNN模型评分')
    cycle_amplitude = db.Column(db.Float, nullable=True, default=0, comment='周期振幅')
    
    # 基金持仓字段
    fund_holdings_count = db.Column(db.Integer, nullable=True, default=0, comment='持有该股票的基金数量')
    fund_holdings_ratio = db.Column(db.Float, nullable=True, default=0, comment='基金持仓总比例')
    fund_holdings_avg_ratio = db.Column(db.Float, nullable=True, default=0, comment='基金平均持仓比例')
    fund_holdings_max_ratio = db.Column(db.Float, nullable=True, default=0, comment='基金最大持仓比例')
    
    # 交易字段
    position = db.Column(db.Integer, nullable=True, default=1, comment='持仓状态')
    position_num = db.Column(db.Float, nullable=True, default=0, comment='持仓数量')
    stop_loss = db.Column(db.Float, nullable=True, default=0, comment='止损价格')
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'stock_code': self.stock_code,
            'date': self.date,
            'open': self.open,
            'close': self.close,
            'high': self.high,
            'low': self.low,
            'volume': self.volume,
            'money': self.money,
            'trends': self.trends,
            'signal_id': self.signal_id,
            'signal_start_time': self.signal_start_time,
            're_trend': self.re_trend,
            'cycle_amplitude_per_bar': self.cycle_amplitude_per_bar,
            'cycle_amplitude_max': self.cycle_amplitude_max,
            'cycle_1m_vol_max1': self.cycle_1m_vol_max1,
            'cycle_1m_vol_max5': self.cycle_1m_vol_max5,
            'cycle_length_per_bar': self.cycle_length_per_bar,
            'predict_cycle_length': self.predict_cycle_length,
            'predict_cycle_change': self.predict_cycle_change,
            'predict_cycle_price': self.predict_cycle_price,
            'predict_bar_change': self.predict_bar_change,
            'predict_bar_price': self.predict_bar_price,
            'predict_bar_volume': self.predict_bar_volume,
            'score_rnn_model': self.score_rnn_model,
            'score_board_boll': self.score_board_boll,
            'score_board_money': self.score_board_money,
            'score_board_hot': self.score_board_hot,
            'score_funds_awkward': self.score_funds_awkward,
            'trend_probability': self.trend_probability,
            'rnn_model_score': self.rnn_model_score,
            'cycle_amplitude': self.cycle_amplitude,
            'fund_holdings_count': self.fund_holdings_count,
            'fund_holdings_ratio': self.fund_holdings_ratio,
            'fund_holdings_avg_ratio': self.fund_holdings_avg_ratio,
            'fund_holdings_max_ratio': self.fund_holdings_max_ratio,
            'position': self.position,
            'position_num': self.position_num,
            'stop_loss': self.stop_loss,
        }

# 注意：原来的 create_daily_stock_model 函数已被移除
# 现在使用统一的 StockDaily 模型类，不再需要动态创建模型

def save_daily_stock_data_to_sql(stock_code: str, data: pd.DataFrame) -> bool:
    """
    将股票按天数据保存至 datadaily 数据库的 daily_stock_data 表中。

    Args:
        stock_code: 股票代码
        data: 每天的股票数据 DataFrame，每行包含 date、open、close 等列

    Returns:
        bool: 保存是否成功
    """
    try:
        # 将 DataFrame 转换为模型对象列表
        daily_stock_records = []

        for _, row in data.iterrows():
            record = StockDaily(
                stock_code=stock_code,  # 添加股票代码
                date=row['date'],
                open=row['open'],
                close=row['close'],
                high=row['high'],
                low=row['low'],
                volume=row['volume'],
                money=row['money'],

                # 为新增字段设置默认值
                trends=row.get('trends', 1),
                signal_id=row.get('signal_id', None),
                signal_start_time=row.get('signal_start_time', None),
                re_trend=row.get('re_trend', 1),
                cycle_amplitude_per_bar=row.get('cycle_amplitude_per_bar', 0),
                cycle_amplitude_max=row.get('cycle_amplitude_max', 0),
                cycle_1m_vol_max1=row.get('cycle_1m_vol_max1', 0),
                cycle_1m_vol_max5=row.get('cycle_1m_vol_max5', 0),
                cycle_length_per_bar=row.get('cycle_length_per_bar', 0),
                predict_cycle_length=row.get('predict_cycle_length', 0),
                predict_cycle_change=row.get('predict_cycle_change', 0),
                predict_cycle_price=row.get('predict_cycle_price', 0),
                predict_bar_change=row.get('predict_bar_change', 0),
                predict_bar_price=row.get('predict_bar_price', 0),
                predict_bar_volume=row.get('predict_bar_volume', 0),
                score_rnn_model=row.get('score_rnn_model', 0),
                score_board_boll=row.get('score_board_boll', 0),
                score_board_money=row.get('score_board_money', 0),
                score_board_hot=row.get('score_board_hot', 0),
                score_funds_awkward=row.get('score_funds_awkward', 0),
                trend_probability=row.get('trend_probability', 0),
                rnn_model_score=row.get('rnn_model_score', 0),
                cycle_amplitude=row.get('cycle_amplitude', 0),
                fund_holdings_count=row.get('fund_holdings_count', 0),
                fund_holdings_ratio=row.get('fund_holdings_ratio', 0),
                fund_holdings_avg_ratio=row.get('fund_holdings_avg_ratio', 0),
                fund_holdings_max_ratio=row.get('fund_holdings_max_ratio', 0),
                position=row.get('position', 0),
                position_num=row.get('position_num', 0),
                stop_loss=row.get('stop_loss', 0),
            )
            daily_stock_records.append(record)

        # 批量插入到数据库，处理重复数据
        inserted_count = 0
        updated_count = 0
        
        for record in daily_stock_records:
            try:
                # 检查是否已存在相同的记录
                existing_record = StockDaily.query.filter_by(
                    stock_code=record.stock_code,
                    date=record.date
                ).first()
                
                if existing_record:
                    # 更新现有记录
                    existing_record.open = record.open
                    existing_record.close = record.close
                    existing_record.high = record.high
                    existing_record.low = record.low
                    existing_record.volume = record.volume
                    existing_record.money = record.money
                    updated_count += 1
                else:
                    # 插入新记录
                    db.session.add(record)
                    inserted_count += 1
                    
            except Exception as e:
                logger.warning(f"处理记录失败: {record.stock_code} - {record.date}, 错误: {e}")
                continue
        
        db.session.commit()
        logger.info(f"成功保存股票 {stock_code} 的日线数据，新增 {inserted_count} 条，更新 {updated_count} 条")
        return True

    except Exception as e:
        db.session.rollback()
        logger.error(f"保存股票 {stock_code} 日线数据时发生未知错误: {e}")
        return False

def get_daily_stock_data(stock_code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    获取股票日线数据

    Args:
        stock_code: 股票代码
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)

    Returns:
        pd.DataFrame: 股票日线数据
    """
    try:
        query = StockDaily.query.filter(StockDaily.stock_code == stock_code)

        if start_date:
            query = query.filter(StockDaily.date >= start_date)
        if end_date:
            query = query.filter(StockDaily.date <= end_date)

        records = query.order_by(StockDaily.date).all()

        # 转换为DataFrame
        data = []
        for record in records:
            data.append(record.to_dict())

        return pd.DataFrame(data)

    except Exception as e:
        logger.error(f"获取股票 {stock_code} 日线数据时发生错误: {e}")
        return pd.DataFrame()


def get_multiple_stocks_data(stock_codes: List[str], start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    获取多只股票的日线数据（跨股票查询）

    Args:
        stock_codes: 股票代码列表
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)

    Returns:
        pd.DataFrame: 多只股票的日线数据
    """
    try:
        query = StockDaily.query.filter(StockDaily.stock_code.in_(stock_codes))

        if start_date:
            query = query.filter(StockDaily.date >= start_date)
        if end_date:
            query = query.filter(StockDaily.date <= end_date)

        records = query.order_by(StockDaily.stock_code, StockDaily.date).all()

        # 转换为DataFrame
        data = []
        for record in records:
            data.append(record.to_dict())

        return pd.DataFrame(data)

    except Exception as e:
        logger.error(f"获取多只股票日线数据时发生错误: {e}")
        return pd.DataFrame()


def get_stock_list() -> List[str]:
    """
    获取所有已存储的股票代码列表

    Returns:
        List[str]: 股票代码列表
    """
    try:
        # 使用 DISTINCT 查询获取所有唯一的股票代码
        result = db.session.query(StockDaily.stock_code).distinct().all()
        return [row[0] for row in result]
    except Exception as e:
        logger.error(f"获取股票列表时发生错误: {e}")
        return []


def get_market_overview(start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    获取市场概览数据（所有股票的最新数据）

    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)

    Returns:
        pd.DataFrame: 市场概览数据
    """
    try:
        # 获取每个股票的最新数据
        subquery = db.session.query(
            StockDaily.stock_code,
            db.func.max(StockDaily.date).label('max_date')
        ).group_by(StockDaily.stock_code).subquery()

        query = StockDaily.query.join(
            subquery,
            db.and_(
                StockDaily.stock_code == subquery.c.stock_code,
                StockDaily.date == subquery.c.max_date
            )
        )

        if start_date:
            query = query.filter(StockDaily.date >= start_date)
        if end_date:
            query = query.filter(StockDaily.date <= end_date)

        records = query.order_by(StockDaily.stock_code).all()

        # 转换为DataFrame
        data = []
        for record in records:
            data.append(record.to_dict())

        return pd.DataFrame(data)

    except Exception as e:
        logger.error(f"获取市场概览数据时发生错误: {e}")
        return pd.DataFrame()


def update_fund_holdings_data(analysis_date: str = None):
    """
    更新基金持仓数据到daily_stock_data表
    
    Args:
        analysis_date: 分析日期，格式为YYYYMMDD，如果为None则使用最新日期
    """
    try:
        from App.models.data.FundsAwkward import get_funds_holdings_from_csv, list_available_dates
        from datetime import datetime, timedelta
        
        # 获取分析日期
        if analysis_date is None:
            available_dates = list_available_dates()
            if not available_dates:
                logger.error("没有找到可用的基金数据日期")
                return False
            analysis_date = available_dates[0]
        
        # 转换为日期对象
        fund_date_obj = datetime.strptime(analysis_date, '%Y%m%d').date()
        
        # 获取基金持仓数据（限制为前500只基金）
        fund_data = get_funds_holdings_from_csv(fund_date_obj, limit_funds=500)
        
        if fund_data.empty:
            logger.error(f"没有找到 {analysis_date} 的基金持仓数据")
            return False
        
        logger.info(f"开始更新 {analysis_date} 的基金持仓数据，共 {len(fund_data)} 条记录")
        
        # 按股票代码分组，计算基金持仓统计
        stock_fund_stats = fund_data.groupby('stock_code').agg({
            'fund_code': 'count',  # 持有该股票的基金数量
            'holdings_ratio': ['sum', 'mean', 'max']  # 总比例、平均比例、最大比例
        }).round(4)
        
        # 重命名列
        stock_fund_stats.columns = ['fund_holdings_count', 'fund_holdings_ratio', 'fund_holdings_avg_ratio', 'fund_holdings_max_ratio']
        stock_fund_stats = stock_fund_stats.reset_index()
        
        # 更新数据库中的记录
        updated_count = 0
        not_found_count = 0
        
        for _, row in stock_fund_stats.iterrows():
            stock_code = row['stock_code']
            
            # 查找该股票在指定日期的记录
            daily_record = StockDaily.query.filter(
                StockDaily.stock_code == stock_code,
                StockDaily.date == fund_date_obj
            ).first()
            
            if daily_record:
                # 更新基金持仓字段
                daily_record.fund_holdings_count = int(row['fund_holdings_count'])
                daily_record.fund_holdings_ratio = float(row['fund_holdings_ratio'])
                daily_record.fund_holdings_avg_ratio = float(row['fund_holdings_avg_ratio'])
                daily_record.fund_holdings_max_ratio = float(row['fund_holdings_max_ratio'])
                updated_count += 1
            else:
                # 如果指定日期没有记录，尝试查找最近的交易日记录
                nearest_record = find_nearest_trading_day_record(stock_code, fund_date_obj)
                if nearest_record:
                    # 更新最近交易日记录的基金持仓字段
                    nearest_record.fund_holdings_count = int(row['fund_holdings_count'])
                    nearest_record.fund_holdings_ratio = float(row['fund_holdings_ratio'])
                    nearest_record.fund_holdings_avg_ratio = float(row['fund_holdings_avg_ratio'])
                    nearest_record.fund_holdings_max_ratio = float(row['fund_holdings_max_ratio'])
                    updated_count += 1
                    logger.info(f"股票 {stock_code} 在 {fund_date_obj} 无记录，更新到最近交易日 {nearest_record.date}")
                else:
                    not_found_count += 1
                    logger.warning(f"股票 {stock_code} 没有找到任何交易日记录")
        
        # 提交更改
        db.session.commit()
        
        logger.info(f"成功更新 {updated_count} 只股票的基金持仓数据")
        if not_found_count > 0:
            logger.warning(f"有 {not_found_count} 只股票没有找到对应的交易日记录")
        
        return True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"更新基金持仓数据失败: {e}")
        return False


def find_nearest_trading_day_record(stock_code: str, fund_date, days_range: int = 7):
    """
    查找股票在基金日期附近最近的交易日记录
    
    Args:
        stock_code: 股票代码
        fund_date: 基金数据日期
        days_range: 查找范围（天数）
        
    Returns:
        StockDaily: 最近的交易日记录，如果没有找到则返回None
    """
    try:
        from datetime import timedelta
        
        # 向前查找最近的交易日记录
        for i in range(days_range + 1):
            # 向前查找
            check_date = fund_date - timedelta(days=i)
            record = StockDaily.query.filter(
                StockDaily.stock_code == stock_code,
                StockDaily.date == check_date
            ).first()
            if record:
                return record
            
            # 向后查找（如果基金日期是周末，可能向后查找）
            if i > 0:  # 避免重复查找同一天
                check_date = fund_date + timedelta(days=i)
                record = StockDaily.query.filter(
                    StockDaily.stock_code == stock_code,
                    StockDaily.date == check_date
                ).first()
                if record:
                    return record
        
        return None
        
    except Exception as e:
        logger.error(f"查找股票 {stock_code} 最近交易日记录失败: {e}")
        return None
