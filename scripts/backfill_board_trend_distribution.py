# -*- coding: utf-8 -*-
"""回填板块「趋势阶段分布」每日历史快照到 eval_board_trend_distribution。

分布趋势页（/board_overview/distribution）只展示已记录的快照行，而快照是从
某天起才开始自动记录的，所以历史是空的。本脚本用已有的板块趋势打分
（eval_board_trend_score）逐日 as-of 还原历史分布：

  对每个「有打分记录的交易日 D」，把每个主表板块在 D 当天（含）的最近一条
  打分阶段统计出来——这与线上「记录今日分布」的口径完全一致：
  线上快照 = 每个板块取最新一条评分的阶段分布。

  · 某板块在 D 之前从未打过分 → 计入「无评分(none)」
  · 板块清单取当前主表 eval_board；主表为空时回退到打分记录里出现过的板块集合
  · 按 record_date 唯一 upsert，source='backfill'；已存在的快照默认跳过（除非 --overwrite）

用法：
    python scripts/backfill_board_trend_distribution.py                 # 回填全部历史日
    python scripts/backfill_board_trend_distribution.py --start 2026-01-01 --end 2026-06-17
    python scripts/backfill_board_trend_distribution.py --overwrite     # 覆盖已存在的快照
    python scripts/backfill_board_trend_distribution.py --dry-run       # 只打印不写库
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def refresh_distribution(start=None, end=None, overwrite=False, dry_run=False, verbose=True):
    """在已有 app_context 内，用 eval_board_trend_score 逐日 as-of 还原分布快照。

    可被其它脚本（如重算历史打分后）直接调用。start/end 为 date 或 None。
    返回 {written, skipped, dates}。
    """
    from App.exts import db
    from App.models.evaluation.Board import Board
    from App.models.evaluation.BoardTrendScore import BoardTrendScore, TREND_STAGES
    from App.models.evaluation.BoardTrendDistribution import BoardTrendDistribution

    Board.ensure_table()
    BoardTrendDistribution.ensure_table()

    # 1) 板块清单：优先当前主表；主表为空时回退到打分记录里的板块集合
    master_codes = [b.board_code for b in Board.query.all()]

    # 2) 一次性拉取全部打分（只取需要的列），按日期升序在内存里 as-of 扫描
    rows = (db.session.query(BoardTrendScore.board_code,
                             BoardTrendScore.record_date,
                             BoardTrendScore.trend_stage)
            .order_by(BoardTrendScore.record_date.asc()).all())
    if not rows:
        if verbose:
            print('[dist] eval_board_trend_score 没有任何打分记录，无法回填。')
        return {'written': 0, 'skipped': 0, 'dates': 0}

    if not master_codes:
        master_codes = sorted({r.board_code for r in rows})
        if verbose:
            print(f'[dist] 主表 eval_board 为空，回退用打分记录里的 {len(master_codes)} 个板块。')
    total_boards = len(master_codes)

    by_date = defaultdict(list)   # date -> [(board_code, stage)]
    for r in rows:
        by_date[r.record_date].append((r.board_code, r.trend_stage))

    all_dates = sorted(by_date.keys())
    valid_stages = set(TREND_STAGES)

    # 3) 升序扫描，维护「每个板块到当前日为止的最近阶段」
    latest_stage = {}
    results = []        # [(date, stage_counts, total)]
    for d in all_dates:
        for code, stage in by_date[d]:
            latest_stage[code] = stage
        if start and d < start:
            continue
        if end and d > end:
            continue
        counts = {s: 0 for s in TREND_STAGES}
        counts['none'] = 0
        for code in master_codes:
            st = latest_stage.get(code)
            if st not in valid_stages:   # None / 空 / 非法 → 无评分
                st = 'none'
            counts[st] += 1
        results.append((d, counts, total_boards))

    if not results:
        if verbose:
            print('[dist] 选定日期范围内没有可回填的交易日。')
        return {'written': 0, 'skipped': 0, 'dates': 0}

    # 4) 写库（或 dry-run 打印）
    existing = {r.record_date for r in BoardTrendDistribution.query.all()}
    written = skipped = 0
    if verbose:
        print(f'[dist] 板块数={total_boards}，候选日期 {len(results)} 个'
              f'（{results[0][0]} ~ {results[-1][0]}）')
        print(f'{"日期":<12}{"上前":>5}{"上后":>5}{"下前":>5}{"下后":>5}{"未识":>5}{"无评":>5}{"总":>6}')
    for d, counts, total in results:
        if verbose:
            print(f'{d.isoformat():<12}'
                  f'{counts["up_early"]:>5}{counts["up_late"]:>5}'
                  f'{counts["down_early"]:>5}{counts["down_late"]:>5}'
                  f'{counts["unknown"]:>5}{counts["none"]:>5}{total:>6}')
        if dry_run:
            continue
        if d in existing and not overwrite:
            skipped += 1
            continue
        BoardTrendDistribution.upsert(d, counts, total, source='backfill')
        written += 1

    if verbose:
        if dry_run:
            print('[dist] dry-run，未写入数据库。')
        else:
            print(f'[dist] 完成：写入/更新 {written} 条，跳过已存在 {skipped} 条。')
    return {'written': written, 'skipped': skipped, 'dates': len(results)}


def main():
    ap = argparse.ArgumentParser(description='回填板块趋势阶段分布历史快照')
    ap.add_argument('--start', type=str, default=None, help='起始日期 YYYY-MM-DD（含）')
    ap.add_argument('--end', type=str, default=None, help='结束日期 YYYY-MM-DD（含）')
    ap.add_argument('--overwrite', action='store_true', help='覆盖已存在的快照行')
    ap.add_argument('--dry-run', action='store_true', help='只打印结果，不写数据库')
    args = ap.parse_args()

    start = datetime.strptime(args.start, '%Y-%m-%d').date() if args.start else None
    end = datetime.strptime(args.end, '%Y-%m-%d').date() if args.end else None

    from App import create_app
    app = create_app()
    with app.app_context():
        refresh_distribution(start=start, end=end,
                             overwrite=args.overwrite, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
