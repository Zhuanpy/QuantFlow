#!/usr/bin/env python3
"""批量计算个股三正态分布快照

默认行为：扫 data/15m/*.parquet 取所有"我收集的"股票，对每只跑一次 snapshot。
（约 378 只，单只 ~50ms，预计 20s 内跑完）

用法：
    python scripts/Others/compute_dist_snapshots.py                  # 跑全部 data/15m 下的股票
    python scripts/Others/compute_dist_snapshots.py --pool watching  # 只跑某股票池（trading/watching/candidate）
    python scripts/Others/compute_dist_snapshots.py --code 002812    # 只跑单只
    python scripts/Others/compute_dist_snapshots.py --codes 002812,000791  # 多只
    python scripts/Others/compute_dist_snapshots.py --date 2026-05-22 # 用历史日期当 snapshot_date
    python scripts/Others/compute_dist_snapshots.py --merge-below 5   # 合并<5%小周期(去噪)口径

调度建议：每天收盘下载数据完成后跑一次（见 scripts/Others/daily_close_pipeline.py
          会先下载行情→转 15m，再调用本批处理；用 Windows 任务计划程序统一调度）。

复用入口：
    run_batch(target_date, ...) —— 假定调用方已 push app_context，供 daily_close_pipeline 直接调用，
    避免重复 create_app()。
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import date, datetime
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger('compute_dist_snapshots')


def run_batch(target_date: Optional[date] = None,
              code: Optional[str] = None,
              codes: Optional[list] = None,
              pool: Optional[str] = None,
              include_boards: bool = False,
              merge_below: float = 0.0) -> dict:
    """对一批个股算分布快照并 upsert。**假定调用方已 push app_context。**

    选股优先级：codes > code > pool > 默认(扫 data/15m/*.parquet 全部个股)。
    同日同口径(merge_below)重跑会按唯一约束覆盖。

    Returns:
        {'ok':.., 'fail':.., 'empty':.., 'total':.., 'elapsed':.., 'date':..,
         'merge_below':.., 'failed':[(code,msg)..], 'empty_codes':[..]}
    """
    from pathlib import Path
    from App.exts import db
    from App.models.strategy.StockPool import StockPool
    from App.models.data.basic_info import StockInfo
    from App.services.dist_snapshot_service import compute_dist_snapshot
    from config import Config

    target_date = target_date or date.today()

    # 1) 决定要跑哪些股票
    name_map = {}
    if codes:
        codes = [c.strip() for c in codes if str(c).strip()]
    elif code:
        codes = [code.strip()]
    elif pool:
        pool_rows = (StockPool.query
                     .filter(StockPool.is_active == 1,
                             StockPool.pool_type == pool)
                     .all())
        codes = [r.stock_code for r in pool_rows]
        name_map = {r.stock_code: r.stock_name for r in pool_rows}
    else:
        # 默认：扫 data/15m/*.parquet —— 这是"我收集的"股票集合的 ground truth
        d15 = Path(Config.get_project_root()) / 'data' / '15m'
        # 只认 6 位数字个股代码 或 BK 板块代码；排除 *.v1.bak 之类备份文件
        all_codes = sorted(p.stem for p in d15.glob('*.parquet')
                           if re.fullmatch(r'\d{6}', p.stem) or p.stem.upper().startswith('BK'))
        if not include_boards:
            codes = [c for c in all_codes if not c.upper().startswith('BK')]
            skipped = len(all_codes) - len(codes)
            logger.info(f'[scan] data/15m/ 下找到 {len(all_codes)} 个 parquet，'
                        f'过滤掉 {skipped} 个板块代码后 = {len(codes)} 只个股')
        else:
            codes = all_codes
            logger.info(f'[scan] data/15m/ 下找到 {len(codes)} 个 parquet（含板块代码）')

    # 给所有 code 顺便查一遍名字（用于落库时填 stock_name）
    if not name_map and codes:
        rows = (StockInfo.query
                .filter(StockInfo.code.in_(codes))
                .with_entities(StockInfo.code, StockInfo.name, StockInfo.MarketCode).all())
        buckets = {}
        for r in rows:
            buckets.setdefault(r.code, []).append((r.name, (r.MarketCode or '').lower()))
        for c, cands in buckets.items():
            pick = next((x for x in cands if x[1].startswith(('sz', 'sh', 'bj'))), cands[0])
            name_map[c] = pick[0]

    summary = {'ok': 0, 'fail': 0, 'empty': 0, 'total': len(codes), 'elapsed': 0.0,
               'date': target_date.isoformat(), 'merge_below': merge_below,
               'failed': [], 'empty_codes': []}
    if not codes:
        logger.warning('没有要处理的股票（股票池为空？data/15m 为空？）')
        return summary

    tag = f'（合并<{merge_below}%小周期口径）' if merge_below > 0 else '（原始口径）'
    logger.info(f'[batch] target_date={target_date}  total {len(codes)} stocks {tag}')
    PROGRESS_EVERY = 50 if len(codes) > 30 else 10
    t0 = time.time()
    ok = fail = empty = 0
    dir_counts = {1: 0, -1: 0, 0: 0}
    for i, c in enumerate(codes, 1):
        try:
            row = compute_dist_snapshot(
                c, snapshot_date=target_date, stock_name=name_map.get(c),
                commit=True, merge_below=merge_below)
            if row is None:
                empty += 1
                summary['empty_codes'].append(c)
            else:
                ok += 1
                dir_counts[row.current_direction or 0] += 1
        except Exception as e:
            fail += 1
            summary['failed'].append((c, str(e)))
            logger.warning(f'  [{i:4d}/{len(codes)}] {c}  [fail] {str(e)[:120]}')
            try:
                db.session.rollback()
            except Exception:
                pass
        if i % PROGRESS_EVERY == 0 or i == len(codes):
            rate = i / max(time.time() - t0, 0.001)
            eta = (len(codes) - i) / rate
            logger.info(f'  progress {i:4d}/{len(codes)}  ok={ok}  fail={fail}  empty={empty}  '
                        f'{rate:.1f}/s  eta={eta:.0f}s')

    summary.update({'ok': ok, 'fail': fail, 'empty': empty, 'elapsed': time.time() - t0})
    logger.info(f'done. ok={ok}  fail={fail}  empty={empty}  total={len(codes)}  '
                f'elapsed={summary["elapsed"]:.1f}s')
    logger.info(f'direction: up={dir_counts[1]}  down={dir_counts[-1]}  flat/unknown={dir_counts[0]}')
    if summary['empty_codes'][:10]:
        logger.info(f'empty data ({len(summary["empty_codes"])} total, first 10): '
                    f'{", ".join(summary["empty_codes"][:10])}')
    if summary['failed']:
        logger.info(f'failed ({len(summary["failed"])} total, first 10):')
        for c, msg in summary['failed'][:10]:
            logger.info(f'  {c}: {msg[:120]}')
    return summary


def main():
    parser = argparse.ArgumentParser(description='批量计算个股三正态分布快照')
    parser.add_argument('--date', type=str, default=None,
                        help='快照日期 YYYY-MM-DD，默认今天；同日重跑会覆盖')
    parser.add_argument('--code', type=str, default=None,
                        help='只跑这一只代码，跳过股票池遍历')
    parser.add_argument('--codes', type=str, default=None,
                        help='只跑这几只，逗号分隔；优先级高于 --code')
    parser.add_argument('--pool', type=str, default=None,
                        help='只跑某股票池：trading/watching/candidate/archived；不传则跑 data/15m 下全部股票')
    parser.add_argument('--include-boards', action='store_true',
                        help='默认会跳过 BK 开头的板块代码（它们不是个股）；加此 flag 一起跑')
    parser.add_argument('--merge-below', type=float, default=0.0,
                        help='合并<X%%小周期(去噪)口径，与 screener/详情页一致；默认 0=原始口径')
    args = parser.parse_args()

    target_date = datetime.strptime(args.date, '%Y-%m-%d').date() if args.date else date.today()
    codes = [c.strip() for c in args.codes.split(',') if c.strip()] if args.codes else None

    from App import create_app
    app = create_app()
    with app.app_context():
        run_batch(target_date=target_date, code=args.code, codes=codes,
                  pool=args.pool, include_boards=args.include_boards,
                  merge_below=args.merge_below)


if __name__ == '__main__':
    main()
