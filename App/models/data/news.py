# -*- coding: utf-8 -*-
"""
财经新闻 / 热点话题数据模型

四张表分工：
- news_articles: 去重后的新闻原文（URL 唯一）
- news_article_entities: 文章 → 股票/板块/关键词 的多对多映射
- news_topics_daily: 每日话题聚合（前端直接读这张，无需现算）
- news_fetch_log: 抓取任务日志（监控用）
"""
from datetime import datetime

from sqlalchemy.dialects.mysql import MEDIUMTEXT

from App.exts import db


class NewsArticle(db.Model):
    """新闻原文（已去重）"""
    __tablename__ = 'news_articles'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    source = db.Column(db.String(32), nullable=False, index=True,
                       comment='来源: eastmoney_kuaixun / cls_telegraph / ...')
    source_id = db.Column(db.String(64), index=True,
                          comment='原站文章 ID（同源去重用）')
    url = db.Column(db.String(512), nullable=False, unique=True,
                    comment='文章 URL，天然去重键')
    title = db.Column(db.String(512), nullable=False, comment='标题')
    content = db.Column(MEDIUMTEXT, comment='正文（部分快讯无正文）')
    published_at = db.Column(db.DateTime, index=True,
                             comment='发布时间（原站）')
    fetched_at = db.Column(db.DateTime, default=datetime.now, index=True,
                           comment='抓取入库时间')
    raw_tags = db.Column(db.Text, comment='原站自带标签 JSON 串')
    importance = db.Column(db.SmallInteger, default=0,
                           comment='原站重要性标记: 0=普通 1=重要')

    __table_args__ = (
        db.Index('idx_news_published', 'published_at'),
        db.Index('idx_news_source_published', 'source', 'published_at'),
    )

    def __repr__(self):
        return f'<NewsArticle {self.source} {self.title[:30]}>'


class NewsArticleEntity(db.Model):
    """文章 → 实体（股票/板块/关键词）映射

    一篇文章可命中多个股票/板块/关键词。weight 用于排序（命中次数 / TF-IDF）。
    """
    __tablename__ = 'news_article_entities'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    article_id = db.Column(db.BigInteger, nullable=False, index=True)
    entity_type = db.Column(db.String(16), nullable=False,
                            comment='stock / board / concept / keyword')
    entity_code = db.Column(db.String(64), nullable=False,
                            comment='股票代码 / 板块代码 / 关键词文本')
    entity_name = db.Column(db.String(128),
                            comment='可读名称（股票名/板块名）')
    weight = db.Column(db.Float, default=1.0,
                       comment='权重: TF-IDF 或命中次数')

    __table_args__ = (
        db.Index('idx_entity_lookup', 'entity_type', 'entity_code'),
        db.Index('idx_article_entities', 'article_id', 'entity_type'),
    )

    def __repr__(self):
        return f'<Entity {self.entity_type}:{self.entity_code} art={self.article_id}>'


class NewsTopicDaily(db.Model):
    """每日话题聚合表 —— 前端排行榜直接读这张

    每天一个 (date, topic) 行。topic 可以是关键词、板块名或聚类簇心。
    """
    __tablename__ = 'news_topics_daily'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, nullable=False, index=True,
                     comment='聚合日期')
    topic = db.Column(db.String(128), nullable=False,
                      comment='话题名（关键词/板块名/聚类标签）')
    topic_type = db.Column(db.String(16), default='keyword',
                           comment='keyword / board / concept / cluster')
    article_count = db.Column(db.Integer, default=0,
                              comment='当日命中文章数')
    score = db.Column(db.Float, default=0.0,
                      comment='热度分（TF-IDF 加权 / 出现频次）')
    trend_7d_change = db.Column(db.Float,
                                comment='相比过去 7 天均值的变化率')
    related_boards = db.Column(db.Text,
                               comment='关联板块 JSON: [{code,name,count}]')
    related_stocks = db.Column(db.Text,
                               comment='关联股票 JSON: [{code,name,count}]')
    sample_titles = db.Column(db.Text,
                              comment='代表性标题 JSON: [str]，前端预览用')
    computed_at = db.Column(db.DateTime, default=datetime.now,
                            onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('date', 'topic', 'topic_type', name='uq_topic_daily'),
        db.Index('idx_topic_date_score', 'date', 'score'),
    )

    def __repr__(self):
        return f'<TopicDaily {self.date} {self.topic} n={self.article_count}>'


class NewsKeywordOverride(db.Model):
    """用户对关键词的人工覆盖（页面 UI 维护）

    action:
        'stopword' —— 强制加入停用词（即便命中财经词表也会被屏蔽）
        'finance'  —— 强制加入财经词白名单（按核心财经词显示 = 红色）
    同一个 term 一种 action 只允许存在一条（UNIQUE 约束）。
    """
    __tablename__ = 'news_keyword_overrides'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    term = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(16), nullable=False,
                       comment='stopword / finance')
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('term', 'action', name='uq_override_term_action'),
        db.Index('idx_override_action', 'action'),
    )

    def __repr__(self):
        return f'<KwOverride {self.action}:{self.term}>'


class NewsFetchLog(db.Model):
    """抓取任务日志（监控/调度用）"""
    __tablename__ = 'news_fetch_log'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    run_date = db.Column(db.Date, nullable=False, index=True)
    source = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), nullable=False,
                       comment='success / partial / failed')
    fetched_count = db.Column(db.Integer, default=0,
                              comment='本次拉到的条数（含重复）')
    inserted_count = db.Column(db.Integer, default=0,
                               comment='去重后实际入库条数')
    duration_ms = db.Column(db.Integer)
    error_msg = db.Column(db.Text)
    started_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)

    def __repr__(self):
        return f'<FetchLog {self.run_date} {self.source} {self.status} n={self.inserted_count}>'
