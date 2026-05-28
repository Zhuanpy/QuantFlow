"""
个股趋势打分路由（与 BoardTrendScoring 对称）

URL 前缀：/stock_trend
"""
from flask import Blueprint, render_template, request, jsonify
from datetime import datetime, date
import logging
import pandas as pd

from App.exts import db
from App.models.evaluation.StockTrendScore import (
    StockTrendScore, TREND_STAGES, TREND_STRENGTHS, SIGNALS,
)

logger = logging.getLogger(__name__)

stock_trend_bp = Blueprint('stock_trend', __name__, url_prefix='/stock_trend')


# ----------------- 页面 -----------------
@stock_trend_bp.route('/')
def page():
    return render_template(
        'strategy/stock_trend_scoring.html',
        trend_stages=TREND_STAGES,
        trend_strengths=TREND_STRENGTHS,
        signals=SIGNALS,
    )


@stock_trend_bp.route('/stock/<code>')
def stock_detail_page(code):
    return render_template(
        'strategy/stock_trend_detail.html',
        stock_code=code,
    )


# ----------------- 搜索（用于详情页快速切换） -----------------
@stock_trend_bp.route('/api/search')
def api_search():
    """按代码/名称模糊搜索个股，用于详情页搜索框自动补全。

    query:
      q: 关键字（代码或名称的子串）
      limit: 返回上限（默认 15）
    """
    try:
        from sqlalchemy import text, or_
        from App.models.data.basic_info import StockInfo

        q = (request.args.get('q') or '').strip()
        limit = max(1, min(int(request.args.get('limit', 15)), 50))
        if not q:
            return jsonify({'success': True, 'data': []})

        # 数字优先按代码前缀匹配（更符合直觉）
        if q.isdigit():
            primary = (db.session.query(StockInfo)
                .filter(StockInfo.code.like(f'{q}%'))
                .filter(~StockInfo.code.like('BK%'))
                .order_by(StockInfo.code.asc())
                .limit(limit).all())
            items = [{'code': s.code, 'name': s.name} for s in primary]
        else:
            primary = (db.session.query(StockInfo)
                .filter(or_(
                    StockInfo.code.like(f'%{q}%'),
                    StockInfo.name.like(f'%{q}%'),
                ))
                .filter(~StockInfo.code.like('BK%'))
                .order_by(StockInfo.code.asc())
                .limit(limit).all())
            items = [{'code': s.code, 'name': s.name} for s in primary]

        return jsonify({'success': True, 'data': items})
    except Exception as e:
        logger.exception('个股搜索失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 个股基础信息（实时行情 + 基本面） -----------------
@stock_trend_bp.route('/api/stock/<code>/quote')
def api_stock_quote(code):
    """实时行情 + 基本面（最新价 / 涨跌 / 总市值 / 流通市值 / PE / PB / 换手率 等）。

    数据源回退顺序：
      1. data_stock_basic_quote 本地表（盘后由 refresh_basic_quotes_bulk 写入）
      2. East Money push2 stock/get 实时（兜底）
    若 ?live=1 则跳过本地、强制走实时。
    """
    try:
        from App.services.stock_quote_service import fetch_stock_quote, fetch_local_quote
        from App.models.data.basic_info import StockInfo, StockClassification

        code = (code or '').strip()
        if not code:
            return jsonify({'success': False, 'message': 'code 不能为空'}), 400

        force_live = (request.args.get('live') or '').strip() in ('1', 'true', 'yes')

        # 本地静态信息（名称 / 行业分类）始终拼上
        info = StockInfo.query.filter_by(code=code).first()
        cls_row = StockClassification.query.filter_by(code=code).first()
        base = {
            'stock_code': code,
            'stock_name': info.name if info else None,
            'classification': cls_row.classification if cls_row else None,
            'es_code': info.EsCode if info else None,
            'market_code': info.MarketCode if info else None,
        }

        quote = None
        source = None
        if not force_live:
            quote = fetch_local_quote(code)
            if quote:
                source = 'local'

        if not quote:
            quote = fetch_stock_quote(code)
            if quote:
                source = 'live'

        if not quote:
            return jsonify({
                'success': False,
                'message': ('本地无该股的基础信息（请先在下载页跑一次"刷新基本面"），'
                            '实时拉取也失败（限频或网络不通）。仅返回本地静态信息'),
                'data': base,
            }), 200

        return jsonify({'success': True,
                        'data': {**base, **quote, 'source': source}})
    except Exception as e:
        logger.exception('个股 quote 接口失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/refresh_basics', methods=['POST'])
def api_refresh_basics():
    """手动触发：批量刷新所有 A 股的基本面（一次拉全市场，写入 data_stock_basic_quote）。

    通常每日盘后由下载流程自动调用；此接口用于按需手动刷新。
    会自动检测股票名称变化并写入 data_stock_name_history 同时同步到
    data_stock_info / strategy_stock_pool / eval_stock_trend_score。
    """
    try:
        from App.services.stock_quote_service import refresh_basic_quotes_bulk
        result = refresh_basic_quotes_bulk()
        return jsonify({'success': result['success'], 'data': result})
    except Exception as e:
        logger.exception('refresh_basics 失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/stock/<code>/refresh_basic', methods=['POST'])
def api_refresh_basic_single(code):
    """单只个股的基本面 + 名称即时刷新：避免等待全量批量。

    1. 走 East Money push2 stock/get 拉这一只的最新数据
    2. upsert 到 data_stock_basic_quote
    3. 比对名称、同步到 data_stock_info / StockPool / StockTrendScore + 改名历史
    """
    try:
        from datetime import date as _date, datetime as _dt
        from sqlalchemy.dialects.mysql import insert as mysql_insert
        from App.services.stock_quote_service import fetch_stock_quote, _sync_static_info
        from App.models.data.StockBasicQuote import StockBasicQuote

        code = (code or '').strip()
        if not code:
            return jsonify({'success': False, 'message': 'code 不能为空'}), 400

        q = fetch_stock_quote(code)
        if not q:
            return jsonify({'success': False, 'message': '实时拉取失败（限频或网络）'}), 502

        row = {
            'stock_code': q.get('stock_code') or code,
            'stock_name': q.get('name'),
            'price': q.get('price'),
            'prev_close': q.get('prev_close'),
            'change_pct': q.get('change_pct'),
            'change_amt': q.get('change_amt'),
            'open': q.get('open'),
            'high': q.get('high'),
            'low': q.get('low'),
            'volume': int(q['volume']) if q.get('volume') is not None else None,
            'amount': q.get('amount'),
            'turnover': q.get('turnover'),
            'volume_ratio': q.get('volume_ratio'),
            'total_mv': q.get('total_mv'),
            'float_mv': q.get('float_mv'),
            'total_shares': q.get('total_shares'),
            'float_shares': q.get('float_shares'),
            'pe_dynamic': q.get('pe_dynamic'),
            'pe_static': q.get('pe_static'),
            'pe_ttm': q.get('pe_ttm'),
            'pb': q.get('pb'),
            'quote_date': _date.today(),
        }

        try:
            stmt = mysql_insert(StockBasicQuote).values(row)
            update_dict = {c.name: stmt.inserted[c.name]
                           for c in StockBasicQuote.__table__.columns
                           if c.name != 'stock_code'}
            stmt = stmt.on_duplicate_key_update(**update_dict)
            db.session.execute(stmt)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'upsert 失败: {e}'}), 500

        sync = _sync_static_info([row], source='eastmoney_quote_single')
        return jsonify({
            'success': True,
            'data': {
                'stock_code': code,
                'stock_name': row['stock_name'],
                'name_changes': len(sync['changes']),
                'changes_sample': [
                    {'stock_code': c, 'old_name': o, 'new_name': n}
                    for c, o, n in sync['changes']
                ],
            },
        })
    except Exception as e:
        db.session.rollback()
        logger.exception(f'单股 refresh_basic 失败 {code}')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/name_history')
def api_name_history():
    """改名记录（最近 N 条；支持 ?code= 过滤）"""
    try:
        from App.models.data.StockNameHistory import StockNameHistory
        code = (request.args.get('code') or '').strip()
        try:
            limit = max(1, min(int(request.args.get('limit', 100)), 500))
        except ValueError:
            limit = 100
        q = StockNameHistory.query
        if code:
            q = q.filter(StockNameHistory.stock_code == code)
        rows = q.order_by(StockNameHistory.changed_at.desc()).limit(limit).all()
        return jsonify({
            'success': True,
            'data': {
                'count': len(rows),
                'items': [r.to_dict() for r in rows],
            },
        })
    except Exception as e:
        logger.exception('name_history 查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 个股所属板块（含板块最新评分） -----------------
@stock_trend_bp.route('/api/stock/<code>/boards')
def api_stock_boards(code):
    """返回个股所属的板块列表 + 每个板块的最新趋势打分。

    数据源：
      industry_eastmoney → 个股 → 所属 board_code 列表（取每个 board_code 最新 date 的快照）
      eval_board_trend_score → 每个板块的最新一条评分
    """
    try:
        from sqlalchemy import text
        from App.models.evaluation.BoardTrendScore import BoardTrendScore

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT ie.board_code, ie.board_name, MAX(ie.date) AS member_date
                FROM industry_eastmoney ie
                WHERE ie.stock_code = :c
                GROUP BY ie.board_code, ie.board_name
                ORDER BY ie.board_code
                """
            ), {'c': code}).fetchall()

        if not rows:
            return jsonify({'success': True, 'data': []})

        board_codes = [r[0] for r in rows]
        # 每个板块的最新评分日期
        sub = (db.session.query(
                BoardTrendScore.board_code,
                db.func.max(BoardTrendScore.record_date).label('latest'))
            .filter(BoardTrendScore.board_code.in_(board_codes))
            .group_by(BoardTrendScore.board_code).subquery())
        scores = (db.session.query(BoardTrendScore)
            .join(sub, db.and_(
                BoardTrendScore.board_code == sub.c.board_code,
                BoardTrendScore.record_date == sub.c.latest))
            .all())
        score_map = {s.board_code: s for s in scores}

        out = []
        for r in rows:
            sc = score_map.get(r[0])
            out.append({
                'board_code': r[0],
                'board_name': r[1],
                'member_date': r[2].isoformat() if r[2] else None,
                'score': sc.to_dict() if sc else None,
            })
        return jsonify({'success': True, 'data': out})
    except Exception as e:
        logger.exception('个股所属板块查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 单股图表 / 历史 -----------------
@stock_trend_bp.route('/api/stock/<code>/chart')
def api_stock_chart(code):
    """日 K + MA20/MA60 + MACD，数据源 data_stock_daily。"""
    try:
        from App.services.stock_trend_score_service import _load_stock_daily
        from App.services.board_trend_score_service import _ema

        start_date_s = (request.args.get('start_date') or '').strip()
        end_date_s = (request.args.get('end_date') or '').strip()
        use_range = bool(start_date_s and end_date_s)

        if use_range:
            start_d = datetime.strptime(start_date_s, '%Y-%m-%d').date()
            end_d = datetime.strptime(end_date_s, '%Y-%m-%d').date()
            if start_d > end_d:
                return jsonify({'success': False, 'message': '起始日期不能晚于结束日期'}), 400
            lookback = max((end_d - start_d).days + 60, 250)
            df = _load_stock_daily(code, end_d, lookback=lookback)
        else:
            days = max(60, min(int(request.args.get('days', 120)), 1000))
            df = _load_stock_daily(code, date.today(), lookback=max(days + 30, 250))

        if df.empty:
            return jsonify({'success': False, 'message': f'未找到个股 {code} 的日 K 数据'}), 404

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
            'data': {'stock_code': code, 'count': len(rows), 'rows': rows}
        })
    except Exception as e:
        logger.exception('个股图表数据获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


def _load_15m_from_parquet(code: str) -> pd.DataFrame:
    """从标准 15m parquet 路径读取（与 viewer_15m_route / 板块图表一致）。"""
    try:
        from App.routes.data.viewer_15m_route import (
            _get_15m_dir, _find_15m_file, _read_15m_file,
        )
        fpath = _find_15m_file(_get_15m_dir(), code)
        if not fpath:
            return pd.DataFrame()
        df = _read_15m_file(fpath)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.warning(f'读 15m parquet 失败 {code}: {e}')
        return pd.DataFrame()


def _load_1m_then_resample_15m(code: str, calendar_days: int = 365) -> pd.DataFrame:
    """读最近 calendar_days 天的 1m 数据并重采样为 15m。

    优先级：
      1. 文件系统 1m parquet  data/quarters/<year>/<quarter>/<code>.parquet
         （盘后下载流水线 save_1m_to_csv 写在这）
      2. MySQL data_1m_<code>（兜底；该表通常不存在）
    """
    import os
    from App.codes.utils.data_processing import ResampleData
    from App.utils.file_utils import get_stock_data_path

    if not code:
        return pd.DataFrame()

    # 1) 文件系统 — 当前季度 + 最近 4 个季度
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=calendar_days)
    today = pd.Timestamp.now()
    quarters_to_check = []
    y, m = today.year, today.month
    q = (m - 1) // 3 + 1
    for _ in range(5):
        quarters_to_check.append((str(y), f'Q{q}'))
        q -= 1
        if q == 0:
            q = 4
            y -= 1

    frames = []
    for year, quarter in quarters_to_check:
        try:
            p = get_stock_data_path(code, data_type='1m', year=year, quarter=quarter, create=False)
        except Exception:
            continue
        for candidate in [p, p.replace('.parquet', '.csv')]:
            if os.path.exists(candidate):
                try:
                    df = (pd.read_parquet(candidate)
                          if candidate.endswith('.parquet')
                          else pd.read_csv(candidate))
                    df['date'] = pd.to_datetime(df['date'])
                    frames.append(df)
                except Exception as e:
                    logger.warning(f'读 {candidate} 失败: {e}')
                break

    if frames:
        df1m = (pd.concat(frames, ignore_index=True)
                .drop_duplicates(subset=['date'], keep='last')
                .sort_values('date').reset_index(drop=True))
        df1m = df1m[df1m['date'] >= cutoff]
        if not df1m.empty:
            try:
                return ResampleData.resample_1m_data(df1m, freq='15m')
            except Exception as e:
                logger.warning(f'1m → 15m 重采样失败 {code}: {e}')

    # 2) MySQL 兜底（per-year bind 通常未配置；遇到老数据可能能命中）
    import re
    from sqlalchemy import text
    if not re.fullmatch(r'[A-Za-z0-9]{1,16}', code):
        return pd.DataFrame()
    try:
        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            df1m = pd.read_sql(
                text(f"""
                    SELECT date, open, close, high, low, volume, money
                    FROM data_1m_{code}
                    WHERE date >= :start_dt
                    ORDER BY date ASC
                """),
                conn,
                params={'start_dt': cutoff.to_pydatetime()},
            )
        if df1m is not None and not df1m.empty:
            df1m['date'] = pd.to_datetime(df1m['date'])
            return ResampleData.resample_1m_data(df1m, freq='15m')
    except Exception as e:
        logger.info(f'1m 表 data_1m_{code} 读取失败: {e}')

    return pd.DataFrame()


@stock_trend_bp.route('/api/stock/<code>/chart_15m')
def api_stock_chart_15m(code):
    """15m K + MA20/MA60 + MACD + 量能。

    数据源（按优先级回退）：
      1. data/15m/<code>.parquet（盘后下载流水线写入；与 viewer_15m / 板块图表同源）
      2. data/quarters/<year>/<quarter>/<code>.parquet 1m → 实时重采样为 15m
      3. MySQL data_1m_<code> 兜底（per-year bind 通常未配置）

    query:
      bars=192                                    末尾 N 根（默认 ≈ 10 个交易日）
      start_date=YYYY-MM-DD&end_date=YYYY-MM-DD   区间过滤（优先于 bars）
    """
    try:
        from App.services.board_trend_score_service import _ema

        start_date_s = (request.args.get('start_date') or '').strip()
        end_date_s = (request.args.get('end_date') or '').strip()
        use_range = bool(start_date_s and end_date_s)

        df = _load_15m_from_parquet(code)
        source = 'stored_15m'
        if df is None or df.empty:
            df = _load_1m_then_resample_15m(code)
            source = 'resampled_from_1m'
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'message': (f'未找到个股 {code} 的 15m 数据，且 data/quarters/ 下也无可重采样的 1m。'
                            f'请先点"📥 下载数据并纳入"。'),
            }), 404

        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        close = df['close']
        df['ma20'] = close.rolling(20, min_periods=1).mean()
        df['ma60'] = close.rolling(60, min_periods=1).mean()
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        df['dif'] = ema12 - ema26
        df['dea'] = _ema(df['dif'], 9)
        df['bar'] = (df['dif'] - df['dea']) * 2

        if use_range:
            try:
                start_ts = pd.to_datetime(start_date_s)
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
            try:
                bars = int(request.args.get('bars', 192))
            except ValueError:
                bars = 192
            bars = max(48, min(bars, 5000))
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
                'open': _r(r['open']),
                'close': _r(r['close']),
                'high': _r(r['high']),
                'low': _r(r['low']),
                'volume': int(r['volume']) if pd.notna(r.get('volume')) else 0,
                'ma20': _r(r['ma20']),
                'ma60': _r(r['ma60']),
                'dif': _r(r['dif']),
                'dea': _r(r['dea']),
                'bar': _r(r['bar']),
            })
        return jsonify({
            'success': True,
            'data': {'stock_code': code, 'count': len(rows), 'rows': rows, 'source': source}
        })
    except Exception as e:
        logger.exception('个股 15m 图表数据获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/stock/<code>/history')
def api_stock_history(code):
    """该股票在打分表里的历史评分时间线"""
    try:
        from App.models.data.basic_info import StockInfo

        rows = (StockTrendScore.query
                .filter_by(stock_code=code)
                .order_by(StockTrendScore.record_date.desc())
                .limit(120).all())
        if rows:
            stock_name = rows[0].stock_name
        else:
            # 没有历史评分时，从 data_stock_info 拿真名；拿不到就返回 None（让前端只显示代码）
            info = StockInfo.query.filter_by(code=code).first()
            stock_name = info.name if info else None
        return jsonify({
            'success': True,
            'data': {
                'stock_code': code,
                'stock_name': stock_name,
                'count': len(rows),
                'rows': [r.to_dict() for r in rows],
            }
        })
    except Exception as e:
        logger.exception('个股历史评分获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 列表 / 查询 -----------------
@stock_trend_bp.route('/api/list')
def api_list():
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 30, type=int)
        record_date = request.args.get('record_date', '').strip()
        stock_code = request.args.get('stock_code', '').strip()
        stock_name = request.args.get('stock_name', '').strip()
        trend_stage = request.args.get('trend_stage', '').strip()
        sort_by = request.args.get('sort_by', 'total_score_desc')

        q = StockTrendScore.query

        if record_date:
            q = q.filter(StockTrendScore.record_date == record_date)
        else:
            latest = db.session.query(db.func.max(StockTrendScore.record_date)).scalar()
            if latest:
                q = q.filter(StockTrendScore.record_date == latest)
                record_date = latest.isoformat()

        if stock_code:
            q = q.filter(StockTrendScore.stock_code.like(f'%{stock_code}%'))
        if stock_name:
            q = q.filter(StockTrendScore.stock_name.like(f'%{stock_name}%'))
        if trend_stage and trend_stage in TREND_STAGES:
            q = q.filter(StockTrendScore.trend_stage == trend_stage)

        null_last = (StockTrendScore.total_score.is_(None)).asc()
        if sort_by == 'total_score_asc':
            q = q.order_by(null_last, StockTrendScore.total_score.asc())
        elif sort_by == 'updated':
            q = q.order_by(StockTrendScore.updated_at.desc())
        elif sort_by == 'stock_code':
            q = q.order_by(StockTrendScore.stock_code.asc())
        else:
            q = q.order_by(null_last, StockTrendScore.total_score.desc())

        pagination = q.paginate(page=page, per_page=page_size, error_out=False)
        items = [r.to_dict() for r in pagination.items]

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
        logger.exception('个股趋势列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/stats')
def api_stats():
    try:
        record_date = request.args.get('record_date', '').strip()
        if not record_date:
            latest = db.session.query(db.func.max(StockTrendScore.record_date)).scalar()
            record_date = latest.isoformat() if latest else None

        stats = {s: 0 for s in TREND_STAGES}
        if record_date:
            rows = (db.session.query(StockTrendScore.trend_stage, db.func.count())
                    .filter(StockTrendScore.record_date == record_date)
                    .group_by(StockTrendScore.trend_stage).all())
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
        logger.exception('个股趋势统计失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/dates')
def api_dates():
    try:
        rows = (db.session.query(StockTrendScore.record_date)
                .distinct()
                .order_by(StockTrendScore.record_date.desc())
                .limit(60).all())
        return jsonify({
            'success': True,
            'data': [r[0].isoformat() for r in rows if r[0]],
        })
    except Exception as e:
        logger.exception('个股趋势日期列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 写入 -----------------
def _parse_payload(data):
    allowed = {
        'stock_code', 'stock_name', 'record_date',
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


@stock_trend_bp.route('/api/upsert', methods=['POST'])
def api_upsert():
    try:
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)

        stock_code = fields.pop('stock_code', None)
        stock_name = fields.pop('stock_name', None)
        record_date = fields.pop('record_date', None) or date.today()

        if not stock_code:
            return jsonify({'success': False, 'message': 'stock_code 必填'}), 400

        if stock_name is not None:
            fields['stock_name'] = stock_name
        if data.get('is_manual') is None:
            fields['is_manual'] = True

        row = StockTrendScore.upsert(stock_code=stock_code,
                                     record_date=record_date,
                                     **fields)
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('upsert 个股趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/update/<int:row_id>', methods=['PUT'])
def api_update(row_id):
    try:
        row = StockTrendScore.query.get_or_404(row_id)
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)
        fields.pop('stock_code', None)
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
        logger.exception('更新个股趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/delete/<int:row_id>', methods=['DELETE'])
def api_delete(row_id):
    try:
        ok = StockTrendScore.delete_by_id(row_id)
        if not ok:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.exception('删除个股趋势打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 个股清单同步 -----------------
def _stocks_with_daily_data(conn) -> dict:
    """返回所有"有日 K 数据"且非板块（BK*）的个股代码 → 任一一行 record 时拿到的最新数据数。

    返回 dict: code(大写) → True
    """
    from sqlalchemy import text
    rows = conn.execute(text(
        "SELECT DISTINCT stock_code FROM data_stock_daily "
        "WHERE stock_code IS NOT NULL AND stock_code NOT LIKE 'BK%'"
    )).fetchall()
    return {r[0].strip().upper(): True for r in rows if r[0]}


@stock_trend_bp.route('/api/sync_stocks', methods=['POST'])
def api_sync_stocks():
    """从股票池（strategy_stock_pool）把清单批量导入到打分表。

    设计意图：只对"关心的股票"（即股票池中的股票）打分，避免给全市场 5000+ 只
    每日打分。要把股票纳入打分体系，先在股票池里加入即可。

    body: {
        record_date: 'YYYY-MM-DD'（可选，缺省今天）,
        overwrite: false           True 时覆盖已有记录的 stock_name；否则只补缺
        include_empty: false       True 时不过滤无日 K 数据的（默认 False）
        include_excluded: false    True 时包含 is_excluded=True 的（默认 False）
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        if record_date:
            record_date = datetime.strptime(record_date, '%Y-%m-%d').date()
        else:
            record_date = date.today()
        overwrite = bool(data.get('overwrite'))
        include_empty = bool(data.get('include_empty'))
        include_excluded = bool(data.get('include_excluded'))

        from App.models.strategy.StockPool import StockPool
        q = StockPool.query.filter_by(is_active=True)
        if not include_excluded:
            q = q.filter(StockPool.is_excluded.isnot(True))
        pool_rows = q.all()

        stocks = {}  # code → name
        for r in pool_rows:
            code = (r.stock_code or '').strip()
            if code:
                stocks.setdefault(code, (r.stock_name or '').strip() or code)

        total_in_source = len(stocks)

        filtered_out = 0
        if not include_empty:
            eng = db.engines['quanttradingsystem']
            with eng.connect() as conn:
                with_data = _stocks_with_daily_data(conn)
            kept = {c: n for c, n in stocks.items() if c.upper() in with_data}
            filtered_out = len(stocks) - len(kept)
            stocks = kept

        if not stocks:
            return jsonify({
                'success': False,
                'message': (f'股票池里有 {total_in_source} 只活跃股票，但没有任何一只在日 K '
                            f'数据库里。先下载日 K，或传 include_empty=true 强制导入。'
                            if total_in_source > 0
                            else '股票池为空。请先到"股票池管理"添加股票。'),
            }), 404

        created = 0
        updated_name = 0
        skipped = 0
        for code, name in stocks.items():
            existing = StockTrendScore.query.filter_by(
                stock_code=code, record_date=record_date).first()
            if existing is None:
                db.session.add(StockTrendScore(
                    stock_code=code,
                    stock_name=name or code,
                    record_date=record_date,
                    trend_stage='unknown',
                    formula_version='v0',
                    is_manual=False,
                ))
                created += 1
            elif overwrite and name and existing.stock_name != name:
                existing.stock_name = name
                existing.updated_at = datetime.utcnow()
                updated_name += 1
            else:
                skipped += 1

        db.session.commit()
        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date.isoformat(),
                'total_in_source': total_in_source,
                'filtered_out_no_data': filtered_out,
                'total_stocks': len(stocks),
                'created': created,
                'updated_name': updated_name,
                'skipped': skipped,
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('同步个股清单失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/sync_downloaded_stocks', methods=['POST'])
def api_sync_downloaded_stocks():
    """从"已下载日 K 数据的全部个股"同步到打分表。

    与 /api/sync_stocks 的区别：
      - sync_stocks 源：strategy_stock_pool（用户精选清单）
      - 本接口源：data_stock_daily（所有已下载日 K 的股票）
    适用场景：希望对所有下载过的票打分，不只是股票池里的。

    body: {
        record_date: 'YYYY-MM-DD'（可选，缺省今天）,
        overwrite: false   True 时覆盖已有记录的 stock_name；否则只补缺
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        if record_date:
            record_date = datetime.strptime(record_date, '%Y-%m-%d').date()
        else:
            record_date = date.today()
        overwrite = bool(data.get('overwrite'))

        # 1) 已下载（data_stock_daily 里有日 K 数据的）所有非板块代码
        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            with_data = _stocks_with_daily_data(conn)

        if not with_data:
            return jsonify({
                'success': False,
                'message': 'data_stock_daily 表里没有任何已下载的个股日 K 数据。请先在 /download_minute_data_page 下载。',
            }), 404

        # 2) 批量查 StockInfo 拿股票名
        from App.models.data.basic_info import StockInfo
        codes_upper = sorted(with_data.keys())
        info_rows = (StockInfo.query
                     .filter(StockInfo.code.in_(codes_upper))
                     .all())
        name_map = {(r.code or '').strip().upper(): (r.name or '').strip()
                    for r in info_rows}

        # 3) 批量取该 record_date 已有的打分行
        existing_rows = (StockTrendScore.query
                         .filter_by(record_date=record_date)
                         .all())
        existing_map = {(r.stock_code or '').strip().upper(): r
                        for r in existing_rows}

        new_rows = []
        created = 0
        updated_name = 0
        skipped = 0
        for code in codes_upper:
            name = name_map.get(code) or code
            existing = existing_map.get(code)
            if existing is None:
                new_rows.append(StockTrendScore(
                    stock_code=code,
                    stock_name=name,
                    record_date=record_date,
                    trend_stage='unknown',
                    formula_version='v0',
                    is_manual=False,
                ))
                created += 1
            elif overwrite and name and existing.stock_name != name:
                existing.stock_name = name
                existing.updated_at = datetime.utcnow()
                updated_name += 1
            else:
                skipped += 1

        if new_rows:
            db.session.bulk_save_objects(new_rows)
        db.session.commit()

        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date.isoformat(),
                'total_in_source': len(with_data),
                'total_stocks': len(with_data),
                'created': created,
                'updated_name': updated_name,
                'skipped': skipped,
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('从已下载股票同步失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/stock/<code>/enroll', methods=['POST'])
def api_enroll_stock(code):
    """一键下载历史数据并纳入股票池。

    用于在详情页直接补齐"未入库"个股的数据。完整 3 步下载（与 /download_minute_data_page
    盘后下载流水线一致）：
      Step 1  日 K   → data_stock_daily MySQL（pytdx → AKShare 回退）
      Step 2  1 分钟 → data/quarters/<year>/<quarter>/<code>.parquet
      Step 3  15 分钟→ data/15m/<code>.parquet（图表读这里）
    然后若 strategy_stock_pool 中尚无该股票，调用 StockPool.create_manual
    将其纳入观察池（pool_type='watching'）。

    body: {
        days: int  下载天数（默认 240；东财 1m 受限大约可拿数月，pytdx 可拿更多）
        pool_type: str  入池类型（默认 'watching'）
    }
    """
    try:
        from App.services.stock_full_download_service import download_stock_full
        from App.models.strategy.StockPool import StockPool
        from App.models.data.basic_info import StockInfo

        code = (code or '').strip()
        if not code:
            return jsonify({'success': False, 'message': '股票代码不能为空'}), 400
        if code.upper().startswith('BK'):
            return jsonify({'success': False, 'message': '该接口仅支持个股，板块请走板块接口'}), 400

        data = request.get_json(silent=True) or {}
        days = int(data.get('days') or 240)
        days = max(1, min(days, 1200))
        pool_type = (data.get('pool_type') or 'watching').strip() or 'watching'

        info = StockInfo.query.filter_by(code=code).first()
        stock_name = info.name if info else None

        dl_result = download_stock_full(code, days=days)
        if not dl_result.get('success'):
            return jsonify({
                'success': False,
                'message': dl_result.get('message') or f'下载 {code} 数据失败',
                'steps': dl_result.get('steps'),
            }), 500

        pool_row = StockPool.query.filter_by(stock_code=code).first()
        enrolled = False
        if pool_row is None:
            pool_row = StockPool.create_manual(
                stock_code=code,
                stock_name=stock_name or code,
                pool_type=pool_type,
            )
            enrolled = True
        else:
            updated = False
            if not pool_row.is_active:
                pool_row.is_active = True
                updated = True
            if pool_row.is_excluded:
                pool_row.is_excluded = False
                pool_row.exclusion_reason = None
                updated = True
            if stock_name and not pool_row.stock_name:
                pool_row.stock_name = stock_name
                updated = True
            if updated:
                pool_row.updated_at = datetime.utcnow()
                db.session.commit()

        return jsonify({
            'success': True,
            'data': {
                'stock_code': code,
                'stock_name': stock_name,
                'days_requested': days,
                'download_message': dl_result.get('message'),
                'steps': dl_result.get('steps'),
                'enrolled': enrolled,
                'pool_type': pool_row.pool_type,
                'pool_id': pool_row.id,
            },
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('个股一键下载并纳入失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_trend_bp.route('/api/cleanup_empty', methods=['POST'])
def api_cleanup_empty():
    """清理无数据/未打分的自动记录。

    body: {
        record_date: 'YYYY-MM-DD'（可选；缺省=全部日期）,
        scope: 'no_data' | 'unscored' | 'both'（默认 'both'）,
        keep_manual: true
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        if record_date:
            record_date = datetime.strptime(record_date, '%Y-%m-%d').date()
        scope = (data.get('scope') or 'both').lower()
        keep_manual = data.get('keep_manual', True)

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            with_data = _stocks_with_daily_data(conn)

        q = StockTrendScore.query
        if record_date:
            q = q.filter(StockTrendScore.record_date == record_date)
        if keep_manual:
            q = q.filter(StockTrendScore.is_manual.isnot(True))

        candidates = q.all()
        to_delete = []
        for r in candidates:
            no_data = (r.stock_code or '').upper() not in with_data
            unscored = r.total_score is None
            if scope == 'no_data' and no_data:
                to_delete.append(r)
            elif scope == 'unscored' and unscored:
                to_delete.append(r)
            elif scope == 'both' and (no_data or unscored):
                to_delete.append(r)

        sample = [{'stock_code': r.stock_code,
                   'stock_name': r.stock_name,
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
        logger.exception('清理无数据个股失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ----------------- 计算入口 -----------------
@stock_trend_bp.route('/api/compute', methods=['POST'])
def api_compute():
    """自动打分（v1）。

    body: {
        record_date: 'YYYY-MM-DD'（缺省取今天）,
        stock_codes: ['000001', ...]（可选；缺省时按 scope 决定）,
        scope: 'all' | 'unknown' | 'manual_skip'
    }
    """
    try:
        from App.services.stock_trend_score_service import compute_and_persist

        data = request.get_json(silent=True) or {}
        record_date = data.get('record_date')
        record_date = (datetime.strptime(record_date, '%Y-%m-%d').date()
                       if record_date else date.today())

        stock_codes = data.get('stock_codes') or []
        if not stock_codes:
            scope = (data.get('scope') or 'unknown').lower()
            q = StockTrendScore.query.filter(
                StockTrendScore.record_date == record_date)
            if scope == 'unknown':
                q = q.filter(db.or_(
                    StockTrendScore.trend_stage == 'unknown',
                    StockTrendScore.total_score.is_(None),
                ))
            elif scope == 'manual_skip':
                q = q.filter(StockTrendScore.is_manual.isnot(True))
            stock_codes = [r.stock_code for r in q.all()]

        if not stock_codes:
            return jsonify({
                'success': False,
                'message': '没有需要计算的个股。先点"同步个股清单"，或调整 scope。',
            }), 400

        result = compute_and_persist(stock_codes, record_date)
        return jsonify({
            'success': True,
            'data': {
                'record_date': record_date.isoformat(),
                'requested': len(stock_codes),
                **result,
                'errors': result['errors'][:50],
                'updated': result['updated'][:50],
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('个股自动打分失败')
        return jsonify({'success': False, 'message': str(e)}), 500
