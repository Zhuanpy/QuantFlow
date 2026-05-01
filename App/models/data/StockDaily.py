"""
股票日线数据模型
只存储客观行情数据（OHLCV）和基金持仓统计

职责划分：
    data_stock_daily       → 客观行情 + 基金持仓（本表）
    data_daily_task_status → 趋势/预测/评分/周期等计算结果
    trade_positions        → 持仓状态/止损等交易数据
"""
from App.exts import db
import pandas as pd
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class StockDaily(db.Model):
    """
    股票日线数据模型
    单表多股票结构，只保留不变的客观事实数据
    """
    __tablename__ = 'data_stock_daily'
    __bind_key__ = 'quanttradingsystem'

    # ==================== 复合主键 ====================
    stock_code = db.Column(db.String(10), primary_key=True, nullable=False, comment='股票代码')
    date = db.Column(db.Date, primary_key=True, nullable=False, comment='交易日期')

    # ==================== 基础 OHLCV ====================
    open = db.Column(db.Float, nullable=False, comment='开盘价')
    close = db.Column(db.Float, nullable=False, comment='收盘价')
    high = db.Column(db.Float, nullable=False, comment='最高价')
    low = db.Column(db.Float, nullable=False, comment='最低价')
    volume = db.Column(db.BigInteger, nullable=False, comment='成交量')
    money = db.Column(db.BigInteger, nullable=False, comment='成交额')

    # ==================== 基金持仓（按季度更新的外部数据） ====================
    fund_holdings_count = db.Column(db.Integer, nullable=True, default=0, comment='持有该股票的基金数量')
    fund_holdings_ratio = db.Column(db.Float, nullable=True, default=0, comment='基金持仓总比例')
    fund_holdings_avg_ratio = db.Column(db.Float, nullable=True, default=0, comment='基金平均持仓比例')
    fund_holdings_max_ratio = db.Column(db.Float, nullable=True, default=0, comment='基金最大持仓比例')

    def __repr__(self):
        return f'<StockDaily {self.stock_code} {self.date}>'

    def to_dict(self) -> Dict[str, Any]:
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
            'fund_holdings_count': self.fund_holdings_count,
            'fund_holdings_ratio': self.fund_holdings_ratio,
            'fund_holdings_avg_ratio': self.fund_holdings_avg_ratio,
            'fund_holdings_max_ratio': self.fund_holdings_max_ratio,
        }


def save_daily_stock_data_to_sql(stock_code: str, data: pd.DataFrame) -> bool:
    """
    将股票日线数据保存至数据库

    Args:
        stock_code: 股票代码
        data: DataFrame，必须包含 date/open/close/high/low/volume/money

    Returns:
        bool: 保存是否成功
    """
    try:
        import pandas as pd

        # 确保 date 列是纯日期（去掉时间部分），避免 pytdx 的 15:00 时间戳导致主键冲突
        data = data.copy()
        data['date'] = pd.to_datetime(data['date']).dt.date

        inserted_count = 0
        updated_count = 0

        for _, row in data.iterrows():
            row_date = row['date']
            try:
                existing_record = StockDaily.query.filter_by(
                    stock_code=stock_code,
                    date=row_date
                ).first()

                if existing_record:
                    existing_record.open = row['open']
                    existing_record.close = row['close']
                    existing_record.high = row['high']
                    existing_record.low = row['low']
                    existing_record.volume = row['volume']
                    existing_record.money = row['money']
                    updated_count += 1
                else:
                    record = StockDaily(
                        stock_code=stock_code,
                        date=row_date,
                        open=row['open'],
                        close=row['close'],
                        high=row['high'],
                        low=row['low'],
                        volume=row['volume'],
                        money=row['money'],
                        fund_holdings_count=row.get('fund_holdings_count', 0),
                        fund_holdings_ratio=row.get('fund_holdings_ratio', 0),
                        fund_holdings_avg_ratio=row.get('fund_holdings_avg_ratio', 0),
                        fund_holdings_max_ratio=row.get('fund_holdings_max_ratio', 0),
                    )
                    db.session.add(record)
                    inserted_count += 1

            except Exception as e:
                db.session.rollback()
                logger.warning(f"处理记录失败: {stock_code} - {row_date}, 错误: {e}")
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
        data = [record.to_dict() for record in records]
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
        data = [record.to_dict() for record in records]
        return pd.DataFrame(data)

    except Exception as e:
        logger.error(f"获取多只股票日线数据时发生错误: {e}")
        return pd.DataFrame()


def get_stock_list() -> List[str]:
    """获取所有已存储的股票代码列表"""
    try:
        result = db.session.query(StockDaily.stock_code).distinct().all()
        return [row[0] for row in result]
    except Exception as e:
        logger.error(f"获取股票列表时发生错误: {e}")
        return []


def get_market_overview(start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """获取市场概览数据（每个股票的最新一条数据）"""
    try:
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
        data = [record.to_dict() for record in records]
        return pd.DataFrame(data)

    except Exception as e:
        logger.error(f"获取市场概览数据时发生错误: {e}")
        return pd.DataFrame()


def update_fund_holdings_data(analysis_date: str = None):
    """
    更新基金持仓数据到 data_stock_daily 表

    Args:
        analysis_date: 分析日期，格式为YYYYMMDD，如果为None则使用最新日期
    """
    try:
        from App.models.data.FundsAwkward import get_funds_holdings_from_csv, list_available_dates
        from datetime import datetime, timedelta

        if analysis_date is None:
            available_dates = list_available_dates()
            if not available_dates:
                logger.error("没有找到可用的基金数据日期")
                return False
            analysis_date = available_dates[0]

        fund_date_obj = datetime.strptime(analysis_date, '%Y%m%d').date()
        # 不限制基金数量，确保 fund_holdings_count 反映全部基金的真实持有数
        fund_data = get_funds_holdings_from_csv(fund_date_obj, limit_funds=None)

        if fund_data.empty:
            logger.error(f"没有找到 {analysis_date} 的基金持仓数据")
            return False

        logger.info(f"开始更新 {analysis_date} 的基金持仓数据，共 {len(fund_data)} 条记录")

        stock_fund_stats = fund_data.groupby('stock_code').agg({
            'fund_code': 'nunique',  # 去重后的基金数，避免同基金重复行导致计数虚高
            'holdings_ratio': ['sum', 'mean', 'max']
        }).round(4)

        stock_fund_stats.columns = ['fund_holdings_count', 'fund_holdings_ratio', 'fund_holdings_avg_ratio', 'fund_holdings_max_ratio']
        stock_fund_stats = stock_fund_stats.reset_index()

        updated_count = 0
        not_found_count = 0

        for _, row in stock_fund_stats.iterrows():
            # CSV 里 stock_code 被 pandas 默认解析为 int64（丢失前导 0）；
            # DB 里是 VARCHAR '002475' — 必须 zfill 还原成 6 位字符串才能匹配
            raw_code = row['stock_code']
            s = str(raw_code).strip()
            # 全数字（含 numpy 数字类型转 str 后）→ 补齐到 6 位
            if s.replace('.', '').replace('-', '').isdigit():
                # 处理 '600276.0' 这种浮点 str
                if '.' in s:
                    s = s.split('.')[0]
                stock_code = s.zfill(6)
            else:
                stock_code = s

            daily_record = StockDaily.query.filter(
                StockDaily.stock_code == stock_code,
                StockDaily.date == fund_date_obj
            ).first()

            if daily_record:
                daily_record.fund_holdings_count = int(row['fund_holdings_count'])
                daily_record.fund_holdings_ratio = float(row['fund_holdings_ratio'])
                daily_record.fund_holdings_avg_ratio = float(row['fund_holdings_avg_ratio'])
                daily_record.fund_holdings_max_ratio = float(row['fund_holdings_max_ratio'])
                updated_count += 1
            else:
                nearest_record = find_nearest_trading_day_record(stock_code, fund_date_obj)
                if nearest_record:
                    nearest_record.fund_holdings_count = int(row['fund_holdings_count'])
                    nearest_record.fund_holdings_ratio = float(row['fund_holdings_ratio'])
                    nearest_record.fund_holdings_avg_ratio = float(row['fund_holdings_avg_ratio'])
                    nearest_record.fund_holdings_max_ratio = float(row['fund_holdings_max_ratio'])
                    updated_count += 1
                else:
                    not_found_count += 1

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
    """查找股票在基金日期附近最近的交易日记录"""
    try:
        from datetime import timedelta

        for i in range(days_range + 1):
            check_date = fund_date - timedelta(days=i)
            record = StockDaily.query.filter(
                StockDaily.stock_code == stock_code,
                StockDaily.date == check_date
            ).first()
            if record:
                return record

            if i > 0:
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
