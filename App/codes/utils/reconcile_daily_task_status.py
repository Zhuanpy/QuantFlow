# -*- coding: utf-8 -*-
"""
DailyTaskStatus ↔ 磁盘/DB 对账工具

背景：DailyTaskStatus 表的 is_*_downloaded / is_*_generated 等标志
原本只在下载流水线"成功"时翻 True，从不翻 False。一旦磁盘文件被删（清理脏数据、
迁移目录等），表里就会留下"假阳性"——表说有，磁盘没。

这个脚本扫描真实落盘和数据库，把表的标志位修正回实际状态：
  - is_1m_downloaded   ← data/quarters/<Y>/<Q>/<code>.parquet 是否存在且包含该 date
  - is_15m_generated   ← data/15m/<code>.parquet 是否存在且包含该 date
  - is_daily_processed ← MySQL data_stock_daily 是否有 (stock_code, date) 行
  - is_macd_calculated ← data/15m/<code>.parquet 文件中 (MACD/Dif/Dea) 列是否非空

不会翻动以下"派生"标志（它们由后续计算/预测决定，磁盘对账无法确定）：
  is_1m_cleaned, is_volume_processed, is_feature_generated,
  is_rnn_predicted, is_board_downloaded, is_fund_analyzed

用法：
  python -m App.codes.utils.reconcile_daily_task_status              # 对账并写回
  python -m App.codes.utils.reconcile_daily_task_status --dry-run    # 只看差异、不改
  python -m App.codes.utils.reconcile_daily_task_status --code BK1016  # 仅对账某只
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional, Set, Tuple

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    try:
        from config import Config
        return Path(Config.get_project_root())
    except Exception:
        return Path(__file__).parent.parent.parent.parent


def _load_15m_dates(stock_code: str) -> Set[date]:
    """读 data/15m/<code>.parquet，返回里面所有交易日的集合。"""
    fp = _project_root() / 'data' / '15m' / f'{stock_code}.parquet'
    if not fp.exists():
        return set()
    try:
        df = pd.read_parquet(fp, columns=['date'])
        if df.empty:
            return set()
        return set(pd.to_datetime(df['date']).dt.date.unique().tolist())
    except Exception as e:
        logger.warning(f'读取 {fp} 失败: {e}')
        return set()


def _load_15m_macd_dates(stock_code: str) -> Set[date]:
    """读 15m parquet，返回 MACD/Dif/Dea 都非空的交易日集合。"""
    fp = _project_root() / 'data' / '15m' / f'{stock_code}.parquet'
    if not fp.exists():
        return set()
    try:
        df = pd.read_parquet(fp)
        if df.empty:
            return set()
        cols = [c for c in ('MACD', 'Dif', 'Dea') if c in df.columns]
        if len(cols) < 3:
            return set()
        df['date'] = pd.to_datetime(df['date'])
        # 一个交易日里只要有任意一根 K 的 MACD 三列都非空，就算"已计算"
        valid = df.dropna(subset=cols, how='any')
        return set(valid['date'].dt.date.unique().tolist())
    except Exception as e:
        logger.warning(f'读 MACD 状态失败 {fp}: {e}')
        return set()


def _load_1m_dates(stock_code: str) -> Set[date]:
    """扫 data/quarters/<Y>/<Q>/<code>.parquet（所有季度），返回里面所有交易日。"""
    quarters_root = _project_root() / 'data' / 'quarters'
    if not quarters_root.exists():
        return set()
    out: Set[date] = set()
    for year_dir in quarters_root.iterdir():
        if not year_dir.is_dir():
            continue
        for q_dir in year_dir.iterdir():
            if not q_dir.is_dir():
                continue
            for ext in ('.parquet', '.csv'):
                fp = q_dir / f'{stock_code}{ext}'
                if not fp.exists():
                    continue
                try:
                    if ext == '.parquet':
                        df = pd.read_parquet(fp, columns=['date'])
                    else:
                        df = pd.read_csv(fp, usecols=['date'], parse_dates=['date'])
                    if df.empty:
                        continue
                    out.update(pd.to_datetime(df['date']).dt.date.unique().tolist())
                except Exception as e:
                    logger.warning(f'读取 {fp} 失败: {e}')
    return out


def _load_daily_dates_from_db(conn, stock_code: str) -> Set[date]:
    """读 MySQL data_stock_daily 该 stock_code 的所有 date。"""
    rows = conn.execute(text(
        'SELECT date FROM data_stock_daily WHERE stock_code = :code'
    ), {'code': stock_code}).fetchall()
    return {r[0] for r in rows if r[0] is not None}


def _all_stock_codes_in_table(conn) -> list:
    """返回 DailyTaskStatus 表里出现过的所有 stock_code。"""
    rows = conn.execute(text(
        'SELECT DISTINCT stock_code FROM data_daily_task_status'
    )).fetchall()
    return [r[0] for r in rows]


def reconcile_one(conn, stock_code: str, dry_run: bool = False) -> dict:
    """对账单只股票/板块，返回修正统计。"""
    truth_1m   = _load_1m_dates(stock_code)
    truth_15m  = _load_15m_dates(stock_code)
    truth_macd = _load_15m_macd_dates(stock_code)
    truth_dly  = _load_daily_dates_from_db(conn, stock_code)

    rows = conn.execute(text('''
        SELECT date, is_1m_downloaded, is_15m_generated,
               is_daily_processed, is_macd_calculated
        FROM data_daily_task_status
        WHERE stock_code = :c
    '''), {'c': stock_code}).fetchall()

    flips = {'is_1m_downloaded': 0, 'is_15m_generated': 0,
             'is_daily_processed': 0, 'is_macd_calculated': 0}
    updates = []  # (date, dict_of_changes)

    for r in rows:
        d = r[0]
        cur = {
            'is_1m_downloaded':   bool(r[1]),
            'is_15m_generated':   bool(r[2]),
            'is_daily_processed': bool(r[3]),
            'is_macd_calculated': bool(r[4]),
        }
        truth = {
            'is_1m_downloaded':   d in truth_1m,
            'is_15m_generated':   d in truth_15m,
            'is_daily_processed': d in truth_dly,
            'is_macd_calculated': d in truth_macd,
        }
        diff = {k: v for k, v in truth.items() if v != cur[k]}
        if diff:
            for k in diff:
                flips[k] += 1
            updates.append((d, diff))

    if updates and not dry_run:
        for d, diff in updates:
            assignments = ', '.join(f'{k}=:{k}' for k in diff)
            conn.execute(
                text(f'''UPDATE data_daily_task_status
                         SET {assignments}, updated_at=NOW()
                         WHERE stock_code=:c AND date=:d'''),
                {**{k: int(v) for k, v in diff.items()}, 'c': stock_code, 'd': d}
            )

    return {
        'stock_code': stock_code,
        'rows_inspected': len(rows),
        'rows_changed': len(updates),
        'flips': flips,
    }


def reconcile_all(stock_codes: Optional[list] = None, dry_run: bool = False) -> dict:
    """对账多只（默认所有出现在 DailyTaskStatus 表里的）。

    每只股票一个独立事务，避免单事务过大导致 MySQL 超时/卡死。
    """
    from App.exts import db
    eng = db.engines['quanttradingsystem']

    with eng.connect() as conn:
        codes = stock_codes or _all_stock_codes_in_table(conn)

    total = {'stocks': 0, 'rows_inspected': 0, 'rows_changed': 0,
             'flips': {'is_1m_downloaded': 0, 'is_15m_generated': 0,
                       'is_daily_processed': 0, 'is_macd_calculated': 0}}

    n = len(codes)
    for i, code in enumerate(codes, 1):
        # 每只股票独立事务
        ctx = eng.connect() if dry_run else eng.begin()
        try:
            with ctx as conn:
                res = reconcile_one(conn, code, dry_run=dry_run)
        except Exception as e:
            logger.warning(f'  {code}: 对账失败 {e}（继续下一只）')
            continue

        total['stocks'] += 1
        total['rows_inspected'] += res['rows_inspected']
        total['rows_changed'] += res['rows_changed']
        for k in total['flips']:
            total['flips'][k] += res['flips'][k]

        if res['rows_changed']:
            logger.info(f"  [{i}/{n}] {code}: 检视 {res['rows_inspected']} 行，"
                        f"修正 {res['rows_changed']} 行")
        elif i % 200 == 0:
            logger.info(f"  [{i}/{n}] 进度心跳，累计修正 {total['rows_changed']} 行")

    return total


def main():
    parser = argparse.ArgumentParser(description='DailyTaskStatus 对账工具')
    parser.add_argument('--dry-run', action='store_true',
                        help='只看差异不改库')
    parser.add_argument('--code', help='只对账指定 stock_code（可选）')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s'
    )

    from App import create_app
    app = create_app()
    with app.app_context():
        codes = [args.code] if args.code else None
        result = reconcile_all(stock_codes=codes, dry_run=args.dry_run)

    mode = '[DRY-RUN]' if args.dry_run else '[已写入]'
    print(f'\n{mode} 对账完成')
    print(f"  扫描股票数: {result['stocks']}")
    print(f"  检视任务行: {result['rows_inspected']}")
    print(f"  修正任务行: {result['rows_changed']}")
    print(f"  各标志翻转数: {result['flips']}")


if __name__ == '__main__':
    main()
