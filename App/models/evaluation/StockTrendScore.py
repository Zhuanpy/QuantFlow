"""
个股趋势打分数据模型

结构与字段含义与 BoardTrendScore 完全对齐，仅把主体由"板块"换成"个股"。
一个个股在一个交易日只允许有一条记录（stock_code + record_date 唯一）。
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


TREND_STAGES = (
    'up_early',
    'up_late',
    'down_early',
    'down_late',
    'unknown',
)

TREND_STRENGTHS = ('strong', 'medium', 'weak', 'none')

SIGNALS = ('buy', 'hold', 'sell', 'wait', 'none')


class StockTrendScore(db.Model):
    __tablename__ = 'eval_stock_trend_score'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('stock_code', 'record_date', name='uix_stock_date'),
        db.Index('ix_stock_trend_date', 'record_date'),
        db.Index('ix_stock_trend_stage', 'trend_stage'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')

    stock_code = db.Column(db.String(20), nullable=False, comment='股票代码（如 000001）')
    stock_name = db.Column(db.String(50), nullable=False, comment='股票名称')
    record_date = db.Column(db.Date, nullable=False, comment='记录日期')

    trend_stage = db.Column(db.String(20), default='unknown',
                            comment='趋势阶段 up_early/up_late/down_early/down_late/unknown')
    trend_stage_confidence = db.Column(db.Float, default=0.0, comment='阶段判定置信度 0-100')
    trend_strength = db.Column(db.String(10), default='none',
                               comment='趋势强度 strong/medium/weak/none')
    signal = db.Column(db.String(10), default='none',
                       comment='交易信号 buy/hold/sell/wait/none')

    price_structure_score = db.Column(db.Float, comment='价格结构分（高低点/突破）')
    ma_score = db.Column(db.Float, comment='均线系统分（排列/斜率）')
    macd_score = db.Column(db.Float, comment='MACD 分（柱体/零轴/背离）')
    volume_score = db.Column(db.Float, comment='量能配合分')
    momentum_score = db.Column(db.Float, comment='动量分（RSI 等）')
    volatility_score = db.Column(db.Float, comment='波动率分（ATR）')

    total_score = db.Column(db.Float, comment='综合趋势分 0-100')

    close = db.Column(db.Float, comment='收盘价')
    change_pct = db.Column(db.Float, comment='涨跌幅%')
    ma20 = db.Column(db.Float, comment='MA20')
    ma60 = db.Column(db.Float, comment='MA60')
    macd_dif = db.Column(db.Float, comment='MACD DIF')
    macd_dea = db.Column(db.Float, comment='MACD DEA')
    macd_bar = db.Column(db.Float, comment='MACD 柱体')
    atr = db.Column(db.Float, comment='ATR')

    formula_version = db.Column(db.String(20), default='v0',
                                comment='打分公式版本，便于后续重算追溯')
    notes = db.Column(db.Text, comment='手工备注')
    is_manual = db.Column(db.Boolean, default=False, comment='是否手工录入/修改')

    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return (f'<StockTrendScore {self.stock_code}:{self.stock_name} '
                f'{self.record_date} stage={self.trend_stage} score={self.total_score}>')

    def to_dict(self):
        return {
            'id': self.id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'record_date': self.record_date.isoformat() if self.record_date else None,
            'trend_stage': self.trend_stage,
            'trend_stage_confidence': self.trend_stage_confidence,
            'trend_strength': self.trend_strength,
            'signal': self.signal,
            'price_structure_score': self.price_structure_score,
            'ma_score': self.ma_score,
            'macd_score': self.macd_score,
            'volume_score': self.volume_score,
            'momentum_score': self.momentum_score,
            'volatility_score': self.volatility_score,
            'total_score': self.total_score,
            'close': self.close,
            'change_pct': self.change_pct,
            'ma20': self.ma20,
            'ma60': self.ma60,
            'macd_dif': self.macd_dif,
            'macd_dea': self.macd_dea,
            'macd_bar': self.macd_bar,
            'atr': self.atr,
            'formula_version': self.formula_version,
            'notes': self.notes,
            'is_manual': self.is_manual,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def get_by_date(cls, record_date):
        return (cls.query.filter_by(record_date=record_date)
                .order_by(cls.total_score.is_(None).asc(),
                          cls.total_score.desc())
                .all())

    @classmethod
    def get_history(cls, stock_code, start_date=None, end_date=None, limit=None):
        q = cls.query.filter_by(stock_code=stock_code)
        if start_date:
            q = q.filter(cls.record_date >= start_date)
        if end_date:
            q = q.filter(cls.record_date <= end_date)
        q = q.order_by(cls.record_date.desc())
        if limit:
            q = q.limit(limit)
        return q.all()

    @classmethod
    def upsert(cls, stock_code, record_date, **fields):
        fields.pop('id', None)
        fields.pop('stock_code', None)
        fields.pop('record_date', None)

        row = cls.query.filter_by(stock_code=stock_code,
                                  record_date=record_date).first()
        if row is None:
            row = cls(stock_code=stock_code, record_date=record_date, **fields)
            db.session.add(row)
        else:
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @classmethod
    def delete_by_id(cls, row_id):
        row = cls.query.get(row_id)
        if not row:
            return False
        db.session.delete(row)
        db.session.commit()
        return True
