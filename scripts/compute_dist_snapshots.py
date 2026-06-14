#!/usr/bin/env python3
"""批量计算个股三正态分布快照

默认行为：扫 data/15m/*.parquet 取所有"我收集的"股票，对每只跑一次 snapshot。
（约 378 只，单只 ~50ms，预计 20s 内跑完）

用法：
    python scripts/compute_dist_snapshots.py                  # 跑全部 data/15m 下的股票
    python scripts/compute_dist_snapshots.py --pool watching  # 只跑某股票池（trading/watching/candidate）
    python scripts/compute_dist_snapshots.py --code 002812    # 只跑单只
    python scripts/compute_dist_snapshots.py --codes 002812,000791  # 多只
    python scripts/compute_dist_snapshots.py --date 2026-05-22 # 用历史日期当 snapshot_date

调度建议：每天收盘下载数据完成后跑一次（cron / 任务计划程序）
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import date, datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger('compute_dist_snapshots')


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
    args = parser.parse_args()

    target_date = datetime.strptime(args.date, '%Y-%m-%d').date() if args.date else date.today()

    from pathlib import Path
    from App import create_app
    from App.exts import db
    from App.models.strategy.StockPool import StockPool
    from App.models.data.basic_info import StockInfo
    from App.services.dist_snapshot_service import compute_dist_snapshot
    from config import Config

    app = create_app()
    with app.app_context():
        # 1) 决定要跑哪些股票
        name_map = {}
        if args.codes:
            codes = [c.strip() for c in args.codes.split(',') if c.strip()]
        elif args.code:
            codes = [args.code.strip()]
        elif args.pool:
            pool_rows = (StockPool.query
                         .filter(StockPool.is_active == 1,
                                 StockPool.pool_type == args.pool)
                         .all())
            codes = [r.stock_code for r in pool_rows]
            name_map = {r.stock_code: r.stock_name for r in pool_rows}
        else:
            # 默认：扫 data/15m/*.parquet —— 这是"我收集的"股票集合的 ground truth
            d15 = Path(Config.get_project_root()) / 'data' / '15m'
            # 只认 6 位数字个股代码 或 BK 板块代码；排除 *.v1.bak 之类备份文件
            # （其 stem 形如 000063.v1.bak，非真实代码、且会撑爆 stock_code 列）
            all_codes = sorted(p.stem for p in d15.glob('*.parquet')
                               if re.fullmatch(r'\d{6}', p.stem) or p.stem.upper().startswith('BK'))
            # 默认过滤掉 BK 开头的板块代码 —— 它们和个股一起存在同目录但语义不同
            if not args.include_boards:
                codes = [c for c in all_codes if not c.upper().startswith('BK')]
                skipped = len(all_codes) - len(codes)
                print(f'[scan] data/15m/ 下找到 {len(all_codes)} 个 parquet，过滤掉 {skipped} 个板块代码后 = {len(codes)} 只个股')
            else:
                codes = all_codes
                print(f'[scan] data/15m/ 下找到 {len(codes)} 个 parquet（含板块代码）')

        # 给所有 code 顺便查一遍名字（用于落库时填 stock_name）
        # 同 code 可能多条（例如 000001 = 平安银行 + 上证指数），按 MarketCode 选个股
        if not name_map and codes:
            rows = (StockInfo.query
                    .filter(StockInfo.code.in_(codes))
                    .with_entities(StockInfo.code, StockInfo.name, StockInfo.MarketCode).all())
            # 按 code 收集所有候选，挑个股优先
            buckets = {}
            for r in rows:
                buckets.setdefault(r.code, []).append((r.name, (r.MarketCode or '').lower()))
            for code, cands in buckets.items():
                # 个股优先：sz/sh/bj 前缀
                pick = next((c for c in cands if c[1].startswith(('sz', 'sh', 'bj'))), cands[0])
                name_map[code] = pick[0]

        if not codes:
            print('没有要处理的股票（股票池为空？）')
            return

        print(f'[batch] target_date={target_date}  total {len(codes)} stocks')
        # 单只输出太多 —— 只对失败/无数据的打详细行；每 PROGRESS_EVERY 只打一次进度摘要
        PROGRESS_EVERY = 50 if len(codes) > 30 else 10
        t0 = time.time()
        ok = fail = empty = 0
        empty_list = []
        failed_list = []
        # 记录方向分布，最后打个一行摘要
        dir_counts = {1: 0, -1: 0, 0: 0}
        for i, code in enumerate(codes, 1):
            try:
                row = compute_dist_snapshot(
                    code,
                    snapshot_date=target_date,
                    stock_name=name_map.get(code),
                    commit=True,
                )
                if row is None:
                    empty += 1
                    empty_list.append(code)
                else:
                    ok += 1
                    dir_counts[row.current_direction or 0] += 1
            except Exception as e:
                fail += 1
                failed_list.append((code, str(e)))
                logger.warning(f'  [{i:4d}/{len(codes)}] {code}  [fail] {str(e)[:120]}')
                try:
                    db.session.rollback()
                except Exception:
                    pass

            if i % PROGRESS_EVERY == 0 or i == len(codes):
                rate = i / max(time.time() - t0, 0.001)
                eta = (len(codes) - i) / rate
                print(f'  progress {i:4d}/{len(codes)}  ok={ok}  fail={fail}  empty={empty}  '
                      f'{rate:.1f}/s  eta={eta:.0f}s')

        elapsed = time.time() - t0
        print(f'\ndone. ok={ok}  fail={fail}  empty={empty}  total={len(codes)}  elapsed={elapsed:.1f}s')
        print(f'direction: up={dir_counts[1]}  down={dir_counts[-1]}  flat/unknown={dir_counts[0]}')
        if empty_list[:10]:
            print(f'\nempty data ({len(empty_list)} total, first 10): {", ".join(empty_list[:10])}')
        if failed_list:
            print(f'\nfailed ({len(failed_list)} total, first 10):')
            for code, msg in failed_list[:10]:
                print(f'  {code}: {msg[:120]}')


if __name__ == '__main__':
    main()
