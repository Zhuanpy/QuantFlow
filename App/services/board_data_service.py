# -*- coding: utf-8 -*-
"""
板块数据公共服务

把原先挤在 routes/strategy/BoardTrendScoring.py 里的"跨模块通用"逻辑下沉到这里，
供 board_data / board_trend / board_overview 等多个蓝图复用，消除重复：

- 数据状态：latest_trading_date / query_data_status_batch / classify_data_status
- 东财·akshare 成分股与市值抓取：em_industry_cons_via_http / ak_industry_cons / ak_individual_caps
- 板块日 K 存在性：boards_with_daily_data
"""
import logging
from datetime import datetime, date, timedelta

import pandas as pd
from sqlalchemy import text

from App.exts import db

logger = logging.getLogger(__name__)


# ----------------- 数据状态 -----------------
def latest_trading_date() -> date:
    """获取最近一个 A 股交易日（粗略实现：今天起向前找首个工作日）。

    精确实现见 download_data_route.get_latest_trading_date()，这里用粗略版本避免
    引入循环依赖；只用作"是否落后"的参考点。
    """
    d = date.today()
    while d.weekday() >= 5:  # 跳过周末
        d -= timedelta(days=1)
    return d


def query_data_status_batch(codes: list) -> dict:
    """批量查 DailyTaskStatus 表，返回 {code: {latest_daily, latest_1m, latest_15m}}。

    Args:
        codes: 板块/股票代码列表
    Returns:
        dict, key 是 stock_code，value 含三种数据各自最新成功日期（datetime.date 或 None）
    """
    if not codes:
        return {}
    eng = db.engines['quanttradingsystem']
    sql = '''
        SELECT stock_code,
               MAX(CASE WHEN is_daily_processed = 1 THEN date END) AS latest_daily,
               MAX(CASE WHEN is_1m_downloaded   = 1 THEN date END) AS latest_1m,
               MAX(CASE WHEN is_15m_generated   = 1 THEN date END) AS latest_15m
          FROM data_daily_task_status
         WHERE stock_code IN :codes
         GROUP BY stock_code
    '''
    with eng.connect() as conn:
        rows = conn.execute(text(sql), {'codes': tuple(codes)}).fetchall()
    return {
        r[0]: {'latest_daily': r[1], 'latest_1m': r[2], 'latest_15m': r[3]}
        for r in rows
    }


def classify_data_status(latest_daily, latest_1m, latest_15m, ref_date: date) -> dict:
    """把三种数据的最新日期 + 参考交易日转成可展示的 status 标签。

    Returns: {
        'overall': 'ok' | 'lag' | 'stale' | 'missing',
        'daily':  'ok' | 'lag' | 'missing',
        'min1':   'ok' | 'lag' | 'missing',
        'min15':  'ok' | 'lag' | 'missing',
        'latest_daily': 'YYYY-MM-DD' or None,
        'latest_1m':    'YYYY-MM-DD' or None,
        'latest_15m':   'YYYY-MM-DD' or None,
        'lag_days': int  (= max 三类数据的滞后天数；单类缺失时按 9999 算)
    }
    """
    def _one(dv):
        if dv is None:
            return 'missing', 9999
        # SQL MAX 出来在 pymysql 下是 datetime.date，确保类型
        if isinstance(dv, datetime):
            dv = dv.date()
        diff = (ref_date - dv).days
        if diff <= 0:
            return 'ok', 0
        if diff <= 3:
            return 'lag', diff
        return 'stale', diff

    s_daily, lag_d = _one(latest_daily)
    s_1m,    lag_1 = _one(latest_1m)
    s_15m,   lag_5 = _one(latest_15m)

    lag_days = max(lag_d, lag_1, lag_5)

    if s_daily == 'missing' and s_1m == 'missing' and s_15m == 'missing':
        overall = 'missing'
    elif s_daily == 'ok' and s_1m == 'ok' and s_15m == 'ok':
        overall = 'ok'
    elif lag_days >= 4:
        overall = 'stale'
    else:
        overall = 'lag'

    def _fmt(d):
        if d is None:
            return None
        if isinstance(d, datetime):
            d = d.date()
        return d.isoformat()

    return {
        'overall': overall,
        'daily':   s_daily if s_daily != 'stale' else 'lag',  # 三类细分只暴露 ok/lag/missing
        'min1':    s_1m    if s_1m    != 'stale' else 'lag',
        'min15':   s_15m   if s_15m   != 'stale' else 'lag',
        'latest_daily': _fmt(latest_daily),
        'latest_1m':    _fmt(latest_1m),
        'latest_15m':   _fmt(latest_15m),
        'lag_days': lag_days if lag_days < 9999 else None,
    }


# ----------------- 东财 / akshare 成分股 & 市值抓取 -----------------
_EM_HOSTS = (
    'push2.eastmoney.com',
    'push2his.eastmoney.com',
    '82.push2.eastmoney.com',
    '45.push2.eastmoney.com',
)
_EM_UAS = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
)


def em_industry_cons_via_http(board_code: str, retries: int = 4):
    """直连东方财富 clist API 拉板块成分股 + 总市值/流通市值。

    URL: http(s)://push2.eastmoney.com/api/qt/clist/get?fs=b:BKxxxx+f:!50&fields=f12,f14,f20,f21&...
    字段：f12 代码 / f14 名称 / f20 总市值(元) / f21 流通市值(元)
    重试策略：在多个 host × http/https × 多 UA 上轮换。
    Returns: list[dict(stock_code, stock_name, total_cap, circ_cap)] 或抛出 RuntimeError
    """
    import time
    import json as _json
    import requests

    def _to_num(v):
        if v is None or v == '' or v == '-':
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    attempts = []
    for i in range(retries):
        host = _EM_HOSTS[i % len(_EM_HOSTS)]
        ua = _EM_UAS[i % len(_EM_UAS)]
        scheme = 'http' if i % 2 == 1 else 'https'
        url = f'{scheme}://{host}/api/qt/clist/get'
        params = {
            'pn': 1, 'pz': 500, 'po': 1, 'np': 1,
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': 2, 'invt': 2, 'fid': 'f3',
            'fs': f'b:{board_code}+f:!50',
            'fields': 'f12,f14,f20,f21',
        }
        headers = {
            'User-Agent': ua,
            'Referer': 'https://quote.eastmoney.com/',
            'Accept': '*/*',
        }
        tag = f'{scheme}://{host}'
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                attempts.append(f'{tag} HTTP {resp.status_code}')
                time.sleep(1.0 * (i + 1))
                continue
            try:
                payload = _json.loads(resp.text)
            except _json.JSONDecodeError:
                attempts.append(f'{tag} non-JSON: {resp.text[:80]!r}')
                time.sleep(1.0 * (i + 1))
                continue
            diff = ((payload or {}).get('data') or {}).get('diff') or []
            return [{
                'stock_code': str(r.get('f12', '')).strip().zfill(6),
                'stock_name': str(r.get('f14', '')).strip(),
                'total_cap': _to_num(r.get('f20')),
                'circ_cap': _to_num(r.get('f21')),
            } for r in diff if r.get('f12')]
        except requests.exceptions.RequestException as e:
            attempts.append(f'{tag} {type(e).__name__}: {str(e)[:80]}')
            time.sleep(1.0 * (i + 1))
    raise RuntimeError('东财 HTTP 兜底全部失败：' + ' | '.join(attempts))


def ak_industry_cons(board_code: str, retries: int = 3, sleep_sec: float = 1.5):
    """抓取单个东财行业板块的成分股 + 市值。

    数据源链：
      1) akshare.stock_board_industry_cons_em（首选；akshare 不返回 f20/f21，市值需要后补）
      2) 直连东方财富 clist HTTP API（兜底；同时返回市值）

    Returns: list[dict(stock_code, stock_name, total_cap, circ_cap)] 或抛出异常
    """
    import time
    import akshare as ak
    ak_err = None
    members_from_ak = None
    for i in range(retries):
        try:
            df = ak.stock_board_industry_cons_em(symbol=board_code)
            if df is None or df.empty:
                members_from_ak = []
                break
            code_col = '代码' if '代码' in df.columns else df.columns[1]
            name_col = '名称' if '名称' in df.columns else df.columns[2]
            members_from_ak = [{
                'stock_code': str(r[code_col]).strip().zfill(6),
                'stock_name': str(r[name_col]).strip(),
                'total_cap': None,
                'circ_cap': None,
            } for _, r in df.iterrows() if pd.notna(r[code_col])]
            break
        except Exception as e:
            ak_err = e
            if i < retries - 1:
                time.sleep(sleep_sec * (i + 1))

    if members_from_ak is not None:
        # akshare 拿到了名单，再尝试用 HTTP 补一次市值；补不到就算了
        try:
            http_members = em_industry_cons_via_http(board_code)
            cap_map = {m['stock_code']: m for m in http_members}
            for m in members_from_ak:
                hit = cap_map.get(m['stock_code'])
                if hit:
                    m['total_cap'] = hit['total_cap']
                    m['circ_cap'] = hit['circ_cap']
        except Exception as http_err:
            logger.info(f'{board_code} 市值补全失败（{http_err}），仅返回名单')
        return members_from_ak

    # akshare 全部重试都失败 → 切到原生 HTTP 兜底
    logger.warning(f'akshare 抓取 {board_code} 失败（{ak_err}），切换到 HTTP 直连兜底')
    try:
        return em_industry_cons_via_http(board_code)
    except Exception as http_err:
        raise RuntimeError(
            f'akshare 失败: {ak_err}; HTTP 兜底也失败: {http_err}'
        )


def ak_individual_caps(stock_codes, retries: int = 2, sleep_sec: float = 0.6):
    """用 akshare.stock_individual_info_em 逐只拉总市值/流通市值。

    背景：东财 push2 子域挂掉时，clist API 不可用，但 emweb 子域的个股信息接口仍可用。
    Returns: dict[code -> (total_cap_or_None, circ_cap_or_None)]，失败的 code 不在返回里
    """
    import time
    import akshare as ak
    out = {}
    for sc in stock_codes:
        last_err = None
        for i in range(retries):
            try:
                df = ak.stock_individual_info_em(symbol=sc)
                if df is None or df.empty:
                    break
                row_map = dict(zip(df['item'].astype(str), df['value']))
                tc = row_map.get('总市值')
                cc = row_map.get('流通市值')

                def _f(v):
                    try:
                        return float(v) if v is not None and v != '' else None
                    except (TypeError, ValueError):
                        return None
                out[sc] = (_f(tc), _f(cc))
                break
            except Exception as e:
                last_err = e
                if i < retries - 1:
                    time.sleep(sleep_sec * (i + 1))
        if last_err is not None and sc not in out:
            logger.info(f'个股 {sc} 市值拉取失败：{last_err}')
        time.sleep(sleep_sec)  # 节流
    return out


# ----------------- 板块日 K 存在性 -----------------
def boards_with_daily_data(conn) -> set:
    """返回所有"有日 K 数据"的板块代码集合（大写）。

    判定条件：data_stock_daily 中有行 OR datadaily.{code 小写} 表存在。
    与 service 层 _load_board_daily 的取数逻辑保持一致。
    """
    have = set()
    rows = conn.execute(text(
        "SELECT DISTINCT stock_code FROM data_stock_daily "
        "WHERE stock_code LIKE 'BK%'"
    )).fetchall()
    for r in rows:
        if r[0]:
            have.add(r[0].strip().upper())

    rows = conn.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'datadaily' AND table_name LIKE 'bk%'"
    )).fetchall()
    for r in rows:
        if r[0]:
            have.add(r[0].strip().upper())
    return have
