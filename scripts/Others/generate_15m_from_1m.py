# -*- coding: utf-8 -*-
"""
从已有 1m 数据生成 15m 数据

思路（数据库驱动）：
1. 查询 data_daily_task_status → 找出 is_1m_downloaded=1 但 is_15m_generated=0 的股票+日期
2. 读取 1m 数据：优先 parquet 文件，不足的日期从 MySQL (data1m{year}) 补充
3. 重采样 1m → 15m（open=first, close=last, high=max, low=min, volume=sum, money=sum）
4. 保存到 data/data/15m/{stock_code}.parquet（追加合并）
5. 更新 data_daily_task_status.is_15m_generated = 1

用法:
    python scripts/generate_15m_from_1m.py               # 预览模式
    python scripts/generate_15m_from_1m.py --execute      # 正式执行
    python scripts/generate_15m_from_1m.py --execute --all # 忽略 task_status，处理所有有 parquet 的股票
    python scripts/generate_15m_from_1m.py --codes 000001 600519  # 只处理指定股票
"""
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql
import pandas as pd

# ==================== 配置 ====================
PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QUARTERS_DIR = PROJECT_ROOT / 'data' / 'data' / 'quarters' / '2025'
OUTPUT_15M_DIR = PROJECT_ROOT / 'data' / 'data' / '15m'
QUARTER_LIST = ['Q1', 'Q2', 'Q3', 'Q4']
BATCH_SIZE = 500


def _load_db_config():
    """从 .env 文件加载数据库配置"""
    env_file = PROJECT_ROOT / '.env'
    env_vars = {}
    if env_file.exists():
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return {
        'host': env_vars.get('DB_HOST', os.getenv('DB_HOST', 'localhost')),
        'port': int(env_vars.get('DB_PORT', os.getenv('DB_PORT', '3306'))),
        'user': env_vars.get('DB_USER', os.getenv('DB_USER', 'root')),
        'password': env_vars.get('DB_PASSWORD', os.getenv('DB_PASSWORD', '')),
        'database': env_vars.get('DB_NAME', os.getenv('DB_NAME', 'quanttradingsystem')),
    }


DB_CONFIG = _load_db_config()


def get_connection(database=None):
    return pymysql.connect(
        host=DB_CONFIG['host'],
        port=DB_CONFIG['port'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        database=database or DB_CONFIG['database'],
        charset='utf8mb4'
    )


# ==================== Step 1: 查数据库 ====================

def get_stocks_needing_15m(conn):
    """查询 is_1m_downloaded=1 但 is_15m_generated=0 的股票列表（含年份信息）"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stock_code, YEAR(`date`) as yr, COUNT(*) as cnt "
            "FROM data_daily_task_status "
            "WHERE is_1m_downloaded = 1 AND is_15m_generated = 0 "
            "GROUP BY stock_code, YEAR(`date`) "
            "ORDER BY stock_code, yr"
        )
        # 返回 {stock_code: {year: count, ...}, ...}
        result = {}
        for row in cur.fetchall():
            code, yr, cnt = row
            if code not in result:
                result[code] = {}
            result[code][yr] = cnt
        return result


def get_pending_dates(conn, stock_code, year=None):
    """获取某只股票待生成 15m 的日期"""
    with conn.cursor() as cur:
        if year:
            cur.execute(
                "SELECT `date` FROM data_daily_task_status "
                "WHERE stock_code = %s AND is_1m_downloaded = 1 AND is_15m_generated = 0 "
                "AND YEAR(`date`) = %s",
                (stock_code, year)
            )
        else:
            cur.execute(
                "SELECT `date` FROM data_daily_task_status "
                "WHERE stock_code = %s AND is_1m_downloaded = 1 AND is_15m_generated = 0",
                (stock_code,)
            )
        return {row[0] for row in cur.fetchall()}


# ==================== Step 2: 读取 1m parquet ====================

def scan_parquet_files():
    """扫描 quarters/2025/Q1~Q4 下所有 parquet 文件，返回 {stock_code: [file_path, ...]}"""
    result = {}
    for q in QUARTER_LIST:
        q_dir = QUARTERS_DIR / q
        if not q_dir.exists():
            continue
        for f in sorted(q_dir.iterdir()):
            if f.suffix.lower() != '.parquet':
                continue
            code = f.stem
            if code.upper().startswith('BK'):
                continue
            if code not in result:
                result[code] = []
            result[code].append(str(f))
    return result


def load_1m_from_parquet(file_paths):
    """读取并合并多个季度的 1m parquet 文件"""
    all_dfs = []
    for fp in file_paths:
        try:
            df = pd.read_parquet(fp)
            df['date'] = pd.to_datetime(df['date'])
            all_dfs.append(df)
        except Exception as e:
            print(f"    warn: read {fp} failed: {e}")
    if not all_dfs:
        return pd.DataFrame()
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
    return combined


def load_1m_from_mysql(stock_code, year, dates=None):
    """从 MySQL data1m{year} 数据库读取 1m 数据，可选只读指定日期"""
    db_name = f'data1m{year}'
    table_name = f'data_1m_{stock_code}'
    try:
        conn_1m = get_connection(database=db_name)
        with conn_1m.cursor() as cur:
            cur.execute(f"SHOW TABLES LIKE '{table_name}'")
            if not cur.fetchone():
                conn_1m.close()
                return pd.DataFrame()
            if dates:
                # 只读取指定日期的数据
                date_strs = [d.strftime('%Y-%m-%d') for d in dates]
                placeholders = ','.join(['%s'] * len(date_strs))
                cur.execute(
                    f"SELECT date, open, close, high, low, volume, money "
                    f"FROM `{table_name}` WHERE DATE(date) IN ({placeholders}) ORDER BY date",
                    date_strs
                )
            else:
                cur.execute(f"SELECT date, open, close, high, low, volume, money FROM `{table_name}` ORDER BY date")
            rows = cur.fetchall()
        conn_1m.close()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=['date', 'open', 'close', 'high', 'low', 'volume', 'money'])
        df['date'] = pd.to_datetime(df['date'])
        return df
    except Exception:
        return pd.DataFrame()


def load_1m_data(stock_code, parquet_files_list, pending_dates):
    """
    加载 1m 数据：优先 parquet，缺失日期从 MySQL 补充
    返回 (df_1m, covered_dates)
    """
    all_dfs = []

    # 1. 从 parquet 加载
    if parquet_files_list:
        df_pq = load_1m_from_parquet(parquet_files_list)
        if not df_pq.empty:
            all_dfs.append(df_pq)

    # 汇总已有日期
    if all_dfs:
        covered = set(pd.concat(all_dfs)['date'].dt.date.unique())
    else:
        covered = set()

    # 2. 找出 parquet 未覆盖的日期，按年份从 MySQL 只读缺失日期
    missing_dates = pending_dates - covered
    if missing_dates:
        by_year = {}
        for d in missing_dates:
            by_year.setdefault(d.year, []).append(d)
        for yr, yr_dates in sorted(by_year.items()):
            df_mysql = load_1m_from_mysql(stock_code, yr, yr_dates)
            if not df_mysql.empty:
                all_dfs.append(df_mysql)

    if not all_dfs:
        return pd.DataFrame(), set()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
    covered = set(combined['date'].dt.date.unique())
    return combined, covered


# ==================== Step 3: 重采样 1m -> 15m ====================

def resample_1m_to_15m(df_1m):
    """
    将 1m 数据重采样为 15m
    与 ResampleData.resample_fun(data, '15T') 逻辑一致
    """
    if df_1m.empty:
        return pd.DataFrame()

    df = df_1m.copy()
    df['date'] = pd.to_datetime(df['date'])

    # 修复 open=0
    mask = df['open'] == 0
    if mask.any():
        df.loc[mask, 'open'] = df.loc[mask, 'close']

    df['index_date'] = df['date']
    df = df.set_index('index_date')

    resampled = df.resample('15min', closed='right', label='right').agg({
        'open': 'first',
        'close': 'last',
        'high': 'max',
        'low': 'min',
        'volume': 'sum',
        'money': 'sum'
    }).reset_index()

    resampled = resampled.rename(columns={'index_date': 'date'}).dropna(how='any')
    resampled = resampled[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]
    return resampled


# ==================== Step 4: 保存 15m parquet ====================

def save_15m_parquet(df_15m, stock_code):
    """
    保存 15m 数据到 data/data/15m/{stock_code}.parquet
    如果已存在则合并去重
    """
    OUTPUT_15M_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = OUTPUT_15M_DIR / f'{stock_code}.parquet'

    if parquet_path.exists():
        try:
            existing = pd.read_parquet(parquet_path)
            existing['date'] = pd.to_datetime(existing['date'])
            df_15m = pd.concat([existing, df_15m], ignore_index=True)
            df_15m = df_15m.drop_duplicates(subset=['date'], keep='last')
            df_15m = df_15m.sort_values('date').reset_index(drop=True)
        except Exception:
            pass

    # 删除同名旧 csv（如果有）
    csv_path = OUTPUT_15M_DIR / f'{stock_code}.csv'
    if csv_path.exists():
        try:
            existing_csv = pd.read_csv(str(csv_path))
            existing_csv['date'] = pd.to_datetime(existing_csv['date'])
            df_15m = pd.concat([existing_csv, df_15m], ignore_index=True)
            df_15m = df_15m.drop_duplicates(subset=['date'], keep='last')
            df_15m = df_15m.sort_values('date').reset_index(drop=True)
        except Exception:
            pass

    df_15m.to_parquet(str(parquet_path), index=False, engine='pyarrow')
    return str(parquet_path)


# ==================== Step 5: 更新 task_status ====================

def batch_update_15m_status(conn, stock_code, dates):
    """批量更新 is_15m_generated = 1"""
    if not dates:
        return
    sql = """
    UPDATE `data_daily_task_status`
    SET `is_15m_generated` = 1
    WHERE `stock_code` = %s AND `date` = %s
    """
    rows = [(stock_code, d) for d in dates]
    with conn.cursor() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            cur.executemany(sql, rows[i:i + BATCH_SIZE])
    conn.commit()


# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(description='从 1m 数据生成 15m 数据')
    parser.add_argument('--execute', action='store_true', help='正式执行（默认预览）')
    parser.add_argument('--codes', nargs='+', help='只处理指定股票代码')
    parser.add_argument('--all', action='store_true', help='处理所有有 parquet 的股票，忽略 task_status')
    args = parser.parse_args()

    dry_run = not args.execute
    conn = get_connection()

    print("=" * 70)
    print(f"  1m -> 15m resample")
    print(f"  mode: {'preview (add --execute)' if dry_run else 'EXECUTE'}")
    print(f"  source: parquet + MySQL fallback")
    print(f"  output: {OUTPUT_15M_DIR}")
    print("=" * 70)

    # Step 1: 扫描 parquet 文件
    print("\n[Step 1] scan 1m parquet files...")
    parquet_files = scan_parquet_files()
    print(f"  found {len(parquet_files)} stocks with parquet data")

    # Step 2: 确定需要处理的股票（只处理有 parquet 文件的股票）
    if args.codes:
        target_codes = sorted(c for c in args.codes if c in parquet_files)
        print(f"\n  specified: {len(target_codes)} stocks (with parquet)")
    elif args.all:
        target_codes = sorted(parquet_files.keys())
        print(f"\n  --all mode: processing all {len(target_codes)} stocks")
    else:
        print("\n[Step 2] query task_status for pending stocks...")
        pending_map = get_stocks_needing_15m(conn)
        # 只保留有 parquet 文件的股票
        target_codes = sorted(c for c in pending_map.keys() if c in parquet_files)
        total_pending = sum(
            sum(yrs.values()) for c, yrs in pending_map.items() if c in parquet_files
        )
        print(f"  pending in DB: {len(pending_map)} stocks")
        print(f"  with parquet: {len(target_codes)} stocks, {total_pending} records")

    if not target_codes:
        print("\n  nothing to process")
        conn.close()
        return

    # Step 3~5: 逐只处理
    print(f"\n[Step 3~5] processing {len(target_codes)} stocks...")
    print("-" * 70)

    stats = {
        'total': len(target_codes),
        'success': 0,
        'skipped': 0,
        'failed': 0,
        'total_15m_records': 0,
        'dates_updated': 0,
        'from_parquet': 0,
        'from_mysql': 0,
    }
    start_time = time.time()

    for i, code in enumerate(target_codes, 1):
        try:
            # 获取待处理日期
            pending_dates = get_pending_dates(conn, code)
            if not pending_dates:
                stats['skipped'] += 1
                continue

            # 读取 1m 数据（parquet + MySQL fallback）
            pq_files = parquet_files.get(code, [])
            df_1m, covered = load_1m_data(code, pq_files, pending_dates)
            if df_1m.empty:
                stats['skipped'] += 1
                continue

            # 重采样
            df_15m = resample_1m_to_15m(df_1m)
            if df_15m.empty:
                stats['skipped'] += 1
                continue

            # 只更新有数据覆盖的 pending 日期
            dates_to_update = list(pending_dates & covered)

            if dry_run:
                src = "pq" if pq_files else "mysql"
                print(f"  [{i}/{stats['total']}] {code}  "
                      f"1m={len(df_1m)}  15m={len(df_15m)}  "
                      f"upd={len(dates_to_update)}/{len(pending_dates)}  src={src}")
            else:
                # 保存 15m parquet
                save_15m_parquet(df_15m, code)

                # 更新 task_status
                batch_update_15m_status(conn, code, dates_to_update)

                stats['success'] += 1
                stats['total_15m_records'] += len(df_15m)
                stats['dates_updated'] += len(dates_to_update)
                if pq_files:
                    stats['from_parquet'] += 1
                else:
                    stats['from_mysql'] += 1

                print(f"  [{i}/{stats['total']}] {code}  "
                      f"1m={len(df_1m)}  15m={len(df_15m)}  "
                      f"upd={len(dates_to_update)} done")

        except Exception as e:
            stats['failed'] += 1
            print(f"  [{i}/{stats['total']}] {code}  FAILED: {str(e)[:80]}")

        if i % 100 == 0:
            elapsed = time.time() - start_time
            eta = elapsed / i * (stats['total'] - i)
            print(f"  --- progress {i}/{stats['total']}  "
                  f"elapsed {elapsed:.0f}s  eta {eta:.0f}s ---")

    # summary
    elapsed = time.time() - start_time
    print()
    print("=" * 70)
    print(f"  done! {elapsed:.1f}s")
    if dry_run:
        print(f"  [PREVIEW] add --execute to run for real")
    else:
        print(f"  saved: {stats['success']} stocks  "
              f"15m records: {stats['total_15m_records']}  "
              f"task dates updated: {stats['dates_updated']}")
        print(f"  source: parquet={stats['from_parquet']}  mysql={stats['from_mysql']}")
    print(f"  skipped: {stats['skipped']}  failed: {stats['failed']}")
    print("=" * 70)

    conn.close()


if __name__ == '__main__':
    main()
