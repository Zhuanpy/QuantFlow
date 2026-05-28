# -*- coding: utf-8 -*-
"""东方财富 7×24 快讯抓取

接口（已用 probe 验证）：
  https://newsapi.eastmoney.com/kuaixun/v2/api/list?column=102&pageindex=N&pagesize=50

返回 JSON 结构（节选）：
  { rc:1, news: [
      { id, newsid, url_unique, url_w, title, digest, showtime,
        column, Art_Media_Name, ... }
  ], PageCount, AllCount, AtPage }

column=102 是 7x24 快讯主流。

抓取策略：
- 翻页直到 (a) 拿到 max_pages，或 (b) 命中"已知最新文章 ID"则停（增量抓取）
- 原始 JSON 直接落盘 data/news/raw/<YYYY-MM-DD>/eastmoney_kuaixun_<HHMMSS>.json
- 不做解析、不去重 —— 那是处理流水线的活
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SOURCE_KEY = 'eastmoney_kuaixun'

API_URL = (
    'https://newsapi.eastmoney.com/kuaixun/v2/api/list'
    '?column=102&pageindex={page}&pagesize={size}'
)

DEFAULT_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://kuaixun.eastmoney.com/',
}


def _raw_dir(project_root: Path, day: Optional[str] = None) -> Path:
    """data/news/raw/<YYYY-MM-DD>/ —— 不存在则创建"""
    day = day or datetime.now().strftime('%Y-%m-%d')
    p = project_root / 'data' / 'news' / 'raw' / day
    p.mkdir(parents=True, exist_ok=True)
    return p


def _fetch_page(page: int, size: int = 50, timeout: int = 15) -> Optional[Dict]:
    """拉单页，返回原始 JSON dict，失败返回 None"""
    url = API_URL.format(page=page, size=size)
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        text = resp.text.strip()
        # 接口偶尔会被 cb= 包裹（JSONP），剥掉
        if text.startswith('(') and text.endswith(')'):
            text = text[1:-1]
        return json.loads(text)
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.warning(f'[eastmoney_kuaixun] page={page} 抓取失败: {e}')
        return None


def fetch_kuaixun(
    max_pages: int = 5,
    page_size: int = 50,
    stop_at_id: Optional[str] = None,
    page_delay: float = 0.6,
    project_root: Optional[Path] = None,
) -> Dict:
    """抓取东财 7x24 快讯，原始 JSON 落盘。

    Args:
        max_pages: 最多翻几页（每页 50 条，5 页 = 250 条，覆盖近几小时）
        page_size: 每页条数
        stop_at_id: 增量抓取 —— 命中此 Art_Code 则停止后续翻页
        page_delay: 翻页间隔秒数（避免触发风控）
        project_root: 项目根目录，不传则推断

    Returns:
        {
            'source': 'eastmoney_kuaixun',
            'pages_fetched': int,
            'items_total': int,         # 本次拉到的总条数（含重复）
            'first_art_code': str|None, # 本次最新文章 ID
            'raw_file': str,            # 落盘的原始 JSON 路径
            'items': [ {Art_Code, Art_Title, Art_ShowTime, ...} ],
            'error': str|None,
        }
    """
    if project_root is None:
        # config.get_project_root() 在 app 上下文外也可调用
        try:
            from config import Config
            project_root = Path(Config.get_project_root())
        except Exception:
            project_root = Path(__file__).resolve().parents[4]

    all_items: List[Dict] = []
    pages_fetched = 0
    first_code = None
    error = None
    raw_pages: List[Dict] = []

    for page in range(1, max_pages + 1):
        payload = _fetch_page(page, page_size)
        if payload is None:
            error = f'page {page} 抓取失败'
            break

        raw_pages.append({'page': page, 'payload': payload})

        # v2 接口返回 { rc:1, news:[...] }
        items = payload.get('news') or []
        if not items:
            logger.info(f'[eastmoney_kuaixun] page={page} 无数据，停止翻页')
            break

        pages_fetched += 1
        if first_code is None and items:
            first_code = items[0].get('id') or items[0].get('newsid')

        # 命中已知最新 ID → 增量抓取截断
        hit_stop = False
        for it in items:
            code = it.get('id') or it.get('newsid')
            if stop_at_id and code == stop_at_id:
                hit_stop = True
                break
            all_items.append(it)

        if hit_stop:
            logger.info(f'[eastmoney_kuaixun] 命中已存在 ID={stop_at_id}，停止翻页')
            break

        if page < max_pages:
            time.sleep(page_delay)

    # 原始 JSON 落盘（不管成功失败，只要拉到一页就存）
    raw_file = None
    if raw_pages:
        ts = datetime.now().strftime('%H%M%S')
        out = _raw_dir(project_root) / f'{SOURCE_KEY}_{ts}.json'
        out.write_text(
            json.dumps({
                'source': SOURCE_KEY,
                'fetched_at': datetime.now().isoformat(timespec='seconds'),
                'pages': raw_pages,
            }, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        raw_file = str(out)

    return {
        'source': SOURCE_KEY,
        'pages_fetched': pages_fetched,
        'items_total': len(all_items),
        'first_art_code': first_code,
        'raw_file': raw_file,
        'items': all_items,
        'error': error,
    }


def normalize_items(items: List[Dict]) -> List[Dict]:
    """把东财 v2 接口字段映射到 NewsArticle 字段。

    输出字段对齐 NewsArticle 模型：
      source / source_id / url / title / content / published_at / raw_tags / importance
    """
    out = []
    for it in items:
        code = it.get('id') or it.get('newsid')
        title = (it.get('title') or '').strip()
        if not code or not title:
            continue

        show_time = it.get('showtime') or it.get('ordertime') or ''
        published_at = None
        if show_time:
            try:
                published_at = datetime.strptime(show_time[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                published_at = None

        url = (it.get('url_unique') or it.get('url_w') or it.get('url_m')
               or f'https://finance.eastmoney.com/a/{code}.html')

        # v2 接口列表里只有 digest 摘要，没有正文 —— 留给后续可选的抓详情步骤
        content = (it.get('digest') or '').strip()

        # 原站标签：栏目编号、媒体来源
        raw_tags = {}
        for k in ('column', 'Art_Media_Name', 'newstype', 'type', 'topic'):
            v = it.get(k)
            if v not in (None, ''):
                raw_tags[k] = v

        # v2 接口暂未发现重要性标记，全设 0
        importance = 0

        out.append({
            'source': SOURCE_KEY,
            'source_id': str(code),
            'url': url[:500],
            'title': title[:500],
            'content': content,
            'published_at': published_at,
            'raw_tags': json.dumps(raw_tags, ensure_ascii=False) if raw_tags else None,
            'importance': importance,
        })
    return out
