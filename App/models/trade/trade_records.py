"""
交易记录模型
用于记录所有交易操作和结果
"""
from App.exts import db
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from datetime import datetime
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


class TradeRecord(db.Model):
    """
    交易记录模型
    
    用于记录所有买入、卖出等交易操作
    """
    __tablename__ = 'trade_records'
    __bind_key__ = 'quanttradingsystem'

    # 交易类型常量
    TRADE_TYPE_BUY = 'buy'           # 买入
    TRADE_TYPE_SELL = 'sell'         # 卖出
    TRADE_TYPE_BUY_BACK = 'buy_back' # 回购
    TRADE_TYPE_SHORT = 'short'       # 做空
    TRADE_TYPE_COVER = 'cover'       # 平仓

    # 交易状态常量
    STATUS_PENDING = 'pending'       # 待执行
    STATUS_EXECUTED = 'executed'     # 已执行
    STATUS_CANCELLED = 'cancelled'   # 已取消
    STATUS_FAILED = 'failed'         # 执行失败
    STATUS_PARTIAL = 'partial'       # 部分执行

    # 交易模式常量（与 TradePlan 对齐）
    MODE_SIMULATE = 'simulate'       # 模拟交易
    MODE_REAL = 'real'               # 实盘交易

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 交易基本信息
    trade_id = db.Column(db.String(50), unique=True, nullable=False, comment='交易ID')
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(100), nullable=False, comment='股票名称')
    trade_type = db.Column(db.String(20), nullable=False, comment='交易类型')
    trade_mode = db.Column(db.String(20), nullable=False, default=MODE_REAL,
                           comment='交易模式(simulate/real)，与 trade_plans.trade_mode 对齐')
    status = db.Column(db.String(20), default=STATUS_PENDING, comment='交易状态')
    
    # 交易数量和价格
    quantity = db.Column(db.Integer, nullable=False, comment='交易数量')
    price = db.Column(db.Numeric(10, 2), nullable=False, comment='交易价格')
    total_amount = db.Column(db.Numeric(12, 2), nullable=False, comment='交易总金额')
    
    # 手续费和税费
    commission = db.Column(db.Numeric(10, 2), default=0, comment='手续费/佣金')
    tax = db.Column(db.Numeric(10, 2), default=0, comment='印花税（卖出收 0.1%）')
    other_fee = db.Column(db.Numeric(10, 2), default=0, comment='其他杂费')
    transfer_fee = db.Column(db.Numeric(10, 2), default=0, comment='过户费（沪市股票）')
    # net_amount 改为"带符号净现金流"：买入负、卖出正，与同花顺"发生金额"对齐
    net_amount = db.Column(db.Numeric(14, 2), nullable=False,
                           comment='账户净现金流：买入=-(成交金额+各项费用)，卖出=成交金额-各项费用')

    # 同花顺特有字段（导入功能使用）
    account_balance = db.Column(db.Numeric(14, 2), nullable=True, comment='成交后账户余额（同花顺"本次金额"）')
    contract_no = db.Column(db.String(40), nullable=True, comment='合同编号（同花顺内部订单ID）')
    market = db.Column(db.String(20), nullable=True, comment='交易市场：深圳A股/上海A股')
    
    # 策略信息
    strategy_name = db.Column(db.String(100), nullable=True, comment='策略名称')
    signal_source = db.Column(db.String(50), nullable=True, comment='信号来源')
    confidence_score = db.Column(db.Float, nullable=True, comment='置信度分数')
    
    # 时间信息
    order_time = db.Column(db.DateTime, nullable=False, comment='下单时间')
    execute_time = db.Column(db.DateTime, nullable=True, comment='执行时间')
    cancel_time = db.Column(db.DateTime, nullable=True, comment='取消时间')
    
    # 操作理由/想法（手动填写，复盘用；与系统自动写入的 remarks 区分开）
    # 拆成四段：列表只显示 reason_title 一行 + 标签，正文/感悟点进详情页看，避免单元格把行撑高。
    reason_title = db.Column(db.String(200), nullable=True, comment='操作理由一句话摘要(列表单行显示)')
    reason_tags = db.Column(db.String(200), nullable=True, comment='标签，逗号分隔(如 低吸,板块轮动)')
    reason_insight = db.Column(db.Text, nullable=True, comment='感悟：这笔交易给我的灵感/教训')
    # 详细正文：Quill 富文本 HTML；MEDIUMTEXT 是因为贴截图的 base64 会撑爆 TEXT 的 64KB
    trade_reason = db.Column(MEDIUMTEXT, nullable=True, comment='操作理由详细正文(富文本HTML)')

    # 备注信息
    remarks = db.Column(db.Text, nullable=True, comment='备注')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        """返回对象的字符串表示"""
        return f"<TradeRecord(id={self.id}, trade_id={self.trade_id}, stock_code={self.stock_code}, type={self.trade_type})>"

    @classmethod
    def create_trade_id(cls):
        """生成唯一的交易ID"""
        import uuid
        return f"TR{datetime.now().strftime('%Y%m%d%H%M%S')}{str(uuid.uuid4())[:8]}"

    @classmethod
    def get_trades_by_stock(cls, stock_code: str, limit: int = 100):
        """
        获取指定股票的交易记录
        
        Args:
            stock_code: 股票代码
            limit: 限制数量
            
        Returns:
            List[TradeRecord]: 交易记录列表
        """
        return cls.query.filter_by(stock_code=stock_code).order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_trades_by_type(cls, trade_type: str, limit: int = 100):
        """
        获取指定类型的交易记录
        
        Args:
            trade_type: 交易类型
            limit: 限制数量
            
        Returns:
            List[TradeRecord]: 交易记录列表
        """
        return cls.query.filter_by(trade_type=trade_type).order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_trades_by_status(cls, status: str, limit: int = 100):
        """
        获取指定状态的交易记录
        
        Args:
            status: 交易状态
            limit: 限制数量
            
        Returns:
            List[TradeRecord]: 交易记录列表
        """
        return cls.query.filter_by(status=status).order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_trades_by_date_range(cls, start_date: datetime, end_date: datetime):
        """
        获取指定日期范围的交易记录
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            List[TradeRecord]: 交易记录列表
        """
        return cls.query.filter(
            cls.created_at >= start_date,
            cls.created_at <= end_date
        ).order_by(cls.created_at.desc()).all()

    def execute_trade(self, execute_price: float = None, execute_time: datetime = None):
        """
        执行交易
        
        Args:
            execute_price: 执行价格，如果为None则使用订单价格
            execute_time: 执行时间，如果为None则使用当前时间
            
        Returns:
            bool: 执行是否成功
        """
        try:
            if execute_price:
                self.price = Decimal(str(execute_price))
                self.total_amount = self.price * self.quantity
                self.net_amount = self.total_amount - self.commission - self.tax
            
            self.status = self.STATUS_EXECUTED
            self.execute_time = execute_time or datetime.utcnow()
            self.updated_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"成功执行交易 {self.trade_id}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"执行交易时出错: {str(e)}")
            return False

    def cancel_trade(self, reason: str = None):
        """
        取消交易
        
        Args:
            reason: 取消原因
            
        Returns:
            bool: 取消是否成功
        """
        try:
            self.status = self.STATUS_CANCELLED
            self.cancel_time = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            if reason:
                self.remarks = f"取消原因: {reason}"
            
            db.session.commit()
            logger.info(f"成功取消交易 {self.trade_id}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"取消交易时出错: {str(e)}")
            return False

    def to_dict(self):
        """
        转换为字典格式
        
        Returns:
            dict: 字典格式的数据
        """
        return {
            'id': self.id,
            'trade_id': self.trade_id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'trade_type': self.trade_type,
            'trade_mode': self.trade_mode,
            'status': self.status,
            'quantity': self.quantity,
            'price': float(self.price) if self.price else None,
            'total_amount': float(self.total_amount) if self.total_amount else None,
            'commission': float(self.commission) if self.commission else None,
            'tax': float(self.tax) if self.tax else None,
            'other_fee': float(self.other_fee) if self.other_fee else None,
            'transfer_fee': float(self.transfer_fee) if self.transfer_fee else None,
            'net_amount': float(self.net_amount) if self.net_amount else None,
            'account_balance': float(self.account_balance) if self.account_balance else None,
            'contract_no': self.contract_no,
            'market': self.market,
            'strategy_name': self.strategy_name,
            'signal_source': self.signal_source,
            'confidence_score': self.confidence_score,
            'trade_reason': self.trade_reason,
            'reason_title': self.reason_title,
            'reason_tags': self.reason_tags,
            'reason_tag_list': self.get_reason_tags(),
            'reason_insight': self.reason_insight,
            'has_reason_detail': bool((self.trade_reason or '').strip() or (self.reason_insight or '').strip()),
            'order_time': self.order_time.strftime('%Y-%m-%d %H:%M:%S') if self.order_time else None,
            'execute_time': self.execute_time.strftime('%Y-%m-%d %H:%M:%S') if self.execute_time else None,
            'cancel_time': self.cancel_time.strftime('%Y-%m-%d %H:%M:%S') if self.cancel_time else None,
            'remarks': self.remarks,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }

    # ---------- 操作理由：标签 ----------
    @staticmethod
    def normalize_tags(raw) -> str:
        """把标签统一成 '低吸,板块轮动' 这种逗号分隔的规范串。

        接受 list 或字符串（中英文逗号/顿号/空格分隔都吃），去空白、去重、保序。
        存成规范串是为了标签能被聚合统计——否则「低吸」「低吸 」「低吸,」会被当成三个标签。
        """
        if raw is None:
            return ''
        if isinstance(raw, str):
            import re
            parts = re.split(r'[,，、;；\s]+', raw)
        else:
            parts = list(raw)

        seen, out = set(), []
        for p in parts:
            t = str(p).strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return ','.join(out)

    def get_reason_tags(self) -> list:
        """标签列表；无标签返回 []"""
        return [t for t in (self.reason_tags or '').split(',') if t.strip()]

    @classmethod
    def all_reason_tags(cls, trade_mode: str = None) -> list:
        """已用过的标签及其次数，按次数倒序。

        用途：编辑页把历史标签列出来供点选，避免「低吸」「低吸买入」「低位吸筹」这类
        同义标签泛滥，让标签事后还能拿来做统计。
        """
        q = cls.query.filter(cls.reason_tags.isnot(None), cls.reason_tags != '')
        if trade_mode:
            q = q.filter(cls.trade_mode == trade_mode)

        counter = {}
        for (tags,) in q.with_entities(cls.reason_tags).all():
            for t in (tags or '').split(','):
                t = t.strip()
                if t:
                    counter[t] = counter.get(t, 0) + 1
        return [{'tag': t, 'count': c}
                for t, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    def is_buy_trade(self):
        """检查是否为买入交易"""
        return self.trade_type in [self.TRADE_TYPE_BUY, self.TRADE_TYPE_BUY_BACK]

    def is_sell_trade(self):
        """检查是否为卖出交易"""
        return self.trade_type in [self.TRADE_TYPE_SELL, self.TRADE_TYPE_SHORT, self.TRADE_TYPE_COVER]

    def is_executed(self):
        """检查交易是否已执行"""
        return self.status == self.STATUS_EXECUTED

    def is_cancelled(self):
        """检查交易是否已取消"""
        return self.status == self.STATUS_CANCELLED 