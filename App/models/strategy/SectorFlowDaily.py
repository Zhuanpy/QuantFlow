"""板块（行业+概念）每日资金流/热度快照。

数据源：东方财富 push2 clist/get 板块列表接口（fs=m:90+t:2 行业 / t:3 概念）。
用途：近期热点榜（按涨幅/主力净额排序）+ 设计文档里的资金/广度 Feature 源。
每天每板块一行（date + board_code 唯一），整日替换写入。
"""
from datetime import datetime, date

from App.exts import db


class SectorFlowDaily(db.Model):
    __tablename__ = 'mkt_sector_flow_daily'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('date', 'board_code', name='uq_sector_flow_date_code'),
        db.Index('idx_sector_flow_date', 'date'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, nullable=False, comment='快照日期')
    board_type = db.Column(db.String(10), nullable=False, comment='industry 行业 / concept 概念')
    board_code = db.Column(db.String(20), nullable=False, comment='板块代码 BKxxxx')
    board_name = db.Column(db.String(50), comment='板块名称')

    change_pct = db.Column(db.Float, comment='涨跌幅%')
    main_net = db.Column(db.Float, comment='主力净流入额(元)（东财推断值，仅辅助）')
    main_pct = db.Column(db.Float, comment='主力净流入占比%')
    turnover_rate = db.Column(db.Float, comment='换手率%')
    total_cap = db.Column(db.Float, comment='总市值(元)')
    up_count = db.Column(db.Integer, comment='上涨家数')
    down_count = db.Column(db.Integer, comment='下跌家数')
    lead_stock = db.Column(db.String(50), comment='领涨股名称')
    lead_pct = db.Column(db.Float, comment='领涨股涨跌幅%')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @classmethod
    def ensure_table(cls):
        """惰性建表（项目不做全局 create_all）。"""
        eng = db.engines['quanttradingsystem']
        if not db.inspect(eng).has_table(cls.__tablename__):
            cls.__table__.create(bind=eng)

    def __repr__(self):
        return f'<SectorFlowDaily {self.date} {self.board_type} {self.board_code} {self.change_pct}%>'
