"""
股票基本信息数据模型
包含股票基础信息和代码映射
"""
from App.exts import db
from datetime import datetime


class StockInfo(db.Model):
    """股票基础信息表 - 包含全部个股和板块数据"""
    __tablename__ = 'data_stock_info'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    name = db.Column(db.Text, comment='股票名称')
    code = db.Column(db.Text, comment='股票代码')
    EsCode = db.Column(db.Text, comment='东方财富代码')
    MarketCode = db.Column(db.Text, comment='市场代码')
    TxdMarket = db.Column(db.Text, comment='通达信市场代码')
    HsMarket = db.Column(db.Text, comment='恒生市场代码')
    created_at = db.Column(db.TIMESTAMP, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    # snake_case 别名，兼容模板访问
    @property
    def es_code(self):
        return self.EsCode

    @property
    def market_code(self):
        return self.MarketCode

    @property
    def txd_market(self):
        return self.TxdMarket

    @property
    def hs_market(self):
        return self.HsMarket

    def __repr__(self):
        return f'<StockInfo {self.code}:{self.name}>'


# 保持向后兼容的别名
StockCodes = StockInfo


class StockClassification(db.Model):
    """股票分类信息表"""
    __tablename__ = 'data_stock_classification'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), comment='股票名称')
    code = db.Column(db.String(20), comment='股票代码')
    classification = db.Column(db.String(20), comment='股票分类')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<StockClassification {self.code}:{self.name}>'
