"""
持仓模型
用于管理当前持仓信息
"""
from App.exts import db
from datetime import datetime
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


class Position(db.Model):
    """
    持仓模型
    
    用于记录和管理当前持仓信息
    """
    __tablename__ = 'positions'
    __bind_key__ = 'quanttradingsystem'

    # 持仓类型常量
    POSITION_TYPE_LONG = 'long'       # 多头持仓
    POSITION_TYPE_SHORT = 'short'     # 空头持仓

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 股票基本信息
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(100), nullable=False, comment='股票名称')
    position_type = db.Column(db.String(10), default=POSITION_TYPE_LONG, comment='持仓类型')
    
    # 持仓数量
    total_quantity = db.Column(db.Integer, default=0, comment='总持仓数量')
    available_quantity = db.Column(db.Integer, default=0, comment='可用数量')
    frozen_quantity = db.Column(db.Integer, default=0, comment='冻结数量')
    
    # 成本信息
    avg_cost = db.Column(db.Numeric(10, 4), default=0, comment='平均成本')
    total_cost = db.Column(db.Numeric(12, 2), default=0, comment='总成本')
    
    # 市值信息
    current_price = db.Column(db.Numeric(10, 2), default=0, comment='当前价格')
    market_value = db.Column(db.Numeric(12, 2), default=0, comment='市值')
    unrealized_pnl = db.Column(db.Numeric(12, 2), default=0, comment='未实现盈亏')
    realized_pnl = db.Column(db.Numeric(12, 2), default=0, comment='已实现盈亏')
    
    # 收益率
    total_return_rate = db.Column(db.Float, default=0, comment='总收益率')
    unrealized_return_rate = db.Column(db.Float, default=0, comment='未实现收益率')
    
    # 交易信息
    last_trade_time = db.Column(db.DateTime, nullable=True, comment='最后交易时间')
    last_trade_price = db.Column(db.Numeric(10, 2), nullable=True, comment='最后交易价格')
    
    # 策略信息
    strategy_name = db.Column(db.String(100), nullable=True, comment='策略名称')
    entry_reason = db.Column(db.Text, nullable=True, comment='入场原因')
    
    # 风险控制
    stop_loss_price = db.Column(db.Numeric(10, 2), nullable=True, comment='止损价格')
    take_profit_price = db.Column(db.Numeric(10, 2), nullable=True, comment='止盈价格')
    max_position_value = db.Column(db.Numeric(12, 2), nullable=True, comment='最大持仓金额')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        """返回对象的字符串表示"""
        return f"<Position(id={self.id}, stock_code={self.stock_code}, quantity={self.total_quantity})>"

    @classmethod
    def get_position_by_stock(cls, stock_code: str):
        """
        获取指定股票的持仓
        
        Args:
            stock_code: 股票代码
            
        Returns:
            Position: 持仓对象
        """
        return cls.query.filter_by(stock_code=stock_code).first()

    @classmethod
    def get_all_positions(cls):
        """
        获取所有持仓
        
        Returns:
            List[Position]: 持仓列表
        """
        return cls.query.filter(cls.total_quantity > 0).all()

    @classmethod
    def get_positions_by_type(cls, position_type: str):
        """
        获取指定类型的持仓
        
        Args:
            position_type: 持仓类型
            
        Returns:
            List[Position]: 持仓列表
        """
        return cls.query.filter_by(position_type=position_type).filter(cls.total_quantity > 0).all()

    @classmethod
    def get_profitable_positions(cls):
        """
        获取盈利的持仓
        
        Returns:
            List[Position]: 盈利持仓列表
        """
        return cls.query.filter(cls.unrealized_pnl > 0).all()

    @classmethod
    def get_losing_positions(cls):
        """
        获取亏损的持仓
        
        Returns:
            List[Position]: 亏损持仓列表
        """
        return cls.query.filter(cls.unrealized_pnl < 0).all()

    def update_price(self, current_price: float):
        """
        更新当前价格并重新计算相关指标
        
        Args:
            current_price: 当前价格
            
        Returns:
            bool: 更新是否成功
        """
        try:
            self.current_price = Decimal(str(current_price))
            self.market_value = self.current_price * self.total_quantity
            
            if self.total_cost > 0:
                self.unrealized_pnl = self.market_value - self.total_cost
                self.unrealized_return_rate = float(self.unrealized_pnl / self.total_cost)
                self.total_return_rate = float((self.unrealized_pnl + self.realized_pnl) / self.total_cost)
            
            self.updated_at = datetime.utcnow()
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新价格时出错: {str(e)}")
            return False

    def add_position(self, quantity: int, price: float, trade_time: datetime = None):
        """
        增加持仓
        
        Args:
            quantity: 增加数量
            price: 价格
            trade_time: 交易时间
            
        Returns:
            bool: 操作是否成功
        """
        try:
            if self.total_quantity == 0:
                # 新建持仓
                self.avg_cost = Decimal(str(price))
                self.total_cost = Decimal(str(price)) * quantity
            else:
                # 追加持仓，重新计算平均成本
                total_value = self.total_cost + Decimal(str(price)) * quantity
                self.total_quantity += quantity
                self.avg_cost = total_value / self.total_quantity
                self.total_cost = total_value
            
            self.total_quantity += quantity
            self.available_quantity += quantity
            self.last_trade_time = trade_time or datetime.utcnow()
            self.last_trade_price = Decimal(str(price))
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"成功增加持仓 {self.stock_code}: +{quantity} @ {price}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"增加持仓时出错: {str(e)}")
            return False

    def reduce_position(self, quantity: int, price: float, trade_time: datetime = None):
        """
        减少持仓
        
        Args:
            quantity: 减少数量
            price: 价格
            trade_time: 交易时间
            
        Returns:
            bool: 操作是否成功
        """
        try:
            if quantity > self.available_quantity:
                logger.error(f"可用数量不足: 需要{quantity}, 可用{self.available_quantity}")
                return False
            
            # 计算已实现盈亏
            realized_pnl = (Decimal(str(price)) - self.avg_cost) * quantity
            self.realized_pnl += realized_pnl
            
            self.total_quantity -= quantity
            self.available_quantity -= quantity
            
            if self.total_quantity == 0:
                # 清仓
                self.avg_cost = Decimal('0')
                self.total_cost = Decimal('0')
                self.market_value = Decimal('0')
                self.unrealized_pnl = Decimal('0')
            
            self.last_trade_time = trade_time or datetime.utcnow()
            self.last_trade_price = Decimal(str(price))
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"成功减少持仓 {self.stock_code}: -{quantity} @ {price}, 已实现盈亏: {realized_pnl}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"减少持仓时出错: {str(e)}")
            return False

    def freeze_quantity(self, quantity: int):
        """
        冻结数量
        
        Args:
            quantity: 冻结数量
            
        Returns:
            bool: 操作是否成功
        """
        try:
            if quantity > self.available_quantity:
                return False
            
            self.available_quantity -= quantity
            self.frozen_quantity += quantity
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"冻结数量时出错: {str(e)}")
            return False

    def unfreeze_quantity(self, quantity: int):
        """
        解冻数量
        
        Args:
            quantity: 解冻数量
            
        Returns:
            bool: 操作是否成功
        """
        try:
            if quantity > self.frozen_quantity:
                return False
            
            self.frozen_quantity -= quantity
            self.available_quantity += quantity
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"解冻数量时出错: {str(e)}")
            return False

    def set_stop_loss(self, price: float):
        """
        设置止损价格
        
        Args:
            price: 止损价格
            
        Returns:
            bool: 设置是否成功
        """
        try:
            self.stop_loss_price = Decimal(str(price))
            self.updated_at = datetime.utcnow()
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"设置止损价格时出错: {str(e)}")
            return False

    def set_take_profit(self, price: float):
        """
        设置止盈价格
        
        Args:
            price: 止盈价格
            
        Returns:
            bool: 设置是否成功
        """
        try:
            self.take_profit_price = Decimal(str(price))
            self.updated_at = datetime.utcnow()
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"设置止盈价格时出错: {str(e)}")
            return False

    def check_stop_loss(self):
        """
        检查是否触发止损
        
        Returns:
            bool: 是否触发止损
        """
        if self.stop_loss_price and self.current_price:
            if self.position_type == self.POSITION_TYPE_LONG:
                return self.current_price <= self.stop_loss_price
            else:
                return self.current_price >= self.stop_loss_price
        return False

    def check_take_profit(self):
        """
        检查是否触发止盈
        
        Returns:
            bool: 是否触发止盈
        """
        if self.take_profit_price and self.current_price:
            if self.position_type == self.POSITION_TYPE_LONG:
                return self.current_price >= self.take_profit_price
            else:
                return self.current_price <= self.take_profit_price
        return False

    def to_dict(self):
        """
        转换为字典格式
        
        Returns:
            dict: 字典格式的数据
        """
        return {
            'id': self.id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'position_type': self.position_type,
            'total_quantity': self.total_quantity,
            'available_quantity': self.available_quantity,
            'frozen_quantity': self.frozen_quantity,
            'avg_cost': float(self.avg_cost) if self.avg_cost else None,
            'total_cost': float(self.total_cost) if self.total_cost else None,
            'current_price': float(self.current_price) if self.current_price else None,
            'market_value': float(self.market_value) if self.market_value else None,
            'unrealized_pnl': float(self.unrealized_pnl) if self.unrealized_pnl else None,
            'realized_pnl': float(self.realized_pnl) if self.realized_pnl else None,
            'total_return_rate': self.total_return_rate,
            'unrealized_return_rate': self.unrealized_return_rate,
            'last_trade_time': self.last_trade_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_trade_time else None,
            'last_trade_price': float(self.last_trade_price) if self.last_trade_price else None,
            'strategy_name': self.strategy_name,
            'entry_reason': self.entry_reason,
            'stop_loss_price': float(self.stop_loss_price) if self.stop_loss_price else None,
            'take_profit_price': float(self.take_profit_price) if self.take_profit_price else None,
            'max_position_value': float(self.max_position_value) if self.max_position_value else None,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }

    def is_long_position(self):
        """检查是否为多头持仓"""
        return self.position_type == self.POSITION_TYPE_LONG

    def is_short_position(self):
        """检查是否为空头持仓"""
        return self.position_type == self.POSITION_TYPE_SHORT

    def is_profitable(self):
        """检查是否盈利"""
        return self.unrealized_pnl > 0

    def is_losing(self):
        """检查是否亏损"""
        return self.unrealized_pnl < 0

    def get_position_ratio(self, total_value: float):
        """
        获取持仓比例
        
        Args:
            total_value: 总资产价值
            
        Returns:
            float: 持仓比例
        """
        if total_value > 0 and self.market_value:
            return float(self.market_value / total_value)
        return 0.0 