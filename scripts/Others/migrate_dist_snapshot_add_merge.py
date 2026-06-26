#!/usr/bin/env python3
"""
给 stock_dist_snapshot 表新增 merge_below 列，并把唯一键从
(stock_code, snapshot_date) 改为 (stock_code, snapshot_date, merge_below)。

- merge_below：周期合并阈值(%)，0=原始口径，>0=合并<该值的小周期后再统计。
- 同一天可同时存"原始(0)"与"合并(如5)"两套快照，互不覆盖。
- 幂等：可重复运行。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db

TABLE = 'stock_dist_snapshot'
COLUMN = 'merge_below'
BIND = 'quanttradingsystem'
UQ = 'uq_stock_date'


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines[BIND]
        insp = inspect(engine)
        cols = {c['name'] for c in insp.get_columns(TABLE)}
        dialect = engine.dialect.name

        with engine.begin() as conn:
            # 1) 加列
            if COLUMN in cols:
                print(f'OK  列 {TABLE}.{COLUMN} 已存在，跳过 ADD')
            else:
                if dialect == 'mysql':
                    sql = (f"ALTER TABLE {TABLE} ADD COLUMN `{COLUMN}` FLOAT NOT NULL DEFAULT 0 "
                           f"COMMENT '周期合并阈值(%)，0=不合并' AFTER snapshot_date")
                else:
                    sql = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} FLOAT NOT NULL DEFAULT 0"
                conn.execute(text(sql))
                print(f'OK  已新增列 {TABLE}.{COLUMN}（默认 0）')

            # 2) 回填已有行为 0（NULL 兜底）
            res = conn.execute(text(
                f"UPDATE {TABLE} SET `{COLUMN}` = 0 WHERE `{COLUMN}` IS NULL"))
            if res.rowcount:
                print(f'OK  回填 {res.rowcount} 行 {COLUMN}=0')

            # 3) 重建唯一键，纳入 merge_below
            if dialect == 'mysql':
                idx = {i['name'] for i in insp.get_indexes(TABLE)}
                # MySQL 唯一约束以索引形式存在
                has_uq = UQ in idx
                # 检查现有唯一键是否已含 merge_below
                need_rebuild = True
                for i in insp.get_indexes(TABLE):
                    if i['name'] == UQ and i.get('unique') and COLUMN in (i.get('column_names') or []):
                        need_rebuild = False
                        break
                if need_rebuild:
                    if has_uq:
                        conn.execute(text(f"ALTER TABLE {TABLE} DROP INDEX {UQ}"))
                        print(f'OK  已删除旧唯一键 {UQ}')
                    conn.execute(text(
                        f"ALTER TABLE {TABLE} ADD CONSTRAINT {UQ} "
                        f"UNIQUE (stock_code, snapshot_date, `{COLUMN}`)"))
                    print(f'OK  已重建唯一键 {UQ} (stock_code, snapshot_date, {COLUMN})')
                else:
                    print(f'OK  唯一键 {UQ} 已含 {COLUMN}，跳过')
            else:
                print('注意：非 MySQL，唯一键请手动确认')

            # 验证
            rs = conn.execute(text(
                f"SELECT `{COLUMN}`, COUNT(*) FROM {TABLE} GROUP BY `{COLUMN}`")).fetchall()
            print('\n当前各 merge_below 口径行数：')
            for mb, n in rs:
                print(f'  merge_below={mb}: {n}')


if __name__ == '__main__':
    main()
