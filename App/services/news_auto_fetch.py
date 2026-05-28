# -*- coding: utf-8 -*-
"""新闻自动抓取触发器（非阻塞 / 进程内单实例）

设计：
- 用户打开 /news/hotspots 时，前端先 GET fetch_status；
  如果上次成功抓取距今 > STALE_HOURS，前端 POST auto_fetch 触发后台抓取。
- 这里不依赖任何外部调度器（APScheduler/cron 都没装），所以全靠"页面访问"驱动。
- 同一进程内用 threading.Lock + RUNNING 标志去重，避免并发触发刷出几次。
- 多进程部署下（gunicorn 等）每个 worker 一份状态，可能并发跑 N 次；
  目前是 Flask dev 单进程，先不引入跨进程锁。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 多久算"陈旧" → 自动触发再抓
STALE_HOURS = 24

# 自动模式默认抓的页数 —— 比每天定时多一些，弥补可能跨多日的缺口
DEFAULT_PAGES = 5

# ---- 模块级状态（仅供同进程） ----
_LOCK = threading.Lock()
_RUNNING = False
_LAST_TRIGGER_TS: Optional[datetime] = None  # 上一次"触发后台线程"的时刻
_LAST_RESULT: Optional[dict] = None          # 上一次后台跑完后的概要


def _state_snapshot() -> dict:
    """读全局状态的快照（持锁短时）。"""
    with _LOCK:
        return {
            'running': _RUNNING,
            'last_trigger_at': _LAST_TRIGGER_TS.isoformat() if _LAST_TRIGGER_TS else None,
            'last_result': dict(_LAST_RESULT) if _LAST_RESULT else None,
        }


def is_running() -> bool:
    with _LOCK:
        return _RUNNING


def get_last_success_log():
    """从 NewsFetchLog 表读最近一条 status='success' 记录。
    用 id desc 而不是 finished_at —— id 是自增主键，等价于按插入顺序倒序，
    且 MySQL 不支持 NULLS LAST，避免 finished_at 为 NULL 时排序行为不一致。
    """
    from App.models.data.news import NewsFetchLog
    return (NewsFetchLog.query
            .filter_by(status='success')
            .order_by(NewsFetchLog.id.desc())
            .first())


def get_fetch_status(app=None) -> dict:
    """返回当前抓取状态 + 是否需要自动重抓。"""
    log = get_last_success_log()
    now = datetime.now()
    last_at = log.finished_at or log.started_at if log else None
    hours = (now - last_at).total_seconds() / 3600.0 if last_at else None
    stale = hours is None or hours >= STALE_HOURS

    snap = _state_snapshot()
    return {
        'last_run_at': last_at.strftime('%Y-%m-%d %H:%M:%S') if last_at else None,
        'hours_since': round(hours, 2) if hours is not None else None,
        'stale_threshold_hours': STALE_HOURS,
        'stale': stale,
        'running': snap['running'],
        'last_trigger_at': snap['last_trigger_at'],
        'last_result': snap['last_result'],
    }


def trigger_async(app, pages: int = DEFAULT_PAGES, force: bool = False) -> dict:
    """尝试启动后台抓取。

    Args:
        app: Flask app 实例（线程里要 push app_context）
        pages: 抓取页数
        force: True 时即使数据"不陈旧"也跑；False 仅当 stale 才跑

    Returns:
        dict（含 triggered/running/reason）
    """
    global _RUNNING, _LAST_TRIGGER_TS

    status = get_fetch_status(app)
    if status['running']:
        return {'triggered': False, 'reason': 'already_running', **status}
    if not force and not status['stale']:
        return {'triggered': False, 'reason': 'fresh', **status}

    with _LOCK:
        if _RUNNING:
            # 双检：进入临界区前另一个请求可能已经抢到
            return {'triggered': False, 'reason': 'already_running', **status}
        _RUNNING = True
        _LAST_TRIGGER_TS = datetime.now()

    t = threading.Thread(
        target=_run_pipeline_in_thread,
        args=(app, pages),
        name='news-auto-fetch',
        daemon=True,
    )
    t.start()
    return {'triggered': True, 'reason': 'started', **get_fetch_status(app)}


def _run_pipeline_in_thread(app, pages: int):
    """后台线程入口：fetch → ingest → entities → aggregate（多日）→ 写 log。"""
    global _RUNNING, _LAST_RESULT

    from App.codes.downloads.news.eastmoney_news import fetch_kuaixun, normalize_items
    from App.services.news_pipeline import (
        ingest_articles, extract_entities, aggregate_daily,
    )
    from App.models.data.news import NewsArticle, NewsFetchLog
    from App.exts import db

    t0 = time.time()
    result = {
        'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'pages': pages,
    }
    log_id = None

    try:
        with app.app_context():
            # ---- log: running ----
            log = NewsFetchLog(
                run_date=date.today(),
                source='eastmoney_kuaixun_auto',
                status='running',
                started_at=datetime.now(),
            )
            db.session.add(log)
            db.session.commit()
            log_id = log.id

            # ---- 阶段 1：抓取 ----
            logger.info(f'[auto-fetch] 启动，pages={pages}')
            fr = fetch_kuaixun(max_pages=pages)
            items = normalize_items(fr['items'])
            log.fetched_count = fr['items_total']
            db.session.commit()

            # ---- 阶段 2：入库 ----
            inserted_count, inserted_ids = ingest_articles(items) if items else (0, [])
            log.inserted_count = inserted_count
            db.session.commit()
            result['inserted'] = inserted_count

            # ---- 阶段 3：实体识别（只对新入库的）----
            entity_rows = 0
            if inserted_ids:
                entity_rows = extract_entities(article_ids=inserted_ids)
            result['entity_rows'] = entity_rows

            # ---- 阶段 4：聚合 ----
            # 把新入库文章按 published_at 的日期分组，每个日期都跑一次聚合；
            # 这样跨多天的回补（比如距上次 4 天）能把每天的话题都补齐。
            dates_to_agg = set()
            if inserted_ids:
                for r in (NewsArticle.query
                          .filter(NewsArticle.id.in_(inserted_ids))
                          .with_entities(NewsArticle.published_at).all()):
                    if r.published_at:
                        dates_to_agg.add(r.published_at.date())
            dates_to_agg.add(date.today())  # 兜底 always 跑今天

            agg_total = 0
            agg_detail = []
            for d in sorted(dates_to_agg):
                n = aggregate_daily(target_date=d)
                agg_total += n
                agg_detail.append({'date': d.isoformat(), 'topic_rows': n})
            result['aggregated'] = agg_detail
            result['aggregated_topic_rows'] = agg_total

            # ---- 完成 ----
            log.status = 'success'
            log.finished_at = datetime.now()
            log.duration_ms = int((time.time() - t0) * 1000)
            db.session.commit()
            result['status'] = 'success'
            result['duration_ms'] = log.duration_ms
            logger.info(f'[auto-fetch] 完成: {result}')

    except Exception as e:
        logger.exception('[auto-fetch] 失败')
        result['status'] = 'failed'
        result['error'] = str(e)
        # 把 log 标 failed
        try:
            with app.app_context():
                from App.models.data.news import NewsFetchLog as _L
                from App.exts import db as _db
                if log_id is not None:
                    row = _L.query.get(log_id)
                    if row:
                        row.status = 'failed'
                        row.error_msg = str(e)[:1000]
                        row.finished_at = datetime.now()
                        row.duration_ms = int((time.time() - t0) * 1000)
                        _db.session.commit()
        except Exception:
            logger.exception('[auto-fetch] 写 failed 日志也失败')

    finally:
        with _LOCK:
            _RUNNING = False
            _LAST_RESULT = result
