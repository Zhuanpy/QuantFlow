# -*- coding: utf-8 -*-
"""美股长期跟踪（首个标的：SPCX / SpaceX）—— 三张表。

  us_sec_filing   SEC EDGAR 披露流水（后台每 30 分钟同步；新披露推送）
  us_stock_event  事件时间线（手工录入 + 重要 SEC 披露自动生成）
  us_stock_kpi    季度 KPI（每季一行，指标存 JSON 文本，指标集因标的而异）

为什么事件表要单独存「我的预期 / 事后复盘」
------------------------------------------
长期跟踪真正的产出不是新闻列表，而是半年后回头能回答："哪类事件真的动了价格、
我当时的判断对不对"。事件日之后的价格反应（当日 / 5 日）由日K现算，不入库——
K 线会补、会修，存死的数字会和行情漂移。
"""
import json
from datetime import datetime

from App.exts import db

EVENT_CATEGORIES = {
    'earnings': '财报',
    'launch': '发射/Starship',
    'regulatory': '监管/FAA',
    'lockup': '解禁',
    'insider': '内部人交易',
    'index': '指数/资金',
    'contract': '合同/订单',
    'filing': 'SEC 披露',
    'other': '其他',
}


class UsSecFiling(db.Model):
    __tablename__ = 'us_sec_filing'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('accession_no', name='uq_us_sec_filing_acc'),
        db.Index('ix_us_sec_filing_sym_date', 'symbol', 'filed_date'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(16), nullable=False, comment='美股代码，如 SPCX')
    cik = db.Column(db.String(10), nullable=False, comment='SEC CIK（10位补零）')
    accession_no = db.Column(db.String(25), nullable=False, comment='披露编号 0001234567-26-000123')
    form = db.Column(db.String(20), nullable=False, comment='表格类型 8-K/10-Q/4/...')
    filed_date = db.Column(db.Date, nullable=False, comment='提交日')
    report_date = db.Column(db.Date, comment='报告期/事件日')
    accepted_at = db.Column(db.String(30), comment='EDGAR 受理时间（原样）')
    items = db.Column(db.String(100), comment='8-K 条目号，如 2.02,9.01')
    description = db.Column(db.String(255), comment='主文档描述')
    doc_url = db.Column(db.String(500), comment='主文档链接')
    notified = db.Column(db.Boolean, default=False, comment='是否已推送（首次同步回填的置 True 不推）')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id, 'symbol': self.symbol, 'cik': self.cik,
            'accession_no': self.accession_no, 'form': self.form,
            'filed_date': self.filed_date.isoformat() if self.filed_date else None,
            'report_date': self.report_date.isoformat() if self.report_date else None,
            'accepted_at': self.accepted_at, 'items': self.items,
            'description': self.description, 'doc_url': self.doc_url,
            'notified': bool(self.notified),
        }


class UsStockEvent(db.Model):
    __tablename__ = 'us_stock_event'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('symbol', 'source_ref', name='uq_us_stock_event_ref'),
        db.Index('ix_us_stock_event_sym_date', 'symbol', 'event_date'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(16), nullable=False)
    event_date = db.Column(db.Date, nullable=False, comment='事件日（美东）')
    category = db.Column(db.String(20), nullable=False, default='other')
    title = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text, comment='事件内容')
    expectation = db.Column(db.Text, comment='事前：我的预期/判断')
    review = db.Column(db.Text, comment='事后：复盘')
    importance = db.Column(db.SmallInteger, default=2, comment='1低 2中 3高')
    source_url = db.Column(db.String(500))
    source = db.Column(db.String(10), default='manual', comment='manual / sec')
    source_ref = db.Column(db.String(25), comment='sec 来源时为 accession_no，防重复生成')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            'id': self.id, 'symbol': self.symbol,
            'event_date': self.event_date.isoformat() if self.event_date else None,
            'category': self.category,
            'category_label': EVENT_CATEGORIES.get(self.category, self.category),
            'title': self.title, 'detail': self.detail,
            'expectation': self.expectation, 'review': self.review,
            'importance': self.importance, 'source_url': self.source_url,
            'source': self.source, 'source_ref': self.source_ref,
        }


class UsStockKpi(db.Model):
    __tablename__ = 'us_stock_kpi'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('symbol', 'period', name='uq_us_stock_kpi_period'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(16), nullable=False)
    period = db.Column(db.String(10), nullable=False, comment='如 2026Q3')
    period_end = db.Column(db.Date, comment='报告期末')
    metrics = db.Column(db.Text, comment='JSON {指标key: 数值}')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def get_metrics(self) -> dict:
        try:
            return json.loads(self.metrics) if self.metrics else {}
        except (TypeError, ValueError):
            return {}

    def to_dict(self):
        return {
            'id': self.id, 'symbol': self.symbol, 'period': self.period,
            'period_end': self.period_end.isoformat() if self.period_end else None,
            'metrics': self.get_metrics(), 'notes': self.notes,
        }
