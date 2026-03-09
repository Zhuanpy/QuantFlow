"""
我的股票市场 - 管理用户选择维护的股票清单

从全市场 data_stock_info 中筛选出要维护数据的股票，
标记下载哪些周期的数据，作为股票池的上游数据源。

层级关系：
    data_stock_info (全市场) → MyStockMarket (我的市场) → StockPool (策略股票池)
"""
from App.exts import db
from datetime import datetime, date
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class MyStockMarket(db.Model):
    """
    我的股票市场表

    用户从全市场中选择要维护数据的股票清单，
    控制下载哪些周期的数据，追踪数据维护状态。
    """
    __tablename__ = 'data_my_stock_market'
    __bind_key__ = 'quanttradingsystem'

    # ==================== 主键 ====================
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')

    # ==================== 股票基本信息 ====================
    stock_code = db.Column(db.String(10), nullable=False, unique=True, comment='股票代码')
    stock_name = db.Column(db.String(50), nullable=True, comment='股票名称')
    market = db.Column(db.String(10), nullable=True, comment='市场：SH/SZ/BJ')
    industry = db.Column(db.String(50), nullable=True, comment='所属行业')
    board = db.Column(db.String(20), nullable=True, comment='板块：主板/创业板/科创板/北交所')

    # ==================== 数据下载开关 ====================
    download_daily = db.Column(db.Boolean, default=True, nullable=False, comment='是否下载日线数据')
    download_1m = db.Column(db.Boolean, default=False, nullable=False, comment='是否下载1分钟数据')
    download_15m = db.Column(db.Boolean, default=False, nullable=False, comment='是否下载15分钟数据')
    download_board = db.Column(db.Boolean, default=False, nullable=False, comment='是否下载板块数据')

    # ==================== 数据维护状态 ====================
    daily_start_date = db.Column(db.Date, nullable=True, comment='日线数据起始日期')
    daily_end_date = db.Column(db.Date, nullable=True, comment='日线数据截止日期')
    daily_count = db.Column(db.Integer, default=0, comment='日线数据条数')

    minute_start_date = db.Column(db.Date, nullable=True, comment='分钟数据起始日期')
    minute_end_date = db.Column(db.Date, nullable=True, comment='分钟数据截止日期')
    minute_count = db.Column(db.Integer, default=0, comment='分钟数据条数')

    last_sync_time = db.Column(db.DateTime, nullable=True, comment='最后同步时间')

    # ==================== 分组与标签 ====================
    group_name = db.Column(db.String(30), default='默认', comment='分组名称，便于管理')
    tags = db.Column(db.String(200), nullable=True, comment='标签，逗号分隔')
    priority = db.Column(db.Integer, default=3, comment='优先级：1-5，5最高')
    notes = db.Column(db.Text, nullable=True, comment='备注')

    # ==================== 状态 ====================
    is_active = db.Column(db.Boolean, default=True, nullable=False, comment='是否激活')

    # ==================== 时间戳 ====================
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<MyStockMarket {self.stock_code}:{self.stock_name}>'

    def to_dict(self) -> Dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    # ==================== 查询方法 ====================

    @classmethod
    def get_active_stocks(cls, group_name: str = None) -> List['MyStockMarket']:
        """获取激活的股票列表"""
        query = cls.query.filter_by(is_active=True)
        if group_name:
            query = query.filter_by(group_name=group_name)
        return query.order_by(cls.priority.desc(), cls.stock_code).all()

    @classmethod
    def get_download_list(cls, data_type: str) -> List['MyStockMarket']:
        """
        获取需要下载某类数据的股票列表

        Args:
            data_type: 'daily' / '1m' / '15m' / 'board'
        """
        field_map = {
            'daily': cls.download_daily,
            '1m': cls.download_1m,
            '15m': cls.download_15m,
            'board': cls.download_board,
        }
        field = field_map.get(data_type)
        if field is None:
            return []
        return cls.query.filter(field == True, cls.is_active == True).order_by(cls.priority.desc()).all()

    @classmethod
    def get_by_code(cls, stock_code: str) -> Optional['MyStockMarket']:
        return cls.query.filter_by(stock_code=stock_code).first()

    @classmethod
    def add_stock(cls, stock_code: str, stock_name: str = None, **kwargs) -> 'MyStockMarket':
        """添加股票到我的市场"""
        existing = cls.get_by_code(stock_code)
        if existing:
            logger.info(f"股票 {stock_code} 已存在，跳过添加")
            return existing

        stock = cls(stock_code=stock_code, stock_name=stock_name, **kwargs)
        db.session.add(stock)
        db.session.commit()
        logger.info(f"成功添加股票 {stock_code} 到我的市场")
        return stock

    @classmethod
    def batch_add(cls, stocks: List[Dict[str, Any]]) -> int:
        """
        批量添加股票

        Args:
            stocks: [{'stock_code': '000001', 'stock_name': '平安银行', ...}, ...]

        Returns:
            int: 成功添加的数量
        """
        existing_codes = {s.stock_code for s in cls.query.with_entities(cls.stock_code).all()}
        new_stocks = []
        for item in stocks:
            if item['stock_code'] not in existing_codes:
                new_stocks.append(cls(**item))

        if new_stocks:
            db.session.bulk_save_objects(new_stocks)
            db.session.commit()
            logger.info(f"批量添加 {len(new_stocks)} 只股票到我的市场")
        return len(new_stocks)

    @classmethod
    def remove_stock(cls, stock_code: str) -> bool:
        """从我的市场移除股票（软删除）"""
        stock = cls.get_by_code(stock_code)
        if stock:
            stock.is_active = False
            stock.updated_at = datetime.utcnow()
            db.session.commit()
            return True
        return False

    @classmethod
    def update_sync_status(cls, stock_code: str, data_type: str,
                           start_date: date = None, end_date: date = None,
                           count: int = None) -> bool:
        """更新数据同步状态"""
        stock = cls.get_by_code(stock_code)
        if not stock:
            return False

        if data_type == 'daily':
            if start_date: stock.daily_start_date = start_date
            if end_date: stock.daily_end_date = end_date
            if count is not None: stock.daily_count = count
        elif data_type in ('1m', '15m'):
            if start_date: stock.minute_start_date = start_date
            if end_date: stock.minute_end_date = end_date
            if count is not None: stock.minute_count = count

        stock.last_sync_time = datetime.utcnow()
        stock.updated_at = datetime.utcnow()
        db.session.commit()
        return True

    @classmethod
    def get_groups(cls) -> List[str]:
        """获取所有分组名称"""
        results = db.session.query(cls.group_name).filter(
            cls.is_active == True
        ).distinct().all()
        return [r[0] for r in results]

    @classmethod
    def get_statistics(cls) -> Dict[str, Any]:
        """获取市场统计信息"""
        total = cls.query.filter_by(is_active=True).count()
        download_daily = cls.query.filter_by(is_active=True, download_daily=True).count()
        download_1m = cls.query.filter_by(is_active=True, download_1m=True).count()

        group_stats = db.session.query(
            cls.group_name, db.func.count(cls.id)
        ).filter_by(is_active=True).group_by(cls.group_name).all()

        return {
            'total': total,
            'download_daily': download_daily,
            'download_1m': download_1m,
            'groups': {name: count for name, count in group_stats},
        }
