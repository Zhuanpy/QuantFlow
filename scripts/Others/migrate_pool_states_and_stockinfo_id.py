#!/usr/bin/env python3
"""一次性迁移（幂等，可重复运行）：

1) strategy_stock_pool 新增 pool_states 列（多状态标签，逗号分隔 watching/candidate/trading），
   回填 = 原 pool_type。这是"股票池多状态"功能的前提，缺它股票池页会报错。
2) data_stock_info 回填 id 为 NULL 的行（该表无自增主键，id 由应用层手动分配 = max+1）。
   这些 NULL-id 行无法在 /stock_market_data 按 id 编辑/删除/筛选。

两处都在 quanttradingsystem 库。换机器/部署后拉了新代码，跑一次本脚本即可。

    python scripts/Others/migrate_pool_states_and_stockinfo_id.py
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db

BIND = 'quanttradingsystem'
VALID_STATES = ('watching', 'candidate', 'trading')


def migrate_pool_states(engine):
    table, column = 'strategy_stock_pool', 'pool_states'
    cols = {c['name'] for c in inspect(engine).get_columns(table)}
    with engine.begin() as conn:
        if column in cols:
            print(f'OK  列 {table}.{column} 已存在，跳过 ALTER')
        else:
            if engine.dialect.name == 'mysql':
                sql = (f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR(64) NULL "
                       f"COMMENT '多状态集合,逗号分隔 watching/candidate/trading' AFTER pool_type")
            else:
                sql = f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR(64) NULL"
            conn.execute(text(sql))
            print(f'OK  已新增列 {table}.{column}')

        # 回填：每只股票当前是单状态 → pool_states = pool_type（仅三种有效状态）
        in_list = ', '.join(f"'{s}'" for s in VALID_STATES)
        res = conn.execute(text(
            f"UPDATE {table} SET {column} = pool_type "
            f"WHERE ({column} IS NULL OR {column} = '') AND pool_type IN ({in_list})"
        ))
        print(f'OK  回填 {res.rowcount} 行 pool_states = pool_type')

        rs = conn.execute(text(
            f"SELECT pool_type, COUNT(*) FROM {table} "
            f"WHERE is_active=1 AND is_excluded=0 GROUP BY pool_type"
        )).fetchall()
        print('    活跃池主状态分布：' + ', '.join(f'{t or "(空)"}={n}' for t, n in rs))


def migrate_stockinfo_id(engine):
    table = 'data_stock_info'
    with engine.begin() as conn:
        n_null = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE id IS NULL")).scalar()
        if not n_null:
            print(f'OK  {table} 无 NULL id 行，跳过')
            return
        # 该表无自增主键：给每个 NULL id 行分配 max(id)+递增，保证唯一且大于现有 id
        conn.execute(text(f"SET @n := (SELECT COALESCE(MAX(id),0) FROM {table})"))
        res = conn.execute(text(f"UPDATE {table} SET id = (@n := @n + 1) WHERE id IS NULL"))
        dup = conn.execute(text(
            f"SELECT COUNT(*) FROM (SELECT id FROM {table} WHERE id IS NOT NULL "
            f"GROUP BY id HAVING COUNT(*)>1) t"
        )).scalar()
        print(f'OK  {table} 回填 {res.rowcount} 个 NULL id；重复 id 数={dup}')


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines[BIND]
        print('== 1) strategy_stock_pool.pool_states ==')
        migrate_pool_states(engine)
        print('== 2) data_stock_info.id 回填 ==')
        migrate_stockinfo_id(engine)
        print('\n迁移完成。')


if __name__ == '__main__':
    main()
