"""
板块趋势打分路由

提供板块趋势阶段（上涨前期/上涨后期/下跌前期/下跌后期）评分的页面与 API。
打分公式占位，本路由先完成 CRUD 与列表入口。
"""
from flask import Blueprint, render_template, request, jsonify
from datetime import datetime, date, timedelta
import logging
import pandas as pd
from sqlalchemy import text

from App.exts import db
from App.models.evaluation.BoardTrendScore import (
    BoardTrendScore, TREND_STAGES, TREND_STRENGTHS, SIGNALS,
)

logger = logging.getLogger(__name__)

board_trend_bp = Blueprint('board_trend', __name__, url_prefix='/board_trend')


# ----------------- 数据状态查询（共享给 list / overview） -----------------
def _latest_trading_date_cached() -> date:
    """获取最近一个 A 股交易日（粗略实现：今天起向前找首个工作日）。

    精确实现见 download_data_route.get_latest_trading_date()，这里用粗略版本避免
    引入循环依赖；只用作"是否落后"的参考点。
    """
    d = date.today()
    while d.weekday() >= 5:  # 跳过周末
        d -= timedelta(days=1)
    return d


def _query_data_status_batch(codes: list) -> dict:
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


def _classify_data_status(latest_daily, latest_1m, latest_15m, ref_date: date) -> dict:
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
        if d is None: return None
        if isinstance(d, datetime): d = d.date()
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


# ----------------- 页面 -----------------
@board_trend_bp.route('/')
def page():
    return render_template(
        'strategy/board_trend_scoring.html',
        trend_stages=TREND_STAGES,
        trend_strengths=TREND_STRENGTHS,
        signals=SIGNALS,
    )


@board_trend_bp.route('/board/<code>')
def board_detail_page(code):
    """单板块详情：日 K 图 + 历次打分时间线"""
    return render_template(
        'strategy/board_detail.html',
        board_code=code,
    )


# ----------------- 单板块图表 / 历史 -----------------
@board_trend_bp.route('/api/board/<code>/chart')
def api_board_chart(code):
    """返回单板块的日 K + MA20/MA60 + MACD 数据，供前端绘图。

    query:
      - days=120                          按窗口取末尾 N 日（默认）
      - start_date=YYYY-MM-DD&end_date=YYYY-MM-DD  指定区间（优先于 days）
    数据源同服务层（data_stock_daily 优先）。
    """
    try:
        from App.services.board_trend_score_service import _load_board_daily, _ema

        start_date_s = (request.args.get('start_date') or '').strip()
        end_date_s = (request.args.get('end_date') or '').strip()
        use_range = bool(start_date_s and end_date_s)

        if use_range:
            start_d = datetime.strptime(start_date_s, '%Y-%m-%d').date()
            end_d = datetime.strptime(end_date_s, '%Y-%m-%d').date()
            if start_d > end_d:
                return jsonify({'success': False, 'message': '起始日期不能晚于结束日期'}), 400
            # 多读 60 个交易日缓冲，避免 MA60/MACD 头部扭曲
            lookback = max((end_d - start_d).days + 60, 250)
            df = _load_board_daily(code, end_d, lookback=lookback)
        else:
            days = max(60, min(int(request.args.get('days', 120)), 1000))
            df = _load_board_daily(code, date.today(), lookback=max(days + 30, 250))

        if df.empty:
            return jsonify({'success': False, 'message': f'未找到板块 {code} 的日 K 数据'}), 404

        # 计算 MA / MACD（用全部数据算，避免边缘扭曲），最后再筛选
        close = df['close']
        df['ma20'] = close.rolling(20, min_periods=1).mean()
        df['ma60'] = close.rolling(60, min_periods=1).mean()
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        df['dif'] = ema12 - ema26
        df['dea'] = _ema(df['dif'], 9)
        df['bar'] = (df['dif'] - df['dea']) * 2

        if use_range:
            d_norm = pd.to_datetime(df['date']).dt.normalize()
            mask = (d_norm >= pd.Timestamp(start_d)) & (d_norm <= pd.Timestamp(end_d))
            df = df.loc[mask].reset_index(drop=True)
            if df.empty:
                return jsonify({'success': False,
                                'message': f'区间 {start_date_s} ~ {end_date_s} 内无数据'}), 404
        else:
            df = df.tail(days).reset_index(drop=True)

        def _r(v, n=4):
            try:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return None
                return round(float(v), n)
            except Exception:
                return None

        rows = []
        for _, r in df.iterrows():
            rows.append({
                'date': r['date'].isoformat() if hasattr(r['date'], 'isoformat') else str(r['date']),
                'open': _r(r['open'], 4),
                'close': _r(r['close'], 4),
                'high': _r(r['high'], 4),
                'low': _r(r['low'], 4),
                'volume': int(r['volume']) if pd.notna(r['volume']) else 0,
                'ma20': _r(r['ma20'], 4),
                'ma60': _r(r['ma60'], 4),
                'dif': _r(r['dif'], 4),
                'dea': _r(r['dea'], 4),
                'bar': _r(r['bar'], 4),
            })
        return jsonify({
            'success': True,
            'data': {
                'board_code': code,
                'count': len(rows),
                'rows': rows,
            }
        })
    except Exception as e:
        logger.exception('板块图表数据获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/board/<code>/chart_15m')
def api_board_chart_15m(code):
    """返回单板块的 15 分钟 K 线 + MA20/MA60 + MACD + 量能。

    数据源：data/15m/{code}.parquet（与 viewer_15m 相同）
    query:
      - bars=192                          按根数取末尾 N 根（默认 ≈ 10 日）
      - start_date=YYYY-MM-DD&end_date=YYYY-MM-DD  指定区间（优先于 bars）
    """
    try:
        from App.routes.data.viewer_15m_route import _get_15m_dir, _find_15m_file, _read_15m_file

        start_date_s = (request.args.get('start_date') or '').strip()
        end_date_s = (request.args.get('end_date') or '').strip()
        use_range = bool(start_date_s and end_date_s)

        fpath = _find_15m_file(_get_15m_dir(), code)
        if not fpath:
            return jsonify({'success': False, 'message': f'未找到板块 {code} 的 15m 数据文件'}), 404

        df = _read_15m_file(fpath)
        if df.empty:
            return jsonify({'success': False, 'message': '无法读取 15m 数据（可能需要 pyarrow）'}), 500

        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        # MA20/MA60 现算（parquet 里没有）；DIF/DEA/MACD 用文件里的（首字母大写）
        close = df['close']
        df['ma20'] = close.rolling(20, min_periods=1).mean()
        df['ma60'] = close.rolling(60, min_periods=1).mean()

        if use_range:
            try:
                start_ts = pd.to_datetime(start_date_s)
                # 包含整个 end_date 当天
                end_ts = pd.to_datetime(end_date_s) + pd.Timedelta(hours=23, minutes=59, seconds=59)
            except Exception:
                return jsonify({'success': False, 'message': '日期格式错误'}), 400
            if start_ts > end_ts:
                return jsonify({'success': False, 'message': '起始日期不能晚于结束日期'}), 400
            df = df[(df['date'] >= start_ts) & (df['date'] <= end_ts)].reset_index(drop=True)
            if df.empty:
                return jsonify({'success': False,
                                'message': f'区间 {start_date_s} ~ {end_date_s} 内无 15m 数据'}), 404
        else:
            bars = max(48, min(int(request.args.get('bars', 192)), 5000))
            df = df.tail(bars).reset_index(drop=True)

        def _r(v, n=4):
            try:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return None
                return round(float(v), n)
            except Exception:
                return None

        rows = []
        for _, r in df.iterrows():
            rows.append({
                'date': r['date'].strftime('%Y-%m-%d %H:%M'),
                'open': _r(r['open'], 4),
                'close': _r(r['close'], 4),
                'high': _r(r['high'], 4),
                'low': _r(r['low'], 4),
                'volume': int(r['volume']) if pd.notna(r.get('volume')) else 0,
                'ma20': _r(r['ma20'], 4),
                'ma60': _r(r['ma60'], 4),
                'dif': _r(r.get('Dif'), 4),
                'dea': _r(r.get('Dea'), 4),
                'bar': _r(r.get('MACD'), 4),
            })
        return jsonify({
            'success': True,
            'data': {
                'board_code': code,
                'count': len(rows),
                'rows': rows,
            }
        })
    except Exception as e:
        logger.exception('板块 15m 图表数据获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


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


def _em_industry_cons_via_http(board_code: str, retries: int = 4):
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


def _ak_industry_cons(board_code: str, retries: int = 3, sleep_sec: float = 1.5):
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
            http_members = _em_industry_cons_via_http(board_code)
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
        return _em_industry_cons_via_http(board_code)
    except Exception as http_err:
        raise RuntimeError(
            f'akshare 失败: {ak_err}; HTTP 兜底也失败: {http_err}'
        )


def _ak_individual_caps(stock_codes, retries: int = 2, sleep_sec: float = 0.6):
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


@board_trend_bp.route('/api/refresh_caps', methods=['POST'])
def api_refresh_caps():
    """只刷新市值（基于 industry_eastmoney 已有名单），用 stock_individual_info_em 逐只查。

    适用场景：东财 push2 挂掉、clist API 不通、但 emweb 个股信息接口仍可用时，
    至少能让快照里的市值列保持最新。

    body: { board_code: 'BK0739' }   # 必填，避免一次刷上千只股票
    """
    try:
        from sqlalchemy import text
        data = request.get_json(silent=True) or {}
        code = (data.get('board_code') or '').strip()
        if not code:
            return jsonify({'success': False, 'message': 'board_code 必填'}), 400

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT stock_code FROM industry_eastmoney "
                "WHERE board_code=:c AND date=("
                "  SELECT MAX(date) FROM industry_eastmoney WHERE board_code=:c)"
            ), {'c': code}).fetchall()

        stock_codes = [r[0] for r in rows if r[0]]
        if not stock_codes:
            return jsonify({'success': False,
                            'message': f'industry_eastmoney 中没有 {code} 的成分股名单'}), 404

        cap_map = _ak_individual_caps(stock_codes)
        if not cap_map:
            return jsonify({'success': False,
                            'message': '所有个股的市值都拉取失败，请稍后再试'}), 502

        updated = 0
        with eng.begin() as conn:
            for sc, (tc, cc) in cap_map.items():
                if tc is None and cc is None:
                    continue
                conn.execute(text(
                    "UPDATE industry_eastmoney SET total_cap=:tc, circ_cap=:cc "
                    "WHERE board_code=:bc AND stock_code=:sc"
                ), {'tc': tc, 'cc': cc, 'bc': code, 'sc': sc})
                updated += 1

        return jsonify({'success': True, 'data': {
            'board_code': code,
            'requested': len(stock_codes),
            'updated': updated,
            'failed': len(stock_codes) - len(cap_map),
        }})
    except Exception as e:
        db.session.rollback()
        logger.exception('刷新成分股市值失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/refresh_members', methods=['POST'])
def api_refresh_members():
    """刷新板块成分股名单（来源：akshare 东财行业板块）。

    body: {
        board_code: 'BK0738'   # 可选，传则只刷该板块；不传则刷新所有行业板块
    }
    存储：industry_eastmoney —— 先删除该 board_code 已有记录，再写入今日快照
    """
    try:
        from sqlalchemy import text
        data = request.get_json(silent=True) or {}
        single = (data.get('board_code') or '').strip()

        eng = db.engines['quanttradingsystem']
        # 收集要刷新的 (code, name) 列表
        with eng.connect() as conn:
            if single:
                rows = conn.execute(text(
                    "SELECT code, name FROM data_stock_classification "
                    "WHERE Classification='行业板块' AND code=:c"
                ), {'c': single}).fetchall()
                # 即使分类表里没有，也允许直接刷该板块
                if not rows:
                    rows = [(single, single)]
            else:
                rows = conn.execute(text(
                    "SELECT code, name FROM data_stock_classification "
                    "WHERE Classification='行业板块' AND code IS NOT NULL "
                    "ORDER BY code"
                )).fetchall()

        targets = [(r[0].strip(), (r[1] or r[0]).strip()) for r in rows if r[0]]
        if not targets:
            return jsonify({'success': False, 'message': '没有找到要刷新的板块'}), 400

        today = date.today()
        ok = 0
        empty = 0
        failed = []
        total_members = 0

        with eng.begin() as conn:
            for code, name in targets:
                try:
                    members = _ak_industry_cons(code)
                except Exception as e:
                    failed.append({'board_code': code, 'error': str(e)[:200]})
                    continue
                if not members:
                    empty += 1
                    continue
                # 替换：先删旧、再批量插入
                conn.execute(text(
                    "DELETE FROM industry_eastmoney WHERE board_code=:c"
                ), {'c': code})
                conn.execute(text(
                    "INSERT INTO industry_eastmoney "
                    "(board_name, board_code, stock_code, stock_name, date, total_cap, circ_cap) "
                    "VALUES (:bn, :bc, :sc, :sn, :d, :tc, :cc)"
                ), [{'bn': name, 'bc': code,
                     'sc': m['stock_code'], 'sn': m['stock_name'],
                     'd': today,
                     'tc': m.get('total_cap'), 'cc': m.get('circ_cap')}
                    for m in members])
                ok += 1
                total_members += len(members)

        return jsonify({'success': True, 'data': {
            'mode': 'single' if single else 'all',
            'requested': len(targets),
            'updated': ok,
            'empty': empty,
            'failed_count': len(failed),
            'total_members_written': total_members,
            'snapshot_date': today.isoformat(),
            'failed_sample': failed[:10],
        }})
    except Exception as e:
        logger.exception('刷新板块成分股失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/board/<code>/stocks')
def api_board_stocks(code):
    """返回板块成分股清单（来源 industry_eastmoney 最新日期），并 LEFT JOIN 个股最新趋势打分。

    数据源：industry_eastmoney（东方财富板块成分股）
        每个 board_code 取最新 date 的快照
    评分：eval_stock_trend_score 中每只个股最新一条
    """
    try:
        from sqlalchemy import text
        from App.models.evaluation.StockTrendScore import StockTrendScore

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT ie.stock_code, ie.stock_name, ie.board_name, ie.date AS member_date,
                       ie.total_cap, ie.circ_cap
                FROM industry_eastmoney ie
                INNER JOIN (
                    SELECT board_code, MAX(date) AS d
                    FROM industry_eastmoney
                    WHERE board_code = :c
                    GROUP BY board_code
                ) m ON m.board_code = ie.board_code AND m.d = ie.date
                WHERE ie.board_code = :c
                ORDER BY ie.stock_code
                """
            ), {'c': code}).fetchall()

        if not rows:
            return jsonify({'success': True, 'data': {
                'board_code': code, 'board_name': None, 'member_date': None,
                'count': 0, 'rows': [], 'cap_source': None,
            }})

        stock_codes = [r[0] for r in rows]
        # 每只个股的最新评分日期
        sub = (db.session.query(
                StockTrendScore.stock_code,
                db.func.max(StockTrendScore.record_date).label('latest'))
            .filter(StockTrendScore.stock_code.in_(stock_codes))
            .group_by(StockTrendScore.stock_code).subquery())
        scores = (db.session.query(StockTrendScore)
            .join(sub, db.and_(
                StockTrendScore.stock_code == sub.c.stock_code,
                StockTrendScore.record_date == sub.c.latest))
            .all())
        score_map = {s.stock_code: s for s in scores}

        # 尝试实时刷新市值（best-effort，失败就用快照）。
        # 仅 1 次尝试避免页面加载阻塞过长——专门刷市值请用 /api/refresh_caps 按钮。
        cap_source = 'snapshot'
        live_cap = {}
        try:
            live_members = _em_industry_cons_via_http(code, retries=1)
            live_cap = {m['stock_code']: m for m in live_members}
            if live_cap:
                cap_source = 'live'
        except Exception as e:
            logger.info(f'板块 {code} 实时市值拉取失败，使用快照值：{e}')

        out = []
        for r in rows:
            sc = score_map.get(r[0])
            live = live_cap.get(r[0])
            total_cap = live['total_cap'] if live and live.get('total_cap') is not None else r[4]
            circ_cap = live['circ_cap'] if live and live.get('circ_cap') is not None else r[5]
            out.append({
                'stock_code': r[0],
                'stock_name': r[1],
                'trend_stage': sc.trend_stage if sc else None,
                'trend_strength': sc.trend_strength if sc else None,
                'signal': sc.signal if sc else None,
                'total_score': sc.total_score if sc else None,
                'score_date': sc.record_date.isoformat() if sc and sc.record_date else None,
                'total_cap': total_cap,
                'circ_cap': circ_cap,
            })

        return jsonify({'success': True, 'data': {
            'board_code': code,
            'board_name': rows[0][2],
            'member_date': rows[0][3].isoformat() if rows[0][3] else None,
            'count': len(out),
            'rows': out,
            'cap_source': cap_source,
        }})
    except Exception as e:
        logger.exception('板块成分股查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/board/<code>/history')
def api_board_history(code):
    """返回该板块在打分表里的历史评分时间线"""
    try:
        rows = (BoardTrendScore.query
                .filter_by(board_code=code)
                .order_by(BoardTrendScore.record_date.desc())
                .limit(120).all())
        # 板块名称从最新一条取
        board_name = rows[0].board_name if rows else code
        return jsonify({
            'success': True,
            'data': {
                'board_code': code,
                'board_name': board_name,
                'count': len(rows),
                'rows': [r.to_dict() for r in rows],
            }
        })
    except Exception as e:
        logger.exception('板块历史评分获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500




# ----------------- 列表 / 查询 -----------------
@board_trend_bp.route('/api/list')
def api_list():
    """列表查询。

    query params:
      record_date: YYYY-MM-DD（可选；默认当前最新一日）
      board_code: 模糊匹配
      board_name: 模糊匹配
      trend_stage: 阶段精确匹配
      page, page_size
      sort_by: total_score_desc / total_score_asc / updated / board_code
    """
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 30, type=int)
        record_date = request.args.get('record_date', '').strip()
        board_code = request.args.get('board_code', '').strip()
        board_name = request.args.get('board_name', '').strip()
        trend_stage = request.args.get('trend_stage', '').strip()
        signal = request.args.get('signal', '').strip()
        sort_by = request.args.get('sort_by', 'total_score_desc')

        q = BoardTrendScore.query

        if record_date:
            q = q.filter(BoardTrendScore.record_date == record_date)
        else:
            latest = db.session.query(db.func.max(BoardTrendScore.record_date)).scalar()
            if latest:
                q = q.filter(BoardTrendScore.record_date == latest)
                record_date = latest.isoformat()

        if board_code:
            q = q.filter(BoardTrendScore.board_code.like(f'%{board_code}%'))
        if board_name:
            q = q.filter(BoardTrendScore.board_name.like(f'%{board_name}%'))
        if trend_stage and trend_stage in TREND_STAGES:
            q = q.filter(BoardTrendScore.trend_stage == trend_stage)
        if signal and signal in SIGNALS:
            q = q.filter(BoardTrendScore.signal == signal)

        # MySQL 不支持 NULLS LAST，用 (col IS NULL) 把 NULL 排到最后
        null_last = (BoardTrendScore.total_score.is_(None)).asc()
        if sort_by == 'total_score_asc':
            q = q.order_by(null_last, BoardTrendScore.total_score.asc())
        elif sort_by == 'updated':
            q = q.order_by(BoardTrendScore.updated_at.desc())
        elif sort_by == 'board_code':
            q = q.order_by(BoardTrendScore.board_code.asc())
        else:
            q = q.order_by(null_last, BoardTrendScore.total_score.desc())

        pagination = q.paginate(page=page, per_page=page_size, error_out=False)
        items = [r.to_dict() for r in pagination.items]

        # 批量挂"数据状态"——一次查 DailyTaskStatus，按 board_code 拼回去
        try:
            codes = [it.get('board_code') for it in items if it.get('board_code')]
            status_map = _query_data_status_batch(codes)
            ref_date = _latest_trading_date_cached()
            for it in items:
                code = it.get('board_code')
                hit = status_map.get(code, {})
                it['data_status'] = _classify_data_status(
                    hit.get('latest_daily'),
                    hit.get('latest_1m'),
                    hit.get('latest_15m'),
                    ref_date,
                )
        except Exception as ds_err:
            logger.warning(f'附加 data_status 失败: {ds_err}')
            for it in items:
                it.setdefault('data_status', None)

        return jsonify({
            'success': True,
            'data': {
                'items': items,
                'record_date': record_date,
                'pagination': {
                    'page': pagination.page,
                    'pages': pagination.pages,
                    'per_page': pagination.per_page,
                    'total': pagination.total,
                    'has_prev': pagination.has_prev,
                    'has_next': pagination.has_next,
                }
            }
        })
    except Exception as e:
        logger.exception('板块趋势列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/stats')
def api_stats():
    """按阶段统计当日记录数量"""
    try:
        record_date = request.args.get('record_date', '').strip()
        if not record_date:
            latest = db.session.query(db.func.max(BoardTrendScore.record_date)).scalar()
            record_date = latest.isoformat() if latest else None

        stats = {s: 0 for s in TREND_STAGES}
        if record_date:
            rows = (db.session.query(BoardTrendScore.trend_stage, db.func.count())
                    .filter(BoardTrendScore.record_date == record_date)
                    .group_by(BoardTrendScore.trend_stage).all())
            for stage, cnt in rows:
                stats[stage or 'unknown'] = int(cnt)

        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date,
                'stage_counts': stats,
                'total': sum(stats.values()),
            }
        })
    except Exception as e:
        logger.exception('板块趋势统计失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/dates')
def api_dates():
    """已有打分记录的日期列表（最新在前）"""
    try:
        rows = (db.session.query(BoardTrendScore.record_date)
                .distinct()
                .order_by(BoardTrendScore.record_date.desc())
                .limit(60).all())
        return jsonify({
            'success': True,
            'data': [r[0].isoformat() for r in rows if r[0]],
        })
    except Exception as e:
        logger.exception('板块趋势日期列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 写入 -----------------
def _parse_payload(data):
    """从请求体取出可写字段（白名单），并做基本类型转换"""
    allowed = {
        'board_code', 'board_name', 'record_date',
        'trend_stage', 'trend_stage_confidence', 'trend_strength', 'signal',
        'price_structure_score', 'ma_score', 'macd_score',
        'volume_score', 'momentum_score', 'volatility_score', 'total_score',
        'close', 'change_pct', 'ma20', 'ma60',
        'macd_dif', 'macd_dea', 'macd_bar', 'atr',
        'formula_version', 'notes', 'is_manual',
    }
    out = {}
    for k, v in (data or {}).items():
        if k not in allowed:
            continue
        if v == '' or v is None:
            out[k] = None
            continue
        out[k] = v

    if 'record_date' in out and isinstance(out['record_date'], str):
        out['record_date'] = datetime.strptime(out['record_date'], '%Y-%m-%d').date()

    if out.get('trend_stage') and out['trend_stage'] not in TREND_STAGES:
        raise ValueError(f"非法 trend_stage: {out['trend_stage']}")
    if out.get('trend_strength') and out['trend_strength'] not in TREND_STRENGTHS:
        raise ValueError(f"非法 trend_strength: {out['trend_strength']}")
    if out.get('signal') and out['signal'] not in SIGNALS:
        raise ValueError(f"非法 signal: {out['signal']}")

    return out


@board_trend_bp.route('/api/upsert', methods=['POST'])
def api_upsert():
    """新增或更新一条板块趋势打分记录（按 board_code+record_date 唯一）"""
    try:
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)

        board_code = fields.pop('board_code', None)
        board_name = fields.pop('board_name', None)
        record_date = fields.pop('record_date', None) or date.today()

        if not board_code:
            return jsonify({'success': False, 'message': 'board_code 必填'}), 400

        if board_name is not None:
            fields['board_name'] = board_name
        if data.get('is_manual') is None:
            fields['is_manual'] = True

        row = BoardTrendScore.upsert(board_code=board_code,
                                     record_date=record_date,
                                     **fields)
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('upsert 板块趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/update/<int:row_id>', methods=['PUT'])
def api_update(row_id):
    """更新指定记录（不可改 board_code/record_date 唯一键）"""
    try:
        row = BoardTrendScore.query.get_or_404(row_id)
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)
        fields.pop('board_code', None)
        fields.pop('record_date', None)

        for k, v in fields.items():
            if hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('更新板块趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/delete/<int:row_id>', methods=['DELETE'])
def api_delete(row_id):
    try:
        ok = BoardTrendScore.delete_by_id(row_id)
        if not ok:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.exception('删除板块趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 板块清单同步 -----------------
def _boards_with_daily_data(conn) -> set:
    """返回所有"有日 K 数据"的板块代码集合（大写）。

    判定条件：data_stock_daily 中有行 OR datadaily.{code 小写} 表存在。
    与 service 层 _load_board_daily 的取数逻辑保持一致。
    """
    from sqlalchemy import text
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


@board_trend_bp.route('/api/sync_boards', methods=['POST'])
def api_sync_boards():
    """从已有板块数据源把板块清单批量导入到打分表（仅导入有日 K 数据的板块）。

    body: {
        record_date: 'YYYY-MM-DD'（可选，缺省今天）,
        source: 'industry' | 'concept' | 'all'（默认 'all'）,
        overwrite: false       True 时覆盖已有记录的 board_name；否则只补缺
        include_empty: false   True 时不过滤无数据板块（默认 False，因为没数据无法打分）
    }
    数据源：
      industry → data_stock_classification 中 classification='行业板块'
      concept  → concept_board
    "有数据" 定义：data_stock_daily 中存在该 stock_code 的行，或 datadaily.{code} 表存在。
    """
    try:
        from sqlalchemy import text
        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        if record_date:
            record_date = datetime.strptime(record_date, '%Y-%m-%d').date()
        else:
            record_date = date.today()
        source = (data.get('source') or 'all').lower()
        overwrite = bool(data.get('overwrite'))
        include_empty = bool(data.get('include_empty'))

        eng = db.engines['quanttradingsystem']
        boards = {}  # code -> name（去重，industry 优先）

        with eng.connect() as conn:
            if source in ('concept', 'all'):
                rows = conn.execute(text(
                    "SELECT stock_code AS code, stock_name AS name FROM concept_board "
                    "WHERE stock_code IS NOT NULL"
                )).fetchall()
                for r in rows:
                    if r[0]:
                        boards.setdefault(r[0].strip(), (r[1] or '').strip())

            if source in ('industry', 'all'):
                rows = conn.execute(text(
                    "SELECT code, name FROM data_stock_classification "
                    "WHERE classification = '行业板块' AND code IS NOT NULL"
                )).fetchall()
                for r in rows:
                    if r[0]:
                        # 行业板块覆盖概念板块同代码（行业更标准）
                        boards[r[0].strip()] = (r[1] or '').strip()

            total_in_source = len(boards)

            # 默认过滤掉没有日 K 数据的板块
            filtered_out = 0
            if not include_empty:
                with_data = _boards_with_daily_data(conn)
                kept = {c: n for c, n in boards.items() if c.upper() in with_data}
                filtered_out = len(boards) - len(kept)
                boards = kept

        if not boards:
            return jsonify({
                'success': False,
                'message': (f'源里有 {total_in_source} 个板块，但没有任何一个在日 K 数据库里。'
                            f'先下载日 K，或传 include_empty=true 强制导入。'),
            }), 404

        created = 0
        updated_name = 0
        skipped = 0
        for code, name in boards.items():
            existing = BoardTrendScore.query.filter_by(
                board_code=code, record_date=record_date).first()
            if existing is None:
                db.session.add(BoardTrendScore(
                    board_code=code,
                    board_name=name or code,
                    record_date=record_date,
                    trend_stage='unknown',
                    formula_version='v0',
                    is_manual=False,
                ))
                created += 1
            elif overwrite and name and existing.board_name != name:
                existing.board_name = name
                existing.updated_at = datetime.utcnow()
                updated_name += 1
            else:
                skipped += 1

        db.session.commit()
        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date.isoformat(),
                'source': source,
                'total_in_source': total_in_source,
                'filtered_out_no_data': filtered_out,
                'total_boards': len(boards),
                'created': created,
                'updated_name': updated_name,
                'skipped': skipped,
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('同步板块清单失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_trend_bp.route('/api/cleanup_empty', methods=['POST'])
def api_cleanup_empty():
    """清理无数据/未打分的自动记录。

    body: {
        record_date: 'YYYY-MM-DD'（可选；缺省=全部日期）,
        scope: 'no_data' | 'unscored' | 'both'  (默认 'both')
            no_data  - 板块在日 K 数据库里查不到（确实没数据）
            unscored - total_score IS NULL（未打分占位）
            both     - 两个并集
        keep_manual: true  保留 is_manual=True 的手工记录（默认 True）
    }
    返回: {deleted: N, sample: [...]}
    """
    try:
        from sqlalchemy import text
        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        if record_date:
            record_date = datetime.strptime(record_date, '%Y-%m-%d').date()
        scope = (data.get('scope') or 'both').lower()
        keep_manual = data.get('keep_manual', True)

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            with_data = _boards_with_daily_data(conn)

        q = BoardTrendScore.query
        if record_date:
            q = q.filter(BoardTrendScore.record_date == record_date)
        if keep_manual:
            q = q.filter(BoardTrendScore.is_manual.isnot(True))

        candidates = q.all()
        to_delete = []
        for r in candidates:
            no_data = (r.board_code or '').upper() not in with_data
            unscored = r.total_score is None
            if scope == 'no_data' and no_data:
                to_delete.append(r)
            elif scope == 'unscored' and unscored:
                to_delete.append(r)
            elif scope == 'both' and (no_data or unscored):
                to_delete.append(r)

        sample = [{'board_code': r.board_code,
                   'board_name': r.board_name,
                   'record_date': r.record_date.isoformat() if r.record_date else None}
                  for r in to_delete[:20]]
        for r in to_delete:
            db.session.delete(r)
        db.session.commit()
        return jsonify({
            'success': True,
            'data': {
                'deleted': len(to_delete),
                'scope': scope,
                'record_date': record_date.isoformat() if record_date else 'ALL',
                'sample': sample,
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('清理无数据板块失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 计算入口 -----------------
@board_trend_bp.route('/api/compute', methods=['POST'])
def api_compute():
    """自动打分（v1）。

    body: {
        record_date: 'YYYY-MM-DD'（缺省取今天）,
        board_codes: ['BK0420', ...]（可选；缺省时按 scope 决定）,
        scope: 'all' | 'unknown' | 'manual_skip'  仅在 board_codes 为空时生效
            all          → 当日表里所有板块
            unknown      → 当日 stage=unknown 或 total_score IS NULL
            manual_skip  → all 但跳过 is_manual=True 的（避免覆盖手工录入）
    }
    """
    try:
        from App.services.board_trend_score_service import compute_and_persist

        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        record_date = (datetime.strptime(record_date, '%Y-%m-%d').date()
                       if record_date else date.today())

        board_codes = data.get('board_codes') or []
        if not board_codes:
            scope = (data.get('scope') or 'unknown').lower()
            q = BoardTrendScore.query.filter(
                BoardTrendScore.record_date == record_date)
            if scope == 'unknown':
                q = q.filter(db.or_(
                    BoardTrendScore.trend_stage == 'unknown',
                    BoardTrendScore.total_score.is_(None),
                ))
            elif scope == 'manual_skip':
                q = q.filter(BoardTrendScore.is_manual.isnot(True))
            # scope == 'all' 不加额外过滤
            board_codes = [r.board_code for r in q.all()]

        if not board_codes:
            return jsonify({
                'success': False,
                'message': '没有需要计算的板块。先点"同步板块清单"，或调整 scope。',
            }), 400

        result = compute_and_persist(board_codes, record_date)
        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date.isoformat(),
                'requested': len(board_codes),
                **result,
                # 防止 errors 列表过长撑爆响应
                'errors': result['errors'][:50],
                'updated': result['updated'][:50],
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('板块自动打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500