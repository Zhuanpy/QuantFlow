# -*- coding: utf-8 -*-
"""
修复缺失的1m数据

问题: 部分股票在 2025年12月~2026年3月之间存在1m数据缺失，
      导致15m数据也有缺口。
原因: 日常更新时 days = min(5, days)，无法追赶超过5天的数据缺口。
方案: 直接使用 pytdx 批量下载历史数据，按季度拆分保存，再重建15m。

用法:
    python scripts/fix_missing_1m_data.py                 # 扫描+修复
    python scripts/fix_missing_1m_data.py --dry-run       # 仅扫描不修复
    python scripts/fix_missing_1m_data.py --code 002475   # 只修复指定股票
"""
import sys
import os
import io
import time
import logging
from datetime import date, timedelta
from collections import defaultdict

# Windows GBK 编码兼容
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

from App import create_app
from App.exts import db

app = create_app()


# ── 交易日历（简单方式：排除周末，节假日靠实际数据判断） ──────────────
def get_trading_days(start: date, end: date) -> list:
    """生成 start~end 之间的工作日列表（排除周末）"""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # 周一~周五
            days.append(d)
        d += timedelta(days=1)
    return days


# ── 检查季度文件中包含哪些交易日 ────────────────────────────────────
def get_dates_in_quarter_file(stock_code: str, year: str, quarter: str) -> set:
    """读取季度文件，返回文件中包含的日期集合"""
    from App.utils.path_manager import get_stock_data_path

    file_path = get_stock_data_path(stock_code, data_type='1m',
                                    year=year, quarter=quarter, create=False)
    for fpath in [file_path, file_path.replace('.parquet', '.csv')]:
        if os.path.exists(fpath):
            try:
                if fpath.endswith('.parquet'):
                    df = pd.read_parquet(fpath, columns=['date'])
                else:
                    df = pd.read_csv(fpath, usecols=['date'], parse_dates=['date'])
                df['date'] = pd.to_datetime(df['date'])
                return set(df['date'].dt.date.unique())
            except Exception as e:
                logger.warning(f"{stock_code} 读取 {year}/{quarter} 失败: {e}")
                return set()
    return set()


# ── 分析一只股票的缺失情况 ──────────────────────────────────────
def analyze_stock_gaps(stock_code: str, expected_start: date, expected_end: date):
    """
    分析股票在 expected_start ~ expected_end 区间的数据缺失情况。
    返回: (missing_days: list[date], total_expected: int)
    """
    from App.codes.RnnDataFile.save_download import get_quarter_from_month

    expected_trading_days = get_trading_days(expected_start, expected_end)
    if not expected_trading_days:
        return [], 0

    # 按季度分组检查
    existing_dates = set()
    quarters_checked = set()

    for d in expected_trading_days:
        q_key = (str(d.year), get_quarter_from_month(d.month))
        if q_key not in quarters_checked:
            quarters_checked.add(q_key)
            dates_in_file = get_dates_in_quarter_file(stock_code, q_key[0], q_key[1])
            existing_dates.update(dates_in_file)

    missing = [d for d in expected_trading_days if d not in existing_dates]
    return missing, len(expected_trading_days)


# ── 按季度拆分并保存1m数据 ─────────────────────────────────────
def save_1m_by_quarter(data: pd.DataFrame, stock_code: str):
    """将1m数据按季度拆分，分别保存（合并已有数据）"""
    from App.codes.RnnDataFile.save_download import get_quarter_from_month
    from App.utils.path_manager import get_stock_data_path

    data = data.copy()
    data['date'] = pd.to_datetime(data['date'])
    data['_year'] = data['date'].dt.year.astype(str)
    data['_quarter'] = data['date'].dt.month.map(
        lambda m: get_quarter_from_month(m)
    )

    saved_count = 0
    for (year, quarter), group in data.groupby(['_year', '_quarter']):
        chunk = group.drop(columns=['_year', '_quarter']).copy()
        chunk = chunk.sort_values('date').reset_index(drop=True)

        file_path = get_stock_data_path(stock_code, data_type='1m',
                                        year=year, quarter=quarter, create=True)

        # 合并已有数据
        for fpath in [file_path, file_path.replace('.parquet', '.csv')]:
            if os.path.exists(fpath):
                try:
                    if fpath.endswith('.parquet'):
                        existing = pd.read_parquet(fpath)
                    else:
                        existing = pd.read_csv(fpath, parse_dates=['date'])
                    existing['date'] = pd.to_datetime(existing['date'])
                    chunk = pd.concat([existing, chunk], ignore_index=True)
                    chunk = chunk.drop_duplicates(subset=['date'], keep='last')
                    chunk = chunk.sort_values('date').reset_index(drop=True)
                except Exception as e:
                    logger.warning(f"{stock_code} 读取已有 {year}/{quarter} 失败: {e}")
                break

        # 保存
        try:
            chunk.to_parquet(file_path, index=False, engine='pyarrow')
            saved_count += len(chunk)
            logger.info(f"  {stock_code} {year}/{quarter}: 保存 {len(chunk)} 条")
        except ImportError:
            csv_path = file_path.replace('.parquet', '.csv')
            chunk.to_csv(csv_path, index=False)
            saved_count += len(chunk)
            logger.info(f"  {stock_code} {year}/{quarter}: 保存CSV {len(chunk)} 条")

    return saved_count


# ── 使用 pytdx 下载数据 ──────────────────────────────────────
def download_via_pytdx(stock_code: str, days: int):
    """直接调用 pytdx 下载指定天数的1m数据"""
    from App.codes.downloads.DlPytdx import PytdxDownloader

    with PytdxDownloader() as downloader:
        df, end_date = downloader.get_1m_data(
            stock_code, days=days, use_batch=True
        )
    return df, end_date


# ── 重建15m数据 ──────────────────────────────────────────────
def rebuild_15m(stock_code: str):
    """重建15m数据（合并Q4+Q1的1m数据，重新处理）"""
    from scripts.diagnose_and_repair import process_15m
    # 用当前季度的日期触发处理
    today = date.today()
    return process_15m(stock_code, today)


# ── 主流程 ──────────────────────────────────────────────────
def scan_and_fix(dry_run=False, target_code=None):
    """扫描所有股票，找到数据缺失并修复"""
    with app.app_context():
        from App.models.data.Stock1m import DownloadRecord
        from App.models.data.basic_info import StockCodes

        # 获取所有有效股票
        records = db.session.query(
            StockCodes.code,
            StockCodes.name,
            DownloadRecord.end_date,
            DownloadRecord.start_date,
            DownloadRecord.download_status,
        ).join(
            StockCodes, DownloadRecord.stock_code_id == StockCodes.id
        ).filter(
            DownloadRecord.end_date != date(2050, 1, 1),
        ).all()

        if target_code:
            records = [r for r in records if r.code == target_code]

        print(f"\n{'='*70}")
        print(f"1m数据缺失扫描")
        print(f"检查范围: 2025-12-01 ~ 2026-03-14")
        print(f"股票总数: {len(records)}")
        print(f"模式: {'仅扫描' if dry_run else '扫描+修复'}")
        print(f"{'='*70}\n")

        check_start = date(2025, 12, 1)
        check_end = date(2026, 3, 14)  # 今天

        # ── 第一步：扫描所有股票 ──
        gap_stocks = []  # (code, name, missing_days, total_expected)

        for i, rec in enumerate(records):
            if (i + 1) % 100 == 0:
                print(f"  扫描进度: {i+1}/{len(records)}...")

            # 跳过板块（BK开头不通过pytdx个股接口下载）
            if rec.code.startswith('BK'):
                continue

            missing, total = analyze_stock_gaps(rec.code, check_start, check_end)
            if missing:
                gap_stocks.append((rec.code, rec.name, missing, total))

        # ── 第二步：输出诊断报告 ──
        print(f"\n{'='*70}")
        print(f"扫描结果: {len(gap_stocks)} 只股票存在数据缺失")
        print(f"{'='*70}")

        if not gap_stocks:
            print("所有股票数据完整！")
            return

        # 按缺失天数排序
        gap_stocks.sort(key=lambda x: len(x[2]), reverse=True)

        # 缺失统计
        gap_distribution = defaultdict(int)
        for code, name, missing, total in gap_stocks:
            gap_len = len(missing)
            if gap_len >= 40:
                gap_distribution['>=40天'] += 1
            elif gap_len >= 20:
                gap_distribution['20-39天'] += 1
            elif gap_len >= 5:
                gap_distribution['5-19天'] += 1
            else:
                gap_distribution['1-4天'] += 1

        print(f"\n缺失分布:")
        for k, v in sorted(gap_distribution.items()):
            print(f"  {k}: {v} 只")

        print(f"\n缺失最多的前20只股票:")
        for code, name, missing, total in gap_stocks[:20]:
            first_miss = min(missing).strftime('%m-%d')
            last_miss = max(missing).strftime('%m-%d')
            print(f"  {code} {name:　<6} 缺 {len(missing):>3}/{total} 天  "
                  f"({first_miss} ~ {last_miss})")

        if dry_run:
            print(f"\n[dry-run] 不执行修复，退出")
            return

        # ── 第三步：修复 ──
        print(f"\n{'='*70}")
        print(f"开始修复: {len(gap_stocks)} 只股票")
        print(f"{'='*70}")

        fix_ok = 0
        fix_fail = 0
        fix_15m_ok = 0

        for i, (code, name, missing, total) in enumerate(gap_stocks):
            gap_days = len(missing)
            # 计算需要下载的天数（从最早缺失日到今天）
            earliest_miss = min(missing)
            download_days = (date.today() - earliest_miss).days + 5  # 多几天余量

            print(f"\n[{i+1}/{len(gap_stocks)}] {code} {name} "
                  f"缺 {gap_days} 天, 下载 {download_days} 天数据...")

            try:
                # 下载1m数据
                df, end_date = download_via_pytdx(code, download_days)

                if df is None or df.empty:
                    print(f"  X pytdx 下载失败（空数据）")
                    fix_fail += 1
                    continue

                print(f"  OK 下载 {len(df)} 条, "
                      f"{df['date'].min().strftime('%Y-%m-%d')} ~ "
                      f"{df['date'].max().strftime('%Y-%m-%d')}")

                # 按季度保存
                save_count = save_1m_by_quarter(df, code)

                # 标记 DailyTaskStatus
                from App.models.data.DailyTaskStatus import DailyTaskStatus
                df['date'] = pd.to_datetime(df['date'])
                for d in df['date'].dt.date.unique():
                    DailyTaskStatus.mark_task(code, d, 'is_1m_downloaded')
                db.session.commit()

                fix_ok += 1

                # 重建15m
                print(f"  -> 重建15m数据...", end=' ', flush=True)
                if rebuild_15m(code):
                    fix_15m_ok += 1
                    print("OK")
                else:
                    print("失败（不影响1m数据）")

            except Exception as e:
                print(f"  X 异常: {e}")
                fix_fail += 1

            # 避免API限流
            time.sleep(0.5)

        # ── 总结 ──
        print(f"\n{'='*70}")
        print(f"修复完成")
        print(f"  1m下载成功: {fix_ok}")
        print(f"  1m下载失败: {fix_fail}")
        print(f"  15m重建成功: {fix_15m_ok}")
        print(f"{'='*70}")


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    target = None

    for j, arg in enumerate(sys.argv):
        if arg == '--code' and j + 1 < len(sys.argv):
            target = sys.argv[j + 1]

    scan_and_fix(dry_run=dry, target_code=target)
