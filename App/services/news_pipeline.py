# -*- coding: utf-8 -*-
"""新闻处理流水线

阶段：
  1. ingest_articles(items)        —— 入库 news_articles（URL 去重）
  2. extract_entities(article_ids) —— 命中股票名/代码、板块名、jieba 关键词 → news_article_entities
  3. aggregate_daily(date)         —— 聚合 news_topics_daily

设计：
- 每一步都可单独跑（便于排错和重跑）
- 实体识别用 StockInfo 全表 + 中文姓名匹配；不依赖外部 NER
- 关键词用 jieba TF-IDF 提取 top-K（默认每篇 5 个）
- 聚合时把当天所有文章的实体/关键词按出现次数排序，写入 news_topics_daily
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from App.exts import db
from App.models.data.basic_info import StockInfo
from App.models.data.news import (
    NewsArticle, NewsArticleEntity, NewsTopicDaily,
)

logger = logging.getLogger(__name__)

# 关键词停用词（金融常见噪声）
_STOPWORDS = {
    '公司', '股份', '集团', '科技', '股票', '股价', '今日', '今天', '昨日',
    '宣布', '表示', '认为', '披露', '发布', '公告', '上涨', '下跌', '涨幅',
    '跌幅', '亿元', '万元', '人民币', '美元', '截至', '目前', '一季度',
    '二季度', '三季度', '四季度', '同比', '环比', '增长', '下降', '业务',
    '产品', '行业', '市场', '投资者', '消息', '记者', '相关', '方面',
    '我国', '中国', '一财', '财联社', '一财网', '记者获悉', '据悉', '获悉',
}

# 最小关键词长度 —— 过滤单字噪声
_MIN_KEYWORD_LEN = 2


# ============================================================
# 阶段 1：入库
# ============================================================

def ingest_articles(items: List[Dict]) -> Tuple[int, List[int]]:
    """把 normalize 后的文章列表入库 news_articles，按 URL 去重。

    Args:
        items: normalize_items() 输出，dict 含 source/source_id/url/title/...

    Returns:
        (inserted_count, inserted_ids)
    """
    if not items:
        return 0, []

    urls = [it['url'] for it in items]
    existing = {a.url for a in NewsArticle.query.filter(NewsArticle.url.in_(urls)).all()}

    seen_in_batch: Set[str] = set()
    inserted_ids: List[int] = []
    inserted_objs: List[NewsArticle] = []
    for it in items:
        url = it['url']
        if url in existing or url in seen_in_batch:
            continue
        seen_in_batch.add(url)
        a = NewsArticle(
            source=it['source'],
            source_id=it.get('source_id'),
            url=it['url'],
            title=it['title'],
            content=it.get('content') or '',
            published_at=it.get('published_at'),
            raw_tags=it.get('raw_tags'),
            importance=it.get('importance') or 0,
        )
        db.session.add(a)
        inserted_objs.append(a)

    if inserted_objs:
        db.session.flush()  # 拿 id
        inserted_ids = [a.id for a in inserted_objs]
        db.session.commit()
        logger.info(f'[ingest] 入库 {len(inserted_ids)} 条新文章（跳过 {len(items)-len(inserted_ids)} 条已存在）')
    else:
        logger.info(f'[ingest] 全部已存在，无新增（共 {len(items)} 条）')

    return len(inserted_ids), inserted_ids


# ============================================================
# 阶段 2：实体识别
# ============================================================

_stock_index_cache: Optional[Dict] = None


def _build_stock_index() -> Dict:
    """从 StockInfo 加载全表，构建 {name: (code, name)} 索引。

    缓存在进程级（一次启动只查一次）。
    """
    global _stock_index_cache
    if _stock_index_cache is not None:
        return _stock_index_cache

    name_to_stock: Dict[str, Tuple[str, str]] = {}
    code_set: Set[str] = set()
    rows = StockInfo.query.with_entities(StockInfo.code, StockInfo.name).all()
    for code, name in rows:
        if not name or not code:
            continue
        name = name.strip()
        code = code.strip()
        if len(name) >= 2:
            name_to_stock[name] = (code, name)
        code_set.add(code)

    _stock_index_cache = {'name_to_stock': name_to_stock, 'code_set': code_set}
    logger.info(f'[entity] 股票索引就绪：{len(name_to_stock)} 个名称 / {len(code_set)} 个代码')
    return _stock_index_cache


def _extract_stock_hits(text: str, idx: Dict) -> List[Tuple[str, str, int]]:
    """在文本里找股票名/代码命中。

    Returns: [(code, name, hit_count)]
    """
    name_to_stock = idx['name_to_stock']
    code_set = idx['code_set']

    hits: Dict[str, Tuple[str, int]] = {}  # code -> (name, count)

    # 1) 股票名命中：直接 in test
    for name, (code, full_name) in name_to_stock.items():
        if name and name in text:
            c = text.count(name)
            if c > 0:
                prev = hits.get(code)
                hits[code] = (full_name, (prev[1] if prev else 0) + c)

    # 2) 6 位代码命中（A 股）
    for m in re.finditer(r'\b(\d{6})\b', text):
        code = m.group(1)
        if code in code_set:
            prev = hits.get(code)
            hits[code] = (prev[0] if prev else code, (prev[1] if prev else 0) + 1)

    return [(code, name, cnt) for code, (name, cnt) in hits.items()]


def _extract_keywords(text: str, top_k: int = 8) -> List[Tuple[str, float, bool]]:
    """财经域关键词提取。委托给 news_keywords 模块。

    Returns: [(word, weight, is_finance_term)]
    """
    from App.services.news_keywords import extract_finance_keywords
    return extract_finance_keywords(text, top_k=top_k, min_len=_MIN_KEYWORD_LEN)


def extract_entities(article_ids: Optional[List[int]] = None,
                     date_from: Optional[date] = None) -> int:
    """对指定文章批量做实体识别 + 关键词抽取，写入 news_article_entities。

    Args:
        article_ids: 指定文章 ID 列表；若为 None，则用 date_from 过滤
        date_from: 只处理此日期（含）之后入库的文章

    Returns:
        本次写入的实体行数
    """
    q = NewsArticle.query
    if article_ids:
        q = q.filter(NewsArticle.id.in_(article_ids))
    elif date_from:
        q = q.filter(NewsArticle.fetched_at >= datetime.combine(date_from, datetime.min.time()))
    else:
        # 安全默认：只处理今天的
        today = date.today()
        q = q.filter(NewsArticle.fetched_at >= datetime.combine(today, datetime.min.time()))

    articles = q.all()
    if not articles:
        logger.info('[entity] 无待处理文章')
        return 0

    idx = _build_stock_index()
    rows_written = 0

    for a in articles:
        text = (a.title or '') + ' ' + (a.content or '')

        # 先清掉本文章的旧实体（重跑场景）
        NewsArticleEntity.query.filter_by(article_id=a.id).delete()

        # 股票命中
        for code, name, cnt in _extract_stock_hits(text, idx):
            db.session.add(NewsArticleEntity(
                article_id=a.id, entity_type='stock',
                entity_code=code, entity_name=name, weight=float(cnt),
            ))
            rows_written += 1

        # 关键词（财经域 TF + 财经词加权）
        # is_finance 用 entity_name 携带（'fin' / ''），聚合时用作过滤依据
        for kw, w, is_fin in _extract_keywords(text, top_k=8):
            db.session.add(NewsArticleEntity(
                article_id=a.id, entity_type='keyword',
                entity_code=kw, entity_name='fin' if is_fin else '',
                weight=w,
            ))
            rows_written += 1

    db.session.commit()
    logger.info(f'[entity] 处理 {len(articles)} 篇文章，写入 {rows_written} 行实体')
    return rows_written


# ============================================================
# 阶段 3：每日话题聚合
# ============================================================

def aggregate_daily(target_date: Optional[date] = None,
                    top_n: int = 30,
                    min_count: int = 2) -> int:
    """把指定日期的文章按关键词/股票聚合到 news_topics_daily。

    话题热度 score = sum(weight) across articles。
    related_stocks/related_boards 留存关联实体快照（前端面板用）。

    Args:
        target_date: 目标日期，默认今天
        top_n: 只保留热度 top N 个话题
        min_count: 文章数 < 此值的话题不入库（避免单篇噪声）

    Returns:
        本次写入/更新的话题行数
    """
    target_date = target_date or date.today()
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # 拉当日所有文章 + 实体
    articles = NewsArticle.query.filter(
        NewsArticle.published_at >= day_start,
        NewsArticle.published_at < day_end,
    ).all()
    article_ids = [a.id for a in articles]
    if not article_ids:
        logger.info(f'[aggregate] {target_date} 无文章')
        return 0

    title_by_id = {a.id: a.title for a in articles}

    ents = NewsArticleEntity.query.filter(
        NewsArticleEntity.article_id.in_(article_ids),
    ).all()

    # topic 维度：keyword 类型
    # （stock 单独走 related_stocks，不作为独立 topic —— 避免被热门股票刷屏）
    kw_articles: Dict[str, Set[int]] = defaultdict(set)
    kw_score: Dict[str, float] = defaultdict(float)
    kw_is_finance: Dict[str, bool] = {}
    stock_articles: Dict[str, Set[int]] = defaultdict(set)
    stock_name_map: Dict[str, str] = {}

    for e in ents:
        if e.entity_type == 'keyword':
            kw_articles[e.entity_code].add(e.article_id)
            kw_score[e.entity_code] += float(e.weight or 1.0)
            # entity_name == 'fin' 表示该词命中财经词表
            kw_is_finance[e.entity_code] = (
                kw_is_finance.get(e.entity_code, False) or e.entity_name == 'fin'
            )
        elif e.entity_type == 'stock':
            stock_articles[e.entity_code].add(e.article_id)
            if e.entity_name:
                stock_name_map[e.entity_code] = e.entity_name

    # 财经相关性 + 文章数门槛
    # - 命中财经词表：min_count 起即可入榜（默认 2）
    # - 仅靠股票关联：要求 ≥ max(min_count, 3)（避免单篇噪声混进来）
    filtered: List[Tuple[str, Set[int]]] = []
    fin_min = max(min_count, 1)
    nonfin_min = max(min_count, 3)
    for topic, art_set in kw_articles.items():
        is_fin_term = kw_is_finance.get(topic, False)
        has_stock = any((sart & art_set) for sart in stock_articles.values())
        n = len(art_set)
        if is_fin_term and n >= fin_min:
            filtered.append((topic, art_set))
        elif has_stock and n >= nonfin_min:
            filtered.append((topic, art_set))

    # 选出 top_n 关键词作为今日话题
    topic_rank = sorted(
        filtered,
        key=lambda x: (len(x[1]), kw_score[x[0]]),
        reverse=True,
    )

    # 同日重写（不增量，每次重算）—— 同时清掉旧的 keyword/finance 两种 type
    NewsTopicDaily.query.filter(
        NewsTopicDaily.date == target_date,
        NewsTopicDaily.topic_type.in_(['keyword', 'finance']),
    ).delete(synchronize_session=False)

    written = 0
    for topic, art_set in topic_rank[:top_n]:
        if len(art_set) < min_count:
            continue

        # 关联股票：哪些股票在这个话题的文章里被提到
        related_stocks = []
        sc = Counter()
        for sid, sart in stock_articles.items():
            overlap = sart & art_set
            if overlap:
                sc[sid] = len(overlap)
        for sid, n in sc.most_common(10):
            related_stocks.append({
                'code': sid,
                'name': stock_name_map.get(sid, sid),
                'count': n,
            })

        sample_titles = []
        for aid in list(art_set)[:5]:
            t = title_by_id.get(aid)
            if t:
                sample_titles.append(t)

        row = NewsTopicDaily(
            date=target_date,
            topic=topic,
            # 用 topic_type 兼承"是否命中财经词表"信号：finance / keyword
            topic_type='finance' if kw_is_finance.get(topic) else 'keyword',
            article_count=len(art_set),
            score=float(kw_score[topic]),
            related_stocks=json.dumps(related_stocks, ensure_ascii=False),
            sample_titles=json.dumps(sample_titles, ensure_ascii=False),
        )
        db.session.add(row)
        written += 1

    db.session.commit()
    logger.info(f'[aggregate] {target_date} 写入 {written} 个话题')
    return written


# ============================================================
# 编排：一键跑全流程
# ============================================================

def run_full_pipeline(items: List[Dict], target_date: Optional[date] = None) -> Dict:
    """ingest → entity → aggregate 一条龙。供 scripts/手动运行调用。

    Args:
        items: 已 normalize 的文章列表
        target_date: 聚合的目标日期（默认今天）

    Returns: 各阶段统计
    """
    inserted_count, inserted_ids = ingest_articles(items)
    entity_rows = extract_entities(article_ids=inserted_ids or None)
    topic_rows = aggregate_daily(target_date=target_date or date.today())

    return {
        'inserted': inserted_count,
        'entities': entity_rows,
        'topics': topic_rows,
    }
