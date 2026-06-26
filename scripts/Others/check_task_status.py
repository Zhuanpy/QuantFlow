"""检查 task_status 同步结果"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from App.config.secrets import Secrets
import pymysql

conn = pymysql.connect(
    host=Secrets.get_db_host(), port=int(Secrets.get_db_port()),
    user=Secrets.get_db_user(), password=Secrets.get_db_password(),
    database=Secrets.get_db_name(), charset='utf8mb4'
)
cur = conn.cursor()

# 1. task_status 总量
cur.execute("SELECT COUNT(*), SUM(is_1m_downloaded), COUNT(DISTINCT stock_code) FROM data_daily_task_status")
row = cur.fetchone()
print(f"task_status 总记录: {row[0]}, is_1m_downloaded=1: {row[1]}, 股票数: {row[2]}")

# 2. 看看具体某只股票
for code in ['600438', '002142', '000001']:
    cur.execute(
        "SELECT COUNT(*), SUM(is_1m_downloaded), MIN(`date`), MAX(`date`) "
        "FROM data_daily_task_status WHERE stock_code = %s", (code,)
    )
    r = cur.fetchone()
    print(f"  {code}: 总记录 {r[0]}, 1m已下载 {r[1]}, 范围 {r[2]} ~ {r[3]}")

    # 最近10条
    cur.execute(
        "SELECT `date`, is_1m_downloaded FROM data_daily_task_status "
        "WHERE stock_code = %s ORDER BY `date` DESC LIMIT 5", (code,)
    )
    for dr in cur.fetchall():
        print(f"    {dr[0]} -> is_1m_downloaded={dr[1]}")

# 3. 看看 daily_viewer 的 LEFT JOIN 是否能匹配
print("\n--- LEFT JOIN 测试 (600438 最近5天) ---")
cur.execute("""
    SELECT d.`date`, d.stock_code, t.is_1m_downloaded
    FROM data_stock_daily d
    LEFT JOIN data_daily_task_status t
        ON d.stock_code = t.stock_code AND d.`date` = t.`date`
    WHERE d.stock_code = '600438'
    ORDER BY d.`date` DESC LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {r[0]} | daily={r[1]} | is_1m_dl={r[2]}")

cur.close()
conn.close()
