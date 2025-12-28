"""
评估模块数据模型
包含股票池统计和板块统计相关的数据模型
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class CountBoard(db.Model):
    """板块统计信息表"""
    __tablename__ = 'count_board'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    name = db.Column(db.String(20), nullable=False, comment='板块名称')
    code = db.Column(db.String(20), nullable=False, comment='板块代码')
    trends = db.Column(db.Integer, default=0, comment='趋势值')
    record_date = db.Column(db.Date, nullable=False, comment='记录日期')
    
    # 新增字段
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<CountBoard {self.code}:{self.name} - {self.record_date}>'

    @classmethod
    def get_board_by_date(cls, record_date: str):
        """
        获取指定日期的板块统计信息
        
        Args:
            record_date: 记录日期 (YYYY-MM-DD)
            
        Returns:
            List[CountBoard]: 板块统计信息列表
        """
        return cls.query.filter_by(record_date=record_date).all()

    @classmethod
    def get_board_by_code(cls, code: str, start_date: str = None, end_date: str = None):
        """
        获取指定板块的统计信息
        
        Args:
            code: 板块代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            List[CountBoard]: 板块统计信息列表
        """
        query = cls.query.filter_by(code=code)
        
        if start_date:
            query = query.filter(cls.record_date >= start_date)
        if end_date:
            query = query.filter(cls.record_date <= end_date)
            
        return query.order_by(cls.record_date.desc()).all()

    @classmethod
    def get_latest_board_stats(cls):
        """
        获取最新的板块统计信息
        
        Returns:
            List[CountBoard]: 最新日期的板块统计信息列表
        """
        latest_date = db.session.query(db.func.max(cls.record_date)).scalar()
        if latest_date:
            return cls.query.filter_by(record_date=latest_date).all()
        return []

    @classmethod
    def update_board_trend(cls, code: str, record_date: str, trends: int):
        """
        更新板块趋势信息
        
        Args:
            code: 板块代码
            record_date: 记录日期
            trends: 趋势值
            
        Returns:
            bool: 更新是否成功
        """
        try:
            board = cls.query.filter_by(code=code, record_date=record_date).first()
            if board:
                board.trends = trends
                board.updated_at = datetime.utcnow()
            else:
                board = cls(
                    code=code,
                    record_date=record_date,
                    trends=trends
                )
                db.session.add(board)
            
            db.session.commit()
            logger.info(f"成功更新板块 {code} 在 {record_date} 的趋势信息")
            return True
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新板块趋势信息时发生错误: {e}")
            return False


class CountStockPool(db.Model):
    """统计股票池信息表"""
    __tablename__ = 'count_stock_pool'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    # 主键 ID
    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')

    # 记录日期
    date = db.Column(db.Date, nullable=False, comment='记录日期')

    # 上涨股票计数
    up = db.Column(db.Integer, default=0, comment='上涨股票计数')

    # 上涨股票趋势反转股票计数
    up_reversal = db.Column(db.Integer, default=0, comment='上涨股票趋势反转股票计数')

    # 下跌股票计数
    down = db.Column(db.Integer, default=0, comment='下跌股票计数')

    # 下跌股票反转股票计数
    down_reversal = db.Column(db.Integer, default=0, comment='下跌股票反转股票计数')

    # 个股股票"上涨开始阶段"和"上涨结束阶段" 计数
    up_phase_start = db.Column(db.Integer, default=0, comment='上涨开始阶段计数')
    up_phase_end = db.Column(db.Integer, default=0, comment='上涨结束阶段计数')

    # 个股股票"下跌开始阶段"和"下跌结束阶段" 计数
    down_phase_start = db.Column(db.Integer, default=0, comment='下跌开始阶段计数')
    down_phase_end = db.Column(db.Integer, default=0, comment='下跌结束阶段计数')

    # 股票的上涨 "前期、中期、后期" 计数
    up_initial = db.Column(db.Integer, default=0, comment='上涨前期计数')
    up_median = db.Column(db.Integer, default=0, comment='上涨中期计数')
    up_final = db.Column(db.Integer, default=0, comment='上涨后期计数')

    # 股票的下跌 "前期、中期、后期" 计数
    down_initial = db.Column(db.Integer, default=0, comment='下跌前期计数')
    down_median = db.Column(db.Integer, default=0, comment='下跌中期计数')
    down_final = db.Column(db.Integer, default=0, comment='下跌后期计数')

    # 板块"上涨开始阶段"和"上涨结束阶段" 计数
    board_up_phase_start = db.Column(db.Integer, default=0, comment='板块上涨开始阶段计数')
    board_up_phase_end = db.Column(db.Integer, default=0, comment='板块上涨结束阶段计数')

    # 板块"下跌开始阶段"和"下跌结束阶段" 计数
    board_down_phase_start = db.Column(db.Integer, default=0, comment='板块下跌开始阶段计数')
    board_down_phase_end = db.Column(db.Integer, default=0, comment='板块下跌结束阶段计数')
    
    # 新增字段
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<CountStockPool {self.date} - 上涨:{self.up} 下跌:{self.down}>'

    @classmethod
    def get_pool_stats_by_date(cls, date: str):
        """
        获取指定日期的股票池统计信息
        
        Args:
            date: 记录日期 (YYYY-MM-DD)
            
        Returns:
            CountStockPool: 股票池统计信息
        """
        return cls.query.filter_by(date=date).first()

    @classmethod
    def get_pool_stats_by_date_range(cls, start_date: str, end_date: str):
        """
        获取指定日期范围的股票池统计信息
        
        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            List[CountStockPool]: 股票池统计信息列表
        """
        return cls.query.filter(
            cls.date >= start_date,
            cls.date <= end_date
        ).order_by(cls.date.desc()).all()

    @classmethod
    def get_latest_pool_stats(cls):
        """
        获取最新的股票池统计信息
        
        Returns:
            CountStockPool: 最新日期的股票池统计信息
        """
        latest_date = db.session.query(db.func.max(cls.date)).scalar()
        if latest_date:
            return cls.query.filter_by(date=latest_date).first()
        return None

    @classmethod
    def update_pool_stats(cls, date: str, **kwargs):
        """
        更新股票池统计信息
        
        Args:
            date: 记录日期
            **kwargs: 要更新的字段和值
            
        Returns:
            bool: 更新是否成功
        """
        try:
            pool_stats = cls.query.filter_by(date=date).first()
            if pool_stats:
                # 更新现有记录
                for field, value in kwargs.items():
                    if hasattr(pool_stats, field) and value is not None:
                        setattr(pool_stats, field, value)
                pool_stats.updated_at = datetime.utcnow()
            else:
                # 创建新记录
                pool_stats = cls(date=date, **kwargs)
                db.session.add(pool_stats)
            
            db.session.commit()
            logger.info(f"成功更新股票池统计信息，日期: {date}")
            return True
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新股票池统计信息时发生错误: {e}")
            return False

    def get_total_stocks(self):
        """
        获取总股票数量
        
        Returns:
            int: 总股票数量
        """
        return self.up + self.down

    def get_up_ratio(self):
        """
        获取上涨股票比例
        
        Returns:
            float: 上涨股票比例
        """
        total = self.get_total_stocks()
        return (self.up / total * 100) if total > 0 else 0

    def get_down_ratio(self):
        """
        获取下跌股票比例
        
        Returns:
            float: 下跌股票比例
        """
        total = self.get_total_stocks()
        return (self.down / total * 100) if total > 0 else 0

    def get_reversal_ratio(self):
        """
        获取反转股票比例
        
        Returns:
            float: 反转股票比例
        """
        total = self.get_total_stocks()
        reversal_total = self.up_reversal + self.down_reversal
        return (reversal_total / total * 100) if total > 0 else 0
