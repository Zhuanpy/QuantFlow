#!/usr/bin/env python3
"""
创建新闻 / 热点话题相关表

只建这 4 张：news_articles / news_article_entities / news_topics_daily / news_fetch_log
其他模型已存在的表不会重建（create_all 是 idempotent 的）
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App import create_app
from App.exts import db
from App.models.data.news import (
    NewsArticle, NewsArticleEntity, NewsTopicDaily, NewsFetchLog,
)


def main():
    app = create_app()
    with app.app_context():
        target_tables = [
            NewsArticle.__table__,
            NewsArticleEntity.__table__,
            NewsTopicDaily.__table__,
            NewsFetchLog.__table__,
        ]
        # 指定 bind 创建，避免误操作默认 bind
        engine = db.engines['quanttradingsystem']
        for t in target_tables:
            t.create(bind=engine, checkfirst=True)
            print(f'OK  {t.name}')

        from sqlalchemy import inspect
        insp = inspect(engine)
        for t in target_tables:
            cols = insp.get_columns(t.name)
            print(f'\n[{t.name}] 共 {len(cols)} 列')
            for c in cols:
                print(f'  - {c["name"]:20} {c["type"]}')


if __name__ == '__main__':
    main()
