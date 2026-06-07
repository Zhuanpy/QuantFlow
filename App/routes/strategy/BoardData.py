"""
板块数据路由 /board_data

职责：板块"本身"的数据管理与原始行情读取
  - 板块主表 eval_board 的增删改查 + 从行业/概念源同步 + 清理
  - 板块原始数据：日 K 图、15m 图、成分股名单、成分股/市值刷新
  - 板块详情页

趋势打分见 board_trend，个人偏好见 board_pref，整体看板见 board_overview。
"""
from flask import Blueprint, render_template, request, jsonify
from datetime import datetime, date
import logging
import pandas as pd
from sqlalchemy import text

from App.exts import db
from App.models.evaluation.Board import Board, CLASSIFICATIONS, SOURCES
from App.services.board_data_service import (
    latest_trading_date, query_data_status_batch, classify_data_status,
    em_industry_cons_via_http, ak_industry_cons, ak_individual_caps,
    boards_with_daily_data,
)

logger = logging.getLogger(__name__)

board_data_bp = Blueprint('board_data', __name__, url_prefix='/board_data')


# ===================== 页面 =====================
@board_data_bp.route('/')
def page():
    return render_template('strategy/board_data_list.html',
                           classifications=CLASSIFICATIONS)


@board_data_bp.route('/board')
def board_detail_page():
    """单板块详情：日 K / 15m 图 + 成分股 + 历次打分时间线。
    板块代码用查询参数传入：/board_data/board?code=BK0478
    """
    code = request.args.get('code', '')
    return render_template('strategy/board_detail.html', board_code=code)


# ===================== 主表 CRUD =====================
@board_data_bp.route('/api/list')
def api_list():
    """板块主表分页查询。

    query: page, page_size, board_code(模糊), board_name(模糊),
           classification(精确), enabled(1/0), has_data(1/0),
           sort_by: board_code / member_desc / updated
    """
    try:
        Board.ensure_table()
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 30, type=int)
        board_code = request.args.get('board_code', '').strip()
        board_name = request.args.get('board_name', '').strip()
        classification = request.args.get('classification', '').strip()
        enabled = request.args.get('enabled', '').strip()
        has_data = request.args.get('has_data', '').strip()
        sort_by = request.args.get('sort_by', 'board_code')

        q = Board.query
        if board_code:
            q = q.filter(Board.board_code.like(f'%{board_code}%'))
        if board_name:
            q = q.filter(Board.board_name.like(f'%{board_name}%'))
        if classification and classification in CLASSIFICATIONS:
            q = q.filter(Board.classification == classification)
        if enabled in ('0', '1'):
            q = q.filter(Board.enabled.is_(enabled == '1'))
        if has_data in ('0', '1'):
            q = q.filter(Board.has_daily_data.is_(has_data == '1'))

        if sort_by == 'member_desc':
            q = q.order_by((Board.member_count.is_(None)).asc(),
                           Board.member_count.desc())
        elif sort_by == 'updated':
            q = q.order_by(Board.updated_at.desc())
        else:
            q = q.order_by(Board.board_code.asc())

        pagination = q.paginate(page=page, per_page=page_size, error_out=False)
        items = [r.to_dict() for r in pagination.items]

        # 批量挂数据状态
        try:
            codes = [it['board_code'] for it in items if it.get('board_code')]
            status_map = query_data_status_batch(codes)
            ref_date = latest_trading_date()
            for it in items:
                hit = status_map.get(it['board_code'], {})
                it['data_status'] = classify_data_status(
                    hit.get('latest_daily'), hit.get('latest_1m'),
                    hit.get('latest_15m'), ref_date)
        except Exception as ds_err:
            logger.warning(f'附加 data_status 失败: {ds_err}')
            for it in items:
                it.setdefault('data_status', None)

        return jsonify({'success': True, 'data': {
            'items': items,
            'pagination': {
                'page': pagination.page, 'pages': pagination.pages,
                'per_page': pagination.per_page, 'total': pagination.total,
                'has_prev': pagination.has_prev, 'has_next': pagination.has_next,
            }
        }})
    except Exception as e:
        logger.exception('板块主表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


def _parse_board_payload(data):
    """白名单解析主表可写字段。"""
    allowed = {'board_name', 'classification', 'source', 'member_count',
               'has_daily_data', 'enabled', 'notes'}
    out = {}
    for k, v in (data or {}).items():
        if k not in allowed:
            continue
        out[k] = v
    if out.get('classification') and out['classification'] not in CLASSIFICATIONS:
        raise ValueError(f"非法 classification: {out['classification']}")
    if out.get('source') and out['source'] not in SOURCES:
        raise ValueError(f"非法 source: {out['source']}")
    return out


@board_data_bp.route('/api/create', methods=['POST'])
def api_create():
    """手工新增一个板块。"""
    try:
        Board.ensure_table()
        data = request.get_json(silent=True) or {}
        board_code = (data.get('board_code') or '').strip()
        if not board_code:
            return jsonify({'success': False, 'message': 'board_code 必填'}), 400
        if Board.query.filter_by(board_code=board_code).first():
            return jsonify({'success': False, 'message': f'{board_code} 已存在'}), 409
        fields = _parse_board_payload(data)
        fields.setdefault('source', 'manual')
        fields.setdefault('classification', '自定义')
        row = Board(board_code=board_code, **fields)
        db.session.add(row)
        db.session.commit()
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('新增板块失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/update/<int:row_id>', methods=['PUT'])
def api_update(row_id):
    """更新板块（board_code 不可改）。"""
    try:
        row = Board.query.get_or_404(row_id)
        data = request.get_json(silent=True) or {}
        fields = _parse_board_payload(data)
        for k, v in fields.items():
            setattr(row, k, v)
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('更新板块失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/delete/<int:row_id>', methods=['DELETE'])
def api_delete(row_id):
    try:
        ok = Board.delete_by_id(row_id)
        if not ok:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.exception('删除板块失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ===================== 同步 / 清理 =====================
@board_data_bp.route('/api/sync_boards', methods=['POST'])
def api_sync_boards():
    """从行业/概念源同步板块清单进主表 eval_board（唯一权威同步入口）。

    body: {
        source: 'industry' | 'concept' | 'all'（默认 'all'）,
        include_empty: false   True 时也导入无日 K 数据的板块（默认 False）
    }
    会同时回填 has_daily_data 与 member_count（来自 industry_eastmoney 最新快照）。
    """
    try:
        Board.ensure_table()
        data = request.get_json(silent=True) or {}
        source = (data.get('source') or 'all').lower()
        include_empty = bool(data.get('include_empty'))

        eng = db.engines['quanttradingsystem']
        # code -> (name, classification, source_tag)
        boards = {}
        with eng.connect() as conn:
            if source in ('concept', 'all'):
                rows = conn.execute(text(
                    "SELECT stock_code AS code, stock_name AS name FROM concept_board "
                    "WHERE stock_code IS NOT NULL"
                )).fetchall()
                for r in rows:
                    if r[0]:
                        boards.setdefault(r[0].strip(),
                                          ((r[1] or '').strip(), '概念板块', 'concept'))
            if source in ('industry', 'all'):
                rows = conn.execute(text(
                    "SELECT code, name FROM data_stock_classification "
                    "WHERE classification = '行业板块' AND code IS NOT NULL"
                )).fetchall()
                for r in rows:
                    if r[0]:
                        # 行业覆盖同代码概念（行业更标准）
                        boards[r[0].strip()] = ((r[1] or '').strip(), '行业板块', 'industry')

            total_in_source = len(boards)
            with_data = boards_with_daily_data(conn)

            # 成分股数：industry_eastmoney 最新快照按 board_code 计数
            member_counts = {}
            mc_rows = conn.execute(text(
                """
                SELECT ie.board_code, COUNT(*) AS cnt
                FROM industry_eastmoney ie
                INNER JOIN (
                    SELECT board_code, MAX(date) AS d
                    FROM industry_eastmoney GROUP BY board_code
                ) m ON m.board_code = ie.board_code AND m.d = ie.date
                GROUP BY ie.board_code
                """
            )).fetchall()
            for r in mc_rows:
                if r[0]:
                    member_counts[r[0].strip().upper()] = int(r[1])

        if not include_empty:
            boards = {c: v for c, v in boards.items() if c.upper() in with_data}
        filtered_out = total_in_source - len(boards)

        if not boards:
            return jsonify({'success': False, 'message': (
                f'源里有 {total_in_source} 个板块，但没有任何一个在日 K 数据库里。'
                f'先下载日 K，或传 include_empty=true 强制导入。')}), 404

        now = datetime.utcnow()
        created = updated = 0
        for code, (name, classification, src) in boards.items():
            cu = code.upper()
            fields = dict(
                board_name=name or code,
                classification=classification,
                source=src,
                has_daily_data=(cu in with_data),
                member_count=member_counts.get(cu),
                last_member_sync=now if cu in member_counts else None,
            )
            existing = Board.query.filter_by(board_code=code).first()
            if existing is None:
                db.session.add(Board(board_code=code, enabled=True, **fields))
                created += 1
            else:
                existing.board_name = fields['board_name']
                existing.classification = fields['classification']
                existing.source = fields['source']
                existing.has_daily_data = fields['has_daily_data']
                if fields['member_count'] is not None:
                    existing.member_count = fields['member_count']
                    existing.last_member_sync = now
                existing.updated_at = now
                updated += 1
        db.session.commit()

        return jsonify({'success': True, 'data': {
            'source': source,
            'total_in_source': total_in_source,
            'filtered_out_no_data': filtered_out,
            'total_boards': len(boards),
            'created': created,
            'updated': updated,
        }})
    except Exception as e:
        db.session.rollback()
        logger.exception('同步板块主表失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/cleanup_empty', methods=['POST'])
def api_cleanup_empty():
    """清理主表中无日 K 数据的板块。

    body: { keep_manual: true (默认，保留 source=manual 的手工板块) }
    """
    try:
        Board.ensure_table()
        data = request.get_json(silent=True) or {}
        keep_manual = data.get('keep_manual', True)

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            with_data = boards_with_daily_data(conn)

        q = Board.query
        if keep_manual:
            q = q.filter(Board.source != 'manual')
        candidates = q.all()
        to_delete = [r for r in candidates
                     if (r.board_code or '').upper() not in with_data]
        sample = [{'board_code': r.board_code, 'board_name': r.board_name}
                  for r in to_delete[:20]]
        for r in to_delete:
            db.session.delete(r)
        db.session.commit()
        return jsonify({'success': True, 'data': {
            'deleted': len(to_delete), 'sample': sample,
        }})
    except Exception as e:
        db.session.rollback()
        logger.exception('清理无数据板块失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ===================== 原始数据：日 K / 15m / 成分股 =====================
def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


@board_data_bp.route('/api/board/<code>/chart')
def api_board_chart(code):
    """返回单板块的日 K + MA20/MA60 + MACD 数据，供前端绘图。

    query:
      - days=120                          按窗口取末尾 N 日（默认）
      - start_date=YYYY-MM-DD&end_date=YYYY-MM-DD  指定区间（优先于 days）
    """
    try:
        from App.services.board_trend_score_service import _load_board_daily

        start_date_s = (request.args.get('start_date') or '').strip()
        end_date_s = (request.args.get('end_date') or '').strip()
        use_range = bool(start_date_s and end_date_s)

        if use_range:
            start_d = datetime.strptime(start_date_s, '%Y-%m-%d').date()
            end_d = datetime.strptime(end_date_s, '%Y-%m-%d').date()
            if start_d > end_d:
                return jsonify({'success': False, 'message': '起始日期不能晚于结束日期'}), 400
            lookback = max((end_d - start_d).days + 60, 250)
            df = _load_board_daily(code, end_d, lookback=lookback)
        else:
            days = max(60, min(int(request.args.get('days', 120)), 1000))
            df = _load_board_daily(code, date.today(), lookback=max(days + 30, 250))

        if df.empty:
            return jsonify({'success': False, 'message': f'未找到板块 {code} 的日 K 数据'}), 404

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
        return jsonify({'success': True, 'data': {
            'board_code': code, 'count': len(rows), 'rows': rows,
        }})
    except Exception as e:
        logger.exception('板块图表数据获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/board/<code>/chart_15m')
def api_board_chart_15m(code):
    """返回单板块的 15 分钟 K 线 + MA20/MA60 + MACD + 量能。

    数据源：data/15m/{code}.parquet
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

        close = df['close']
        df['ma20'] = close.rolling(20, min_periods=1).mean()
        df['ma60'] = close.rolling(60, min_periods=1).mean()

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
        return jsonify({'success': True, 'data': {
            'board_code': code, 'count': len(rows), 'rows': rows,
        }})
    except Exception as e:
        logger.exception('板块 15m 图表数据获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/board/<code>/stocks')
def api_board_stocks(code):
    """返回板块成分股清单（来源 industry_eastmoney 最新日期），LEFT JOIN 个股最新趋势打分。"""
    try:
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

        cap_source = 'snapshot'
        live_cap = {}
        try:
            live_members = em_industry_cons_via_http(code, retries=1)
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

        # 板块权重：按流通市值占比（东财行业板块指数=流通市值加权，此即真实口径）
        total_circ = sum(o['circ_cap'] for o in out if o.get('circ_cap'))
        for o in out:
            o['weight'] = (round(o['circ_cap'] / total_circ * 100, 2)
                           if (o.get('circ_cap') and total_circ) else None)
        # 按权重降序展示（无市值的排最后）
        out.sort(key=lambda o: (o['weight'] is None, -(o['weight'] or 0)))
        sorted_w = sorted([o['weight'] or 0 for o in out], reverse=True)
        cr5 = round(sum(sorted_w[:5]), 2)
        cr10 = round(sum(sorted_w[:10]), 2)

        return jsonify({'success': True, 'data': {
            'board_code': code,
            'board_name': rows[0][2],
            'member_date': rows[0][3].isoformat() if rows[0][3] else None,
            'count': len(out),
            'rows': out,
            'cap_source': cap_source,
            'weight_basis': 'circ_cap',   # 流通市值加权
            'weighted_count': sum(1 for o in out if o['weight'] is not None),
            'cr5': cr5,
            'cr10': cr10,
        }})
    except Exception as e:
        logger.exception('板块成分股查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ===================== 成分股 / 市值刷新 =====================
@board_data_bp.route('/api/refresh_members', methods=['POST'])
def api_refresh_members():
    """刷新板块成分股名单（来源 akshare 东财行业板块）。

    body: { board_code: 'BK0738'（可选，传则只刷该板块；不传则刷新所有行业板块） }
    存储：industry_eastmoney —— 先删除该 board_code 已有记录，再写入今日快照
    """
    try:
        data = request.get_json(silent=True) or {}
        single = (data.get('board_code') or '').strip()

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            if single:
                rows = conn.execute(text(
                    "SELECT code, name FROM data_stock_classification "
                    "WHERE Classification='行业板块' AND code=:c"
                ), {'c': single}).fetchall()
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
        ok = empty = 0
        failed = []
        total_members = 0

        with eng.begin() as conn:
            for code, name in targets:
                try:
                    members = ak_industry_cons(code)
                except Exception as e:
                    failed.append({'board_code': code, 'error': str(e)[:200]})
                    continue
                if not members:
                    empty += 1
                    continue
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


@board_data_bp.route('/api/refresh_caps', methods=['POST'])
def api_refresh_caps():
    """只刷新市值（基于 industry_eastmoney 已有名单），用 stock_individual_info_em 逐只查。

    body: { board_code: 'BK0739' }   # 必填，避免一次刷上千只股票
    """
    try:
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

        cap_map = ak_individual_caps(stock_codes)
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
