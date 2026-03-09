import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from App.config.secrets import Secrets
import pymysql

conn = pymysql.connect(host=Secrets.get_db_host(), port=int(Secrets.get_db_port()),
    user=Secrets.get_db_user(), password=Secrets.get_db_password(),
    database=Secrets.get_db_name(), charset='utf8mb4')
cur = conn.cursor()

# 收盘47.30, volume=732496
cur.execute(
    "SELECT stock_code FROM data_stock_daily "
    "WHERE `date`='2026-03-06' AND close BETWEEN 47.2 AND 47.4"
)
for r in cur.fetchall():
    print(r)

# Check 1m batch download status
cur.execute("SHOW TABLES FROM data1m2025")
t2025 = cur.fetchall()
cur.execute("SHOW TABLES FROM data1m2026")
t2026 = cur.fetchall()
print(f"\ndata1m2025: {len(t2025)} tables")
print(f"data1m2026: {len(t2026)} tables")

cur.close()
conn.close()
