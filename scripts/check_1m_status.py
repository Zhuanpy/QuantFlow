"""检查 1m 数据现状"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App.config.secrets import Secrets
import pymysql

conn = pymysql.connect(
    host=Secrets.get_db_host(), port=int(Secrets.get_db_port()),
    user=Secrets.get_db_user(), password=Secrets.get_db_password(),
    charset='utf8mb4'
)
cur = conn.cursor()

# 1. 查看有哪些 data1m 数据库
cur.execute("SHOW DATABASES LIKE 'data1m%'")
dbs = [r[0] for r in cur.fetchall()]
print("=== 1m 年度数据库 ===")
print(f"  数据库列表: {dbs}")

# 2. 每个库有多少表、抽样统计
for db_name in dbs:
    cur.execute("USE `{}`".format(db_name))
    cur.execute("SHOW TABLES")
    tables = [r[0] for r in cur.fetchall()]

    # 分类统计表名前缀
    prefixed = [t for t in tables if t.startswith('data_1m_')]
    bare = [t for t in tables if not t.startswith('data_1m_')]

    # 抽样5张表看行数和日期范围
    sample_info = []
    for t in tables[:5]:
        cur.execute("SELECT COUNT(*), MIN(`date`), MAX(`date`) FROM `{}`".format(t))
        row = cur.fetchone()
        sample_info.append((t, row[0], row[1], row[2]))

    print(f"\n  {db_name}: 共 {len(tables)} 张表")
    print(f"    有前缀(data_1m_): {len(prefixed)}, 无前缀: {len(bare)}")
    if bare:
        print(f"    无前缀示例: {bare[:10]}")
    print(f"    抽样:")
    for name, cnt, min_d, max_d in sample_info:
        print(f"      {name}: {cnt} 条  {min_d} ~ {max_d}")

# 3. 查看 CSV 文件情况
print("\n=== 1m CSV 文件 ===")
data_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
if os.path.exists(data_root):
    csv_count = 0
    for root, dirs, files in os.walk(data_root):
        for f in files:
            if '1m' in root.lower() and f.endswith('.csv'):
                csv_count += 1
    print(f"  data/ 目录下 1m CSV 文件数: {csv_count}")
else:
    print(f"  data/ 目录不存在: {data_root}")

# 4. 查看 DailyTaskStatus 中 is_1m_downloaded 统计
cur.execute("USE `{}`".format(Secrets.get_db_name()))
cur.execute("""
    SELECT
        COUNT(*) as total,
        SUM(is_1m_downloaded) as downloaded,
        COUNT(DISTINCT stock_code) as stock_count,
        MIN(`date`) as min_date,
        MAX(`date`) as max_date
    FROM data_daily_task_status
""")
row = cur.fetchone()
print(f"\n=== DailyTaskStatus 1m下载状态 ===")
print(f"  总记录: {row[0]}, 已下载1m: {row[1]}, 涉及股票: {row[2]}")
print(f"  日期范围: {row[3]} ~ {row[4]}")

# 5. pytdx 能获取多少天 1m 数据
print("\n=== pytdx 1m 数据可获取量测试 ===")
try:
    from pytdx.hq import TdxHq_API
    api = TdxHq_API()
    if api.connect('60.191.117.167', 7709, time_out=5):
        # 尝试拉取最大量的 1m 数据
        all_bars = 0
        offset = 0
        first_date = None
        last_date = None
        while True:
            data = api.get_security_bars(8, 1, '600438', offset, 800)
            if not data or len(data) == 0:
                break
            if last_date is None:
                last_date = data[0]['datetime']
            first_date = data[-1]['datetime']
            all_bars += len(data)
            offset += 800
            if len(data) < 800:
                break
        api.disconnect()
        print(f"  600438 通过 pytdx 共能获取: {all_bars} 条 1m 数据")
        print(f"  时间范围: {first_date} ~ {last_date}")
        trading_days = all_bars / 240
        print(f"  约 {trading_days:.0f} 个交易日")
    else:
        print("  pytdx 连接失败")
except Exception as e:
    print(f"  pytdx 测试失败: {e}")

cur.close()
conn.close()
