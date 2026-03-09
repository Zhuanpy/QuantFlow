# -*- coding: utf-8 -*-
"""
整理 2025 年 1m CSV 数据脚本

思路（数据库驱动）：
1. 查询 data_stock_daily 表 → 获取所有已维护的股票列表
2. 查询每只股票 2025 年已有的日线日期 → 找出缺失日期
3. 遍历 quarters/2025/Q1~Q4 的 1m CSV 文件 → 只处理需要的股票
4. 统一 CSV 格式 → 转存为 parquet（最新格式）
5. 聚合 1m → 日线 → 写入 data_stock_daily
6. 更新 data_daily_task_status

用法:
    python scripts/migrate_2025_1m_csv.py                # 预览模式（默认 dry-run）
    python scripts/migrate_2025_1m_csv.py --execute       # 正式执行
    python scripts/migrate_2025_1m_csv.py --codes 000001 600519  # 只处理指定股票
    python scripts/migrate_2025_1m_csv.py --skip-parquet  # 跳过 parquet 转存，只补日线
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
QUARTER_LIST = ['Q1', 'Q2', 'Q3', 'Q4']
BATCH_SIZE = 500


# 直接读取 .env 文件获取数据库配置，避免触发 Flask 导入链
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


def get_connection():
    return pymysql.connect(
        host=DB_CONFIG['host'],
        port=DB_CONFIG['port'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        database=DB_CONFIG['database'],
        charset='utf8mb4'
    )


# ==================== Step 1: 查数据库 ====================

def get_all_stock_codes(conn):
    """从 data_stock_daily 获取所有已维护的股票代码"""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM data_stock_daily ORDER BY stock_code")
        return [row[0] for row in cur.fetchall()]


def get_stock_daily_dates_2025(conn, stock_code):
    """获取某只股票 2025 年已有的日线日期集合"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `date` FROM data_stock_daily "
            "WHERE stock_code = %s AND `date` >= '2025-01-01' AND `date` <= '2025-12-31'",
            (stock_code,)
        )
        return {row[0] for row in cur.fetchall()}


# ==================== Step 2: 扫描 1m CSV 文件 ====================

def scan_csv_files():
    """
    扫描 quarters/2025/Q1~Q4 下所有 CSV 文件
    返回 {stock_code: [(quarter, file_path), ...]}
    """
    result = {}
    for q in QUARTER_LIST:
        q_dir = QUARTERS_DIR / q
        if not q_dir.exists():
            continue
        for f in sorted(q_dir.iterdir()):
            if not f.suffix.lower() == '.csv':
                continue
            code = f.stem
            # 跳过板块数据
            if code.upper().startswith('BK'):
                continue
            if code not in result:
                result[code] = []
            result[code].append((q, str(f)))
    return result


# ==================== Step 3: 读取并标准化 CSV ====================

def read_1m_csv(file_path, quarter):
    """
    读取 1m CSV 文件，自动处理两种格式：
    - Q1~Q3 标准格式: date,open,close,high,low,volume,money
    - Q4 异常格式: open,close,high,low,volume,money,year,month,day,hour,minute,date (有 BOM)
    """
    try:
        df = pd.read_csv(file_path, encoding='utf-8-sig')
    except Exception:
        df = pd.read_csv(file_path, encoding='utf-8')

    if df.empty:
        return pd.DataFrame()

    # 统一列名（去除空格和 BOM 残留）
    df.columns = [c.strip().lstrip('\ufeff') for c in df.columns]

    # 标准列
    required = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']

    # 检查是否为 Q4 异常格式（date 不是第一列，有 year/month/day 列）
    if 'year' in df.columns and 'month' in df.columns:
        # Q4 格式：确保 date 列存在
        if 'date' not in df.columns:
            # 用 year,month,day,hour,minute 拼接
            df['date'] = pd.to_datetime(
                df[['year', 'month', 'day', 'hour', 'minute']].astype(str).agg(
                    lambda r: f"{r['year']}-{r['month'].zfill(2)}-{r['day'].zfill(2)} "
                              f"{r['hour'].zfill(2)}:{r['minute'].zfill(2)}:00",
                    axis=1
                )
            )
        # 只保留标准列
        df = df[required].copy()

    # 确保所有必需列存在
    missing = set(required) - set(df.columns)
    if missing:
        print(f"    警告: {file_path} 缺少列 {missing}，跳过")
        return pd.DataFrame()

    df = df[required].copy()
    df['date'] = pd.to_datetime(df['date'])

    # 数值类型转换
    for col in ['open', 'close', 'high', 'low']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)
    df['money'] = pd.to_numeric(df['money'], errors='coerce').fillna(0).astype(int)

    # 去重排序
    df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)

    return df


# ==================== Step 4: 保存 parquet ====================

def save_as_parquet(df, stock_code, quarter):
    """
    将 1m 数据保存为 parquet 格式（最新标准）
    路径: quarters/2025/{quarter}/{stock_code}.parquet
    如果已有 parquet 文件则合并去重
    """
    parquet_dir = QUARTERS_DIR / quarter
    parquet_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = parquet_dir / f'{stock_code}.parquet'

    # 如果已有 parquet，合并
    if parquet_path.exists():
        try:
            existing = pd.read_parquet(parquet_path)
            existing['date'] = pd.to_datetime(existing['date'])
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=['date'], keep='last')
            df = df.sort_values('date').reset_index(drop=True)
        except Exception as e:
            print(f"    警告: 读取已有 parquet 失败 ({e})，将覆盖")

    df.to_parquet(str(parquet_path), index=False, engine='pyarrow')
    return str(parquet_path)


# ==================== Step 5: 聚合日线 ====================

def aggregate_to_daily(df_1m):
    """
    将 1m 数据聚合为日线 OHLCV
    处理 open=0 的问题：如果 open 为 0，用该分钟的 close 替代
    """
    df = df_1m.copy()
    df['date'] = pd.to_datetime(df['date'])

    # 修复 open=0 的问题
    mask = df['open'] == 0
    if mask.any():
        df.loc[mask, 'open'] = df.loc[mask, 'close']

    daily = df.groupby(df['date'].dt.date).agg(
        open=('open', 'first'),
        close=('close', 'last'),
        high=('high', 'max'),
        low=('low', 'min'),
        volume=('volume', 'sum'),
        money=('money', 'sum'),
    ).reset_index()

    daily = daily.rename(columns={'date': 'date'})
    return daily


# ==================== Step 6: 写入数据库 ====================

def batch_upsert_daily(conn, stock_code, df):
    """批量写入日线数据 INSERT ... ON DUPLICATE KEY UPDATE"""
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


def batch_upsert_task_status(conn, stock_code, dates):
    """批量更新 data_daily_task_status 标记 is_1m_downloaded 和 is_daily_processed"""
    if not dates:
        return

    sql = """
    INSERT INTO `data_daily_task_status`
        (`stock_code`, `date`,
         `is_1m_downloaded`, `is_1m_cleaned`, `is_15m_generated`,
         `is_macd_calculated`, `is_volume_processed`, `is_daily_processed`,
         `is_feature_generated`, `is_rnn_predicted`, `is_board_downloaded`, `is_fund_analyzed`)
    VALUES (%s, %s, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    ON DUPLICATE KEY UPDATE
        `is_1m_downloaded` = 1,
        `is_daily_processed` = 1
    """

    rows = [(stock_code, d) for d in dates]
    with conn.cursor() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            cur.executemany(sql, rows[i:i + BATCH_SIZE])
    conn.commit()


# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(description='整理 2025 年 1m CSV 数据')
    parser.add_argument('--execute', action='store_true', help='正式执行（默认为预览模式）')
    parser.add_argument('--codes', nargs='+', help='只处理指定股票代码')
    parser.add_argument('--skip-parquet', action='store_true', help='跳过 parquet 转存')
    args = parser.parse_args()

    dry_run = not args.execute
    conn = get_connection()

    print("=" * 70)
    print(f"  整理 2025 年 1m CSV 数据")
    print(f"  模式: {'预览 (加 --execute 正式执行)' if dry_run else '正式执行'}")
    print(f"  数据目录: {QUARTERS_DIR}")
    print("=" * 70)

    # Step 1: 查数据库获取股票列表
    print("\n[Step 1] 查询数据库已维护的股票列表...")
    db_stock_codes = set(get_all_stock_codes(conn))
    print(f"  data_stock_daily 中共有 {len(db_stock_codes)} 只股票")

    # Step 2: 扫描 1m CSV 文件
    print("\n[Step 2] 扫描 2025 年 1m CSV 文件...")
    csv_files = scan_csv_files()
    print(f"  共发现 {len(csv_files)} 只股票的 CSV 文件")
    for q in QUARTER_LIST:
        q_count = sum(1 for code, items in csv_files.items() for qq, _ in items if qq == q)
        print(f"    {q}: {q_count} 个文件")

    # 确定需要处理的股票
    if args.codes:
        target_codes = [c for c in args.codes if c in csv_files]
        print(f"\n  指定处理 {len(target_codes)} 只股票")
    else:
        # 取交集：数据库中有 AND CSV 文件中有
        target_codes = sorted(db_stock_codes & set(csv_files.keys()))
        # 也处理 CSV 中有但数据库中没有的（新股票）
        new_codes = sorted(set(csv_files.keys()) - db_stock_codes)
        print(f"\n  数据库中已有 + CSV 可用: {len(target_codes)} 只")
        print(f"  CSV 中有但数据库中无（新增）: {len(new_codes)} 只")
        target_codes = target_codes + new_codes

    if not target_codes:
        print("\n  没有需要处理的股票，退出")
        conn.close()
        return

    # Step 3~6: 逐只处理
    print(f"\n[Step 3~6] 开始处理 {len(target_codes)} 只股票...")
    print("-" * 70)

    stats = {
        'total': len(target_codes),
        'parquet_saved': 0,
        'daily_added': 0,
        'daily_updated': 0,
        'skipped': 0,
        'failed': 0,
        'failed_codes': [],
    }
    start_time = time.time()

    for i, code in enumerate(target_codes, 1):
        try:
            # 获取该股票 2025 年已有日线日期
            existing_dates = get_stock_daily_dates_2025(conn, code)

            # 读取所有季度的 CSV 并合并
            all_1m = []
            quarters_with_data = []
            for quarter, file_path in csv_files.get(code, []):
                df_1m = read_1m_csv(file_path, quarter)
                if not df_1m.empty:
                    all_1m.append((quarter, df_1m))
                    quarters_with_data.append(quarter)

            if not all_1m:
                stats['skipped'] += 1
                continue

            # 按季度分别保存 parquet
            total_1m_records = 0
            for quarter, df_1m in all_1m:
                total_1m_records += len(df_1m)
                if not args.skip_parquet and not dry_run:
                    save_as_parquet(df_1m, code, quarter)
                    stats['parquet_saved'] += 1

            # 合并所有季度 1m 数据 → 聚合日线
            combined_1m = pd.concat([df for _, df in all_1m], ignore_index=True)
            combined_1m = combined_1m.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
            daily_df = aggregate_to_daily(combined_1m)

            # 计算新增/更新数量
            daily_dates = set(daily_df['date'].tolist())
            new_dates = daily_dates - existing_dates
            update_dates = daily_dates & existing_dates

            if dry_run:
                prefix = f"[{i}/{stats['total']}] {code}"
                print(f"  {prefix}  1m={total_1m_records}条  "
                      f"季度={','.join(quarters_with_data)}  "
                      f"日线={len(daily_df)}天  "
                      f"新增={len(new_dates)}  更新={len(update_dates)}  "
                      f"{'parquet待转存' if not args.skip_parquet else ''}")
            else:
                # 写入日线
                batch_upsert_daily(conn, code, daily_df)
                stats['daily_added'] += len(new_dates)
                stats['daily_updated'] += len(update_dates)

                # 更新任务状态
                batch_upsert_task_status(conn, code, list(daily_dates))

                # parquet 已在上面保存
                prefix = f"[{i}/{stats['total']}] {code}"
                print(f"  {prefix}  1m={total_1m_records} -> parquet  "
                      f"daily +{len(new_dates)} upd={len(update_dates)}")

        except Exception as e:
            stats['failed'] += 1
            stats['failed_codes'].append(code)
            print(f"  [{i}/{stats['total']}] {code}  失败: {str(e)[:80]}")

        # 每 100 只显示进度
        if i % 100 == 0:
            elapsed = time.time() - start_time
            eta = elapsed / i * (stats['total'] - i)
            print(f"  --- 进度 {i}/{stats['total']}  "
                  f"耗时 {elapsed:.0f}s  预计剩余 {eta:.0f}s ---")

    # 汇总
    elapsed = time.time() - start_time
    print()
    print("=" * 70)
    print(f"  完成！耗时 {elapsed:.1f} 秒")
    if dry_run:
        print(f"  [预览模式] 以上为预计操作，加 --execute 正式执行")
    else:
        print(f"  Parquet 转存: {stats['parquet_saved']} 个文件")
        print(f"  日线新增: {stats['daily_added']} 条  更新: {stats['daily_updated']} 条")
    print(f"  跳过（无数据）: {stats['skipped']} 只  失败: {stats['failed']} 只")
    if stats['failed_codes']:
        print(f"  失败代码: {', '.join(stats['failed_codes'][:30])}")
    print("=" * 70)

    conn.close()


if __name__ == '__main__':
    main()
