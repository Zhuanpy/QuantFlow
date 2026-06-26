#!/usr/bin/env python3
"""
给 trade_records 表新增 trade_mode 列（与 trade_plans.trade_mode 对齐）。

- 已存在则跳过 ALTER。
- 已有行（同花顺导入的实盘成交）一律回填为 'real'。
- 幂等：可重复运行。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db


TABLE = 'trade_records'
COLUMN = 'trade_mode'
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
                # MySQL：放在 trade_type 之后；如不在 MySQL 上则去掉 AFTER 子句
                dialect = engine.dialect.name
                if dialect == 'mysql':
                    sql = (
                        f"ALTER TABLE {TABLE} "
                        f"ADD COLUMN {COLUMN} VARCHAR(20) NOT NULL DEFAULT 'real' "
                        f"COMMENT '交易模式(simulate/real)，与 trade_plans.trade_mode 对齐' "
                        f"AFTER trade_type"
                    )
                else:
                    sql = (
                        f"ALTER TABLE {TABLE} "
                        f"ADD COLUMN {COLUMN} VARCHAR(20) NOT NULL DEFAULT 'real'"
                    )
                conn.execute(text(sql))
                print(f'OK  已新增列 {TABLE}.{COLUMN}（默认值 real）')

            # 回填：把 NULL / 空 / 旧记录统一标 real
            res = conn.execute(text(
                f"UPDATE {TABLE} SET {COLUMN} = 'real' "
                f"WHERE {COLUMN} IS NULL OR {COLUMN} = ''"
            ))
            print(f'OK  回填 {res.rowcount} 行为 real')

            # 验证
            rs = conn.execute(text(
                f"SELECT {COLUMN}, COUNT(*) FROM {TABLE} GROUP BY {COLUMN}"
            )).fetchall()
            print('\n当前各 mode 分布：')
            for mode, n in rs:
                print(f'  {mode:10} {n}')


if __name__ == '__main__':
    main()
