"""
批量补充日K数据脚本

从数据库读取所有已维护的股票代码，通过 pytdx 下载最近N年日线数据，
使用 INSERT ... ON DUPLICATE KEY UPDATE 高效写入。

数据源: pytdx（通达信协议，速度快、稳定）

用法:
    python scripts/batch_download_daily.py                  # 默认下载2年
    python scripts/batch_download_daily.py --days 730       # 指定天数
    python scripts/batch_download_daily.py --codes 600438 002142   # 只下指定股票
    python scripts/batch_download_daily.py --dry-run        # 预览，不写入
    python scripts/batch_download_daily.py --start-from 300001     # 断点续传
"""
import sys
import os
import time
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App.config.secrets import Secrets
import pymysql
import pandas as pd

# ==================== 配置 ====================
BATCH_SIZE = 500          # 每批写入行数
REQUEST_INTERVAL = 0.1    # pytdx 本地协议很快，间隔可以很短
RETRY_MAX = 2             # 单只股票最大重试次数
RETRY_WAIT = 3            # 重试等待(秒)

# pytdx 服务器列表（按优先级）
TDX_SERVERS = [
    ('60.191.117.167', 7709),
    ('115.238.56.198', 7709),
    ('218.75.126.9', 7709),
    ('124.160.88.183', 7709),
    ('112.95.140.74', 7709),
    ('221.231.141.60', 7709),
    ('119.147.212.81', 7709),
    ('59.175.238.38', 7709),
]


def get_connection():
    return pymysql.connect(
        host=Secrets.get_db_host(),
        port=int(Secrets.get_db_port()),
        user=Secrets.get_db_user(),
        password=Secrets.get_db_password(),
        database=Secrets.get_db_name(),
        charset='utf8mb4'
    )


def get_stock_list(conn):
    """从 data_stock_daily 获取所有已维护的股票代码"""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM data_stock_daily ORDER BY stock_code")
        return [row[0] for row in cur.fetchall()]


def get_stock_stats(conn, stock_code):
    """获取单只股票的现有记录统计"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), MIN(`date`), MAX(`date`) "
            "FROM data_stock_daily WHERE stock_code = %s",
            (stock_code,)
        )
        row = cur.fetchone()
        return {
            'count': row[0] or 0,
            'min_date': row[1],
            'max_date': row[2],
        }


def get_market_code(stock_code):
    """根据股票代码判断市场: 0=深圳, 1=上海"""
    if stock_code.startswith(('6', '9', '5')):
        return 1  # 上海
    return 0  # 深圳


def connect_tdx():
    """连接 pytdx 服务器，自动选择可用服务器"""
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


def download_daily_pytdx(api, stock_code, days=730):
    """
    使用 pytdx 下载日线数据

    pytdx get_security_bars: category=9 表示日线
    每次最多返回 800 条，需要分页拉取
    """
    market = get_market_code(stock_code)
    # 计算需要拉取的总条数（交易日约 250天/年）
    need_bars = int(days * 250 / 365) + 50  # 多拉一些确保覆盖

    all_data = []
    offset = 0
    page_size = 800  # pytdx 单次上限

    while offset < need_bars:
        count = min(page_size, need_bars - offset)
        data = api.get_security_bars(9, market, stock_code, offset, count)
        if not data or len(data) == 0:
            break
        all_data.extend(data)
        if len(data) < count:
            break  # 没有更多数据了
        offset += count

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)

    # pytdx 返回的列: datetime, open, close, high, low, vol, amount
    df = df.rename(columns={
        'datetime': 'date',
        'vol': 'volume',
        'amount': 'money',
    })

    df['date'] = pd.to_datetime(df['date']).dt.date

    # 按日期范围过滤
    cutoff_date = (datetime.now() - timedelta(days=days)).date()
    df = df[df['date'] >= cutoff_date]

    df = df[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]
    df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)

    return df


def batch_upsert(conn, stock_code, df):
    """
    批量写入日线数据，使用 INSERT ... ON DUPLICATE KEY UPDATE
    比逐条 ORM 查询快 50~100 倍
    """
    if df.empty:
        return 0

    sql = """
    INSERT INTO `data_stock_daily`
        (`stock_code`, `date`, `open`, `close`, `high`, `low`, `volume`, `money`)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        `open`   = VALUES(`open`),
        `close`  = VALUES(`close`),
        `high`   = VALUES(`high`),
        `low`    = VALUES(`low`),
        `volume` = VALUES(`volume`),
        `money`  = VALUES(`money`)
    """

    rows = []
    for _, r in df.iterrows():
        rows.append((
            stock_code, r['date'],
            float(r['open']), float(r['close']),
            float(r['high']), float(r['low']),
            int(r['volume']), int(r['money']),
        ))

    total_affected = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            cur.executemany(sql, batch)
            total_affected += cur.rowcount

    conn.commit()
    return total_affected


def main():
    parser = argparse.ArgumentParser(description='批量补充日K数据（pytdx数据源）')
    parser.add_argument('--days', type=int, default=730, help='下载天数（默认730=2年）')
    parser.add_argument('--codes', nargs='+', help='只下载指定股票代码')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不写入数据库')
    parser.add_argument('--start-from', type=str, help='从指定代码开始（断点续传）')
    args = parser.parse_args()

    conn = get_connection()

    # 获取股票列表
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
            print(f"未找到代码 {args.start_from}，从头开始")

    total = len(stock_codes)
    print("=" * 70)
    print(f"  批量补充日K数据 (pytdx)")
    print(f"  股票数: {total}   下载天数: {args.days}   "
          f"模式: {'预览' if args.dry_run else '正式写入'}")
    print("=" * 70)

    # 连接 pytdx
    api = connect_tdx()

    success_count = 0
    fail_count = 0
    skip_count = 0
    total_added = 0
    failed_codes = []
    start_time = time.time()

    for i, code in enumerate(stock_codes, 1):
        stats = get_stock_stats(conn, code)
        prefix = f"[{i}/{total}] {code}"

        # 下载
        df = pd.DataFrame()
        last_error = ""
        for retry in range(RETRY_MAX + 1):
            try:
                df = download_daily_pytdx(api, code, days=args.days)
                break
            except Exception as e:
                last_error = str(e)
                if retry < RETRY_MAX:
                    print(f"  {prefix} 重试 {retry + 1}/{RETRY_MAX} ({last_error[:60]})")
                    time.sleep(RETRY_WAIT)
                    # 重新连接
                    try:
                        api.disconnect()
                    except Exception:
                        pass
                    api = connect_tdx()

        if df.empty:
            fail_count += 1
            failed_codes.append(code)
            print(f"  {prefix} 失败 - {last_error[:80] if last_error else '返回空数据'}")
            time.sleep(REQUEST_INTERVAL)
            continue

        # 写入
        if args.dry_run:
            print(f"  {prefix} [预览] 获取 {len(df)} 条  "
                  f"({df['date'].min()} ~ {df['date'].max()})  "
                  f"现有 {stats['count']} 条")
            skip_count += 1
        else:
            try:
                batch_upsert(conn, code, df)
                new_stats = get_stock_stats(conn, code)
                added = new_stats['count'] - stats['count']
                total_added += max(added, 0)

                status = f"+{added}" if added > 0 else "仅更新"
                print(f"  {prefix} 完成  获取 {len(df)} 条  {status}  "
                      f"现有 {new_stats['count']} 条  "
                      f"({new_stats['min_date']} ~ {new_stats['max_date']})")
                success_count += 1
            except Exception as e:
                fail_count += 1
                failed_codes.append(code)
                print(f"  {prefix} 写入失败 - {str(e)[:80]}")

        time.sleep(REQUEST_INTERVAL)

        # 每 50 只显示进度
        if i % 50 == 0:
            elapsed = time.time() - start_time
            eta = elapsed / i * (total - i)
            print(f"  --- 进度 {i}/{total}  "
                  f"耗时 {elapsed:.0f}s  预计剩余 {eta:.0f}s ---")

    # 断开 pytdx
    try:
        api.disconnect()
    except Exception:
        pass

    # 汇总
    elapsed = time.time() - start_time
    print()
    print("=" * 70)
    print(f"  完成！耗时 {elapsed:.0f} 秒 ({elapsed / 60:.1f} 分钟)")
    if args.dry_run:
        print(f"  预览: {skip_count} 只    失败: {fail_count} 只")
    else:
        print(f"  成功: {success_count} 只    失败: {fail_count} 只    "
              f"新增记录: {total_added} 条")

    if failed_codes:
        print(f"  失败代码: {', '.join(failed_codes[:30])}"
              f"{'...' if len(failed_codes) > 30 else ''}")
    print("=" * 70)

    conn.close()


if __name__ == '__main__':
    main()
