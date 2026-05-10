# -*- coding: utf-8 -*-
"""
个股每日行情快照 + 基本面（最新价 / 涨跌 / 总市值 / 流通市值 / PE / PB / 换手率 等）

由 `App/services/stock_quote_service.refresh_basic_quotes_bulk()` 通过
East Money 的 `clist/get` 接口一次性批量拉取并 upsert，适合每日盘后跑一次。
"""
from datetime import datetime, date

from App.exts import db


class StockBasicQuote(db.Model):
    __tablename__ = 'data_stock_basic_quote'
    __bind_key__ = 'quanttradingsystem'

    stock_code = db.Column(db.String(10), primary_key=True, comment='股票代码')
    stock_name = db.Column(db.String(50), comment='股票名称')

    # 行情
    price = db.Column(db.Float, comment='最新价')
    prev_close = db.Column(db.Float, comment='昨收')
    change_pct = db.Column(db.Float, comment='涨跌幅(%)')
    change_amt = db.Column(db.Float, comment='涨跌额')
    open = db.Column(db.Float, comment='今开')
    high = db.Column(db.Float, comment='最高')
    low = db.Column(db.Float, comment='最低')

    # 量
    volume = db.Column(db.BigInteger, comment='成交量(手)')
    amount = db.Column(db.Float, comment='成交额(元)')
    turnover = db.Column(db.Float, comment='换手率(%)')
    volume_ratio = db.Column(db.Float, comment='量比')

    # 市值 / 股本
    total_mv = db.Column(db.Float, comment='总市值(元)')
    float_mv = db.Column(db.Float, comment='流通市值(元)')
    total_shares = db.Column(db.Float, comment='总股本(股)')
    float_shares = db.Column(db.Float, comment='流通股本(股)')

    # 估值
    pe_dynamic = db.Column(db.Float, comment='市盈率(动)')
    pe_static = db.Column(db.Float, comment='市盈率(静)')
    pe_ttm = db.Column(db.Float, comment='市盈率(TTM)')
    pb = db.Column(db.Float, comment='市净率')

    # 元数据
    quote_date = db.Column(db.Date, default=date.today, comment='行情数据所在交易日')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, comment='更新时间')

    def to_dict(self):
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'price': self.price,
            'prev_close': self.prev_close,
            'change_pct': self.change_pct,
            'change_amt': self.change_amt,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'volume': self.volume,
            'amount': self.amount,
            'turnover': self.turnover,
            'volume_ratio': self.volume_ratio,
            'total_mv': self.total_mv,
            'float_mv': self.float_mv,
            'total_shares': self.total_shares,
            'float_shares': self.float_shares,
            'pe_dynamic': self.pe_dynamic,
            'pe_static': self.pe_static,
            'pe_ttm': self.pe_ttm,
            'pb': self.pb,
            'quote_date': self.quote_date.isoformat() if self.quote_date else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
