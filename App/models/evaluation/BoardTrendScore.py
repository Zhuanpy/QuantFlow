"""
板块趋势打分数据模型

将板块按周期划分为：上涨前期 / 上涨后期 / 下跌前期 / 下跌后期 四个阶段，
按多维度（价格结构 / 均线 / MACD / 量能 / 动量 / 波动率）给出 0-100 子分，
最终汇总为综合分。

打分公式当前版本占位，仅维护表结构和 CRUD 入口；后续可在 services 层补全。
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# 趋势阶段枚举
TREND_STAGES = (
    'up_early',     # 上涨前期：底部突破，均线由空转多
    'up_late',      # 上涨后期：加速上涨，可能出现顶背离
    'down_early',   # 下跌前期：高点回落，均线由多转空
    'down_late',    # 下跌后期：加速下跌或筑底
    'unknown',      # 未识别 / 横盘
)

TREND_STRENGTHS = ('strong', 'medium', 'weak', 'none')

SIGNALS = ('buy', 'hold', 'sell', 'wait', 'none')


class BoardTrendScore(db.Model):
    """板块趋势打分表

    一个板块在一个交易日只允许有一条记录（board_code + record_date 唯一）。
    """
    __tablename__ = 'eval_board_trend_score'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('board_code', 'record_date', name='uix_board_date'),
        db.Index('ix_board_trend_date', 'record_date'),
        db.Index('ix_board_trend_stage', 'trend_stage'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')

    # —— 基础信息 ——
    board_code = db.Column(db.String(20), nullable=False, comment='板块代码（如 BK0437）')
    board_name = db.Column(db.String(50), nullable=False, comment='板块名称')
    record_date = db.Column(db.Date, nullable=False, comment='记录日期')

    # —— 趋势阶段（核心输出）——
    trend_stage = db.Column(db.String(20), default='unknown',
                            comment='趋势阶段 up_early/up_late/down_early/down_late/unknown')
    trend_stage_confidence = db.Column(db.Float, default=0.0, comment='阶段判定置信度 0-100')
    trend_strength = db.Column(db.String(10), default='none',
                               comment='趋势强度 strong/medium/weak/none')
    signal = db.Column(db.String(10), default='none',
                       comment='交易信号 buy/hold/sell/wait/none')

    # —— 各维度子分 0-100，留空（NULL）表示未计算 ——
    price_structure_score = db.Column(db.Float, comment='价格结构分（高低点/突破）')
    ma_score = db.Column(db.Float, comment='均线系统分（排列/斜率）')
    macd_score = db.Column(db.Float, comment='MACD 分（柱体/零轴/背离）')
    volume_score = db.Column(db.Float, comment='量能配合分')
    momentum_score = db.Column(db.Float, comment='动量分（RSI 等）')
    volatility_score = db.Column(db.Float, comment='波动率分（ATR）')

    # —— 综合 ——
    total_score = db.Column(db.Float, comment='综合趋势分 0-100')

    # —— 当日行情快照（用于审阅评分依据，可空）——
    close = db.Column(db.Float, comment='收盘价')
    change_pct = db.Column(db.Float, comment='涨跌幅%')
    ma20 = db.Column(db.Float, comment='MA20')
    ma60 = db.Column(db.Float, comment='MA60')
    macd_dif = db.Column(db.Float, comment='MACD DIF')
    macd_dea = db.Column(db.Float, comment='MACD DEA')
    macd_bar = db.Column(db.Float, comment='MACD 柱体')
    atr = db.Column(db.Float, comment='ATR')

    # —— 元数据 ——
    formula_version = db.Column(db.String(20), default='v0',
                                comment='打分公式版本，便于后续重算追溯')
    notes = db.Column(db.Text, comment='手工备注')
    is_manual = db.Column(db.Boolean, default=False, comment='是否手工录入/修改')

    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return (f'<BoardTrendScore {self.board_code}:{self.board_name} '
                f'{self.record_date} stage={self.trend_stage} score={self.total_score}>')

    # ---------------- 序列化 ----------------
    def to_dict(self):
        return {
            'id': self.id,
            'board_code': self.board_code,
            'board_name': self.board_name,
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

    # ---------------- 查询 ----------------
    @classmethod
    def get_by_date(cls, record_date):
        """按日期取所有板块当日打分（NULL 分数排到最后，兼容 MySQL）"""
        return (cls.query.filter_by(record_date=record_date)
                .order_by(cls.total_score.is_(None).asc(),
                          cls.total_score.desc())
                .all())

    @classmethod
    def get_history(cls, board_code, start_date=None, end_date=None, limit=None):
        """按板块取历史打分"""
        q = cls.query.filter_by(board_code=board_code)
        if start_date:
            q = q.filter(cls.record_date >= start_date)
        if end_date:
            q = q.filter(cls.record_date <= end_date)
        q = q.order_by(cls.record_date.desc())
        if limit:
            q = q.limit(limit)
        return q.all()

    @classmethod
    def get_latest_per_board(cls):
        """每个板块取最新一条"""
        sub = (db.session.query(cls.board_code, db.func.max(cls.record_date).label('mx'))
               .group_by(cls.board_code).subquery())
        return (db.session.query(cls)
                .join(sub, db.and_(cls.board_code == sub.c.board_code,
                                   cls.record_date == sub.c.mx))
                .all())

    # ---------------- 写入 ----------------
    @classmethod
    def upsert(cls, board_code, record_date, **fields):
        """按 (board_code, record_date) 唯一键 upsert。fields 中不允许覆盖主键和唯一键。"""
        fields.pop('id', None)
        fields.pop('board_code', None)
        fields.pop('record_date', None)

        row = cls.query.filter_by(board_code=board_code,
                                  record_date=record_date).first()
        if row is None:
            row = cls(board_code=board_code, record_date=record_date, **fields)
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