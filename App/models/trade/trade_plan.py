"""
交易计划模型
用于记录模拟交易和实盘交易的交易计划、止盈止损设置及交易结果
"""
from App.exts import db
from datetime import datetime
import logging
from decimal import Decimal
import uuid

logger = logging.getLogger(__name__)


class TradePlan(db.Model):
    """
    交易计划模型

    支持模拟交易和实盘交易，包含：
    - 预期入场价格、目标价格
    - 止盈止损设置
    - 风险收益比计算
    - 交易结果统计
    """
    __tablename__ = 'trade_plans'
    __bind_key__ = 'quanttradingsystem'

    # 交易模式常量
    MODE_SIMULATE = 'simulate'   # 模拟交易
    MODE_REAL = 'real'           # 实盘交易

    # 交易方向常量
    DIRECTION_LONG = 'long'      # 做多
    DIRECTION_SHORT = 'short'    # 做空

    # 计划状态常量
    STATUS_PLANNING = 'planning'     # 计划中
    STATUS_PENDING = 'pending'       # 待执行
    STATUS_ACTIVE = 'active'         # 持仓中
    STATUS_COMPLETED = 'completed'   # 已完成
    STATUS_CANCELLED = 'cancelled'   # 已取消
    STATUS_EXPIRED = 'expired'       # 已过期

    # 交易结果常量
    RESULT_WIN = 'win'           # 盈利
    RESULT_LOSS = 'loss'         # 亏损
    RESULT_BREAK_EVEN = 'break_even'  # 持平
    RESULT_PENDING = 'pending'   # 未结算

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')

    # 计划标识
    plan_id = db.Column(db.String(50), unique=True, nullable=False, comment='计划ID')

    # 基本信息
    stock_code = db.Column(db.String(10), nullable=False, index=True, comment='股票代码')
    stock_name = db.Column(db.String(100), nullable=False, comment='股票名称')
    trade_mode = db.Column(db.String(20), nullable=False, default=MODE_SIMULATE, comment='交易模式(simulate/real)')
    direction = db.Column(db.String(20), nullable=False, default=DIRECTION_LONG, comment='交易方向(long/short)')
    status = db.Column(db.String(20), default=STATUS_PLANNING, comment='计划状态')

    # 价格设置
    entry_price = db.Column(db.Numeric(10, 3), nullable=False, comment='预期入场价格')
    target_price = db.Column(db.Numeric(10, 3), nullable=True, comment='目标价格')
    stop_loss_price = db.Column(db.Numeric(10, 3), nullable=True, comment='止损价格')
    take_profit_price = db.Column(db.Numeric(10, 3), nullable=True, comment='止盈价格')
    current_price = db.Column(db.Numeric(10, 3), nullable=True, comment='当前价格')

    # 数量金额
    quantity = db.Column(db.Integer, nullable=False, default=100, comment='交易数量(股)')
    position_ratio = db.Column(db.Float, nullable=True, comment='仓位比例(%)')
    total_amount = db.Column(db.Numeric(12, 2), nullable=True, comment='交易总金额')

    # 风险指标
    risk_reward_ratio = db.Column(db.Float, nullable=True, comment='风险收益比')
    max_loss_amount = db.Column(db.Numeric(12, 2), nullable=True, comment='最大亏损金额')
    potential_profit = db.Column(db.Numeric(12, 2), nullable=True, comment='潜在盈利金额')
    stop_loss_pct = db.Column(db.Float, nullable=True, comment='止损比例(%)')
    take_profit_pct = db.Column(db.Float, nullable=True, comment='止盈比例(%)')

    # 实际交易结果
    actual_entry_price = db.Column(db.Numeric(10, 3), nullable=True, comment='实际入场价格')
    actual_exit_price = db.Column(db.Numeric(10, 3), nullable=True, comment='实际出场价格')
    actual_quantity = db.Column(db.Integer, nullable=True, comment='实际交易数量')

    # 盈亏统计
    profit_loss = db.Column(db.Numeric(12, 2), nullable=True, comment='盈亏金额')
    profit_loss_pct = db.Column(db.Float, nullable=True, comment='盈亏比例(%)')
    result = db.Column(db.String(20), default=RESULT_PENDING, comment='交易结果(win/loss/break_even)')

    # 时间信息
    plan_time = db.Column(db.DateTime, default=datetime.utcnow, comment='计划创建时间')
    entry_time = db.Column(db.DateTime, nullable=True, comment='入场时间')
    exit_time = db.Column(db.DateTime, nullable=True, comment='出场时间')
    expire_time = db.Column(db.DateTime, nullable=True, comment='计划过期时间')

    # 交易原因和分析
    entry_reason = db.Column(db.Text, nullable=True, comment='入场理由')
    exit_reason = db.Column(db.Text, nullable=True, comment='出场理由')
    strategy_name = db.Column(db.String(100), nullable=True, comment='使用策略')
    notes = db.Column(db.Text, nullable=True, comment='备注')

    # 手续费
    commission = db.Column(db.Numeric(10, 2), default=0, comment='手续费')
    tax = db.Column(db.Numeric(10, 2), default=0, comment='税费')

    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f"<TradePlan(id={self.id}, plan_id={self.plan_id}, stock={self.stock_code}, mode={self.trade_mode})>"

    @classmethod
    def create_plan_id(cls):
        """生成唯一的计划ID"""
        return f"TP{datetime.now().strftime('%Y%m%d%H%M%S')}{str(uuid.uuid4())[:6].upper()}"

    def calculate_risk_metrics(self):
        """
        计算风险指标

        自动计算：
        - 风险收益比
        - 最大亏损金额
        - 潜在盈利金额
        - 止损/止盈比例
        """
        if not self.entry_price or self.entry_price == 0:
            return

        entry = float(self.entry_price)

        # 计算止损比例和最大亏损
        if self.stop_loss_price:
            stop_loss = float(self.stop_loss_price)
            if self.direction == self.DIRECTION_LONG:
                self.stop_loss_pct = round((entry - stop_loss) / entry * 100, 2)
            else:
                self.stop_loss_pct = round((stop_loss - entry) / entry * 100, 2)
            self.max_loss_amount = Decimal(str(abs(entry - stop_loss) * self.quantity))

        # 计算止盈比例和潜在盈利
        if self.take_profit_price:
            take_profit = float(self.take_profit_price)
            if self.direction == self.DIRECTION_LONG:
                self.take_profit_pct = round((take_profit - entry) / entry * 100, 2)
            else:
                self.take_profit_pct = round((entry - take_profit) / entry * 100, 2)
            self.potential_profit = Decimal(str(abs(take_profit - entry) * self.quantity))

        # 计算风险收益比
        if self.max_loss_amount and self.max_loss_amount > 0 and self.potential_profit:
            self.risk_reward_ratio = round(float(self.potential_profit) / float(self.max_loss_amount), 2)

        # 计算交易总金额
        self.total_amount = Decimal(str(entry * self.quantity))

    def enter_trade(self, actual_price: float = None, actual_quantity: int = None, entry_time: datetime = None):
        """
        执行入场

        Args:
            actual_price: 实际入场价格
            actual_quantity: 实际交易数量
            entry_time: 入场时间
        """
        try:
            self.actual_entry_price = Decimal(str(actual_price)) if actual_price else self.entry_price
            self.actual_quantity = actual_quantity if actual_quantity else self.quantity
            self.entry_time = entry_time or datetime.utcnow()
            self.status = self.STATUS_ACTIVE
            self.updated_at = datetime.utcnow()

            db.session.commit()
            logger.info(f"交易计划 {self.plan_id} 已入场")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"入场失败: {str(e)}")
            return False

    def exit_trade(self, exit_price: float, exit_reason: str = None, exit_time: datetime = None):
        """
        执行出场并计算盈亏

        Args:
            exit_price: 出场价格
            exit_reason: 出场原因
            exit_time: 出场时间
        """
        try:
            self.actual_exit_price = Decimal(str(exit_price))
            self.exit_time = exit_time or datetime.utcnow()
            if exit_reason:
                self.exit_reason = exit_reason

            # 计算盈亏
            entry = float(self.actual_entry_price or self.entry_price)
            exit_p = float(exit_price)
            qty = self.actual_quantity or self.quantity

            if self.direction == self.DIRECTION_LONG:
                self.profit_loss = Decimal(str((exit_p - entry) * qty))
                self.profit_loss_pct = round((exit_p - entry) / entry * 100, 2)
            else:
                self.profit_loss = Decimal(str((entry - exit_p) * qty))
                self.profit_loss_pct = round((entry - exit_p) / entry * 100, 2)

            # 扣除手续费和税费
            if self.commission:
                self.profit_loss -= self.commission
            if self.tax:
                self.profit_loss -= self.tax

            # 判断交易结果
            if self.profit_loss > 0:
                self.result = self.RESULT_WIN
            elif self.profit_loss < 0:
                self.result = self.RESULT_LOSS
            else:
                self.result = self.RESULT_BREAK_EVEN

            self.status = self.STATUS_COMPLETED
            self.updated_at = datetime.utcnow()

            db.session.commit()
            logger.info(f"交易计划 {self.plan_id} 已出场，盈亏: {self.profit_loss}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"出场失败: {str(e)}")
            return False

    def cancel(self, reason: str = None):
        """取消交易计划"""
        try:
            self.status = self.STATUS_CANCELLED
            if reason:
                self.notes = f"取消原因: {reason}"
            self.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"交易计划 {self.plan_id} 已取消")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"取消失败: {str(e)}")
            return False

    @classmethod
    def get_statistics(cls, trade_mode: str = None, start_date: datetime = None, end_date: datetime = None):
        """
        获取交易统计

        Args:
            trade_mode: 交易模式过滤
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            dict: 统计结果
        """
        query = cls.query.filter(cls.status == cls.STATUS_COMPLETED)

        if trade_mode:
            query = query.filter(cls.trade_mode == trade_mode)
        if start_date:
            query = query.filter(cls.exit_time >= start_date)
        if end_date:
            query = query.filter(cls.exit_time <= end_date)

        completed_trades = query.all()

        if not completed_trades:
            return {
                'total_trades': 0,
                'win_count': 0,
                'loss_count': 0,
                'win_rate': 0,
                'total_profit_loss': 0,
                'avg_profit': 0,
                'avg_loss': 0,
                'max_profit': 0,
                'max_loss': 0,
                'profit_factor': 0
            }

        total = len(completed_trades)
        wins = [t for t in completed_trades if t.result == cls.RESULT_WIN]
        losses = [t for t in completed_trades if t.result == cls.RESULT_LOSS]

        total_profit = sum(float(t.profit_loss) for t in wins) if wins else 0
        total_loss = abs(sum(float(t.profit_loss) for t in losses)) if losses else 0

        return {
            'total_trades': total,
            'win_count': len(wins),
            'loss_count': len(losses),
            'break_even_count': total - len(wins) - len(losses),
            'win_rate': round(len(wins) / total * 100, 2) if total > 0 else 0,
            'total_profit_loss': sum(float(t.profit_loss) for t in completed_trades),
            'avg_profit': round(total_profit / len(wins), 2) if wins else 0,
            'avg_loss': round(total_loss / len(losses), 2) if losses else 0,
            'max_profit': max(float(t.profit_loss) for t in wins) if wins else 0,
            'max_loss': min(float(t.profit_loss) for t in losses) if losses else 0,
            'profit_factor': round(total_profit / total_loss, 2) if total_loss > 0 else 0
        }

    @classmethod
    def get_by_mode(cls, trade_mode: str, status: str = None, limit: int = 100):
        """获取指定模式的交易计划"""
        query = cls.query.filter(cls.trade_mode == trade_mode)
        if status:
            query = query.filter(cls.status == status)
        return query.order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_active_plans(cls, trade_mode: str = None):
        """获取活跃的交易计划（持仓中）"""
        query = cls.query.filter(cls.status == cls.STATUS_ACTIVE)
        if trade_mode:
            query = query.filter(cls.trade_mode == trade_mode)
        return query.order_by(cls.entry_time.desc()).all()

    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'plan_id': self.plan_id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'trade_mode': self.trade_mode,
            'direction': self.direction,
            'status': self.status,
            'entry_price': float(self.entry_price) if self.entry_price else None,
            'target_price': float(self.target_price) if self.target_price else None,
            'stop_loss_price': float(self.stop_loss_price) if self.stop_loss_price else None,
            'take_profit_price': float(self.take_profit_price) if self.take_profit_price else None,
            'current_price': float(self.current_price) if self.current_price else None,
            'quantity': self.quantity,
            'position_ratio': self.position_ratio,
            'total_amount': float(self.total_amount) if self.total_amount else None,
            'risk_reward_ratio': self.risk_reward_ratio,
            'max_loss_amount': float(self.max_loss_amount) if self.max_loss_amount else None,
            'potential_profit': float(self.potential_profit) if self.potential_profit else None,
            'stop_loss_pct': self.stop_loss_pct,
            'take_profit_pct': self.take_profit_pct,
            'actual_entry_price': float(self.actual_entry_price) if self.actual_entry_price else None,
            'actual_exit_price': float(self.actual_exit_price) if self.actual_exit_price else None,
            'actual_quantity': self.actual_quantity,
            'profit_loss': float(self.profit_loss) if self.profit_loss else None,
            'profit_loss_pct': self.profit_loss_pct,
            'result': self.result,
            'plan_time': self.plan_time.strftime('%Y-%m-%d %H:%M:%S') if self.plan_time else None,
            'entry_time': self.entry_time.strftime('%Y-%m-%d %H:%M:%S') if self.entry_time else None,
            'exit_time': self.exit_time.strftime('%Y-%m-%d %H:%M:%S') if self.exit_time else None,
            'entry_reason': self.entry_reason,
            'exit_reason': self.exit_reason,
            'strategy_name': self.strategy_name,
            'notes': self.notes,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }
