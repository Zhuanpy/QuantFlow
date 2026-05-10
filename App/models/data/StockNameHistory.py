# -*- coding: utf-8 -*-
"""
个股改名历史

由 refresh_basic_quotes_bulk() 在检测到 East Money 返回的最新名称
与本地 data_stock_info.name 不一致时自动写入。
"""
from datetime import datetime
from App.exts import db


class StockNameHistory(db.Model):
    __tablename__ = 'data_stock_name_history'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    stock_code = db.Column(db.String(10), nullable=False, index=True, comment='股票代码')
    old_name = db.Column(db.String(50), comment='原名称')
    new_name = db.Column(db.String(50), nullable=False, comment='新名称')
    source = db.Column(db.String(30), default='eastmoney_clist',
                       comment='检测到改名的数据源')
    changed_at = db.Column(db.DateTime, default=datetime.utcnow,
                           comment='检测到的时间')

    def to_dict(self):
        return {
            'id': self.id,
            'stock_code': self.stock_code,
            'old_name': self.old_name,
            'new_name': self.new_name,
            'source': self.source,
            'changed_at': self.changed_at.strftime('%Y-%m-%d %H:%M:%S') if self.changed_at else None,
        }
