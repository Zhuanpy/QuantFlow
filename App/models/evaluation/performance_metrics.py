"""
性能指标评估模型
包含策略回测、风险评估等性能指标相关的数据模型
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class StrategyPerformance(db.Model):
    """策略性能评估表"""
    __tablename__ = 'strategy_performance'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    strategy_name = db.Column(db.String(50), nullable=False, comment='策略名称')
    stock_code = db.Column(db.String(20), nullable=False, comment='股票代码')
    start_date = db.Column(db.Date, nullable=False, comment='回测开始日期')
    end_date = db.Column(db.Date, nullable=False, comment='回测结束日期')
    
    # 基础指标
    total_return = db.Column(db.Float, default=0.0, comment='总收益率(%)')
    annual_return = db.Column(db.Float, default=0.0, comment='年化收益率(%)')
    max_drawdown = db.Column(db.Float, default=0.0, comment='最大回撤(%)')
    sharpe_ratio = db.Column(db.Float, default=0.0, comment='夏普比率')
    sortino_ratio = db.Column(db.Float, default=0.0, comment='索提诺比率')
    calmar_ratio = db.Column(db.Float, default=0.0, comment='卡玛比率')
    
    # 风险指标
    volatility = db.Column(db.Float, default=0.0, comment='波动率(%)')
    var_95 = db.Column(db.Float, default=0.0, comment='95% VaR')
    cvar_95 = db.Column(db.Float, default=0.0, comment='95% CVaR')
    beta = db.Column(db.Float, default=0.0, comment='贝塔系数')
    alpha = db.Column(db.Float, default=0.0, comment='阿尔法系数')
    
    # 交易指标
    total_trades = db.Column(db.Integer, default=0, comment='总交易次数')
    win_rate = db.Column(db.Float, default=0.0, comment='胜率(%)')
    profit_factor = db.Column(db.Float, default=0.0, comment='盈亏比')
    avg_win = db.Column(db.Float, default=0.0, comment='平均盈利')
    avg_loss = db.Column(db.Float, default=0.0, comment='平均亏损')
    max_consecutive_wins = db.Column(db.Integer, default=0, comment='最大连续盈利次数')
    max_consecutive_losses = db.Column(db.Integer, default=0, comment='最大连续亏损次数')
    
    # 其他指标
    initial_capital = db.Column(db.Float, default=0.0, comment='初始资金')
    final_capital = db.Column(db.Float, default=0.0, comment='最终资金')
    benchmark_return = db.Column(db.Float, default=0.0, comment='基准收益率(%)')
    excess_return = db.Column(db.Float, default=0.0, comment='超额收益率(%)')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<StrategyPerformance {self.strategy_name}:{self.stock_code} - 收益率:{self.total_return:.2f}%>'

    @classmethod
    def get_performance_by_strategy(cls, strategy_name: str, start_date: str = None, end_date: str = None):
        """
        获取指定策略的性能数据
        
        Args:
            strategy_name: 策略名称
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            List[StrategyPerformance]: 策略性能数据列表
        """
        query = cls.query.filter_by(strategy_name=strategy_name)
        
        if start_date:
            query = query.filter(cls.start_date >= start_date)
        if end_date:
            query = query.filter(cls.end_date <= end_date)
            
        return query.order_by(cls.end_date.desc()).all()

    @classmethod
    def get_performance_by_stock(cls, stock_code: str, start_date: str = None, end_date: str = None):
        """
        获取指定股票的策略性能数据
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            List[StrategyPerformance]: 股票策略性能数据列表
        """
        query = cls.query.filter_by(stock_code=stock_code)
        
        if start_date:
            query = query.filter(cls.start_date >= start_date)
        if end_date:
            query = query.filter(cls.end_date <= end_date)
            
        return query.order_by(cls.end_date.desc()).all()

    @classmethod
    def get_best_performers(cls, limit: int = 10, metric: str = 'total_return'):
        """
        获取表现最好的策略
        
        Args:
            limit: 返回数量限制
            metric: 排序指标
            
        Returns:
            List[StrategyPerformance]: 表现最好的策略列表
        """
        if hasattr(cls, metric):
            return cls.query.order_by(getattr(cls, metric).desc()).limit(limit).all()
        return []

    @classmethod
    def update_performance(cls, strategy_name: str, stock_code: str, start_date: str, 
                          end_date: str, **kwargs):
        """
        更新策略性能数据
        
        Args:
            strategy_name: 策略名称
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 要更新的字段和值
            
        Returns:
            bool: 更新是否成功
        """
        try:
            performance = cls.query.filter_by(
                strategy_name=strategy_name,
                stock_code=stock_code,
                start_date=start_date,
                end_date=end_date
            ).first()
            
            if performance:
                # 更新现有记录
                for field, value in kwargs.items():
                    if hasattr(performance, field) and value is not None:
                        setattr(performance, field, value)
                performance.updated_at = datetime.utcnow()
            else:
                # 创建新记录
                performance = cls(
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    **kwargs
                )
                db.session.add(performance)
            
            db.session.commit()
            logger.info(f"成功更新策略 {strategy_name} 在股票 {stock_code} 的性能数据")
            return True
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新策略性能数据时发生错误: {e}")
            return False


class RiskMetrics(db.Model):
    """风险指标表"""
    __tablename__ = 'risk_metrics'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    stock_code = db.Column(db.String(20), nullable=False, comment='股票代码')
    date = db.Column(db.Date, nullable=False, comment='计算日期')
    
    # 风险指标
    volatility_20d = db.Column(db.Float, default=0.0, comment='20日波动率(%)')
    volatility_60d = db.Column(db.Float, default=0.0, comment='60日波动率(%)')
    var_95_20d = db.Column(db.Float, default=0.0, comment='20日95% VaR')
    var_99_20d = db.Column(db.Float, default=0.0, comment='20日99% VaR')
    cvar_95_20d = db.Column(db.Float, default=0.0, comment='20日95% CVaR')
    cvar_99_20d = db.Column(db.Float, default=0.0, comment='20日99% CVaR')
    
    # 技术指标
    rsi_14 = db.Column(db.Float, default=0.0, comment='14日RSI')
    macd = db.Column(db.Float, default=0.0, comment='MACD')
    macd_signal = db.Column(db.Float, default=0.0, comment='MACD信号线')
    macd_histogram = db.Column(db.Float, default=0.0, comment='MACD柱状图')
    
    # 布林带指标
    bb_upper = db.Column(db.Float, default=0.0, comment='布林带上轨')
    bb_middle = db.Column(db.Float, default=0.0, comment='布林带中轨')
    bb_lower = db.Column(db.Float, default=0.0, comment='布林带下轨')
    bb_width = db.Column(db.Float, default=0.0, comment='布林带宽度')
    bb_position = db.Column(db.Float, default=0.0, comment='布林带位置')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<RiskMetrics {self.stock_code}:{self.date} - 波动率:{self.volatility_20d:.2f}%>'

    @classmethod
    def get_risk_metrics_by_stock(cls, stock_code: str, start_date: str = None, end_date: str = None):
        """
        获取指定股票的风险指标
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            List[RiskMetrics]: 风险指标列表
        """
        query = cls.query.filter_by(stock_code=stock_code)
        
        if start_date:
            query = query.filter(cls.date >= start_date)
        if end_date:
            query = query.filter(cls.date <= end_date)
            
        return query.order_by(cls.date.desc()).all()

    @classmethod
    def get_latest_risk_metrics(cls, stock_code: str):
        """
        获取指定股票最新的风险指标
        
        Args:
            stock_code: 股票代码
            
        Returns:
            RiskMetrics: 最新的风险指标
        """
        latest_date = db.session.query(db.func.max(cls.date)).filter_by(stock_code=stock_code).scalar()
        if latest_date:
            return cls.query.filter_by(stock_code=stock_code, date=latest_date).first()
        return None

    @classmethod
    def update_risk_metrics(cls, stock_code: str, date: str, **kwargs):
        """
        更新风险指标
        
        Args:
            stock_code: 股票代码
            date: 计算日期
            **kwargs: 要更新的字段和值
            
        Returns:
            bool: 更新是否成功
        """
        try:
            risk_metrics = cls.query.filter_by(stock_code=stock_code, date=date).first()
            
            if risk_metrics:
                # 更新现有记录
                for field, value in kwargs.items():
                    if hasattr(risk_metrics, field) and value is not None:
                        setattr(risk_metrics, field, value)
                risk_metrics.updated_at = datetime.utcnow()
            else:
                # 创建新记录
                risk_metrics = cls(stock_code=stock_code, date=date, **kwargs)
                db.session.add(risk_metrics)
            
            db.session.commit()
            logger.info(f"成功更新股票 {stock_code} 在 {date} 的风险指标")
            return True
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新风险指标时发生错误: {e}")
            return False 