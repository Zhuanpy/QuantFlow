#!/usr/bin/env python3
"""创建 stock_dist_snapshot 表（个股三正态分布快照）

幂等：CREATE TABLE IF NOT EXISTS，已存在不会动它。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App import create_app
from App.exts import db
from App.models.strategy.StockDistSnapshot import StockDistSnapshot


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['quanttradingsystem']
        StockDistSnapshot.__table__.create(bind=engine, checkfirst=True)

        from sqlalchemy import inspect
        insp = inspect(engine)
        cols = insp.get_columns(StockDistSnapshot.__tablename__)
        print(f'OK  {StockDistSnapshot.__tablename__}  共 {len(cols)} 列')
        for c in cols:
            print(f"  {c['name']:25s} {str(c['type']):20s} nullable={c['nullable']}")


if __name__ == '__main__':
    main()
