"""
批量补充 1m 数据脚本

通过 pytdx 下载所有维护股票的 1 分钟K线数据，直接写入 MySQL。
pytdx 约能获取最近 4 个月（~96 个交易日）的 1m 数据。

数据源: pytdx（通达信协议）
存储: 直接 INSERT ... ON DUPLICATE KEY UPDATE 写入 data1m{year} 库

用法:
    python scripts/batch_download_1m.py                     # 下载全部股票
    python scripts/batch_download_1m.py --codes 600438 002142   # 指定股票
    python scripts/batch_download_1m.py --start-from 300001     # 断点续传
    python scripts/batch_download_1m.py --dry-run               # 预览不写入
"""
import sys
import os
import time
import argparse
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App.config.secrets import Secrets
import pymysql
import pandas as pd

# ==================== 配置 ====================
BATCH_SIZE = 1000         # 每批写入行数
REQUEST_INTERVAL = 0.15   # 每只股票间隔(秒)
RETRY_MAX = 2
RETRY_WAIT = 3

TDX_SERVERS = [
    ('60.191.117.167', 7709),
    ('115.238.56.198', 7709),
    ('218.75.126.9', 7709),
    ('124.160.88.183', 7709),
    ('112.95.140.74', 7709),
    ('221.231.141.60', 7709),
]


def get_connection():
    return pymysql.connect(
        host=Secrets.get_db_host(),
        port=int(Secrets.get_db_port()),
        user=Secrets.get_db_user(),
        password=Secrets.get_db_password(),
        charset='utf8mb4'
    )


def get_stock_list(conn):
    """从 data_stock_daily 获取所有已维护的股票代码"""
    db_name = Secrets.get_db_name()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM `{}`.data_stock_daily ORDER BY stock_code".format(db_name))
        return [row[0] for row in cur.fetchall()]


def get_market_code(stock_code):
    """0=深圳, 1=上海"""
    if stock_code.startswith(('6', '9', '5')):
        return 1
    return 0


def connect_tdx():
    """连接 pytdx 服务器"""
    from pytdx.hq import TdxHq_API
    api = TdxHq_API()
    for host, port in TDX_SERVERS:
        try:
            if api.connect(host, port, time_out=5):
                print(f"  pytdx 已连接: {host}:{port}")
                return api
        except Exception:
            continue
    raise ConnectionError("所有 pytdx 服务器连接失败")


def download_1m_pytdx(api, stock_code):
    """
    通过 pytdx 拉取尽可能多的 1m 数据
    category=8 为 1 分钟线，每次最多 800 条，循环拉取直到没有数据
    """
    market = get_market_code(stock_code)
    all_data = []
    offset = 0

    while True:
        data = api.get_security_bars(8, market, stock_code, offset, 800)
        if not data or len(data) == 0:
            break
        all_data.extend(data)
        if len(data) < 800:
            break
        offset += 800

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df = df.rename(columns={'datetime': 'date', 'vol': 'volume', 'amount': 'money'})
    df['date'] = pd.to_datetime(df['date'])

    # 过滤交易时间
    t = df['date'].dt.time
    from datetime import time as dt_time
    mask = (
        ((t >= dt_time(9, 31)) & (t <= dt_time(11, 30))) |
        ((t >= dt_time(13, 1)) & (t <= dt_time(15, 0)))
    )
    df = df[mask]

    df = df[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]
    df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)

    return df


def ensure_database_exists(conn, year):
    """确保 data1m{year} 数据库存在"""
    db_name = f"data1m{year}"
    with conn.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS `{}` DEFAULT CHARACTER SET utf8mb4".format(db_name))


def ensure_table_exists(conn, year, stock_code):
    """确保 1m 表存在"""
    db_name = f"data1m{year}"
    table_name = f"data_1m_{stock_code}"
    create_sql = """
    CREATE TABLE IF NOT EXISTS `{}`.`{}` (
        `date`   DATETIME NOT NULL PRIMARY KEY COMMENT '交易时间',
        `open`   FLOAT NOT NULL COMMENT '开盘价',
        `close`  FLOAT NOT NULL COMMENT '收盘价',
        `high`   FLOAT NOT NULL COMMENT '最高价',
        `low`    FLOAT NOT NULL COMMENT '最低价',
        `volume` BIGINT NOT NULL COMMENT '成交量',
        `money`  BIGINT NOT NULL COMMENT '成交额'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='1分钟K线 {}'
    """.format(db_name, table_name, stock_code)
    with conn.cursor() as cur:
        cur.execute(create_sql)


def batch_upsert_1m(conn, year, stock_code, df):
    """批量写入 1m 数据"""
    if df.empty:
        return 0

    db_name = f"data1m{year}"
    table_name = f"data_1m_{stock_code}"

    sql = """
    INSERT INTO `{}`.`{}` (`date`, `open`, `close`, `high`, `low`, `volume`, `money`)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        `open`=VALUES(`open`), `close`=VALUES(`close`),
        `high`=VALUES(`high`), `low`=VALUES(`low`),
        `volume`=VALUES(`volume`), `money`=VALUES(`money`)
    """.format(db_name, table_name)

    rows = []
    for _, r in df.iterrows():
        rows.append((
            r['date'].strftime('%Y-%m-%d %H:%M:%S'),
            float(r['open']), float(r['close']),
            float(r['high']), float(r['low']),
            int(r['volume']), int(r['money']),
        ))

    with conn.cursor() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            cur.executemany(sql, rows[i:i + BATCH_SIZE])

    conn.commit()
    return len(rows)


def get_existing_count(conn, year, stock_code):
    """获取已有记录数"""
    db_name = f"data1m{year}"
    table_name = f"data_1m_{stock_code}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM `{}`.`{}`".format(db_name, table_name))
            return cur.fetchone()[0]
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description='批量补充1m数据（pytdx）')
    parser.add_argument('--codes', nargs='+', help='只下载指定股票')
    parser.add_argument('--start-from', type=str, help='断点续传')
    parser.add_argument('--dry-run', action='store_true', help='预览模式')
    args = parser.parse_args()

    conn = get_connection()

    if args.codes:
        stock_codes = args.codes
    else:
        stock_codes = get_stock_list(conn)

    if args.start_from:
        try:
            idx = stock_codes.index(args.start_from)
            stock_codes = stock_codes[idx:]
            print(f"从 {args.start_from} 开始，跳过前 {idx} 只")
        except ValueError:
            print(f"未找到 {args.start_from}，从头开始")

    total = len(stock_codes)
    print("=" * 70)
    print(f"  批量补充 1m 数据 (pytdx)")
    print(f"  股票数: {total}   模式: {'预览' if args.dry_run else '正式写入'}")
    print(f"  pytdx 约能获取最近 ~4 个月 1m 数据")
    print("=" * 70)

    api = connect_tdx()

    success_count = 0
    fail_count = 0
    total_bars = 0
    failed_codes = []
    year_stats = defaultdict(int)  # 每年写入多少条
    start_time = time.time()

    for i, code in enumerate(stock_codes, 1):
        prefix = f"[{i}/{total}] {code}"

        # 下载
        df = pd.DataFrame()
        last_error = ""
        for retry in range(RETRY_MAX + 1):
            try:
                df = download_1m_pytdx(api, code)
                break
            except Exception as e:
                last_error = str(e)
                if retry < RETRY_MAX:
                    print(f"  {prefix} 重试 {retry+1}/{RETRY_MAX}")
                    time.sleep(RETRY_WAIT)
                    try:
                        api.disconnect()
                    except Exception:
                        pass
                    api = connect_tdx()

        if df.empty:
            fail_count += 1
            failed_codes.append(code)
            print(f"  {prefix} 失败 - {last_error[:60] if last_error else '空数据'}")
            time.sleep(REQUEST_INTERVAL)
            continue

        # 按年分组写入
        df['year'] = df['date'].dt.year
        years_in_data = df['year'].unique()

        if args.dry_run:
            date_min = df['date'].min().strftime('%Y-%m-%d')
            date_max = df['date'].max().strftime('%Y-%m-%d')
            days = df['date'].dt.date.nunique()
            print(f"  {prefix} [预览] {len(df)} 条  {days}天  "
                  f"{date_min} ~ {date_max}  年份: {sorted(years_in_data)}")
            success_count += 1
            total_bars += len(df)
        else:
            try:
                written = 0
                for year in sorted(years_in_data):
                    year_df = df[df['year'] == year].drop(columns=['year'])

                    ensure_database_exists(conn, year)
                    ensure_table_exists(conn, year, code)

                    old_count = get_existing_count(conn, year, code)
                    batch_upsert_1m(conn, year, code, year_df)
                    new_count = get_existing_count(conn, year, code)

                    added = new_count - old_count
                    written += len(year_df)
                    year_stats[year] += max(added, 0)

                date_min = df['date'].min().strftime('%Y-%m-%d')
                date_max = df['date'].max().strftime('%Y-%m-%d')
                days = df['date'].dt.date.nunique()
                print(f"  {prefix} 完成  {written} 条  {days}天  "
                      f"{date_min} ~ {date_max}")
                success_count += 1
                total_bars += written
            except Exception as e:
                fail_count += 1
                failed_codes.append(code)
                print(f"  {prefix} 写入失败 - {str(e)[:60]}")

        time.sleep(REQUEST_INTERVAL)

        if i % 50 == 0:
            elapsed = time.time() - start_time
            eta = elapsed / i * (total - i)
            print(f"  --- 进度 {i}/{total}  耗时 {elapsed:.0f}s  "
                  f"剩余 {eta:.0f}s ---")

    try:
        api.disconnect()
    except Exception:
        pass

    elapsed = time.time() - start_time
    print()
    print("=" * 70)
    print(f"  完成！耗时 {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")
    print(f"  成功: {success_count}    失败: {fail_count}    "
          f"总条数: {total_bars:,}")
    if year_stats:
        print(f"  各年新增: {dict(sorted(year_stats.items()))}")
    if failed_codes:
        print(f"  失败: {', '.join(failed_codes[:30])}")
    print("=" * 70)

    conn.close()


if __name__ == '__main__':
    main()
