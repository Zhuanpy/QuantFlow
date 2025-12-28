"""
股票基本信息数据模型
包含股票基础信息和代码映射
"""
from App.exts import db
from datetime import datetime


class StockCodes(db.Model):
    """股票市场数据表 - 包含全部个股和板块数据"""
    __tablename__ = 'stock_market_data'
    __bind_key__ = 'quanttradingsystem'  # 绑定到 quanttradingsystem 数据库
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    name = db.Column(db.Text, comment='股票名称')
    code = db.Column(db.Text, comment='股票代码')
    EsCode = db.Column(db.Text, comment='东方财富代码')
    MarketCode = db.Column(db.Text, comment='市场代码')
    TxdMarket = db.Column(db.Text, comment='通达信市场代码')
    HsMarket = db.Column(db.Text, comment='恒生市场代码')
    created_at = db.Column(db.TIMESTAMP, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<StockMarketData {self.code}:{self.name}>'


class StockClassification(db.Model):
    """股票基本信息表"""
    __tablename__ = 'stock_classification'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), comment='股票名称')
    code = db.Column(db.String(20), comment='股票代码')
    classification = db.Column(db.String(20), comment='股票分类')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<StockBasicInformationStock {self.code}:{self.name}>'
