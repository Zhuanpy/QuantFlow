# -*- coding: utf-8 -*-
"""回填历史个股统计字段（ss_*）到 data_stock_daily。

逐个交易日 as-of 重算（compute_dist_snapshot 支持按 snapshot_date 截断 15m 重算周期），
所以分布(长度/振幅/量能)可精确还原到当天；板块趋势/RNN 取「≤当天的最近一条」近似
（这两类没有逐日历史：板块趋势仅打分过的日期有，RNN 只有按月/最近一次的记录）。

用法：
    python scripts/backfill_daily_stats.py --code 002812 --days 120
    python scripts/backfill_daily_stats.py --codes 002812,300782 --days 60
    python scripts/backfill_daily_stats.py --all --days 30      # 全部收集个股（很慢，谨慎）

注意：会顺带把各历史日的分布快照 upsert 进 stock_dist_snapshot（screener 历史也因此可用）。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time as dtime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description='回填 data_stock_daily 的 ss_* 历史字段')
    ap.add_argument('--code', type=str, default=None, help='单只代码')
    ap.add_argument('--codes', type=str, default=None, help='多只，逗号分隔')
    ap.add_argument('--all', action='store_true', help='全部 data/15m 下个股（很慢）')
    ap.add_argument('--days', type=int, default=120, help='回填最近 N 个交易日（默认 120）')
    ap.add_argument('--start', type=str, default=None, help='起始日期 YYYY-MM-DD（给了则忽略 --days 下界）')
    ap.add_argument('--end', type=str, default=None, help='结束日期 YYYY-MM-DD')
    args = ap.parse_args()

    from pathlib import Path
    from App import create_app
    from App.exts import db
    from App.models.data.StockDaily import StockDaily
    from App.models.evaluation.BoardTrendScore import BoardTrendScore
    from App.models.strategy.RnnRunningRecords import RnnRunningRecord
    from App.services.dist_snapshot_service import compute_dist_snapshot
    from config import Config
    from sqlalchemy import text

    app = create_app()
    with app.app_context():
        # 1) 代码集合
        if args.codes:
            codes = [c.strip() for c in args.codes.split(',') if c.strip()]
        elif args.code:
            codes = [args.code.strip()]
        elif args.all:
            import re
            d15 = Path(Config.get_project_root()) / 'data' / '15m'
            codes = sorted(p.stem for p in d15.glob('*.parquet') if re.fullmatch(r'\d{6}', p.stem))
        else:
            print('请指定 --code / --codes / --all')
            return

        start = datetime.strptime(args.start, '%Y-%m-%d').date() if args.start else None
        end = datetime.strptime(args.end, '%Y-%m-%d').date() if args.end else None
        eng = db.engines['quanttradingsystem']

        def _board_of(code):
            """该股最新东财行业板块 (board_code, board_name)。"""
            with eng.connect() as conn:
                r = conn.execute(text(
                    'SELECT board_code, board_name FROM industry_eastmoney '
                    'WHERE stock_code=:c ORDER BY date DESC LIMIT 1'), {'c': code}).fetchone()
            return (r.board_code, r.board_name) if r else (None, None)

        total_codes = len(codes)
        grand_ok = 0
        for ci, code in enumerate(codes, 1):
            board_code, board_name = _board_of(code)
            # 该股要回填的交易日 = data_stock_daily 里的日期（按范围/最近N）
            q = db.session.query(StockDaily.date).filter(StockDaily.stock_code == code)
            if start:
                q = q.filter(StockDaily.date >= start)
            if end:
                q = q.filter(StockDaily.date <= end)
            q = q.order_by(StockDaily.date.desc())
            if not start:
                q = q.limit(args.days)
            dates = [r[0] for r in q.all()]
            if not dates:
                print(f'[{ci}/{total_codes}] {code} 无日K，跳过')
                continue

            ok = 0
            for dt in dates:
                snap = compute_dist_snapshot(code, snapshot_date=dt, commit=True)
                if snap is None:
                    continue
                row = StockDaily.query.filter_by(stock_code=code, date=dt).first()
                if row is None:
                    continue
                up = (snap.current_direction == 1)
                d = 'up' if up else 'dn'

                def g(metric, stat):
                    return getattr(snap, f'{metric}_{d}_{stat}', None)

                # 板块趋势 as-of dt（≤dt 最近一条）
                bt = None
                if board_code:
                    bt = (BoardTrendScore.query
                          .filter(BoardTrendScore.board_code == board_code,
                                  BoardTrendScore.record_date <= dt)
                          .order_by(BoardTrendScore.record_date.desc()).first())
                # RNN as-of dt（renew_date ≤ 当天最近一条）
                rnn = (RnnRunningRecord.query
                       .filter(RnnRunningRecord.code == code,
                               RnnRunningRecord.renew_date <= datetime.combine(dt, dtime.max))
                       .order_by(RnnRunningRecord.id.desc()).first())

                row.ss_direction = snap.current_direction
                row.ss_signal_name = snap.current_signal_name
                row.ss_len_current = g('len', 'current'); row.ss_len_mean = g('len', 'mean')
                row.ss_len_z = g('len', 'z'); row.ss_len_pct = g('len', 'pct'); row.ss_len_n = g('len', 'n')
                row.ss_amp_current = g('amp', 'current'); row.ss_amp_mean = g('amp', 'mean')
                row.ss_amp_z = g('amp', 'z'); row.ss_amp_pct = g('amp', 'pct')
                row.ss_v5_current = g('v5', 'current'); row.ss_v5_mean = g('v5', 'mean')
                row.ss_v5_z = g('v5', 'z'); row.ss_v5_pct = g('v5', 'pct')
                row.ss_board_code = board_code; row.ss_board_name = board_name
                row.ss_board_trend_stage = bt.trend_stage if bt else None
                row.ss_board_trend_score = bt.total_score if bt else None
                row.ss_board_signal = bt.signal if bt else None
                row.ss_rnn_trends = rnn.trends if rnn else None
                row.ss_rnn_trade_point = rnn.trade_point if rnn else None
                row.ss_rnn_score_trends = rnn.score_trends if rnn else None
                row.ss_rnn_predict_cycle_length = rnn.predict_cycle_length if rnn else None
                row.ss_rnn_real_cycle_length = rnn.real_cycle_length if rnn else None
                row.ss_rnn_predict_cycle_change = rnn.predict_cycle_change if rnn else None
                row.ss_rnn_real_cycle_change = rnn.real_cycle_change if rnn else None
                ok += 1
            db.session.commit()
            grand_ok += ok
            print(f'[{ci}/{total_codes}] {code} 回填 {ok}/{len(dates)} 天')
        print(f'[backfill] 完成：{total_codes} 只，共回填 {grand_ok} 行')


if __name__ == '__main__':
    main()
