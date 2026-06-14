# -*- coding: utf-8 -*-
"""把「个股统计」字段（stock_stats 整页）写进 data_stock_daily 当日行。

每天收盘后运行一次（需先跑 compute_dist_snapshots.py 生成当日分布快照）：
    python scripts/save_daily_stats.py                # 写入今天
    python scripts/save_daily_stats.py --date 2026-06-14

口径：维持 15m —— 取当日 stock_dist_snapshot「当前方向」有效值 + 板块/板块趋势 +
各股最新 RNN 预测，UPDATE 到 data_stock_daily(stock_code, date) 那一行的 ss_* 列。
仅更新已存在的日K行（当天必须已有日K数据）；缺失的计入 skip。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description='写个股统计字段到 data_stock_daily 当日行')
    ap.add_argument('--date', type=str, default=None, help='目标日期 YYYY-MM-DD，默认今天')
    args = ap.parse_args()
    target = datetime.strptime(args.date, '%Y-%m-%d').date() if args.date else date.today()

    from App import create_app
    from App.exts import db
    from App.models.strategy.StockDistSnapshot import StockDistSnapshot
    from App.models.data.StockDaily import StockDaily
    from App.routes.strategy.dist_filter import _enrich_boards
    from App.routes.strategy.stock_stats import _latest_rnn_by_codes

    app = create_app()
    with app.app_context():
        snaps = (StockDistSnapshot.query
                 .filter(StockDistSnapshot.snapshot_date == target)
                 .filter(StockDistSnapshot.merge_below == 0)   # 日K入库用原始口径
                 .filter(~db.func.upper(StockDistSnapshot.stock_code).like('BK%'))
                 .all())
        if not snaps:
            print(f'[save_daily_stats] {target} 无分布快照，请先跑 compute_dist_snapshots.py')
            return

        codes = [s.stock_code for s in snaps]
        # 板块 / 板块趋势（复用 screener 富集）
        board_rows = [{'stock_code': c} for c in codes]
        _enrich_boards(board_rows)
        board_map = {b['stock_code']: b for b in board_rows}
        # 各股最新 RNN 预测
        rnn_map = _latest_rnn_by_codes(codes)

        ok = skip_no_daily = 0
        BATCH = 200
        for i, s in enumerate(snaps, 1):
            code = s.stock_code
            row = StockDaily.query.filter_by(stock_code=code, date=target).first()
            if row is None:
                skip_no_daily += 1
                continue
            up = (s.current_direction == 1)        # 非上涨(含0/None)按下跌组取值，与 /stock_stats 页一致
            d = 'up' if up else 'dn'

            def g(metric, stat):
                return getattr(s, f'{metric}_{d}_{stat}', None)

            b = board_map.get(code, {})
            r = rnn_map.get(code) or {}

            row.ss_direction = s.current_direction
            row.ss_signal_name = s.current_signal_name
            row.ss_len_current = g('len', 'current'); row.ss_len_mean = g('len', 'mean')
            row.ss_len_z = g('len', 'z'); row.ss_len_pct = g('len', 'pct'); row.ss_len_n = g('len', 'n')
            row.ss_amp_current = g('amp', 'current'); row.ss_amp_mean = g('amp', 'mean')
            row.ss_amp_z = g('amp', 'z'); row.ss_amp_pct = g('amp', 'pct')
            row.ss_v5_current = g('v5', 'current'); row.ss_v5_mean = g('v5', 'mean')
            row.ss_v5_z = g('v5', 'z'); row.ss_v5_pct = g('v5', 'pct')
            row.ss_board_code = b.get('board_code'); row.ss_board_name = b.get('board_name')
            row.ss_board_trend_stage = b.get('board_trend_stage')
            row.ss_board_trend_score = b.get('board_trend_score')
            row.ss_board_signal = b.get('board_signal')
            row.ss_rnn_trends = r.get('trends')
            row.ss_rnn_trade_point = r.get('trade_point')
            row.ss_rnn_score_trends = r.get('score_trends')
            row.ss_rnn_predict_cycle_length = r.get('predict_cycle_length')
            row.ss_rnn_real_cycle_length = r.get('real_cycle_length')
            row.ss_rnn_predict_cycle_change = r.get('predict_cycle_change')
            row.ss_rnn_real_cycle_change = r.get('real_cycle_change')
            ok += 1
            if i % BATCH == 0:
                db.session.commit()
        db.session.commit()
        print(f'[save_daily_stats] 日期={target}  写入 {ok} 行  跳过(无当日日K) {skip_no_daily}  '
              f'快照 {len(snaps)} 只')


if __name__ == '__main__':
    main()
