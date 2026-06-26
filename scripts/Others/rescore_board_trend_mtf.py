# -*- coding: utf-8 -*-
"""用「多周期(日K + 1h + 15m)综合引擎」重算历史板块趋势打分，并刷新分布快照。

背景：板块趋势打分已升级为多周期综合（board_trend_score_service.compute_one_mtf），
但历史 eval_board_trend_score 里的行还是旧的日K-only(v1)。本脚本对每个有打分记录的
历史交易日，按「as-of 当日」重算成综合分：

  · 日K：data_stock_daily/datadaily 截至当日（历史很长，精确）
  · 15m / 1h：data/15m/{code}.parquet 截至当日；当日早于 15m 数据起点(~2026-05)时，
              15m/1h 缺失，综合分自动退化为日K-only（权重重新归一化）。

口径与线上完全一致：
  · 综合分 = 0.5·日K + 0.3·1h + 0.2·15m
  · 阶段 = 日K 阶段（主导）；所以分布图的阶段计数不会因综合分而改变，
          只有当现行 _classify_stage 逻辑与历史落库时不同，阶段才会被纠正。
  · is_manual=True 的手工行默认跳过，不覆盖。

重算完成后自动刷新 eval_board_trend_distribution（--overwrite），分布趋势页即时更新。

用法：
    python scripts/rescore_board_trend_mtf.py                      # 重算全部历史日期
    python scripts/rescore_board_trend_mtf.py --start 2026-05-08   # 只重算 15m 有数据的区间
    python scripts/rescore_board_trend_mtf.py --dry-run            # 只看会重算哪些日期/板块
    python scripts/rescore_board_trend_mtf.py --skip-distribution  # 不刷新分布快照
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 便于 import 兄弟脚本


def main():
    ap = argparse.ArgumentParser(description='多周期综合引擎重算历史板块趋势打分')
    ap.add_argument('--start', type=str, default=None, help='起始日期 YYYY-MM-DD（含）')
    ap.add_argument('--end', type=str, default=None, help='结束日期 YYYY-MM-DD（含）')
    ap.add_argument('--dry-run', action='store_true', help='只打印将重算的日期/板块数，不写库')
    ap.add_argument('--skip-distribution', action='store_true', help='重算后不刷新分布快照')
    ap.add_argument('--fill-gaps', action='store_true',
                    help='补齐缺口：把「有板块日K但无评分」的交易日也算上，并对全部主表板块打分')
    args = ap.parse_args()

    start = datetime.strptime(args.start, '%Y-%m-%d').date() if args.start else None
    end = datetime.strptime(args.end, '%Y-%m-%d').date() if args.end else None

    from App import create_app
    from App.exts import db
    from App.models.evaluation.Board import Board
    from App.models.evaluation.BoardTrendScore import BoardTrendScore
    from App.services.board_trend_score_service import compute_and_persist
    from backfill_board_trend_distribution import refresh_distribution
    from sqlalchemy import text

    app = create_app()
    with app.app_context():
        # 1) 要重算的历史日期 = 打分表里出现过的 record_date
        dq = db.session.query(BoardTrendScore.record_date).distinct()
        if start:
            dq = dq.filter(BoardTrendScore.record_date >= start)
        if end:
            dq = dq.filter(BoardTrendScore.record_date <= end)
        date_set = {d[0] for d in dq.all() if d[0]}

        master_codes = [b.board_code for b in Board.query.all()]
        if args.fill_gaps:
            # 补齐：把「有板块日K数据」的交易日也并进来（缺口日会被全主表打分）
            sql = "SELECT DISTINCT date FROM data_stock_daily WHERE stock_code LIKE 'BK%'"
            params = {}
            if start:
                sql += ' AND date >= :s'; params['s'] = start
            if end:
                sql += ' AND date <= :e'; params['e'] = end
            eng = db.engines['quanttradingsystem']
            with eng.connect() as c:
                td = [r[0] for r in c.execute(text(sql), params).fetchall() if r[0]]
            before = len(date_set)
            date_set |= set(td)
            print(f'[rescore] 补齐模式：并入板块日K交易日 {len(td)} 个，'
                  f'新增缺口日 {len(date_set) - before} 个。')

        # 历史工具：永不触碰今天及以后（当天数据可能未完整，交给盘后自动流程）
        from datetime import date as _date
        today = _date.today()
        skipped_today = sorted(d for d in date_set if d >= today)
        if skipped_today:
            print(f'[rescore] 跳过今天及以后 {len(skipped_today)} 天（'
                  f'{", ".join(x.isoformat() for x in skipped_today)}）→ 交给盘后自动流程。')
        dates = sorted(d for d in date_set if d < today)
        if not dates:
            print('[rescore] 没有匹配的历史日期。')
            return

        print(f'[rescore] 待重算 {len(dates)} 个交易日（{dates[0]} ~ {dates[-1]}）')
        grand_ok = grand_fail = grand_manual = grand_changed = 0

        for d in dates:
            rows = BoardTrendScore.query.filter_by(record_date=d).all()
            manual_codes = {r.board_code for r in rows if r.is_manual}
            prev_stage = {r.board_code: r.trend_stage for r in rows if not r.is_manual}
            manual = len(manual_codes)
            if args.fill_gaps:
                # 全主表板块（排除手工），缺口日也能完整打分
                codes = [c for c in master_codes if c not in manual_codes]
            else:
                # 仅重算已有评分行
                codes = [r.board_code for r in rows if not r.is_manual]
            if not codes:
                print(f'  {d}: 无可重算板块（手工 {manual}），跳过')
                grand_manual += manual
                continue

            if args.dry_run:
                print(f'  {d}: 将重算 {len(codes)} 个板块（跳过手工 {manual}）')
                grand_manual += manual
                continue

            res = compute_and_persist(codes, d)
            # 统计阶段被纠正的板块数
            changed = 0
            after = BoardTrendScore.query.filter_by(record_date=d).all()
            for r in after:
                if r.board_code in prev_stage and r.trend_stage != prev_stage[r.board_code]:
                    changed += 1
            grand_ok += res['ok']
            grand_fail += res['fail']
            grand_manual += manual
            grand_changed += changed
            print(f'  {d}: 重算 {res["ok"]}/{len(codes)}  失败 {res["fail"]}  '
                  f'跳过手工 {manual}  阶段纠正 {changed}')

        if args.dry_run:
            print(f'[rescore] dry-run 结束：{len(dates)} 个日期，跳过手工累计 {grand_manual}。')
            return

        print(f'[rescore] 重算完成：成功 {grand_ok} / 失败 {grand_fail} / '
              f'跳过手工 {grand_manual} / 阶段纠正 {grand_changed}')

        # 2) 刷新分布快照（只覆盖到已重算的最后一个历史日，不触碰今天）
        if not args.skip_distribution:
            print('[rescore] 刷新分布快照...')
            r = refresh_distribution(start=start, end=dates[-1], overwrite=True, verbose=False)
            print(f'[rescore] 分布快照已刷新：写入/更新 {r["written"]} 条 / 共 {r["dates"]} 天。')


if __name__ == '__main__':
    main()
