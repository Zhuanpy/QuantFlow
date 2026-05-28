#!/usr/bin/env python3
"""
给 strategy_issues 表新增 type 列（想法/问题/功能/优化）。

- 已存在则跳过 ALTER。
- 已有行（旧记录）一律回填为 '想法'。
- 幂等：可重复运行。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db


TABLE = 'strategy_issues'
COLUMN = 'type'
BIND = 'quanttradingsystem'
DEFAULT_TYPE = '想法'


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
                    sql = (
                        f"ALTER TABLE {TABLE} "
                        f"ADD COLUMN `{COLUMN}` VARCHAR(20) NOT NULL DEFAULT '{DEFAULT_TYPE}' "
                        f"COMMENT '类型：想法/问题/功能/优化' "
                        f"AFTER status"
                    )
                else:
                    sql = (
                        f"ALTER TABLE {TABLE} "
                        f"ADD COLUMN {COLUMN} VARCHAR(20) NOT NULL DEFAULT '{DEFAULT_TYPE}'"
                    )
                conn.execute(text(sql))
                print(f'OK  已新增列 {TABLE}.{COLUMN}（默认值 {DEFAULT_TYPE}）')

            # 回填：NULL / 空 / 非法值统一打 想法
            valid = ('想法', '问题', '功能', '优化')
            placeholders = ', '.join([f"'{v}'" for v in valid])
            res = conn.execute(text(
                f"UPDATE {TABLE} SET `{COLUMN}` = '{DEFAULT_TYPE}' "
                f"WHERE `{COLUMN}` IS NULL OR `{COLUMN}` = '' "
                f"OR `{COLUMN}` NOT IN ({placeholders})"
            ))
            print(f'OK  回填 {res.rowcount} 行为 {DEFAULT_TYPE}')

            # 验证
            rs = conn.execute(text(
                f"SELECT `{COLUMN}`, COUNT(*) FROM {TABLE} GROUP BY `{COLUMN}`"
            )).fetchall()
            print('\n当前各类型分布：')
            for tp, n in rs:
                print(f'  {tp:8} {n}')


if __name__ == '__main__':
    main()
