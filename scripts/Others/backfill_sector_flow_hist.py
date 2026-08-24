#!/usr/bin/env python3
"""用本地板块日K回补 mkt_sector_flow_daily 的历史（**仅行业板块**）。

为什么需要
----------
L0 热度快照来自东财"当日"板块列表接口，**没抓就永久缺那一天**，回补不了。
但行业板块（BKxxxx）的日K已经躺在 `data_stock_daily` 里（涨跌幅可由收盘价算、
成交额=money），所以行业这条线可以往回补出几个月历史，让"持续 vs 一日游"
的可视化今天就能用，而不用等两周攒数据。

概念板块无本地日K（`data_stock_daily` 只有 86 个行业 BK），补不了 —— 概念只能从
接上调度那天起往后攒。

口径差异（重要，不要混用）
--------------------------
回补行 `source='hist'`、`heat_version='heat-v0-hist'`：
  - 有：change_pct（由收盘价算）、amount（money）、amount_share、各排名、heat_score
  - 无：换手率 / 主力净额 / 涨跌家数 / 领涨股（板块日K里没有）
heat_score 的定义与线上口径一致（50%涨幅百分位 + 50%成交额占比百分位），
所以 hist 与 em 两段可以接成一条线；但换手/家数字段在 hist 段是 NULL。

安全约束
--------
1. **绝不覆盖实盘快照**：某 (date, industry) 已存在 source in ('em','ak') 的行就整天跳过。
2. **横截面不足不补**：板块日K有残缺日（只有个位数板块），在这种日子上算百分位是噪声。
   少于 --min-boards（默认 40）个板块的日期直接跳过。

用法：
    python scripts/Others/backfill_sector_flow_hist.py                 # 补全部可补日期
    python scripts/Others/backfill_sector_flow_hist.py --start 2026-06-01
    python scripts/Others/backfill_sector_flow_hist.py --dry-run       # 只看会补哪些天
    python scripts/Others/backfill_sector_flow_hist.py --min-boards 60
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('backfill_sector_flow')


def main():
    ap = argparse.ArgumentParser(description='用本地板块日K回补行业板块热度快照历史')
    ap.add_argument('--start', help='起始日期 YYYY-MM-DD（缺省=板块日K最早日期）')
    ap.add_argument('--end', help='结束日期 YYYY-MM-DD（缺省=最新）')
    ap.add_argument('--min-boards', type=int, default=40,
                    help='当日板块数少于该值则跳过（横截面太薄，百分位无意义），默认 40')
    ap.add_argument('--dry-run', action='store_true', help='只打印将要回补的日期，不写库')
    args = ap.parse_args()

    from App import create_app
    from App.exts import db
    from sqlalchemy import text
    from App.models.strategy.SectorFlowDaily import SectorFlowDaily, HEAT_V_HIST, SRC_HIST
    from App.services.sector_flow_service import compute_derived

    app = create_app()
    with app.app_context():
        SectorFlowDaily.ensure_table()
        eng = db.engines['quanttradingsystem']

        # 1) 板块名称表
        names = {}
        try:
            with eng.connect() as conn:
                for code, nm in conn.execute(text(
                        'SELECT board_code, board_name FROM eval_board')).fetchall():
                    names[code] = nm
        except Exception as e:
            logger.warning(f'读 eval_board 名称失败（不影响回补）: {e}')

        # 2) 拉板块日K
        where = ["stock_code LIKE 'BK%'", "stock_code NOT LIKE '%S'"]
        params = {}
        if args.start:
            where.append('date >= :start')
            params['start'] = args.start
        if args.end:
            where.append('date <= :end')
            params['end'] = args.end
        sql = (f"SELECT stock_code, date, close, money FROM data_stock_daily "
               f"WHERE {' AND '.join(where)} ORDER BY stock_code, date")
        with eng.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        if not rows:
            logger.error('板块日K无数据，无可回补')
            return 1

        # 3) 组织成 {code: [(date, close, money)...]}，算逐日涨跌幅
        series = {}
        for code, d, close, money in rows:
            series.setdefault(code, []).append((d, float(close or 0), float(money or 0)))

        by_date = {}   # {date: [row dict...]}
        for code, pts in series.items():
            for i in range(1, len(pts)):
                d, close, money = pts[i]
                prev_close = pts[i - 1][1]
                if not prev_close or not close:
                    continue
                by_date.setdefault(d, []).append({
                    'board_type': 'industry',
                    'board_code': code,
                    'board_name': names.get(code) or code,
                    'change_pct': round((close / prev_close - 1) * 100, 4),
                    'amount': money or None,
                    'main_net': None, 'main_pct': None, 'turnover_rate': None,
                    'total_cap': None, 'up_count': None, 'down_count': None,
                    'lead_stock': None, 'lead_pct': None,
                })

        # 4) 已有实盘快照的日期：整天跳过，绝不覆盖
        with eng.connect() as conn:
            live_dates = {r[0] for r in conn.execute(text(
                "SELECT DISTINCT date FROM mkt_sector_flow_daily "
                "WHERE board_type='industry' "
                "AND (source IS NULL OR source IN ('em','em-delay','ak'))"
            )).fetchall()}

        todo, skip_thin, skip_live = [], [], []
        for d in sorted(by_date):
            if d in live_dates:
                skip_live.append(d)
            elif len(by_date[d]) < args.min_boards:
                skip_thin.append(d)
            else:
                todo.append(d)

        logger.info(f'可回补 {len(todo)} 天；跳过 已有实盘快照 {len(skip_live)} 天、'
                    f'横截面不足(<{args.min_boards}) {len(skip_thin)} 天')
        if skip_thin:
            logger.info('横截面不足的日期（板块日K当天残缺）: '
                        + ', '.join(str(x) for x in skip_thin[:15])
                        + (' ...' if len(skip_thin) > 15 else ''))
        if not todo:
            logger.info('没有需要回补的日期')
            return 0
        logger.info(f'区间: {todo[0]} ~ {todo[-1]}')
        if args.dry_run:
            logger.info('--dry-run，未写库')
            return 0

        # 5) 逐日算派生量并写入
        now = datetime.utcnow()
        written = 0
        for d in todo:
            day_rows = by_date[d]
            compute_derived(day_rows, heat_version=HEAT_V_HIST)
            (SectorFlowDaily.query
             .filter(SectorFlowDaily.date == d,
                     SectorFlowDaily.board_type == 'industry')
             .delete(synchronize_session=False))
            db.session.bulk_insert_mappings(SectorFlowDaily, [
                {**r, 'date': d, 'source': SRC_HIST, 'is_intraday': False,
                 'created_at': now} for r in day_rows])
            db.session.commit()
            written += len(day_rows)
        logger.info(f'回补完成：{len(todo)} 天 / {written} 行（source=hist）')
        return 0


if __name__ == '__main__':
    sys.exit(main())
