#!/usr/bin/env python3
"""创建美股长期跟踪三张表（us_sec_filing / us_stock_event / us_stock_kpi），并给 SPCX 种两条已知事件。

幂等：CREATE TABLE IF NOT EXISTS；种子事件按 (symbol, title) 判重，重复跑不会多插。
锁定期解禁日**不种**——需从 424B4 招股书 "Lock-up" 章节核实后在档案页手工录入。
"""
import os
import sys
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from App import create_app
from App.exts import db
from App.models.data.UsWatch import UsSecFiling, UsStockEvent, UsStockKpi

SEEDS = [
    dict(symbol='SPCX', event_date=date(2026, 6, 12), category='index', importance=3,
         title='纳斯达克上市（SPCX），发行价 $135',
         detail='Class A 普通股在 Nasdaq Global Select Market 与 Nasdaq Texas 挂牌。',
         source_url='https://ir.spacex.com/'),
    dict(symbol='SPCX', event_date=date(2026, 7, 7), category='index', importance=2,
         title='纳入纳斯达克100指数',
         detail='被动资金被动买入；之后关注指数年度/季度再平衡与权重调整。',
         source_url='https://keeptrack.space/deep-dive/can-you-buy-spacex-stock'),
]


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['quanttradingsystem']
        for m in (UsSecFiling, UsStockEvent, UsStockKpi):
            m.__table__.create(bind=engine, checkfirst=True)
            print(f'OK  {m.__tablename__}')
        n = 0
        for s in SEEDS:
            if not UsStockEvent.query.filter_by(symbol=s['symbol'], title=s['title']).first():
                db.session.add(UsStockEvent(source='manual', **s))
                n += 1
        db.session.commit()
        print(f'种子事件新增 {n} 条')


if __name__ == '__main__':
    main()
