# -*- coding: utf-8 -*-
"""财经新闻抓取子模块

每个文件一个数据源（eastmoney_news / cls_telegraph / ...）
抓取结果统一：原始 JSON 落 data/news/raw/<date>/，由处理流水线读回解析入库。
"""
