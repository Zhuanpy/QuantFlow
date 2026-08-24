"""板块**时序**热度分（heat_ts）—— 与横截面热度分并列的第二个口径。

为什么要它
----------
`mkt_sector_flow_daily.heat_score` 是**横截面**百分位（今天 504 个概念里排第几），
算它必须有全市场同期快照 —— 而那份快照来自东财"当日"接口，**历史不可回补**。
概念板块因此长期只有一两天样本，判不了"一日游还是持续"。

换个问法就绕开了横截面：**这个板块今天的涨幅和成交额，在它自己过去 N 天里排第几分位？**
只要有它自己的日K就算得出来 —— 而池内概念已经有合成指数（`board_synth_daily`，回到 2021 年）。
于是概念立刻拥有几年的热度历史。

    heat_ts = 50 × pct_self(涨跌幅, N) + 50 × pct_self(成交额, N)

两个口径的分工（**不可混用、不可画进同一条线**）：
    heat_score(横截面)  "今天谁最热"          —— 横向比较板块之间
    heat_ts(时序)       "它比自己平时热多少"   —— 纵向比较板块与自身历史

时序口径还有个横截面给不了的好处：**不受成分池变化影响**。横截面排名的分母
（当天参与排名的板块数）一变，历史排名就不可比；时序分位只跟自己比，天然稳定。

一个板块一天一行（board_code + date 唯一）。
"""
from datetime import datetime

from App.exts import db

HEAT_TS_VERSION = 'ts-v0'

# 日K来源：东财板块日K / 自建合成指数
SRC_EM_DAILY = 'em'
SRC_SYNTH = 'synth'


class BoardHeatTs(db.Model):
    __tablename__ = 'mkt_board_heat_ts'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('board_code', 'date', name='uq_heat_ts_code_date'),
        db.Index('idx_heat_ts_date', 'date'),
        db.Index('idx_heat_ts_code_date', 'board_code', 'date'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    board_code = db.Column(db.String(20), nullable=False, comment='板块代码 BKxxxx（不带合成后缀）')
    board_name = db.Column(db.String(50), comment='板块名称')
    date = db.Column(db.Date, nullable=False, comment='交易日')

    close = db.Column(db.Float, comment='收盘（板块指数/合成指数）')
    change_pct = db.Column(db.Float, comment='涨跌幅%')
    amount = db.Column(db.Float, comment='成交额')

    pct_chg = db.Column(db.Float, comment='涨跌幅在自身过去 window_n 天中的分位 0-100')
    pct_amt = db.Column(db.Float, comment='成交额在自身过去 window_n 天中的分位 0-100')
    heat_ts = db.Column(db.Float, comment='时序热度分 0-100 = 50×pct_chg + 50×pct_amt')

    window_n = db.Column(db.Integer, comment='分位窗口长度（交易日）')
    data_source = db.Column(db.String(10), comment='日K来源 em/synth')
    version = db.Column(db.String(16), default=HEAT_TS_VERSION, comment='口径版本')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @classmethod
    def ensure_table(cls):
        """惰性建表（项目不做全局 create_all）。"""
        eng = db.engines['quanttradingsystem']
        if not db.inspect(eng).has_table(cls.__tablename__):
            cls.__table__.create(bind=eng)

    @classmethod
    def latest_date(cls, board_code=None):
        q = db.session.query(db.func.max(cls.date))
        if board_code:
            q = q.filter(cls.board_code == board_code)
        return q.scalar()

    @classmethod
    def coverage(cls):
        """每个板块覆盖到哪天、多少行 —— 给页面/脚本判断要不要增量。"""
        rows = (db.session.query(cls.board_code, db.func.count(cls.id),
                                 db.func.min(cls.date), db.func.max(cls.date))
                .group_by(cls.board_code).all())
        return {bc: {'rows': int(n or 0),
                     'first': d0.isoformat() if d0 else None,
                     'last': d1.isoformat() if d1 else None}
                for bc, n, d0, d1 in rows}

    def to_dict(self):
        return {
            'board_code': self.board_code,
            'board_name': self.board_name,
            'date': self.date.isoformat() if self.date else None,
            'close': self.close,
            'change_pct': self.change_pct,
            'amount': self.amount,
            'pct_chg': self.pct_chg,
            'pct_amt': self.pct_amt,
            'heat_ts': self.heat_ts,
            'window_n': self.window_n,
            'data_source': self.data_source,
            'version': self.version,
        }

    def __repr__(self):
        return f'<BoardHeatTs {self.board_code} {self.date} heat_ts={self.heat_ts}>'
