#!/usr/bin/env python3
"""
给 data_download_records 表新增一组 repair_* 列，把「数据修复」的任务状态
从「盘后数据下载」的 download_* 里拆出来。

背景：两个页面（/download_minute_data_page?mode=download|repair）原本共用
download_status / download_progress / error_message / last_download_time，
修复流程会覆盖盘后下载留下的状态和失败原因。拆开后：
  - download_*  只由 download_file() 写
  - repair_*    只由 services/minute_data_repair 写
  - end_date / start_date 是「数据事实」，两边仍共用（不拆，避免两个真值打架）

- 已存在的列跳过 ALTER。
- 幂等：可重复运行。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 项目根

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db


TABLE = 'data_download_records'
BIND = 'quanttradingsystem'

# (列名, MySQL 类型, 注释, AFTER 哪一列)
COLUMNS = [
    ('repair_status', 'VARCHAR(20) NULL', '修复状态：pending/processing/success/failed', 'error_message'),
    ('repair_progress', 'FLOAT NULL', '修复进度(0-100)', 'repair_status'),
    ('repair_error', 'TEXT NULL', '修复错误信息', 'repair_progress'),
    ('repair_time', 'DATETIME NULL', '最后修复时间', 'repair_error'),
]


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines[BIND]
        insp = inspect(engine)
        cols = {c['name'] for c in insp.get_columns(TABLE)}
        dialect = engine.dialect.name

        with engine.begin() as conn:
            for name, coltype, comment, after in COLUMNS:
                if name in cols:
                    print(f'OK  列 {TABLE}.{name} 已存在，跳过 ALTER')
                    continue
                if dialect == 'mysql':
                    tail = f" COMMENT '{comment}'"
                    # AFTER 目标列必须已存在（前一轮刚加的也算），否则退化成追加到末尾
                    if after in cols:
                        tail += f' AFTER {after}'
                    sql = f'ALTER TABLE {TABLE} ADD COLUMN {name} {coltype}{tail}'
                else:
                    sql = f'ALTER TABLE {TABLE} ADD COLUMN {name} {coltype}'
                conn.execute(text(sql))
                cols.add(name)
                print(f'OK  已新增列 {TABLE}.{name}')

            # 不回填历史：repair_* 为 NULL 表示「这只股票还没被修复流程碰过」，
            # 页面显示 '-'。历史上被修复改写过的 download_status 无从区分，保持原样。
            n = conn.execute(text(f'SELECT COUNT(*) FROM {TABLE}')).scalar()
            print(f'\nOK  {TABLE} 共 {n} 行，repair_* 初始为 NULL（未修复过）')


if __name__ == '__main__':
    main()