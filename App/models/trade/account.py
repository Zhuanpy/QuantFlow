"""
账户资金模型
用于管理账户资金信息
"""
from App.exts import db
from datetime import datetime
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


class Account(db.Model):
    """
    账户资金模型
    
    用于管理账户的资金信息
    """
    __tablename__ = 'accounts'
    __bind_key__ = 'quanttradingsystem'

    # 账户状态常量
    STATUS_ACTIVE = 'active'         # 活跃
    STATUS_SUSPENDED = 'suspended'   # 暂停
    STATUS_CLOSED = 'closed'         # 关闭

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 账户基本信息
    account_id = db.Column(db.String(50), unique=True, nullable=False, comment='账户ID')
    account_name = db.Column(db.String(100), nullable=False, comment='账户名称')
    account_type = db.Column(db.String(20), nullable=False, comment='账户类型')
    status = db.Column(db.String(20), default=STATUS_ACTIVE, comment='账户状态')
    
    # 资金信息
    total_balance = db.Column(db.Numeric(12, 2), default=0, comment='总余额')
    available_balance = db.Column(db.Numeric(12, 2), default=0, comment='可用余额')
    frozen_balance = db.Column(db.Numeric(12, 2), default=0, comment='冻结余额')
    
    # 持仓市值
    position_value = db.Column(db.Numeric(12, 2), default=0, comment='持仓市值')
    total_assets = db.Column(db.Numeric(12, 2), default=0, comment='总资产')
    
    # 盈亏信息
    total_pnl = db.Column(db.Numeric(12, 2), default=0, comment='总盈亏')
    realized_pnl = db.Column(db.Numeric(12, 2), default=0, comment='已实现盈亏')
    unrealized_pnl = db.Column(db.Numeric(12, 2), default=0, comment='未实现盈亏')
    
    # 收益率
    total_return_rate = db.Column(db.Float, default=0, comment='总收益率')
    daily_return_rate = db.Column(db.Float, default=0, comment='日收益率')
    
    # 风险指标
    max_drawdown = db.Column(db.Float, default=0, comment='最大回撤')
    sharpe_ratio = db.Column(db.Float, default=0, comment='夏普比率')
    volatility = db.Column(db.Float, default=0, comment='波动率')
    
    # 交易统计
    total_trades = db.Column(db.Integer, default=0, comment='总交易次数')
    winning_trades = db.Column(db.Integer, default=0, comment='盈利交易次数')
    losing_trades = db.Column(db.Integer, default=0, comment='亏损交易次数')
    win_rate = db.Column(db.Float, default=0, comment='胜率')
    
    # 风险控制
    max_position_ratio = db.Column(db.Float, default=0.1, comment='最大单股持仓比例')
    max_total_position_ratio = db.Column(db.Float, default=0.8, comment='最大总持仓比例')
    daily_loss_limit = db.Column(db.Numeric(12, 2), nullable=True, comment='日亏损限制')
    total_loss_limit = db.Column(db.Numeric(12, 2), nullable=True, comment='总亏损限制')
    
    # 时间信息
    last_trade_time = db.Column(db.DateTime, nullable=True, comment='最后交易时间')
    last_update_time = db.Column(db.DateTime, nullable=True, comment='最后更新时间')
    
    # 备注信息
    remarks = db.Column(db.Text, nullable=True, comment='备注')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        """返回对象的字符串表示"""
        return f"<Account(id={self.id}, account_id={self.account_id}, balance={self.total_balance})>"

    @classmethod
    def create_account_id(cls):
        """生成唯一的账户ID"""
        import uuid
        return f"ACC{datetime.now().strftime('%Y%m%d%H%M%S')}{str(uuid.uuid4())[:8]}"

    @classmethod
    def get_account_by_id(cls, account_id: str):
        """
        根据账户ID获取账户
        
        Args:
            account_id: 账户ID
            
        Returns:
            Account: 账户对象
        """
        return cls.query.filter_by(account_id=account_id).first()

    @classmethod
    def get_active_accounts(cls):
        """
        获取所有活跃账户
        
        Returns:
            List[Account]: 活跃账户列表
        """
        return cls.query.filter_by(status=cls.STATUS_ACTIVE).all()

    @classmethod
    def get_accounts_by_type(cls, account_type: str):
        """
        获取指定类型的账户
        
        Args:
            account_type: 账户类型
            
        Returns:
            List[Account]: 账户列表
        """
        return cls.query.filter_by(account_type=account_type).all()

    def deposit(self, amount: float, remarks: str = None):
        """
        存款
        
        Args:
            amount: 存款金额
            remarks: 备注
            
        Returns:
            bool: 操作是否成功
        """
        try:
            amount_decimal = Decimal(str(amount))
            self.total_balance += amount_decimal
            self.available_balance += amount_decimal
            self.total_assets += amount_decimal
            self.last_update_time = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            
            if remarks:
                self.remarks = f"存款: {amount}, 备注: {remarks}"
            
            db.session.commit()
            logger.info(f"成功存款到账户 {self.account_id}: {amount}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"存款时出错: {str(e)}")
            return False

    def withdraw(self, amount: float, remarks: str = None):
        """
        取款
        
        Args:
            amount: 取款金额
            remarks: 备注
            
        Returns:
            bool: 操作是否成功
        """
        try:
            amount_decimal = Decimal(str(amount))
            if amount_decimal > self.available_balance:
                logger.error(f"可用余额不足: 需要{amount}, 可用{self.available_balance}")
                return False
            
            self.total_balance -= amount_decimal
            self.available_balance -= amount_decimal
            self.total_assets -= amount_decimal
            self.last_update_time = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            
            if remarks:
                self.remarks = f"取款: {amount}, 备注: {remarks}"
            
            db.session.commit()
            logger.info(f"成功从账户 {self.account_id} 取款: {amount}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"取款时出错: {str(e)}")
            return False

    def freeze_balance(self, amount: float):
        """
        冻结资金
        
        Args:
            amount: 冻结金额
            
        Returns:
            bool: 操作是否成功
        """
        try:
            amount_decimal = Decimal(str(amount))
            if amount_decimal > self.available_balance:
                return False
            
            self.available_balance -= amount_decimal
            self.frozen_balance += amount_decimal
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"冻结资金时出错: {str(e)}")
            return False

    def unfreeze_balance(self, amount: float):
        """
        解冻资金
        
        Args:
            amount: 解冻金额
            
        Returns:
            bool: 操作是否成功
        """
        try:
            amount_decimal = Decimal(str(amount))
            if amount_decimal > self.frozen_balance:
                return False
            
            self.frozen_balance -= amount_decimal
            self.available_balance += amount_decimal
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"解冻资金时出错: {str(e)}")
            return False

    def update_position_value(self, position_value: float):
        """
        更新持仓市值
        
        Args:
            position_value: 持仓市值
            
        Returns:
            bool: 更新是否成功
        """
        try:
            position_value_decimal = Decimal(str(position_value))
            old_position_value = self.position_value
            self.position_value = position_value_decimal
            self.total_assets = self.total_balance + self.position_value
            
            # 更新未实现盈亏
            if old_position_value > 0:
                self.unrealized_pnl = self.position_value - old_position_value
            
            self.last_update_time = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新持仓市值时出错: {str(e)}")
            return False

    def update_trade_statistics(self, is_winning: bool, pnl: float):
        """
        更新交易统计
        
        Args:
            is_winning: 是否盈利
            pnl: 盈亏金额
            
        Returns:
            bool: 更新是否成功
        """
        try:
            pnl_decimal = Decimal(str(pnl))
            self.total_trades += 1
            
            if is_winning:
                self.winning_trades += 1
                self.realized_pnl += pnl_decimal
            else:
                self.losing_trades += 1
                self.realized_pnl += pnl_decimal
            
            self.total_pnl = self.realized_pnl + self.unrealized_pnl
            self.win_rate = self.winning_trades / self.total_trades if self.total_trades > 0 else 0
            
            self.last_trade_time = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新交易统计时出错: {str(e)}")
            return False

    def calculate_return_rate(self, initial_balance: float):
        """
        计算收益率
        
        Args:
            initial_balance: 初始余额
            
        Returns:
            float: 收益率
        """
        if initial_balance > 0:
            return float((self.total_assets - initial_balance) / initial_balance)
        return 0.0

    def check_risk_limits(self):
        """
        检查风险限制
        
        Returns:
            dict: 风险检查结果
        """
        risk_alerts = []
        
        # 检查日亏损限制
        if self.daily_loss_limit and self.total_pnl < -self.daily_loss_limit:
            risk_alerts.append(f"日亏损超过限制: {self.total_pnl}")
        
        # 检查总亏损限制
        if self.total_loss_limit and self.total_pnl < -self.total_loss_limit:
            risk_alerts.append(f"总亏损超过限制: {self.total_pnl}")
        
        # 检查持仓比例
        if self.total_assets > 0:
            position_ratio = float(self.position_value / self.total_assets)
            if position_ratio > self.max_total_position_ratio:
                risk_alerts.append(f"总持仓比例过高: {position_ratio:.2%}")
        
        return {
            'has_risk': len(risk_alerts) > 0,
            'alerts': risk_alerts
        }

    def to_dict(self):
        """
        转换为字典格式
        
        Returns:
            dict: 字典格式的数据
        """
        risk_check = self.check_risk_limits()
        return {
            'id': self.id,
            'account_id': self.account_id,
            'account_name': self.account_name,
            'account_type': self.account_type,
            'status': self.status,
            'total_balance': float(self.total_balance) if self.total_balance else None,
            'available_balance': float(self.available_balance) if self.available_balance else None,
            'frozen_balance': float(self.frozen_balance) if self.frozen_balance else None,
            'position_value': float(self.position_value) if self.position_value else None,
            'total_assets': float(self.total_assets) if self.total_assets else None,
            'total_pnl': float(self.total_pnl) if self.total_pnl else None,
            'realized_pnl': float(self.realized_pnl) if self.realized_pnl else None,
            'unrealized_pnl': float(self.unrealized_pnl) if self.unrealized_pnl else None,
            'total_return_rate': self.total_return_rate,
            'daily_return_rate': self.daily_return_rate,
            'max_drawdown': self.max_drawdown,
            'sharpe_ratio': self.sharpe_ratio,
            'volatility': self.volatility,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.win_rate,
            'max_position_ratio': self.max_position_ratio,
            'max_total_position_ratio': self.max_total_position_ratio,
            'daily_loss_limit': float(self.daily_loss_limit) if self.daily_loss_limit else None,
            'total_loss_limit': float(self.total_loss_limit) if self.total_loss_limit else None,
            'last_trade_time': self.last_trade_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_trade_time else None,
            'last_update_time': self.last_update_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_update_time else None,
            'remarks': self.remarks,
            'risk_alerts': risk_check['alerts'],
            'has_risk': risk_check['has_risk'],
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }

    def is_active(self):
        """检查账户是否活跃"""
        return self.status == self.STATUS_ACTIVE

    def is_suspended(self):
        """检查账户是否暂停"""
        return self.status == self.STATUS_SUSPENDED

    def is_closed(self):
        """检查账户是否关闭"""
        return self.status == self.STATUS_CLOSED

    def get_available_ratio(self):
        """
        获取可用资金比例
        
        Returns:
            float: 可用资金比例
        """
        if self.total_assets > 0:
            return float(self.available_balance / self.total_assets)
        return 0.0

    def get_position_ratio(self):
        """
        获取持仓比例
        
        Returns:
            float: 持仓比例
        """
        if self.total_assets > 0:
            return float(self.position_value / self.total_assets)
        return 0.0 