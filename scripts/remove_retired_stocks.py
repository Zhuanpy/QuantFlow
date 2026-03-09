"""删除已退市股票数据"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App.config.secrets import Secrets
import pymysql

RETIRED_CODES = ['002013', '600837', '601989']
TABLES = ['data_stock_daily', 'data_daily_task_status']

conn = pymysql.connect(
    host=Secrets.get_db_host(), port=int(Secrets.get_db_port()),
    user=Secrets.get_db_user(), password=Secrets.get_db_password(),
    database=Secrets.get_db_name(), charset='utf8mb4'
)
cur = conn.cursor()

print("=== 退市股票数据清理 ===\n")

# 统计
for code in RETIRED_CODES:
    for table in TABLES:
        cur.execute("SELECT COUNT(*) FROM `%s` WHERE stock_code = %%s" % table, (code,))
        cnt = cur.fetchone()[0]
        print(f"  {table} | {code} : {cnt} 条")

print("\n--- 开始删除 ---")
total_deleted = 0
for code in RETIRED_CODES:
    for table in TABLES:
        cur.execute("DELETE FROM `%s` WHERE stock_code = %%s" % table, (code,))
        deleted = cur.rowcount
        total_deleted += deleted
        print(f"  {table} | {code} : 删除 {deleted} 条")

conn.commit()
print(f"\n共删除 {total_deleted} 条记录")
cur.close()
conn.close()
