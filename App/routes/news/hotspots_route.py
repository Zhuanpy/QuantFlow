# -*- coding: utf-8 -*-
"""热点话题页面 + API

页面: /news/hotspots
API:
    GET /news/api/hotspots/dates    返回有数据的日期列表
    GET /news/api/hotspots?date=YYYY-MM-DD&limit=N
        返回当日话题排行，含关联股票/样例标题
    GET /news/api/hotspots/articles?topic=X&date=YYYY-MM-DD
        点击话题展开：返回该话题在当日的文章列表
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import func

from App.exts import db
from App.models.data.news import (
    NewsArticle, NewsArticleEntity, NewsTopicDaily, NewsKeywordOverride,
)

logger = logging.getLogger(__name__)

news_hotspots_bp = Blueprint(
    'news_hotspots_bp', __name__, url_prefix='/news',
)


# ---------------------------------------------------------------
# 页面
# ---------------------------------------------------------------

@news_hotspots_bp.route('/hotspots')
def hotspots_page():
    return render_template('news/hotspots.html')


# ---------------------------------------------------------------
# API
# ---------------------------------------------------------------

@news_hotspots_bp.route('/api/hotspots/dates', methods=['GET'])
def api_dates():
    """有聚合数据的日期列表（降序），前端做日期下拉"""
    rows = (db.session.query(NewsTopicDaily.date,
                             func.count(NewsTopicDaily.id).label('cnt'))
            .group_by(NewsTopicDaily.date)
            .order_by(NewsTopicDaily.date.desc())
            .limit(30).all())
    return jsonify({
        'success': True,
        'dates': [{'date': r.date.isoformat(), 'topic_count': int(r.cnt)} for r in rows],
    })


@news_hotspots_bp.route('/api/hotspots', methods=['GET'])
def api_hotspots():
    """当日话题排行"""
    date_str = request.args.get('date')
    limit = int(request.args.get('limit', 30))

    if date_str:
        try:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': f'无效日期: {date_str}'}), 400
    else:
        # 默认取最新有数据的一天
        latest = (db.session.query(func.max(NewsTopicDaily.date)).scalar())
        target = latest or date.today()

    stop_terms = _current_stopword_terms()
    q = NewsTopicDaily.query.filter_by(date=target)
    if stop_terms:
        q = q.filter(~NewsTopicDaily.topic.in_(stop_terms))
    rows = (q.order_by(NewsTopicDaily.article_count.desc(),
                       NewsTopicDaily.score.desc())
             .limit(limit).all())

    # 当日文章总数（顶部统计用）
    day_start = datetime.combine(target, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    total_articles = (NewsArticle.query
                      .filter(NewsArticle.published_at >= day_start,
                              NewsArticle.published_at < day_end)
                      .count())

    data = []
    for r in rows:
        data.append({
            'topic': r.topic,
            'topic_type': r.topic_type,
            'article_count': r.article_count,
            'score': round(float(r.score or 0), 2),
            'related_stocks': json.loads(r.related_stocks or '[]'),
            'sample_titles': json.loads(r.sample_titles or '[]'),
        })

    return jsonify({
        'success': True,
        'date': target.isoformat(),
        'total_articles': total_articles,
        'topic_count': len(data),
        'topics': data,
    })


@news_hotspots_bp.route('/api/hotspots/cloud', methods=['GET'])
def api_hotspots_cloud():
    """词云专用：聚合最近 N 天的话题热度。

    Query:
      days   默认 30；窗口 = [latest_date - days + 1, latest_date]
      limit  默认 80；按聚合后文章总数倒序取 top N
    """
    try:
        days = max(1, min(int(request.args.get('days', 30)), 365))
    except ValueError:
        days = 30
    try:
        limit = max(10, min(int(request.args.get('limit', 80)), 300))
    except ValueError:
        limit = 80

    # 锚点：最新有数据的一天；窗口前推 days-1
    latest = db.session.query(func.max(NewsTopicDaily.date)).scalar()
    if latest is None:
        return jsonify({'success': True, 'days': days, 'limit': limit,
                        'window': None, 'topics': []})
    start = latest - timedelta(days=days - 1)

    stop_terms = _current_stopword_terms()
    base_q = (db.session.query(
                NewsTopicDaily.topic,
                NewsTopicDaily.topic_type,
                func.sum(NewsTopicDaily.article_count).label('articles'),
                func.sum(NewsTopicDaily.score).label('total_score'),
                func.count(NewsTopicDaily.id).label('days_appeared'),
                func.max(NewsTopicDaily.date).label('last_date'))
              .filter(NewsTopicDaily.date >= start,
                      NewsTopicDaily.date <= latest))
    if stop_terms:
        base_q = base_q.filter(~NewsTopicDaily.topic.in_(stop_terms))
    rows = (base_q.group_by(NewsTopicDaily.topic, NewsTopicDaily.topic_type)
                  .order_by(func.sum(NewsTopicDaily.article_count).desc())
                  .limit(limit).all())

    # 对前 limit 个 topic 单独拉一遍 related_stocks / sample_titles —— 只取每个 topic
    # 在窗口里最新一天那条（够代表性，避免合并所有日期带来的复杂度）
    out = []
    for r in rows:
        latest_row = (NewsTopicDaily.query
                      .filter(NewsTopicDaily.topic == r.topic,
                              NewsTopicDaily.topic_type == r.topic_type,
                              NewsTopicDaily.date >= start,
                              NewsTopicDaily.date <= latest)
                      .order_by(NewsTopicDaily.date.desc())
                      .first())
        out.append({
            'topic': r.topic,
            'topic_type': r.topic_type,
            'article_count': int(r.articles or 0),
            'score': round(float(r.total_score or 0), 2),
            'days_appeared': int(r.days_appeared or 0),
            'last_date': r.last_date.isoformat() if r.last_date else None,
            'related_stocks': json.loads(latest_row.related_stocks or '[]') if latest_row else [],
            'sample_titles': json.loads(latest_row.sample_titles or '[]') if latest_row else [],
        })

    return jsonify({
        'success': True,
        'days': days,
        'limit': limit,
        'window': {'start': start.isoformat(), 'end': latest.isoformat()},
        'topics': out,
    })


@news_hotspots_bp.route('/api/hotspots/articles', methods=['GET'])
def api_topic_articles():
    """某话题相关文章列表（点击展开用）。

    Query:
      topic   必填：话题（关键词 entity_code）
      date    可选：YYYY-MM-DD；不传或 recent=1 时不按日过滤
      recent  可选：1 → 忽略 date，返回该话题最近 N 篇（跨日）
      limit   可选：最多返回多少篇，默认 10，上限 50
    """
    topic = request.args.get('topic', '').strip()
    date_str = request.args.get('date')
    recent = request.args.get('recent') in ('1', 'true', 'yes')
    try:
        limit = max(1, min(int(request.args.get('limit', 10)), 50))
    except ValueError:
        limit = 10

    if not topic:
        return jsonify({'success': False, 'message': 'topic 不能为空'}), 400

    # 这个话题命中了哪些文章
    ent_rows = (db.session.query(NewsArticleEntity.article_id)
                .filter(NewsArticleEntity.entity_type == 'keyword',
                        NewsArticleEntity.entity_code == topic)
                .all())
    article_ids = [r.article_id for r in ent_rows]
    if not article_ids:
        return jsonify({'success': True, 'topic': topic,
                        'date': date_str if not recent else None,
                        'recent': recent, 'limit': limit, 'articles': []})

    q = NewsArticle.query.filter(NewsArticle.id.in_(article_ids))

    # 当传了 date 且非 recent 模式：按当日严格过滤（保留老行为）
    if date_str and not recent:
        try:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': f'无效日期: {date_str}'}), 400
        day_start = datetime.combine(target, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        q = q.filter(NewsArticle.published_at >= day_start,
                     NewsArticle.published_at < day_end)

    arts = (q.order_by(NewsArticle.published_at.desc())
             .limit(limit).all())

    return jsonify({
        'success': True,
        'topic': topic,
        'date': date_str if not recent else None,
        'recent': recent,
        'limit': limit,
        'count': len(arts),
        'articles': [
            {
                'id': a.id,
                'title': a.title,
                'url': a.url,
                'source': a.source,
                'published_at': a.published_at.isoformat() if a.published_at else None,
            } for a in arts
        ],
    })


# ---------------------------------------------------------------
# 关键词覆盖管理 + 重算
# ---------------------------------------------------------------

_VALID_ACTIONS = {'stopword', 'finance'}


def _current_stopword_terms() -> set:
    """读当前生效的停用词覆盖，用于查询时即时过滤话题。

    用户在 /news/hotspots 加停用词后，老的 news_topics_daily 行还在表里，
    不重跑聚合是不会被抹掉的；这里在 read path 做一次防御性过滤，
    保证界面即时响应（同时仍鼓励用户跑 reprocess 把表清干净）。
    """
    rows = NewsKeywordOverride.query.filter_by(action='stopword').all()
    return {r.term for r in rows}


@news_hotspots_bp.route('/api/overrides', methods=['GET'])
def api_list_overrides():
    """列出所有人工词表覆盖（页面上方"我的词表"面板用）"""
    rows = NewsKeywordOverride.query.order_by(
        NewsKeywordOverride.action,
        NewsKeywordOverride.created_at.desc(),
    ).all()
    return jsonify({
        'success': True,
        'items': [
            {
                'id': r.id,
                'term': r.term,
                'action': r.action,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            } for r in rows
        ],
    })


@news_hotspots_bp.route('/api/overrides', methods=['POST'])
def api_add_override():
    """加一条覆盖。body: {term, action}
    重复添加（term+action 已存在）静默成功；
    同一 term 加 stopword 时会清掉它的 finance 标记（停用词优先）"""
    from App.services.news_keywords import invalidate_overrides_cache

    body = request.get_json(silent=True) or {}
    term = (body.get('term') or '').strip()
    action = (body.get('action') or '').strip()

    if not term:
        return jsonify({'success': False, 'message': 'term 不能为空'}), 400
    if action not in _VALID_ACTIONS:
        return jsonify({'success': False,
                        'message': f'action 必须是 {_VALID_ACTIONS}'}), 400
    if len(term) > 64:
        return jsonify({'success': False, 'message': 'term 过长'}), 400

    # 若已存在同 term+action → 直接返回成功
    exist = NewsKeywordOverride.query.filter_by(term=term, action=action).first()
    if exist:
        return jsonify({'success': True, 'id': exist.id, 'created': False})

    # stopword 优先：加 stopword 时清掉同词的 finance 覆盖
    if action == 'stopword':
        NewsKeywordOverride.query.filter_by(term=term, action='finance').delete()

    row = NewsKeywordOverride(term=term, action=action)
    db.session.add(row)
    db.session.commit()
    invalidate_overrides_cache()

    return jsonify({'success': True, 'id': row.id, 'created': True})


@news_hotspots_bp.route('/api/overrides/<int:override_id>', methods=['DELETE'])
def api_delete_override(override_id):
    """删除一条覆盖（撤销）"""
    from App.services.news_keywords import invalidate_overrides_cache

    row = NewsKeywordOverride.query.get(override_id)
    if not row:
        return jsonify({'success': False, 'message': '不存在'}), 404
    db.session.delete(row)
    db.session.commit()
    invalidate_overrides_cache()
    return jsonify({'success': True})


# ---------------------------------------------------------------
# 自动抓取（页面打开时由前端拉一次：>24h 未抓就后台触发）
# ---------------------------------------------------------------

@news_hotspots_bp.route('/api/fetch_status', methods=['GET'])
def api_fetch_status():
    """看最近一次 success 抓取的时间，前端据此决定是否提醒/触发"""
    from App.services.news_auto_fetch import get_fetch_status
    return jsonify({'success': True, **get_fetch_status()})


@news_hotspots_bp.route('/api/auto_fetch', methods=['POST'])
def api_auto_fetch():
    """触发后台抓取。
    - 默认仅当上次成功 > 24h 才真正起线程；否则直接返回 fresh
    - 传 ?force=1 时强制再抓一次（"立即刷新"按钮用）
    """
    from App.services.news_auto_fetch import trigger_async
    force = (request.args.get('force') or
             (request.get_json(silent=True) or {}).get('force')) in (1, '1', True, 'true', 'yes')
    out = trigger_async(current_app._get_current_object(), force=force)
    return jsonify({'success': True, **out})


@news_hotspots_bp.route('/api/reprocess', methods=['POST'])
def api_reprocess():
    """对指定日期（默认今天）重跑实体识别 + 聚合。
    UI 在用户改完覆盖后调一次，词云立刻更新。
    """
    body = request.get_json(silent=True) or {}
    date_str = body.get('date')
    if date_str:
        try:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': f'无效日期: {date_str}'}), 400
    else:
        target = date.today()
        # 默认重跑最新有数据的一天，方便用户调词表时不必盯今天
        latest = (db.session.query(func.max(NewsTopicDaily.date)).scalar())
        if latest:
            target = latest

    from App.services.news_pipeline import extract_entities, aggregate_daily

    day_start = datetime.combine(target, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    article_ids = [r.id for r in NewsArticle.query
                   .filter(NewsArticle.published_at >= day_start,
                           NewsArticle.published_at < day_end).all()]
    entity_rows = extract_entities(article_ids=article_ids or None) if article_ids else 0
    topic_rows = aggregate_daily(target_date=target)

    return jsonify({
        'success': True,
        'date': target.isoformat(),
        'article_count': len(article_ids),
        'entity_rows': entity_rows,
        'topic_rows': topic_rows,
    })
