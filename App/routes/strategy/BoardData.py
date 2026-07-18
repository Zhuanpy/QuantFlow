"""
板块数据路由 /board_data

职责：板块"本身"的数据管理与原始行情读取
  - 板块主表 eval_board 的增删改查 + 从行业/概念源同步 + 清理
  - 板块原始数据：日 K 图、15m 图、成分股名单、成分股/市值刷新
  - 板块详情页

趋势打分见 board_trend，个人偏好见 board_pref，整体看板见 board_overview。
"""
from flask import Blueprint, render_template, request, jsonify, current_app
from datetime import datetime, date
import logging
import re
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


@board_data_bp.route('/hot')
def board_hot_page():
    """近期热点页：tab=trend 近期热点板块(评分动量) / tab=concept 概念热点(东财快照)，路由参数区分。"""
    tab = request.args.get('tab', 'trend')
    if tab not in ('trend', 'concept'):
        tab = 'trend'
    return render_template('strategy/board_hot.html', tab=tab)


@board_data_bp.route('/api/hot_boards')
def api_hot_boards():
    """近期热点板块聚合：用板块趋势分(v1-mtf)最新值 + 近 ~5 交易日的评分动量/涨幅，
    分档：退潮(分数下滑) / 新兴(分数快速抬升且未到高位) / 持续(高分且仍在上行)。"""
    try:
        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            dates = [r[0] for r in conn.execute(text(
                "SELECT DISTINCT record_date FROM eval_board_trend_score "
                "ORDER BY record_date DESC LIMIT 8")).fetchall()]
            if not dates:
                return jsonify({'success': True, 'as_of': None, 'ref_date': None,
                                'emerging': [], 'sustained': [], 'fading': []})
            latest = dates[0]
            ref = dates[min(5, len(dates) - 1)]   # 约 5 个交易日前
            cur = {r[0]: r for r in conn.execute(text(
                "SELECT board_code, board_name, total_score, trend_stage, trend_strength, "
                "`signal`, volume_score, change_pct, close "
                "FROM eval_board_trend_score WHERE record_date=:d"), {'d': latest}).fetchall()}
            prev = {r[0]: (r[1], r[2]) for r in conn.execute(text(
                "SELECT board_code, total_score, close FROM eval_board_trend_score "
                "WHERE record_date=:d"), {'d': ref}).fetchall()}

        emerging, sustained, fading = [], [], []
        for code, r in cur.items():
            name, score, stage, strength, signal, volscore, chg, close = (
                r[1], r[2] or 0.0, r[3], r[4], r[5], r[6] or 0.0, r[7], r[8])
            p = prev.get(code)
            delta = (score - (p[0] or 0.0)) if p else 0.0
            ret = ((close / p[1] - 1) * 100) if (p and p[1]) else None
            up = stage in ('up_early', 'up_late')
            item = {
                'board_code': code, 'board_name': name,
                'total_score': round(score, 1), 'score_delta': round(delta, 1),
                'ret_pct': (round(ret, 2) if ret is not None else None),
                'trend_stage': stage, 'trend_strength': strength, 'signal': signal,
                'volume_score': round(volscore, 0), 'change_pct': chg,
            }
            if p and delta <= -5 and (p[0] or 0) >= 55:
                fading.append(item)
            elif delta >= 5 and score < 78 and up:
                emerging.append(item)
            elif score >= 70 and up and delta > -3:
                sustained.append(item)

        emerging.sort(key=lambda x: -x['score_delta'])
        sustained.sort(key=lambda x: -x['total_score'])
        fading.sort(key=lambda x: x['score_delta'])
        return jsonify({'success': True, 'as_of': str(latest), 'ref_date': str(ref),
                        'emerging': emerging[:20], 'sustained': sustained[:20],
                        'fading': fading[:20]})
    except Exception as e:
        logger.exception('近期热点板块聚合失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/sync_sector_flow', methods=['POST'])
def api_sync_sector_flow():
    """抓取东财 行业+概念 板块的资金流/热度快照，写入 mkt_sector_flow_daily。"""
    from App.services.sector_flow_service import sync_sector_flow
    app = current_app._get_current_object()
    try:
        res = sync_sector_flow(app)
        ok = not res.get('errors')
        return jsonify({'success': ok, 'data': res}), (200 if ok else 207)
    except Exception as e:
        logger.exception('板块资金流同步失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/hot_concepts')
def api_hot_concepts():
    """近期热点主题：从 mkt_sector_flow_daily 最新快照，给概念涨幅榜/概念资金榜/行业涨幅榜。"""
    from App.models.strategy.SectorFlowDaily import SectorFlowDaily
    try:
        SectorFlowDaily.ensure_table()
        latest = db.session.query(db.func.max(SectorFlowDaily.date)).scalar()
        if latest is None:
            return jsonify({'success': True, 'as_of': None,
                            'concept_up': [], 'concept_flow': [], 'industry_up': []})

        def top(board_type, order_col, limit=15):
            rows = (SectorFlowDaily.query
                    .filter(SectorFlowDaily.date == latest,
                            SectorFlowDaily.board_type == board_type)
                    .order_by(order_col.desc()).limit(limit).all())
            return [{
                'board_code': r.board_code, 'board_name': r.board_name,
                'change_pct': r.change_pct, 'main_net': r.main_net, 'main_pct': r.main_pct,
                'turnover_rate': r.turnover_rate, 'up_count': r.up_count,
                'down_count': r.down_count, 'lead_stock': r.lead_stock, 'lead_pct': r.lead_pct,
            } for r in rows]

        return jsonify({'success': True, 'as_of': str(latest),
                        'concept_up': top('concept', SectorFlowDaily.change_pct),
                        'concept_flow': top('concept', SectorFlowDaily.main_net),
                        'industry_up': top('industry', SectorFlowDaily.change_pct)})
    except Exception as e:
        logger.exception('热点概念读取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/board')
def board_detail_page():
    """单板块详情：日 K / 15m 图 + 成分股 + 历次打分时间线。
    板块代码用查询参数传入：/board_data/board?code=BK0478
    数据源用 source 区分：em=东方财富(默认) / syn=我的合成(读 BKxxxxS)。
    """
    code = request.args.get('code', '')
    source = (request.args.get('source') or 'em').strip().lower()
    if source not in ('em', 'syn'):
        source = 'em'
    return render_template('strategy/board_detail.html', board_code=code, init_source=source)


@board_data_bp.route('/members')
def board_members_page():
    """板块成分股管理页：列出该板块成分股，可按趋势/信号/是否在池筛选，
    多选一键加入股票池。代码用查询参数：/board_data/members?code=BK1033
    可选 date=YYYY-MM-DD：带此参数则直接进入「四象分布图」标签并定位到该历史日快照。
    """
    code = request.args.get('code', '')
    init_qdate = (request.args.get('date') or '').strip()
    return render_template('strategy/board_members.html', board_code=code, init_qdate=init_qdate)


def _parse_tdx_days(val, default=90):
    """解析『全量回溯天数』——仅对本地完全无 1m 数据的股票生效。缺省/非法→default；夹取 [1, 365]。
    注意：已有数据的股票一律按各自「本地最后日期→市场最新日」的缺口增量补齐，不受此值影响。"""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(1, min(365, n))


@board_data_bp.route('/api/repair_members', methods=['POST'])
def api_repair_members():
    """后台修复某板块全部成分股的 1m 数据（本地 zip + TDX 增量 + 重生成 15m）。
    增量策略：逐股读本地 1m 最后日期，只下「该日→市场最新交易日」的缺口，已最新则跳过。
    body{tdx_days:int} 仅作『本地无数据股票』的全量回溯上限（默认90），不影响已有数据的增量补齐。"""
    from App.services.minute_data_repair import start_repair, DEFAULT_ZIP_DIR
    data = request.get_json(silent=True) or {}
    code = (data.get('board_code') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '缺少 board_code'}), 400
    codes = _board_member_codes(code)
    tdx_days = _parse_tdx_days(data.get('tdx_days'))
    app = current_app._get_current_object()
    ok, msg = start_repair(app, code, codes, zip_dir=(data.get('zip_dir') or DEFAULT_ZIP_DIR),
                           tdx_days=tdx_days, then_trend=bool(data.get('with_trend')))
    return jsonify({'success': ok, 'message': msg, 'count': len(codes), 'tdx_days': tdx_days}), (200 if ok else 409)


@board_data_bp.route('/api/repair_status')
def api_repair_status():
    from App.services.minute_data_repair import get_status
    return jsonify({'success': True, 'data': get_status()})


@board_data_bp.route('/api/repair_all', methods=['POST'])
def api_repair_all():
    """一键修复全部板块成分股（去重）。先做「便宜的」上层预筛(纯SQL/文件名，不读parquet)：
    · 已最新(日K已到市场最新交易日) → 跳过；
    · 从未下载(本地无 15m 也无日K) → 默认跳过(repair 只修已有数据)，body{include_new:true} 才纳入；
    · 其余(有数据但落后) → 进入修复。可暂停/继续。"""
    from pathlib import Path
    from config import Config
    from sqlalchemy import bindparam
    from App.services.minute_data_repair import start_repair
    data = request.get_json(silent=True) or {}
    include_new = bool(data.get('include_new'))

    eng = db.engines['quanttradingsystem']
    # 遍历板块：每个板块最新名单的 (板块码, 板块名, 成分股)，按板块→代码排序
    with eng.connect() as conn:
        board_rows = conn.execute(text(
            """
            SELECT ie.board_code, ie.board_name, ie.stock_code FROM industry_eastmoney ie
            JOIN (SELECT board_code, MAX(date) d FROM industry_eastmoney GROUP BY board_code) m
              ON ie.board_code = m.board_code AND ie.date = m.d
            WHERE ie.stock_code NOT LIKE 'BK%'
            ORDER BY ie.board_code, ie.stock_code
            """)).fetchall()
        codes = list({r[2] for r in board_rows})
        mkt = conn.execute(text(
            "SELECT MAX(date) FROM data_stock_daily WHERE stock_code NOT LIKE 'BK%'")).scalar()
        latest = ({r[0]: r[1] for r in conn.execute(text(
            'SELECT stock_code, MAX(date) FROM data_stock_daily WHERE stock_code IN :codes '
            'GROUP BY stock_code').bindparams(bindparam('codes', expanding=True)),
            {'codes': codes}).fetchall()} if codes else {})
    have15 = {p.stem for p in (Path(Config.get_project_root()) / 'data' / '15m').glob('*.parquet')}

    def _has_data(c):
        return c in have15 or latest.get(c) is not None

    def _is_current(c):
        return mkt is not None and latest.get(c) == mkt

    skipped_current = sum(1 for c in codes if _is_current(c))
    new_available = sum(1 for c in codes if not _has_data(c))   # 从未下载的数量

    # 一个板块一个板块地归集候选：每只候选股票归到它出现的第一个板块（不重复修）
    assigned = set()
    ordered, code2board = [], {}
    for bcode, bname, code in board_rows:
        if code in assigned or _is_current(code) or not (_has_data(code) or include_new):
            continue
        assigned.add(code)
        ordered.append(code)
        code2board[code] = f'{bname or bcode}'
    board_total = len(set(code2board.values()))

    # 预览：只返回数量，不启动修复
    if data.get('preview'):
        return jsonify({'success': True, 'preview': True, 'count': len(ordered),
                        'total_members': len(codes), 'skipped_current': skipped_current,
                        'new_available': new_available, 'board_total': board_total})

    tdx_days = _parse_tdx_days(data.get('tdx_days'))
    app = current_app._get_current_object()
    ok, msg = start_repair(app, '全部板块', ordered, tdx_days=tdx_days,
                           then_trend=bool(data.get('with_trend')),
                           board_map=code2board, board_total=board_total)
    return jsonify({
        'success': ok, 'message': msg, 'count': len(ordered), 'board_total': board_total,
        'total_members': len(codes), 'skipped_current': skipped_current,
        'skipped_new': (0 if include_new else new_available), 'tdx_days': tdx_days,
    }), (200 if ok else 409)


@board_data_bp.route('/api/repair_pause', methods=['POST'])
def api_repair_pause():
    """暂停/继续当前修复任务。body{action:'pause'|'resume'|'toggle'(默认)}"""
    from App.services.minute_data_repair import pause_repair, resume_repair, get_status
    action = (request.get_json(silent=True) or {}).get('action', 'toggle')
    st = get_status()
    if action == 'resume' or (action == 'toggle' and st.get('paused')):
        resume_repair()
    else:
        pause_repair()
    return jsonify({'success': True, 'data': get_status()})


@board_data_bp.route('/api/build_synth', methods=['POST'])
def api_build_synth():
    """用成分股(重新)合成「我的板块」指数（写到合成代码 BKxxxxS，不覆盖东财数据）。"""
    from App.services.board_synth_service import start_build
    data = request.get_json(silent=True) or {}
    code = (data.get('board_code') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '缺少 board_code'}), 400
    weighting = (data.get('weighting') or 'circ').strip()   # circ/total/equal
    tfs = data.get('timeframes') or ['daily', '15m']
    app = current_app._get_current_object()
    ok, msg = start_build(app, code, weighting=weighting, timeframes=tfs)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 409)


@board_data_bp.route('/api/build_synth_status')
def api_build_synth_status():
    from App.services.board_synth_service import get_status
    return jsonify({'success': True, 'data': get_status()})


@board_data_bp.route('/api/board/<code>/fill_dist', methods=['POST'])
def api_fill_dist(code):
    """补齐/更新成分股分布快照。body{date:YYYY-MM-DD} 指定日期(默认今天，历史日则 as-of 重算)；
    默认只补「该日无快照」的成分股；body{all:true} 则全部重算。"""
    from datetime import datetime as _dt, date as _date
    from App.services.minute_data_repair import start_fill_dist
    from App.models.strategy.StockDistSnapshot import StockDistSnapshot
    data = request.get_json(silent=True) or {}
    codes = _board_member_codes(code)
    ds = (data.get('date') or '').strip()
    if ds:
        try:
            target_date = _dt.strptime(ds, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': '日期格式错误'}), 400
    else:
        target_date = _date.today()
    if data.get('all'):
        targets = codes
    else:
        # 缺该「具体日期」快照的成分股（非 as-of，要正好这天）
        have = {row[0] for row in db.session.query(StockDistSnapshot.stock_code)
                .filter(StockDistSnapshot.stock_code.in_(codes),
                        StockDistSnapshot.merge_below == 0,
                        StockDistSnapshot.snapshot_date == target_date).distinct().all()}
        targets = [c for c in codes if c not in have]
    app = current_app._get_current_object()
    ok, msg = start_fill_dist(app, code, targets, snapshot_date=target_date)
    return jsonify({'success': ok, 'message': msg, 'count': len(targets)}), (200 if ok else 409)


@board_data_bp.route('/api/board/<code>/fill_dist_status')
def api_fill_dist_status(code):
    from App.services.minute_data_repair import get_fill_status
    return jsonify({'success': True, 'data': get_fill_status()})


@board_data_bp.route('/api/board/<code>/quad')
def api_board_quad(code):
    """四象分布图数据：每只成分股 as-of 指定日期的 15m 分布快照（振幅/长度分位 + 方向）。
    ?date=YYYY-MM-DD 取「≤该日的最新快照」；不传取最新。附带可选日期范围 min/max。"""
    from datetime import datetime as _dt
    from App.models.strategy.StockDistSnapshot import StockDistSnapshot
    from App.models.data.basic_info import StockInfo
    codes = _board_member_codes(code)
    if not codes:
        return jsonify({'success': True, 'data': {'rows': [], 'date': None, 'min_date': None, 'max_date': None}})

    rng = (db.session.query(db.func.min(StockDistSnapshot.snapshot_date),
                            db.func.max(StockDistSnapshot.snapshot_date))
           .filter(StockDistSnapshot.stock_code.in_(codes),
                   StockDistSnapshot.merge_below == 0).first())
    min_date, max_date = (rng[0], rng[1]) if rng else (None, None)

    ds = (request.args.get('date') or '').strip()
    if ds:
        try:
            target = _dt.strptime(ds, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': '日期格式错误'}), 400
    else:
        target = max_date
    if target is None:
        return jsonify({'success': True, 'data': {'rows': [], 'date': None,
                        'min_date': None, 'max_date': None}})

    # 每只成分股 ≤target 的最新一条快照（as-of，兼容周末/缺日）
    snap_sub = (db.session.query(
                    StockDistSnapshot.stock_code,
                    db.func.max(StockDistSnapshot.snapshot_date).label('md'))
                .filter(StockDistSnapshot.stock_code.in_(codes),
                        StockDistSnapshot.merge_below == 0,
                        StockDistSnapshot.snapshot_date <= target)
                .group_by(StockDistSnapshot.stock_code).subquery())
    snaps = (db.session.query(StockDistSnapshot)
             .join(snap_sub, db.and_(
                 StockDistSnapshot.stock_code == snap_sub.c.stock_code,
                 StockDistSnapshot.snapshot_date == snap_sub.c.md))
             .filter(StockDistSnapshot.merge_below == 0).all())

    name_map = {r.code: r.name for r in StockInfo.query
                .filter(StockInfo.code.in_(codes))
                .with_entities(StockInfo.code, StockInfo.name).all() if r.name}
    rows = []
    for s in snaps:
        up = s.current_direction == 1
        dn = s.current_direction == -1
        rows.append({
            'stock_code': s.stock_code,
            'stock_name': name_map.get(s.stock_code, s.stock_code),
            'direction': s.current_direction,
            'amp_pct': (s.amp_up_pct if up else s.amp_dn_pct if dn else None),
            'len_pct': (s.len_up_pct if up else s.len_dn_pct if dn else None),
            'signal_name': s.current_signal_name,
            'snapshot_date': s.snapshot_date.isoformat() if s.snapshot_date else None,
            # 该日无快照、用了更早的近似值（as-of 回退）→ 标记为陈旧，前端提示可「补齐这天」
            'stale': bool(s.snapshot_date and s.snapshot_date != target),
        })
    return jsonify({'success': True, 'data': {
        'rows': rows, 'date': target.isoformat(),
        'min_date': min_date.isoformat() if min_date else None,
        'max_date': max_date.isoformat() if max_date else None,
    }})


@board_data_bp.route('/api/synth_exists')
def api_synth_exists():
    """该板块是否已有合成数据（供前端切「我的合成」前判断，未合成则引导先合成）。"""
    from App.services.board_synth_service import synth_exists
    code = (request.args.get('board_code') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '缺少 board_code'}), 400
    return jsonify({'success': True, 'data': synth_exists(code)})


def _board_member_codes(code):
    """取某板块最新成分股的 6 位个股代码。"""
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT ie.stock_code FROM industry_eastmoney ie
            INNER JOIN (SELECT board_code, MAX(date) d FROM industry_eastmoney
                        WHERE board_code = :c GROUP BY board_code) m
              ON m.board_code = ie.board_code AND m.d = ie.date
            WHERE ie.board_code = :c
            """
        ), {'c': code}).fetchall()
    return [r[0] for r in rows if r[0] and re.fullmatch(r'\d{6}', str(r[0]))]


@board_data_bp.route('/api/recompute_trend', methods=['POST'])
def api_recompute_trend():
    """后台重算某板块全部成分股的 个股趋势打分 + 分布快照（数据修复后用）。"""
    from App.services.minute_data_repair import start_trend_recompute
    data = request.get_json(silent=True) or {}
    code = (data.get('board_code') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '缺少 board_code'}), 400
    codes = _board_member_codes(code)
    app = current_app._get_current_object()
    ok, msg = start_trend_recompute(app, code, codes)
    return jsonify({'success': ok, 'message': msg, 'count': len(codes)}), (200 if ok else 409)


@board_data_bp.route('/api/recompute_trend_status')
def api_recompute_trend_status():
    from App.services.minute_data_repair import get_trend_status
    return jsonify({'success': True, 'data': get_trend_status()})


def _mkt_prefix(code):
    """股票代码 → 市场前缀(与 data_stock_info 口径一致)：sh/sz/bj。"""
    if code.startswith('920') or code.startswith(('4', '8')):
        return 'bj'
    if code.startswith(('6', '5', '9')):
        return 'sh'
    return 'sz'


@board_data_bp.route('/api/add_to_download', methods=['POST'])
def api_add_to_download():
    """把选中成分股加入每日收盘下载表单(data_download_records)。

    body: { codes: [...] }
    不在 data_stock_info 的会先自动登记(id=max+1, 名取自板块成分股名单)；
    已在下载表单的跳过。返回 added/skipped/registered。
    """
    from App.models.data.basic_info import StockInfo
    from App.models.data.Stock1m import DownloadRecord
    data = request.get_json(silent=True) or {}
    codes = [str(c).strip() for c in (data.get('codes') or []) if str(c).strip()]
    if not codes:
        return jsonify({'success': False, 'message': '未选择股票'}), 400
    # 名字兜底：板块成分股名单
    name_map = {}
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        for c in codes:
            r = conn.execute(text(
                "SELECT stock_name FROM industry_eastmoney WHERE stock_code=:c "
                "AND stock_name IS NOT NULL AND stock_name<>'' ORDER BY date DESC LIMIT 1"),
                {'c': c}).fetchone()
            if r and r[0]:
                name_map[c] = r[0]
    added = skipped = registered = 0
    try:
        next_id = db.session.query(db.func.max(StockInfo.id)).scalar() or 1000000
        for code in codes:
            s = StockInfo.find_stock(code) or StockInfo.query.filter_by(code=code).first()
            if not s:
                next_id += 1
                pfx = _mkt_prefix(code)
                s = StockInfo(id=next_id, code=code, name=name_map.get(code),
                              EsCode=code, MarketCode=f'{pfx}{code}', TxdMarket=pfx, HsMarket=pfx)
                db.session.add(s)
                db.session.flush()
                registered += 1
            if DownloadRecord.query.filter_by(stock_code_id=s.id).first():
                skipped += 1
                continue
            db.session.add(DownloadRecord(
                stock_code_id=s.id, download_status='pending', download_progress=0.0,
                end_date=date.today(), record_date=date.today(),
                total_records=0, downloaded_records=0))
            added += 1
        db.session.commit()
        return jsonify({'success': True, 'added': added, 'skipped': skipped,
                        'registered': registered, 'total': len(codes)})
    except Exception as e:
        db.session.rollback()
        logger.exception('加入下载表单失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ===================== 主表 CRUD =====================
@board_data_bp.route('/api/search')
def api_search():
    """板块输入框联想：按 q 模糊匹配 board_code 或 board_name（任一命中），返回前 limit 个。"""
    try:
        Board.ensure_table()
        term = (request.args.get('q') or '').strip()
        try:
            limit = max(1, min(int(request.args.get('limit', 12)), 50))
        except ValueError:
            limit = 12
        if not term:
            return jsonify({'success': True, 'data': []})
        like = f'%{term}%'
        rows = (Board.query
                .filter(db.or_(Board.board_code.like(like), Board.board_name.like(like)))
                .order_by(Board.board_code.asc())
                .limit(limit).all())
        return jsonify({'success': True, 'data': [
            {'board_code': r.board_code, 'board_name': r.board_name} for r in rows]})
    except Exception as e:
        logger.exception('板块搜索失败')
        return jsonify({'success': False, 'message': str(e), 'data': []}), 500


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


_SUB_KEYS = ['price_structure_score', 'ma_score', 'macd_score',
             'volume_score', 'momentum_score', 'volatility_score']


def _score_history(df_tf, n=30):
    """对一个周期的 K 线逐根打分（用截至该根的窗口现算），返回最近 n 根评分行（新→旧）。

    每根需 ≥20 根历史才出分；计算量 = min(n, 可算根数) 次 v1 打分，轻量。
    """
    from App.services.board_trend_score_service import score_dataframe
    if df_tf is None or df_tf.empty:
        return []
    total = len(df_tf)
    start = max(20, total - n)
    rows = []
    for i in range(start, total):
        sc = score_dataframe(df_tf.iloc[:i + 1])
        d = df_tf['date'].iloc[i]
        rows.append({
            'date': d.strftime('%Y-%m-%d %H:%M') if hasattr(d, 'strftime') else str(d),
            'trend_stage': sc['trend_stage'],
            'trend_stage_confidence': sc['trend_stage_confidence'],
            'trend_strength': sc['trend_strength'],
            'signal': sc['signal'],
            'total_score': sc['total_score'],
            **{k: (sc['sub_scores'] or {}).get(k) for k in _SUB_KEYS},
        })
    rows.reverse()  # 新 → 旧
    return rows


def _resample_15m_to_60m(df15):
    """15m DataFrame → 60m(1小时)：按交易日内顺序每 4 根合并（首open/末close/极值/量和）。

    A股每日 16 根 15m（上午8 + 下午8），每 4 根正好一根 1 小时K，且不跨午休
    （上午/下午各 8 根均可被 4 整除），所以日内顺序 4 根一并即标准 1 小时K。
    需含 date/open/close/high/low/volume；返回同结构 60m DataFrame（空则空表）。
    """
    if df15 is None or df15.empty:
        return pd.DataFrame()
    df = df15.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = (df.dropna(subset=['open', 'close', 'high', 'low'])
            .sort_values('date').reset_index(drop=True))
    df['volume'] = pd.to_numeric(df.get('volume'), errors='coerce').fillna(0)
    df['d'] = df['date'].dt.normalize()
    rows = []
    for _, g in df.groupby('d'):
        g = g.reset_index(drop=True)
        for i in range(0, len(g), 4):
            ck = g.iloc[i:i + 4]
            rows.append({
                'date': ck['date'].iloc[-1],
                'open': float(ck['open'].iloc[0]),
                'close': float(ck['close'].iloc[-1]),
                'high': float(ck['high'].max()),
                'low': float(ck['low'].min()),
                'volume': float(ck['volume'].sum()),
            })
    return pd.DataFrame(rows)


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


@board_data_bp.route('/api/board/<code>/chart_60m')
def api_board_chart_60m(code):
    """返回单板块的 1 小时（60 分钟）K 线 + MA20/MA60 + MACD + 量能。

    无独立 60m 数据源：由 15m parquet 按"交易日内顺序每 4 根合并"合成
    （open=首根、close=末根、high/low=极值、volume=求和），MACD 在 60m 序列上重算。
    query:
      - bars=48                           按根数取末尾 N 根（默认，60m 每日 4 根 ≈ 12 日）
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

        # 15m → 60m：按交易日内顺序每 4 根合并
        d60 = _resample_15m_to_60m(df)
        if d60.empty:
            return jsonify({'success': False, 'message': '60m 合成后无数据'}), 404

        close = d60['close']
        d60['ma20'] = close.rolling(20, min_periods=1).mean()
        d60['ma60'] = close.rolling(60, min_periods=1).mean()
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        d60['dif'] = ema12 - ema26
        d60['dea'] = _ema(d60['dif'], 9)
        d60['bar'] = (d60['dif'] - d60['dea']) * 2

        if use_range:
            try:
                start_ts = pd.to_datetime(start_date_s)
                end_ts = pd.to_datetime(end_date_s) + pd.Timedelta(hours=23, minutes=59, seconds=59)
            except Exception:
                return jsonify({'success': False, 'message': '日期格式错误'}), 400
            if start_ts > end_ts:
                return jsonify({'success': False, 'message': '起始日期不能晚于结束日期'}), 400
            d60 = d60[(d60['date'] >= start_ts) & (d60['date'] <= end_ts)].reset_index(drop=True)
            if d60.empty:
                return jsonify({'success': False,
                                'message': f'区间 {start_date_s} ~ {end_date_s} 内无 60m 数据'}), 404
        else:
            bars = max(12, min(int(request.args.get('bars', 120)), 3000))
            d60 = d60.tail(bars).reset_index(drop=True)

        def _r(v, n=4):
            try:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return None
                return round(float(v), n)
            except Exception:
                return None

        rows = []
        for _, r in d60.iterrows():
            rows.append({
                'date': r['date'].strftime('%Y-%m-%d %H:%M'),
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
        logger.exception('板块 60m 图表数据获取失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/board/<code>/multiscore')
def api_board_multiscore(code):
    """多周期评分：对 日K / 60m / 15m 三个周期分别用同一套 v1 公式打分。

    日K 取自 data_stock_daily（_load_board_daily）；60m/15m 由 15m parquet 现算。
    供详情页「日K评分 / 60分K评分 / 15分K评分 / 评分汇总」标签使用。
    """
    try:
        from datetime import date as _date
        from App.services.board_trend_score_service import (
            score_dataframe, _load_board_daily, combine_mtf)
        from App.routes.data.viewer_15m_route import _get_15m_dir, _find_15m_file, _read_15m_file

        # 日K
        daily_df = _load_board_daily(code, _date.today())
        daily = score_dataframe(daily_df)

        # 15m / 60m（来自 parquet）
        m15 = {'sub_scores': {}, 'total_score': None, 'trend_stage': 'unknown',
               'trend_stage_confidence': 0.0, 'trend_strength': 'none',
               'signal': 'none', 'snapshot': {}, 'bars': 0, 'error': '无 15m 数据文件'}
        m60 = dict(m15)
        m60_history, m15_history = [], []
        fpath = _find_15m_file(_get_15m_dir(), code)
        if fpath:
            df15 = _read_15m_file(fpath)
            if not df15.empty:
                cols = ['date', 'open', 'close', 'high', 'low', 'volume']
                base = df15[[c for c in cols if c in df15.columns]].copy()
                base['date'] = pd.to_datetime(base['date'])
                base = (base.dropna(subset=['open', 'close', 'high', 'low'])
                            .sort_values('date').reset_index(drop=True))
                m15 = score_dataframe(base)
                m15_history = _score_history(base, 30)
                d60 = _resample_15m_to_60m(base)
                if not d60.empty:
                    m60 = score_dataframe(d60)
                    m60_history = _score_history(d60, 30)

        # 多周期综合（日K主导 + 1h/15m 置信度）：复用与批量打分同一套合成逻辑
        m60_avail = m60 if m60.get('total_score') is not None else None
        m15_avail = m15 if m15.get('total_score') is not None else None
        composite = combine_mtf(daily, m60_avail, m15_avail)

        return jsonify({'success': True, 'data': {
            'board_code': code,
            'daily': daily, 'm60': m60, 'm15': m15,
            'm60_history': m60_history, 'm15_history': m15_history,
            'composite': {
                'total_score': composite['composite_total'],
                'trend_stage': composite['trend_stage'],
                'trend_stage_confidence': composite['trend_stage_confidence'],
                'trend_strength': composite['trend_strength'],
                'signal': composite['signal'],
                'alignment': composite['alignment'],
                'breakdown': composite['breakdown'],
            },
        }})
    except Exception as e:
        logger.exception('板块多周期评分失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ===================== 持仓跟踪（纳入持仓盘中列表）=====================
@board_data_bp.route('/api/board/<code>/track_status')
def api_board_track_status(code):
    """查询该板块是否已纳入持仓跟踪，及已登记的 ETF 信息。"""
    try:
        from App.models.evaluation.BoardHolding import BoardHolding
        BoardHolding.ensure_table()
        row = BoardHolding.query.filter_by(board_code=code).first()
        return jsonify({'success': True, 'data': {
            'tracked': row is not None,
            'info': row.to_dict() if row else None,
        }})
    except Exception as e:
        logger.exception('查询板块跟踪状态失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/board/<code>/track', methods=['POST'])
def api_board_track(code):
    """纳入持仓跟踪。body: {board_name?, etf_code?, etf_name?, note?}（均可空）。"""
    try:
        from App.models.evaluation.BoardHolding import BoardHolding
        from App.models.evaluation.Board import Board
        BoardHolding.ensure_table()
        data = request.get_json(silent=True) or {}

        board_name = (data.get('board_name') or '').strip()
        if not board_name:
            # 没传名称就从主表补一个
            b = Board.query.filter_by(board_code=code).first()
            board_name = b.board_name if b else code

        row = BoardHolding.upsert(
            board_code=code,
            board_name=board_name,
            etf_code=(data.get('etf_code') or ''),
            etf_name=(data.get('etf_name') or ''),
            note=(data.get('note') or ''),
        )
        return jsonify({'success': True, 'data': row.to_dict()})
    except Exception as e:
        db.session.rollback()
        logger.exception('纳入板块持仓跟踪失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_data_bp.route('/api/board/<code>/untrack', methods=['POST'])
def api_board_untrack(code):
    """取消持仓跟踪。"""
    try:
        from App.models.evaluation.BoardHolding import BoardHolding
        BoardHolding.ensure_table()
        ok = BoardHolding.delete_by_code(code)
        return jsonify({'success': True, 'data': {'removed': ok}})
    except Exception as e:
        db.session.rollback()
        logger.exception('取消板块持仓跟踪失败')
        return jsonify({'success': False, 'message': str(e)}), 500


def _stage_from_snapshot(snap):
    """从 stock_dist_snapshot 派生「15m 趋势阶段」：
    方向取 current_direction（1上/-1下/0或None=盘整未知）；
    早/末期取当前振幅在历史分布的分位——amp_pct ≥ 0.5（超过历史中位）视为末期，否则初期。
    复用页面既有的 up_early/up_late/down_early/down_late 标签。
    """
    if snap is None or not snap.current_direction:
        return 'unknown'
    is_up = snap.current_direction == 1
    pct = snap.amp_up_pct if is_up else snap.amp_dn_pct
    late = (pct is not None and pct >= 0.5)
    if is_up:
        return 'up_late' if late else 'up_early'
    return 'down_late' if late else 'down_early'


@board_data_bp.route('/api/board/<code>/stocks')
def api_board_stocks(code):
    """返回板块成分股清单（来源 industry_eastmoney 最新日期）。
    趋势阶段列取 15m 口径（stock_dist_snapshot 当日快照）；信号/趋势分仍为日K综合(StockTrendScore)。"""
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
            # 概念板块等不在 industry_eastmoney → 实时从东财拉成分（clist fs=b:BKxxxx，对任意板块码有效）
            try:
                from App.services.board_data_service import em_industry_cons_via_http
                live = em_industry_cons_via_http(code, retries=3)
            except Exception:
                logger.exception(f'板块 {code} 实时拉成分失败')
                live = []
            if live:
                bname = code
                try:
                    from App.models.strategy.SectorFlowDaily import SectorFlowDaily
                    sf = (SectorFlowDaily.query.filter(SectorFlowDaily.board_code == code)
                          .order_by(SectorFlowDaily.date.desc()).first())
                    if sf and sf.board_name:
                        bname = sf.board_name
                except Exception:
                    pass
                # 拼成与 SQL 行同结构: (stock_code, stock_name, board_name, member_date, total_cap, circ_cap)
                rows = [(m['stock_code'], m['stock_name'], bname, None,
                         m.get('total_cap'), m.get('circ_cap')) for m in live]

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

        # 15m 当前趋势：取每只成分股最新一条分布快照（merge_below=0），供「趋势阶段」列(15m口径)
        from App.models.strategy.StockDistSnapshot import StockDistSnapshot
        snap_map = {}
        try:
            snap_sub = (db.session.query(
                    StockDistSnapshot.stock_code,
                    db.func.max(StockDistSnapshot.snapshot_date).label('md'))
                .filter(StockDistSnapshot.stock_code.in_(stock_codes),
                        StockDistSnapshot.merge_below == 0)
                .group_by(StockDistSnapshot.stock_code).subquery())
            snaps = (db.session.query(StockDistSnapshot)
                .join(snap_sub, db.and_(
                    StockDistSnapshot.stock_code == snap_sub.c.stock_code,
                    StockDistSnapshot.snapshot_date == snap_sub.c.md))
                .filter(StockDistSnapshot.merge_below == 0).all())
            snap_map = {s.stock_code: s for s in snaps}
        except Exception:
            logger.exception('板块成分股取 15m 分布快照失败')

        # 实时市值拉取较慢(东财 HTTP，~10s+)。默认拉实时(保持板块详情页原行为)；
        # ?live=0 走快照市值(秒开)，成分股管理页用这个。
        cap_source = 'snapshot'
        live_cap = {}
        if (request.args.get('live') or '1') == '1':
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
            snap = snap_map.get(r[0])
            live = live_cap.get(r[0])
            total_cap = live['total_cap'] if live and live.get('total_cap') is not None else r[4]
            circ_cap = live['circ_cap'] if live and live.get('circ_cap') is not None else r[5]
            # 按当前方向取对应的振幅/长度分位（供四象分布图；无快照则 None）
            _up15 = bool(snap and snap.current_direction == 1)
            _dn15 = bool(snap and snap.current_direction == -1)
            amp_pct = (snap.amp_up_pct if _up15 else snap.amp_dn_pct if _dn15 else None) if snap else None
            len_pct = (snap.len_up_pct if _up15 else snap.len_dn_pct if _dn15 else None) if snap else None
            out.append({
                'stock_code': r[0],
                'stock_name': r[1],
                # 趋势阶段：15m 口径（stock_dist_snapshot 当日快照）
                'trend_stage': _stage_from_snapshot(snap),
                'trend_dir_15m': snap.current_direction if snap else None,
                'trend_signal_name': snap.current_signal_name if snap else None,
                'trend_snapshot_date': snap.snapshot_date.isoformat() if snap and snap.snapshot_date else None,
                'amp_pct': amp_pct,   # 当前振幅分位 0~1（当前方向）
                'len_pct': len_pct,   # 当前长度分位 0~1（当前方向）
                'trend_strength': sc.trend_strength if sc else None,
                'signal': sc.signal if sc else None,           # 日K综合信号(StockTrendScore)
                'total_score': sc.total_score if sc else None,  # 日K综合分
                'score_date': sc.record_date.isoformat() if sc and sc.record_date else None,
                'total_cap': total_cap,
                'circ_cap': circ_cap,
            })

        # 市值兜底：本板块快照里 cap 为空（旧名单从未刷过市值）时，
        # 回退到该股「跨所有板块最新一条非空市值」——与个股详情页同口径。
        missing = [o['stock_code'] for o in out if o['total_cap'] is None]
        if missing:
            try:
                from sqlalchemy import bindparam
                with eng.connect() as conn:
                    fb_rows = conn.execute(
                        text(
                            '''
                            SELECT ie.stock_code, ie.total_cap, ie.circ_cap, ie.date
                              FROM industry_eastmoney ie
                              JOIN (SELECT stock_code, MAX(date) AS mx
                                      FROM industry_eastmoney
                                     WHERE stock_code IN :codes AND total_cap IS NOT NULL
                                     GROUP BY stock_code) m
                                ON m.stock_code = ie.stock_code AND m.mx = ie.date
                             WHERE ie.total_cap IS NOT NULL
                            '''
                        ).bindparams(bindparam('codes', expanding=True)),
                        {'codes': missing},
                    ).fetchall()
                fb = {}
                for fr in fb_rows:
                    fb.setdefault(fr[0], fr)   # 同一最新日期可能多板块，取其一
                for o in out:
                    if o['total_cap'] is None and o['stock_code'] in fb:
                        fr = fb[o['stock_code']]
                        o['total_cap'] = float(fr[1]) if fr[1] is not None else None
                        o['circ_cap'] = float(fr[2]) if fr[2] is not None else None
                        o['cap_fallback'] = fr[3].isoformat() if fr[3] else True
                if cap_source == 'snapshot' and any(o.get('cap_fallback') for o in out):
                    cap_source = 'snapshot+fallback'
            except Exception:
                logger.exception('板块成分股市值跨板块兜底失败')

        # 附加：是否在我的股票池(活跃) + 其状态标签
        try:
            from App.models.strategy.StockPool import StockPool
            pool = {p.stock_code: p.get_states() for p in
                    StockPool.query.filter(StockPool.stock_code.in_(stock_codes),
                                           StockPool.is_active == True).all()}
            for o in out:
                st = pool.get(o['stock_code'])
                o['in_pool'] = st is not None
                o['pool_states'] = st or []
        except Exception:
            logger.exception('板块成分股取股票池状态失败')
            for o in out:
                o.setdefault('in_pool', False)
                o.setdefault('pool_states', [])

        # 附加：是否在每日收盘下载表单(data_download_records)
        try:
            from App.models.data.basic_info import StockInfo
            from App.models.data.Stock1m import DownloadRecord
            id_by_code = {}
            for r in (StockInfo.query.filter(StockInfo.code.in_(stock_codes))
                      .with_entities(StockInfo.id, StockInfo.code).all()):
                id_by_code.setdefault(r.code, r.id)
            ids = list(id_by_code.values())
            dl_ids = ({r.stock_code_id for r in
                       DownloadRecord.query.filter(DownloadRecord.stock_code_id.in_(ids))
                       .with_entities(DownloadRecord.stock_code_id).all()} if ids else set())
            for o in out:
                o['in_download'] = id_by_code.get(o['stock_code']) in dl_ids
        except Exception:
            logger.exception('板块成分股取下载表单状态失败')
            for o in out:
                o.setdefault('in_download', False)

        # 附加：1m 数据开始/更新到哪天——优先读 data_download_records 账本(start_date/end_date)，
        # 快且是全局唯一口径；账本无记录的极少数股票，再回退扫 quarters parquet 兜底。
        try:
            from App.models.data.basic_info import StockInfo
            from App.models.data.Stock1m import DownloadRecord
            id2code = {}
            for r in (StockInfo.query.filter(StockInfo.code.in_(stock_codes))
                      .with_entities(StockInfo.id, StockInfo.code).all()):
                id2code[r.id] = r.code
            rec_start, rec_end = {}, {}
            if id2code:
                for rec in (DownloadRecord.query
                            .filter(DownloadRecord.stock_code_id.in_(list(id2code.keys())))
                            .with_entities(DownloadRecord.stock_code_id,
                                           DownloadRecord.start_date, DownloadRecord.end_date).all()):
                    c = id2code.get(rec[0])
                    if not c:
                        continue
                    if rec[1] is not None:
                        rec_start[c] = rec[1].strftime('%Y-%m-%d')
                    if rec[2] is not None:
                        rec_end[c] = rec[2].strftime('%Y-%m-%d')
            for o in out:
                o['m1_first'] = rec_start.get(o['stock_code'])
                o['m1_last'] = rec_end.get(o['stock_code'])

            # 兜底：账本里没有的，扫 parquet 补 开始(最旧季度min)+更新到(最新季度max)
            miss = {o['stock_code'] for o in out if o['m1_last'] is None}
            if miss:
                from pathlib import Path
                from config import Config
                dq = Path(Config.get_project_root()) / 'data' / 'quarters'
                qdirs = sorted([d for y in dq.iterdir() if y.is_dir()
                                for d in y.iterdir() if d.is_dir()],
                               key=lambda d: (d.parent.name, d.name), reverse=True) if dq.exists() else []
                for o in out:
                    if o['stock_code'] not in miss:
                        continue
                    for qd in qdirs:  # 新→旧：第一个含该股的季度即最新
                        p = qd / f"{o['stock_code']}.parquet"
                        if p.exists():
                            try:
                                dd = pd.read_parquet(p, columns=['date'])
                                o['m1_last'] = pd.to_datetime(dd['date']).max().strftime('%Y-%m-%d')
                            except Exception:
                                pass
                            break
                    for qd in reversed(qdirs):  # 旧→新：第一个含该股的季度即最早
                        p = qd / f"{o['stock_code']}.parquet"
                        if p.exists():
                            try:
                                dd = pd.read_parquet(p, columns=['date'])
                                o['m1_first'] = pd.to_datetime(dd['date']).min().strftime('%Y-%m-%d')
                            except Exception:
                                pass
                            break
        except Exception:
            logger.exception('板块成分股取 1m 开始/最新日期失败')
            for o in out:
                o.setdefault('m1_first', None)
                o.setdefault('m1_last', None)

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


def _tdx_caps(stock_codes):
    """TDX(pytdx) 兜底取市值：总市值=总股本×现价, 流通市值=流通股本×现价（单位 元）。
    返回 {code: (total_cap, circ_cap)}；连不上或全失败返回 {}。
    """
    from App.codes.downloads.DlPytdx import AVAILABLE_SERVERS, get_market
    from pytdx.hq import TdxHq_API
    api = None
    for ip, port in AVAILABLE_SERVERS:
        try:
            a = TdxHq_API()
            if a.connect(ip, port, time_out=5):
                api = a
                break
        except Exception:
            continue
    if api is None:
        logger.info('刷市值：TDX 兜底也连不上服务器')
        return {}
    out = {}
    try:
        for code in stock_codes:
            try:
                m = get_market(code)
                fi = api.get_finance_info(m, code) or {}
                q = api.get_security_quotes([(m, code)])
                price = q[0]['price'] if q else None
                if not price or price <= 0:
                    continue
                ltg, zg = fi.get('liutongguben'), fi.get('zongguben')
                total = round(zg * price) if zg else None
                circ = round(ltg * price) if ltg else None
                if total or circ:
                    out[code] = (total, circ)
            except Exception:
                continue
    finally:
        try:
            api.disconnect()
        except Exception:
            pass
    return out


@board_data_bp.route('/api/refresh_caps', methods=['POST'])
def api_refresh_caps():
    """刷新成分股市值（基于 industry_eastmoney 已有名单）。

    数据源优先级：
      1) 批量 clist（em_industry_cons_via_http）—— 一次拿全板块 f20/f21，快
      2) 逐只 akshare 个股信息（ak_individual_caps）—— clist 挂时兜底（emweb 子域）
    两条都拿不到 → 东财整体不可达（限流/RST），返回 502 并提示用快照。

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

        # 1) 首选：批量 clist（一次返回全部市值）
        cap_map = {}
        source = None
        try:
            members = em_industry_cons_via_http(code, retries=2)
            cap_map = {m['stock_code']: (m.get('total_cap'), m.get('circ_cap'))
                       for m in members
                       if m.get('total_cap') is not None or m.get('circ_cap') is not None}
            if cap_map:
                source = 'clist'
        except Exception as e:
            logger.info(f'刷市值：批量 clist 失败，转逐只 akshare 兜底：{str(e)[:120]}')

        # 2) 兜底：逐只 akshare 个股信息
        if not cap_map:
            cap_map = ak_individual_caps(stock_codes)
            if cap_map:
                source = 'akshare'

        # 3) 再兜底：TDX(pytdx) 财务信息 × 现价（东财/akshare 全挂时用，单位 元）
        if not cap_map:
            cap_map = _tdx_caps(stock_codes)
            if cap_map:
                source = 'tdx'

        if not cap_map:
            return jsonify({'success': False, 'message':
                            '东财/akshare/TDX 市值接口都暂不可达，请稍后再试；列表当前显示的是快照市值。'}), 502

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
            'source': source,
        }})
    except Exception as e:
        db.session.rollback()
        logger.exception('刷新成分股市值失败')
        return jsonify({'success': False, 'message': str(e)}), 500
