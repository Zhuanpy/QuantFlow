"""合成板块日K（自有板块指数）—— 与东财板块数据完全分开存储。

由 App/services/board_synth_service.py 用成分股合成后写入，代码用 BKxxxxS。
读取在 board_trend_score_service._load_board_daily 里按合成代码(BK…S)路由到本表。
刻意独立于 data_stock_daily：避免合成的伪代码混进真实行情表（污染全市场扫描/股票列表）。
"""
from App.exts import db


class BoardSynthDaily(db.Model):
    __tablename__ = 'board_synth_daily'
    __bind_key__ = 'quanttradingsystem'

    stock_code = db.Column(db.String(20), primary_key=True, nullable=False,
                           comment='合成板块代码（东财板块码+S，如 BK1015S）')
    date = db.Column(db.Date, primary_key=True, nullable=False, comment='交易日期')
    open = db.Column(db.Float, comment='开盘(指数点位)')
    close = db.Column(db.Float, comment='收盘')
    high = db.Column(db.Float, comment='最高')
    low = db.Column(db.Float, comment='最低')
    volume = db.Column(db.Float, comment='指数量能=成分股成交额合计(turnover口径，非原始股数)')
    money = db.Column(db.Float, comment='成分股成交额合计(同 volume)')

    def __repr__(self):
        return f'<BoardSynthDaily {self.stock_code} {self.date}>'
