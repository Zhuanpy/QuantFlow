"""
同步 1m 下载状态到 data_daily_task_status

扫描 data1m{year} 库中实际存在的 1m 数据，
将对应的 (stock_code, date) 的 is_1m_downloaded 标记为 True。

用法:
    python scripts/sync_1m_task_status.py
    python scripts/sync_1m_task_status.py --years 2025 2026    # 只同步指定年份
"""
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App.config.secrets import Secrets
import pymysql


def get_connection():
    return pymysql.connect(
        host=Secrets.get_db_host(),
        port=int(Secrets.get_db_port()),
        user=Secrets.get_db_user(),
        password=Secrets.get_db_password(),
        charset='utf8mb4'
    )


def main():
    parser = argparse.ArgumentParser(description='同步 1m 状态到 task_status')
    parser.add_argument('--years', nargs='+', type=int, help='只处理指定年份')
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    main_db = Secrets.get_db_name()

    # 1. 找出所有 data1m 数据库
    cur.execute("SHOW DATABASES LIKE 'data1m%'")
    all_dbs = [r[0] for r in cur.fetchall()]

    if args.years:
        dbs = [f"data1m{y}" for y in args.years if f"data1m{y}" in all_dbs]
    else:
        dbs = all_dbs

    print("=" * 70)
    print(f"  同步 1m 下载状态 → data_daily_task_status")
    print(f"  扫描数据库: {dbs}")
    print("=" * 70)

    total_synced = 0
    start_time = time.time()

    for db_name in dbs:
        cur.execute("SHOW TABLES FROM `{}`".format(db_name))
        tables = [r[0] for r in cur.fetchall()]
        stock_tables = [t for t in tables if t.startswith('data_1m_')]

        if not stock_tables:
            print(f"\n  {db_name}: 无股票表，跳过")
            continue

        print(f"\n  {db_name}: {len(stock_tables)} 张表")
        db_synced = 0

        for idx, table in enumerate(stock_tables, 1):
            stock_code = table.replace('data_1m_', '')

            # 获取该表所有交易日（取 date 列的日期部分去重）
            cur.execute(
                "SELECT DISTINCT DATE(`date`) as trade_date "
                "FROM `{}`.`{}` ORDER BY trade_date".format(db_name, table)
            )
            dates = [r[0] for r in cur.fetchall()]

            if not dates:
                continue

            # 批量 upsert 到 data_daily_task_status
            # 所有 boolean NOT NULL 字段需给默认值
            upsert_sql = (
                "INSERT INTO `{}`.`data_daily_task_status` "
                "(`stock_code`, `date`, `is_1m_downloaded`, "
                "`is_1m_cleaned`, `is_15m_generated`, `is_macd_calculated`, "
                "`is_volume_processed`, `is_daily_processed`, "
                "`is_feature_generated`, `is_rnn_predicted`, "
                "`is_board_downloaded`, `is_fund_analyzed`) "
                "VALUES (%s, %s, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0) "
                "ON DUPLICATE KEY UPDATE `is_1m_downloaded` = 1"
            ).format(main_db)

            rows = [(stock_code, d) for d in dates]
            cur.executemany(upsert_sql, rows)
            db_synced += len(rows)

            if idx % 50 == 0:
                conn.commit()
                print(f"    进度 {idx}/{len(stock_tables)}")

        conn.commit()
        total_synced += db_synced
        print(f"    同步 {db_synced} 条记录")

    elapsed = time.time() - start_time
    print()
    print("=" * 70)
    print(f"  完成！耗时 {elapsed:.0f}s  共同步 {total_synced:,} 条")
    print("=" * 70)

    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
