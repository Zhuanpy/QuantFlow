# -*- coding: utf-8 -*-
"""
个股实时行情 + 基本面服务

数据源：东方财富 push2.eastmoney.com /api/qt/stock/get
单次请求返回最新价 / 涨跌 / 总市值 / 流通市值 / 总股本 / 流通股本 / 市盈率 / 市净率 /
换手率 / 量比 / 成交量 / 成交额 等。

字段含义参考（East Money push2 stock/get）：
    f43 最新价     f44 最高     f45 最低     f46 开盘     f47 成交量(手)
    f48 成交额(元) f50 量比     f57 代码     f58 名称     f60 昨收
    f84 总股本(股) f85 流通股本(股)
    f116 总市值(元) f117 流通市值(元)
    f162 市盈率(动) f163 市盈率(静) f164 市盈率(TTM)  f167 市净率(MRQ)
    f168 换手率(%) f169 涨跌额    f170 涨跌幅(%)
    f173 ROE
    f86 时间戳

注意：East Money 返回的数值通常已经做过 *10^N 缩放，需要按字段精度（f59/f152）除以
对应倍数。为了简化，下面对常见价格/比率字段统一按"经验缩放"处理；如某个股票偶发
异常，可在 SCALE 字典里覆盖。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# 字段缩放：East Money 返回的整型值 = 真实值 * 10^N，N 见下表
# 价格/比率字段普遍是 100；股本/市值/成交量/成交额是 1（不缩放）
_SCALE = {
    'f43': 100,   # 最新价
    'f44': 100,   # 最高
    'f45': 100,   # 最低
    'f46': 100,   # 开盘
    'f50': 100,   # 量比
    'f60': 100,   # 昨收
    'f162': 100,  # 市盈率(动)
    'f163': 100,  # 市盈率(静)
    'f164': 100,  # 市盈率(TTM)
    'f167': 100,  # 市净率
    'f168': 100,  # 换手率(%)
    'f169': 100,  # 涨跌额
    'f170': 100,  # 涨跌幅(%)
    'f173': 100,  # ROE
}

# East Money 字段名 → 友好键名
_RENAME = {
    'f43': 'price',
    'f44': 'high',
    'f45': 'low',
    'f46': 'open',
    'f47': 'volume',          # 成交量（手）
    'f48': 'amount',          # 成交额（元）
    'f50': 'volume_ratio',    # 量比
    'f57': 'code',
    'f58': 'name',
    'f60': 'prev_close',
    'f84': 'total_shares',    # 总股本（股）
    'f85': 'float_shares',    # 流通股本（股）
    'f86': 'ts',
    'f116': 'total_mv',       # 总市值（元）
    'f117': 'float_mv',       # 流通市值（元）
    'f162': 'pe_dynamic',
    'f163': 'pe_static',
    'f164': 'pe_ttm',
    'f167': 'pb',
    'f168': 'turnover',       # 换手率（%）
    'f169': 'change_amt',
    'f170': 'change_pct',     # 涨跌幅（%）
    'f173': 'roe',
}


def _convert(field_id: str, raw: Any) -> Any:
    """按字段精度做缩放；'-' 或 None 一律返回 None。"""
    if raw in (None, '-', '', '—'):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return raw
    scale = _SCALE.get(field_id)
    if scale and scale != 1:
        v = v / scale
    return v


_QUOTE_URL = 'https://push2.eastmoney.com/api/qt/stock/get'
_CLIST_URL = 'https://push2.eastmoney.com/api/qt/clist/get'
_QUOTE_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36')


def fetch_stock_quote(stock_code: str) -> Optional[Dict[str, Any]]:
    """拉一只个股的实时行情 + 基本面（北交所/沪/深通用）。

    用裸 requests 直接打 East Money push2 接口（HTTPS）。不走项目 HTTP 客户端，
    因为后者带 Cookie + 默认 HTTP，会触发 HTTP→HTTPS 重定向死循环。

    Returns:
        dict 含 price / change_pct / total_mv / float_mv / pe_dynamic 等键，
        值为已缩放的 Python 原生数值；某字段为空则为 None。
        请求失败返回 None。
    """
    import requests
    from App.codes.utils.stock_utils import StockCode

    code = (stock_code or '').strip()
    if not code:
        return None

    try:
        secid = StockCode.get_secid(code)
    except Exception as e:
        logger.warning(f'[stock_quote] secid 推断失败 {code}: {e}')
        return None

    params = {
        'secid': secid,
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fields': ','.join(_RENAME.keys()),
    }
    try:
        r = requests.get(_QUOTE_URL, params=params,
                         headers={'User-Agent': _QUOTE_UA},
                         timeout=8)
        if r.status_code != 200:
            logger.warning(f'[stock_quote] {code} HTTP {r.status_code}')
            return None
        payload = r.json()
    except Exception as e:
        logger.warning(f'[stock_quote] {code} 请求/解析失败: {e}')
        return None

    data = (payload or {}).get('data')
    if not isinstance(data, dict):
        logger.info(f'[stock_quote] {code} 返回 data 为空 (rc={payload.get("rc")})')
        return None

    out = {'secid': secid, 'stock_code': code}
    for fid, key in _RENAME.items():
        out[key] = _convert(fid, data.get(fid))

    return out


# ============== 本地读取（优先） ==============
def fetch_local_quote(stock_code: str) -> Optional[Dict[str, Any]]:
    """从本地 data_stock_basic_quote 读取（盘后由 refresh_basic_quotes_bulk 写入）。

    返回的 dict 与 fetch_stock_quote 同形（保持前端无差别）。
    """
    from App.models.data.StockBasicQuote import StockBasicQuote
    code = (stock_code or '').strip()
    if not code:
        return None
    row = StockBasicQuote.query.filter_by(stock_code=code).first()
    if not row:
        return None
    d = row.to_dict()
    # 与 fetch_stock_quote 输出对齐：补一个 ts（用 quote_date 推算 15:00:00）
    if d.get('quote_date'):
        try:
            from datetime import datetime as _dt
            ts = _dt.strptime(d['quote_date'], '%Y-%m-%d').replace(hour=15).timestamp()
            d['ts'] = int(ts)
        except Exception:
            d['ts'] = None
    d['source'] = 'local'
    return d


# ============== 批量刷新（盘后跑一次） ==============
# clist/get 字段映射（fltt=2 → 直接拿到 2 位小数 float，无需缩放）
_CLIST_FIELD_MAP = {
    'f12': 'stock_code',
    'f14': 'stock_name',
    'f2':  'price',
    'f3':  'change_pct',
    'f4':  'change_amt',
    'f5':  'volume',          # 手
    'f6':  'amount',          # 元
    'f8':  'turnover',        # %
    'f9':  'pe_dynamic',
    'f10': 'volume_ratio',
    'f15': 'high',
    'f16': 'low',
    'f17': 'open',
    'f18': 'prev_close',
    'f20': 'total_mv',        # 元
    'f21': 'float_mv',        # 元
    'f23': 'pb',
    'f38': 'total_shares',    # 股
    'f39': 'float_shares',    # 股
    'f114': 'pe_static',
    'f115': 'pe_ttm',
}

_CLIST_FIELDS_PARAM = ','.join(_CLIST_FIELD_MAP.keys())

# A 股全部市场过滤器：
#   m:0+t:6  深证主板    m:0+t:80 创业板
#   m:1+t:2  沪市主板    m:1+t:23 科创板
#   m:0+t:81+s:2048 北交所
_CLIST_FS_ALL_A = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048'


def _parse_clist_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把 clist/get 一行 dict 转成 StockBasicQuote 字段。"""
    code = row.get('f12')
    if not code or not isinstance(code, str):
        return None
    out: Dict[str, Any] = {}
    for fid, key in _CLIST_FIELD_MAP.items():
        v = row.get(fid)
        if v in (None, '-', '', '—'):
            out[key] = None
        else:
            try:
                if key in ('volume', 'total_shares', 'float_shares'):
                    out[key] = int(float(v))
                else:
                    out[key] = float(v) if not isinstance(v, str) else v
            except (TypeError, ValueError):
                out[key] = None
    # 名称保持原始字符串
    out['stock_name'] = row.get('f14') or out.get('stock_name')
    return out


def _sync_static_info(rows: list, source: str = 'eastmoney_clist') -> Dict[str, Any]:
    """检测并同步个股最新名称到 data_stock_info / strategy_stock_pool /
    StockTrendScore 的最新一条；同时把改名记录写入 data_stock_name_history。

    Args:
        rows: 已解析的 quote 行（每行至少要有 stock_code, stock_name）
        source: 写入历史表的数据源标识

    Returns:
        {changes: [(code, old, new), ...], info_created: int}
    """
    from datetime import datetime as _dt
    from App.exts import db
    from App.models.data.basic_info import StockInfo
    from App.models.data.StockNameHistory import StockNameHistory
    from App.models.strategy.StockPool import StockPool
    from App.models.evaluation.StockTrendScore import StockTrendScore

    codes = [r['stock_code'] for r in rows
             if r.get('stock_code') and r.get('stock_name')]
    if not codes:
        return {'changes': [], 'info_created': 0}

    # 一次拉所有现有 StockInfo
    existing = {s.code: s for s in
                StockInfo.query.filter(StockInfo.code.in_(codes)).all()}

    changes = []
    info_created = 0
    now = _dt.utcnow()

    for r in rows:
        code = r.get('stock_code')
        new_name = r.get('stock_name')
        if not code or not new_name:
            continue
        cur = existing.get(code)
        if cur is None:
            # 新股票（首次见到），补一条 data_stock_info
            db.session.add(StockInfo(code=code, name=new_name,
                                     created_at=now, updated_at=now))
            info_created += 1
            continue
        old_name = (cur.name or '').strip()
        if old_name != new_name.strip():
            cur.name = new_name
            cur.updated_at = now
            changes.append((code, old_name, new_name))

    # 检测到的改名 → 同步到 StockPool（最近 record）+ StockTrendScore（最新一条）
    if changes:
        change_codes = [c for c, _, _ in changes]

        # StockPool 一只股票通常一行
        pool_rows = StockPool.query.filter(StockPool.stock_code.in_(change_codes)).all()
        new_name_map = {c: n for c, _, n in changes}
        for p in pool_rows:
            n = new_name_map.get(p.stock_code)
            if n and p.stock_name != n:
                p.stock_name = n
                p.updated_at = now

        # StockTrendScore：每只股票更新最新一条；批量 SQL 比循环 ORM 快
        from sqlalchemy import text
        for code, _old, new in changes:
            db.session.execute(text("""
                UPDATE eval_stock_trend_score s
                JOIN (
                    SELECT id FROM eval_stock_trend_score
                    WHERE stock_code = :c
                    ORDER BY record_date DESC, id DESC
                    LIMIT 1
                ) t ON s.id = t.id
                SET s.stock_name = :n
                WHERE s.stock_name <> :n
            """), {'c': code, 'n': new})

        # 写历史表
        for code, old, new in changes:
            db.session.add(StockNameHistory(
                stock_code=code, old_name=old or None,
                new_name=new, source=source, changed_at=now,
            ))

    db.session.commit()

    if changes:
        for code, old, new in changes[:20]:
            logger.info(f'[stock_name] {code}: "{old}" → "{new}"')
        if len(changes) > 20:
            logger.info(f'[stock_name] ... 另外还有 {len(changes) - 20} 处改名')

    return {'changes': changes, 'info_created': info_created}


def refresh_basic_quotes_bulk(page_size: int = 100, page_cap: int = 100) -> Dict[str, Any]:
    """批量从 East Money clist/get 拉全 A 股的最新行情/基本面，upsert 到本地表。

    设计：
      - East Money clist/get 有隐藏的 pz 上限（实测约 100），所以默认 pz=100
        + pn 翻页，先读 total 决定循环次数
      - 用 INSERT ... ON DUPLICATE KEY UPDATE 做高效 upsert
      - 单页失败不影响整体；空页 / 重复 stop

    Args:
        page_size: 每页大小（建议 100）
        page_cap:  最大页数保护（默认 100，可覆盖 ~10000 行，足够 A 股 5850）

    Returns:
        dict: {success, fetched, upserted, pages, total, errors}
    """
    import requests
    import time as _time
    from datetime import date as _date
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    from App.exts import db
    from App.models.data.StockBasicQuote import StockBasicQuote

    fetched = 0
    upserted = 0
    pages = 0
    errors = []
    total = None
    today = _date.today()
    all_rows: list = []  # 收集所有行，最后做一次 name 同步

    pn = 1
    seen_codes = set()
    while pn <= page_cap:
        params = {
            'pn': pn,
            'pz': page_size,
            'po': 1,
            'np': 1,
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': 2,
            'invt': 2,
            'fid': 'f12',
            'fs': _CLIST_FS_ALL_A,
            'fields': _CLIST_FIELDS_PARAM,
        }
        try:
            r = requests.get(_CLIST_URL, params=params,
                             headers={'User-Agent': _QUOTE_UA},
                             timeout=15)
            payload = r.json() if r.status_code == 200 else None
        except Exception as e:
            errors.append({'page': pn, 'error': str(e)})
            logger.exception(f'[refresh_basics] 第 {pn} 页请求失败')
            # 单页失败不直接退出，跳过继续
            pn += 1
            _time.sleep(0.5)
            continue

        data_block = (payload or {}).get('data') or {}
        if total is None:
            total = data_block.get('total')
        diff = data_block.get('diff') or []
        # 单次空 diff 可能是限频，等长一点再试一次再放弃
        if not diff:
            logger.info(f'[refresh_basics] 第 {pn} 页空 diff，等 2s 重试一次')
            _time.sleep(2)
            try:
                r2 = requests.get(_CLIST_URL, params=params,
                                  headers={'User-Agent': _QUOTE_UA},
                                  timeout=15)
                payload2 = r2.json() if r2.status_code == 200 else None
                data_block = (payload2 or {}).get('data') or {}
                diff = data_block.get('diff') or []
            except Exception:
                diff = []
            if not diff:
                logger.warning(f'[refresh_basics] 第 {pn} 页两次都空，提前结束')
                break

        rows: list = []
        for raw in diff:
            parsed = _parse_clist_row(raw)
            if not parsed:
                continue
            code = parsed['stock_code']
            if code in seen_codes:
                continue
            seen_codes.add(code)
            parsed['quote_date'] = today
            rows.append(parsed)

        fetched += len(rows)
        pages += 1
        all_rows.extend(rows)

        if rows:
            try:
                stmt = mysql_insert(StockBasicQuote).values(rows)
                update_dict = {c.name: stmt.inserted[c.name]
                               for c in StockBasicQuote.__table__.columns
                               if c.name != 'stock_code'}
                stmt = stmt.on_duplicate_key_update(**update_dict)
                db.session.execute(stmt)
                db.session.commit()
                upserted += len(rows)
            except Exception as e:
                db.session.rollback()
                errors.append({'page': pn, 'error': f'upsert: {e}'})
                logger.exception(f'[refresh_basics] 第 {pn} 页 upsert 失败')

        logger.debug(f'[refresh_basics] page {pn} ok: +{len(rows)} rows '
                     f'(fetched={fetched}/total={total})')

        # 终止条件：覆盖了 total（或 server 不返回 total 时按本页是否满判断）
        if total is not None and fetched >= total:
            break
        if total is None and len(diff) < page_size:
            break
        pn += 1
        # 礼貌延时，避免 East Money 限频导致后续页返回空
        _time.sleep(0.3)

    if pn > page_cap:
        errors.append({'page': pn, 'error': f'reached page cap={page_cap}'})

    # 改名检测 + 同步到 data_stock_info / strategy_stock_pool / StockTrendScore
    name_sync = {'changes': [], 'info_created': 0}
    if all_rows:
        try:
            name_sync = _sync_static_info(all_rows)
        except Exception as e:
            db.session.rollback()
            errors.append({'page': 'name_sync', 'error': str(e)})
            logger.exception('[refresh_basics] name_sync 失败')

    logger.info(f'[refresh_basics] 完成：{pages} 页 / {fetched} 行 / upsert {upserted} / '
                f'total={total} / 改名 {len(name_sync["changes"])} / '
                f'新建 StockInfo {name_sync["info_created"]}')
    return {
        'success': fetched > 0,
        'fetched': fetched,
        'upserted': upserted,
        'pages': pages,
        'total': total,
        'name_changes': len(name_sync['changes']),
        'name_changes_sample': [
            {'stock_code': c, 'old_name': o, 'new_name': n}
            for c, o, n in name_sync['changes'][:20]
        ],
        'info_created': name_sync['info_created'],
        'errors': errors,
    }
