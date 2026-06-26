#!/usr/bin/env python3
"""
给 trade_records 表新增 trade_reason 列（手动填写的操作理由/想法，复盘用）。

- 已存在则跳过 ALTER。
- 不回填（NULL 即"未填写"）。
- 幂等：可重复运行。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db


TABLE = 'trade_records'
COLUMN = 'trade_reason'
BIND = 'quanttradingsystem'


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines[BIND]
        insp = inspect(engine)
        cols = {c['name'] for c in insp.get_columns(TABLE)}

        with engine.begin() as conn:
            if COLUMN in cols:
                print(f'OK  列 {TABLE}.{COLUMN} 已存在，跳过 ALTER')
            else:
                dialect = engine.dialect.name
                if dialect == 'mysql':
                    # 放在 confidence_score 之后；MySQL 的 TEXT 不能带 DEFAULT
                    sql = (
                        f"ALTER TABLE {TABLE} "
                        f"ADD COLUMN {COLUMN} TEXT NULL "
                        f"COMMENT '操作理由/想法(手动填写)' "
                        f"AFTER confidence_score"
                    )
                else:
                    sql = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} TEXT NULL"
                conn.execute(text(sql))
                print(f'OK  已新增列 {TABLE}.{COLUMN}')

            # 验证：已填写理由的行数
            rs = conn.execute(text(
                f"SELECT COUNT(*) FROM {TABLE} "
                f"WHERE {COLUMN} IS NOT NULL AND {COLUMN} <> ''"
            )).scalar()
            print(f'OK  当前已填写理由的记录数：{rs}')


if __name__ == '__main__':
    main()
