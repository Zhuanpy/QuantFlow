"""
交易信号模型
用于管理交易信号
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TradeSignal(db.Model):
    """
    交易信号模型
    
    用于记录和管理交易信号
    """
    __tablename__ = 'trade_signals'
    __bind_key__ = 'quanttradingsystem'

    # 信号类型常量
    SIGNAL_TYPE_BUY = 'buy'           # 买入信号
    SIGNAL_TYPE_SELL = 'sell'         # 卖出信号
    SIGNAL_TYPE_HOLD = 'hold'         # 持有信号
    SIGNAL_TYPE_SHORT = 'short'       # 做空信号
    SIGNAL_TYPE_COVER = 'cover'       # 平仓信号

    # 信号强度常量
    STRENGTH_WEAK = 'weak'            # 弱信号
    STRENGTH_MEDIUM = 'medium'        # 中等信号
    STRENGTH_STRONG = 'strong'        # 强信号
    STRENGTH_VERY_STRONG = 'very_strong'  # 极强信号

    # 信号状态常量
    STATUS_PENDING = 'pending'        # 待处理
    STATUS_PROCESSED = 'processed'    # 已处理
    STATUS_EXPIRED = 'expired'        # 已过期
    STATUS_CANCELLED = 'cancelled'    # 已取消

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 信号基本信息
    signal_id = db.Column(db.String(50), unique=True, nullable=False, comment='信号ID')
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(100), nullable=False, comment='股票名称')
    signal_type = db.Column(db.String(20), nullable=False, comment='信号类型')
    signal_strength = db.Column(db.String(20), default=STRENGTH_MEDIUM, comment='信号强度')
    status = db.Column(db.String(20), default=STATUS_PENDING, comment='信号状态')
    
    # 价格信息
    current_price = db.Column(db.Numeric(10, 2), nullable=False, comment='当前价格')
    target_price = db.Column(db.Numeric(10, 2), nullable=True, comment='目标价格')
    stop_loss_price = db.Column(db.Numeric(10, 2), nullable=True, comment='止损价格')
    take_profit_price = db.Column(db.Numeric(10, 2), nullable=True, comment='止盈价格')
    
    # 信号指标
    confidence_score = db.Column(db.Float, nullable=False, comment='置信度分数')
    risk_score = db.Column(db.Float, nullable=True, comment='风险分数')
    expected_return = db.Column(db.Float, nullable=True, comment='预期收益率')
    
    # 策略信息
    strategy_name = db.Column(db.String(100), nullable=False, comment='策略名称')
    strategy_version = db.Column(db.String(20), nullable=True, comment='策略版本')
    signal_source = db.Column(db.String(50), nullable=True, comment='信号来源')
    
    # 技术指标
    rsi = db.Column(db.Float, nullable=True, comment='RSI指标')
    macd = db.Column(db.Float, nullable=True, comment='MACD指标')
    bollinger_position = db.Column(db.Float, nullable=True, comment='布林带位置')
    volume_ratio = db.Column(db.Float, nullable=True, comment='成交量比率')
    
    # 时间信息
    signal_time = db.Column(db.DateTime, nullable=False, comment='信号生成时间')
    expire_time = db.Column(db.DateTime, nullable=True, comment='信号过期时间')
    process_time = db.Column(db.DateTime, nullable=True, comment='信号处理时间')
    
    # 执行信息
    executed_quantity = db.Column(db.Integer, default=0, comment='执行数量')
    executed_price = db.Column(db.Numeric(10, 2), nullable=True, comment='执行价格')
    trade_id = db.Column(db.String(50), nullable=True, comment='关联交易ID')
    
    # 备注信息
    remarks = db.Column(db.Text, nullable=True, comment='备注')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        """返回对象的字符串表示"""
        return f"<TradeSignal(id={self.id}, signal_id={self.signal_id}, stock_code={self.stock_code}, type={self.signal_type})>"

    @classmethod
    def create_signal_id(cls):
        """生成唯一的信号ID"""
        import uuid
        return f"SIG{datetime.now().strftime('%Y%m%d%H%M%S')}{str(uuid.uuid4())[:8]}"

    @classmethod
    def get_signals_by_stock(cls, stock_code: str, limit: int = 100):
        """
        获取指定股票的信号
        
        Args:
            stock_code: 股票代码
            limit: 限制数量
            
        Returns:
            List[TradeSignal]: 信号列表
        """
        return cls.query.filter_by(stock_code=stock_code).order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_signals_by_type(cls, signal_type: str, limit: int = 100):
        """
        获取指定类型的信号
        
        Args:
            signal_type: 信号类型
            limit: 限制数量
            
        Returns:
            List[TradeSignal]: 信号列表
        """
        return cls.query.filter_by(signal_type=signal_type).order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_signals_by_status(cls, status: str, limit: int = 100):
        """
        获取指定状态的信号
        
        Args:
            status: 信号状态
            limit: 限制数量
            
        Returns:
            List[TradeSignal]: 信号列表
        """
        return cls.query.filter_by(status=status).order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_pending_signals(cls):
        """
        获取所有待处理的信号
        
        Returns:
            List[TradeSignal]: 待处理信号列表
        """
        return cls.query.filter_by(status=cls.STATUS_PENDING).order_by(cls.confidence_score.desc()).all()

    @classmethod
    def get_strong_signals(cls, min_confidence: float = 0.7):
        """
        获取强信号
        
        Args:
            min_confidence: 最小置信度
            
        Returns:
            List[TradeSignal]: 强信号列表
        """
        return cls.query.filter(
            cls.confidence_score >= min_confidence,
            cls.status == cls.STATUS_PENDING
        ).order_by(cls.confidence_score.desc()).all()

    @classmethod
    def get_signals_by_strategy(cls, strategy_name: str, limit: int = 100):
        """
        获取指定策略的信号
        
        Args:
            strategy_name: 策略名称
            limit: 限制数量
            
        Returns:
            List[TradeSignal]: 信号列表
        """
        return cls.query.filter_by(strategy_name=strategy_name).order_by(cls.created_at.desc()).limit(limit).all()

    def process_signal(self, executed_quantity: int = None, executed_price: float = None, trade_id: str = None):
        """
        处理信号
        
        Args:
            executed_quantity: 执行数量
            executed_price: 执行价格
            trade_id: 交易ID
            
        Returns:
            bool: 处理是否成功
        """
        try:
            self.status = self.STATUS_PROCESSED
            self.process_time = datetime.utcnow()
            
            if executed_quantity is not None:
                self.executed_quantity = executed_quantity
            if executed_price is not None:
                self.executed_price = executed_price
            if trade_id is not None:
                self.trade_id = trade_id
            
            self.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"成功处理信号 {self.signal_id}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"处理信号时出错: {str(e)}")
            return False

    def cancel_signal(self, reason: str = None):
        """
        取消信号
        
        Args:
            reason: 取消原因
            
        Returns:
            bool: 取消是否成功
        """
        try:
            self.status = self.STATUS_CANCELLED
            self.updated_at = datetime.utcnow()
            if reason:
                self.remarks = f"取消原因: {reason}"
            
            db.session.commit()
            logger.info(f"成功取消信号 {self.signal_id}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"取消信号时出错: {str(e)}")
            return False

    def expire_signal(self):
        """
        使信号过期
        
        Returns:
            bool: 操作是否成功
        """
        try:
            self.status = self.STATUS_EXPIRED
            self.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"信号 {self.signal_id} 已过期")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"使信号过期时出错: {str(e)}")
            return False

    def is_expired(self):
        """
        检查信号是否已过期
        
        Returns:
            bool: 是否已过期
        """
        if self.expire_time:
            return datetime.utcnow() > self.expire_time
        return False

    def is_strong_signal(self, threshold: float = 0.7):
        """
        检查是否为强信号
        
        Args:
            threshold: 强度阈值
            
        Returns:
            bool: 是否为强信号
        """
        return self.confidence_score >= threshold

    def is_buy_signal(self):
        """检查是否为买入信号"""
        return self.signal_type in [self.SIGNAL_TYPE_BUY]

    def is_sell_signal(self):
        """检查是否为卖出信号"""
        return self.signal_type in [self.SIGNAL_TYPE_SELL, self.SIGNAL_TYPE_SHORT, self.SIGNAL_TYPE_COVER]

    def is_hold_signal(self):
        """检查是否为持有信号"""
        return self.signal_type == self.SIGNAL_TYPE_HOLD

    def get_risk_reward_ratio(self):
        """
        获取风险收益比
        
        Returns:
            float: 风险收益比
        """
        if self.stop_loss_price and self.take_profit_price and self.current_price:
            if self.is_buy_signal():
                risk = float(self.current_price - self.stop_loss_price)
                reward = float(self.take_profit_price - self.current_price)
            else:
                risk = float(self.stop_loss_price - self.current_price)
                reward = float(self.current_price - self.take_profit_price)
            
            if risk > 0:
                return reward / risk
        return 0.0

    def to_dict(self):
        """
        转换为字典格式
        
        Returns:
            dict: 字典格式的数据
        """
        return {
            'id': self.id,
            'signal_id': self.signal_id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'signal_type': self.signal_type,
            'signal_strength': self.signal_strength,
            'status': self.status,
            'current_price': float(self.current_price) if self.current_price else None,
            'target_price': float(self.target_price) if self.target_price else None,
            'stop_loss_price': float(self.stop_loss_price) if self.stop_loss_price else None,
            'take_profit_price': float(self.take_profit_price) if self.take_profit_price else None,
            'confidence_score': self.confidence_score,
            'risk_score': self.risk_score,
            'expected_return': self.expected_return,
            'strategy_name': self.strategy_name,
            'strategy_version': self.strategy_version,
            'signal_source': self.signal_source,
            'rsi': self.rsi,
            'macd': self.macd,
            'bollinger_position': self.bollinger_position,
            'volume_ratio': self.volume_ratio,
            'signal_time': self.signal_time.strftime('%Y-%m-%d %H:%M:%S') if self.signal_time else None,
            'expire_time': self.expire_time.strftime('%Y-%m-%d %H:%M:%S') if self.expire_time else None,
            'process_time': self.process_time.strftime('%Y-%m-%d %H:%M:%S') if self.process_time else None,
            'executed_quantity': self.executed_quantity,
            'executed_price': float(self.executed_price) if self.executed_price else None,
            'trade_id': self.trade_id,
            'remarks': self.remarks,
            'risk_reward_ratio': self.get_risk_reward_ratio(),
            'is_expired': self.is_expired(),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }

    def is_pending(self):
        """检查信号是否待处理"""
        return self.status == self.STATUS_PENDING

    def is_processed(self):
        """检查信号是否已处理"""
        return self.status == self.STATUS_PROCESSED

    def is_cancelled(self):
        """检查信号是否已取消"""
        return self.status == self.STATUS_CANCELLED 