#!/usr/bin/env python3
"""一键运行新闻抓取 → 处理 → 聚合流水线

用法：
    python scripts/run_news_pipeline.py                # 默认抓 3 页（150 条）
    python scripts/run_news_pipeline.py --pages 10     # 抓更多
    python scripts/run_news_pipeline.py --date 2026-05-20  # 重新聚合某一天

无 --date 时按文章 published_at 的当天日期聚合。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)


def main():
    parser = argparse.ArgumentParser(description='新闻抓取 + 处理 + 聚合')
    parser.add_argument('--pages', type=int, default=3,
                        help='东财快讯抓取页数（每页 50 条）')
    parser.add_argument('--date', type=str, default=None,
                        help='聚合目标日期 YYYY-MM-DD，不填用今天')
    parser.add_argument('--skip-fetch', action='store_true',
                        help='跳过抓取，只重新聚合（用于重跑历史）')
    parser.add_argument('--reprocess', action='store_true',
                        help='不抓取，对当日已入库文章重跑实体识别+聚合'
                             '（停用词/词表改了后用这个重算）')
    args = parser.parse_args()

    target_date = date.today()
    if args.date:
        target_date = datetime.strptime(args.date, '%Y-%m-%d').date()

    from App import create_app
    from App.codes.downloads.news.eastmoney_news import fetch_kuaixun, normalize_items
    from App.services.news_pipeline import (
        ingest_articles, extract_entities, aggregate_daily,
    )
    from App.models.data.news import NewsFetchLog
    from App.exts import db

    app = create_app()
    with app.app_context():
        t0 = time.time()

        # ----- 阶段 1：抓取 -----
        items = []
        log = NewsFetchLog(
            run_date=target_date,
            source='eastmoney_kuaixun',
            status='running',
            started_at=datetime.now(),
        )
        db.session.add(log)
        db.session.commit()

        # --reprocess 意味着 skip-fetch；统一处理
        skip_fetch = args.skip_fetch or args.reprocess

        if skip_fetch:
            print('[skip-fetch] 跳过抓取，直接处理已入库文章')
        else:
            print(f'[fetch] 抓取东财快讯（{args.pages} 页）...')
            result = fetch_kuaixun(max_pages=args.pages)
            print(f'  pages_fetched: {result["pages_fetched"]}')
            print(f'  items_total:   {result["items_total"]}')
            print(f'  raw_file:      {result["raw_file"]}')
            if result['error']:
                print(f'  error: {result["error"]}')

            items = normalize_items(result['items'])
            log.fetched_count = result['items_total']

        # ----- 阶段 2：入库 -----
        if items:
            print(f'[ingest] 入库 {len(items)} 条规范化文章...')
            inserted_count, inserted_ids = ingest_articles(items)
            print(f'  新入库:   {inserted_count}')
            print(f'  跳过重复: {len(items) - inserted_count}')
            log.inserted_count = inserted_count
        else:
            inserted_ids = []

        # ----- 阶段 3：实体识别 -----
        # skip_fetch / reprocess：重新跑当天所有文章的实体（用于改了停用词后重算）
        print('[entity] 实体识别 + 关键词抽取...')
        if skip_fetch:
            # reprocess 跑该日 published_at 当天的所有文章；
            # 仅 skip-fetch 时按 fetched_at 也可以接受
            from datetime import datetime as _dt, timedelta as _td
            from App.models.data.news import NewsArticle as _NA
            from App.exts import db as _db
            day_start = _dt.combine(target_date, _dt.min.time())
            day_end = day_start + _td(days=1)
            article_ids_today = [r.id for r in _NA.query
                                 .filter(_NA.published_at >= day_start,
                                         _NA.published_at < day_end)
                                 .all()]
            rows = extract_entities(article_ids=article_ids_today or None)
        else:
            rows = extract_entities(article_ids=inserted_ids or None)
        print(f'  写入实体行: {rows}')

        # ----- 阶段 4：聚合 -----
        print(f'[aggregate] 聚合话题（{target_date}）...')
        topic_rows = aggregate_daily(target_date=target_date)
        print(f'  写入话题: {topic_rows}')

        log.status = 'success'
        log.duration_ms = int((time.time() - t0) * 1000)
        log.finished_at = datetime.now()
        db.session.commit()

        print(f'\n完成。总耗时 {log.duration_ms} ms')


if __name__ == '__main__':
    main()
