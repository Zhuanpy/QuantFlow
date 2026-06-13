# -*- coding: utf-8 -*-
"""
个股详情页路由

页面：
    GET  /stock/<code>                          详情页

API：
    GET  /stock/api/<code>/overview             基础信息（名称/分类/最近价/基金数）
    GET  /stock/api/<code>/15m?start=&end=      15m K 线数据
    GET  /stock/api/<code>/15m/schema           15m 数据字段一览（列名/类型/非空数）
    GET  /stock/api/<code>/15m/day?date=        某一交易日的全部 15m 明细行
    GET  /stock/api/<code>/cycles               每个信号周期的 CycleLengthMax 等量化指标
    GET  /stock/api/<code>/15m/daily_summary    15m 数据按交易日的完整性统计
    GET  /stock/api/<code>/1m?date=YYYY-MM-DD   单日 1m 数据
    GET  /stock/api/<code>/1m/dates             有 1m 数据的所有日期列表
    GET  /stock/api/<code>/params?month=        标准化参数（指定月份）
    GET  /stock/api/<code>/params/months        有标准化参数的所有月份列表
    GET  /stock/api/<code>/metrics?month=       指标摘要
    GET  /stock/api/<code>/predictions?limit=   预测记录
    GET  /stock/api/<code>/fund_holdings?days=  基金持仓时序
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import text

from App.exts import db
from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.MySql.DataBaseStockData1m import StockData1m
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.models.data.basic_info import StockInfo, StockClassification
from App.models.data.StockDaily import StockDaily
from App.models.strategy.RnnRunningRecords import RnnRunningRecord

logger = logging.getLogger(__name__)

stock_detail_bp = Blueprint(
    'stock_detail_bp', __name__, url_prefix='/stock'
)


# ==================== 页面 ====================
# 三种视角拆成独立路由（同一个 stock_detail_bp 下），共享 ?code= 参数：
#   /stock/         收盘后（默认，完整 widget）
#   /stock/live     盘中（聚焦交易决策，精简 widget）
#   /stock/replay   历史回放（完整 widget + 突出时光机日期选择器）
# 兼容老链接：/stock/<code> 路径式仍可用，等价于 /stock/?code=<code>
def _read_period_arg() -> str:
    """统一从 URL 拿 ?period=，非法值降级回 15m。"""
    p = (request.args.get('period') or '15m').strip().lower()
    return p if p in ('15m', '30m', '60m', 'daily') else '15m'


@stock_detail_bp.route('/')
@stock_detail_bp.route('/<code>')
def stock_detail_page(code: str = None):
    if not code:
        code = (request.args.get('code') or '').strip()
    return render_template('data/stock_view.html', stock_code=code,
                           view_mode='after_close', period=_read_period_arg())


@stock_detail_bp.route('/live')
def stock_live_page():
    code = (request.args.get('code') or '').strip()
    return render_template('data/stock_live.html', stock_code=code,
                           period=_read_period_arg())


@stock_detail_bp.route('/replay')
def stock_replay_page():
    code = (request.args.get('code') or '').strip()
    return render_template('data/stock_view.html', stock_code=code,
                           view_mode='replay', period=_read_period_arg())


@stock_detail_bp.route('/onemin')
def stock_onemin_check_page():
    """个股 1 分钟数据完整性检测页（按年份）。静态路径，优先于 /stock/<code>。"""
    code = (request.args.get('code') or '').strip()
    return render_template('data/onemin_check.html', stock_code=code)


# ==================== 工具函数 ====================
def _project_root() -> Path:
    from config import Config
    return Path(Config.get_project_root())


def _safe_jsonable(v):
    """将 numpy/pandas 类型转换为 Python 原生 JSON 可序列化类型"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, 'item'):
        return v.item()
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


def _parse_as_of(raw):
    """时光机视角：解析 as_of 参数为"当日收盘 15:00:00"的 Timestamp。

    返回 (timestamp 或 None, error 字符串或 None)。
    - 用户传 YYYY-MM-DD 时按 15:00:00 截止（含整个交易日）
    - 用户传 YYYY-MM-DD HH:MM[:SS] 时按精确时间截止
    - 空 / None 视为实时模式（None, None）
    """
    if not raw:
        return None, None
    try:
        ts = pd.to_datetime(raw)
    except Exception:
        return None, f'as_of 格式错误: {raw}'
    # 纯日期 → 当天 15:00:00
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
        ts = ts.replace(hour=15, minute=0, second=0)
    return ts, None


# ==================== API ====================
def _query_trade_info(code: str, latest_close):
    """汇总该股票的交易信息：持仓 / 模式 / 当前状态。

    - 优先看 Position 表；为空时退而求其次用 TradeRecord 推算（已成交 buy 数 - 已成交 sell 数）
    - trade_mode 来源：最近一条 TradePlan 的 trade_mode；无则用最近一条 TradeRecord
    - status_label: 持仓中 / 计划中 / 已平仓 / 未持仓
    """
    info = {
        'holding': False,
        'quantity': 0,
        'avg_cost': None,
        'unrealized_pnl': None,
        'unrealized_return_rate': None,
        'trade_mode': None,           # 'simulate' / 'real' / None
        'active_plan': None,          # 最近一条 active/pending 的 TradePlan 概要
        'status_label': '未持仓',
        'status_color': 'dim',        # dim / green / blue / orange
    }
    try:
        from App.models.trade.positions import Position
        from App.models.trade.trade_plan import TradePlan
        from App.models.trade.trade_records import TradeRecord
    except Exception:
        logger.exception('交易模型不可用')
        return info

    # 1) 优先用 Position 表
    pos = Position.query.filter_by(stock_code=code).first()
    qty = int(pos.total_quantity) if pos and pos.total_quantity else 0
    avg_cost = float(pos.avg_cost) if pos and pos.avg_cost else None
    holding_mode = None        # 当前持仓的交易模式（来自未平仓 buy 批次）

    # 2) Position 为空 → 按 FIFO 走 TradeRecord 算"当前未平仓批次"
    # 关键：不是简单地用所有 buy 的加权均价，那会把已卖掉的成本算进来；
    # FIFO 把 sell 按时间序对冲掉头部的 buy 批次，剩下的 lots 就是当前持仓的真实成本。
    if qty == 0:
        recs = (TradeRecord.query
                .filter(TradeRecord.stock_code == code,
                        TradeRecord.status.in_(('executed', 'partial')))
                .order_by(TradeRecord.order_time.asc(),
                          TradeRecord.id.asc())
                .all())
        lots = []   # [[剩余股数, 单价, mode, order_time], ...] 按时间升序
        for r in recs:
            tt = (r.trade_type or '').lower()
            q = int(r.quantity or 0)
            px = float(r.price or 0)
            if q <= 0:
                continue
            if tt in ('buy', 'buy_back', 'cover'):
                lots.append([q, px, r.trade_mode, r.order_time])
            elif tt in ('sell', 'short'):
                remaining = q
                while remaining > 0 and lots:
                    head = lots[0]
                    take = min(head[0], remaining)
                    head[0] -= take
                    remaining -= take
                    if head[0] == 0:
                        lots.pop(0)
                # 卖超过持仓 → 数据异常，忽略剩余（不引入负仓）

        qty = sum(l[0] for l in lots)
        if qty > 0:
            cost_sum = sum(l[0] * l[1] for l in lots)
            avg_cost = round(cost_sum / qty, 4)
            # 当前持仓的 mode：用最新未平仓 lot（用户最近一次买入的模式）
            holding_mode = lots[-1][2]

    # 3) 取最近一个 TradePlan 拿状态/计划价
    plan = (TradePlan.query
            .filter_by(stock_code=code)
            .order_by(TradePlan.id.desc())
            .first())

    # 决定 trade_mode：当前持仓的 mode 最权威（从未平仓 lots 来）；
    # 没持仓时回退到最近 plan / 最近 TradeRecord
    trade_mode = holding_mode
    if not trade_mode:
        if plan:
            trade_mode = plan.trade_mode
        else:
            last_rec = (TradeRecord.query
                        .filter_by(stock_code=code)
                        .order_by(TradeRecord.id.desc())
                        .first())
            if last_rec:
                trade_mode = last_rec.trade_mode

    info['trade_mode'] = trade_mode
    info['quantity'] = qty
    info['avg_cost'] = avg_cost

    if plan:
        info['active_plan'] = {
            'plan_id': plan.plan_id,
            'status': plan.status,
            'trade_mode': plan.trade_mode,
            'entry_price': float(plan.entry_price) if plan.entry_price is not None else None,
            'stop_loss_price': float(plan.stop_loss_price) if plan.stop_loss_price is not None else None,
            'take_profit_price': float(plan.take_profit_price) if plan.take_profit_price is not None else None,
        }

    # 叠加手动设置的止损/止盈：存在独立的 MANUAL-<code> plan 里，
    # 不会被成交导入的 infer wipe（那个只删 INFER-%）。优先级最高。
    manual = TradePlan.query.filter_by(plan_id=f'MANUAL-{code}').first()
    if manual:
        if info['active_plan'] is None:
            info['active_plan'] = {
                'plan_id': manual.plan_id,
                'status': manual.status,
                'trade_mode': manual.trade_mode,
                'entry_price': float(manual.entry_price) if manual.entry_price is not None else (avg_cost or None),
                'stop_loss_price': None,
                'take_profit_price': None,
            }
        if manual.stop_loss_price is not None:
            info['active_plan']['stop_loss_price'] = float(manual.stop_loss_price)
        if manual.take_profit_price is not None:
            info['active_plan']['take_profit_price'] = float(manual.take_profit_price)

    # 4) 浮盈/浮亏（持仓 + 有均价 + 有最新价时）
    if qty > 0 and avg_cost and latest_close:
        pnl = (float(latest_close) - avg_cost) * qty
        rate = (float(latest_close) - avg_cost) / avg_cost
        info['unrealized_pnl'] = round(pnl, 2)
        info['unrealized_return_rate'] = round(rate, 4)

    # 5) status_label & color：综合持仓 / plan.status
    if qty > 0:
        info['holding'] = True
        info['status_label'] = '持仓中'
        info['status_color'] = 'green'
    elif plan and plan.status in ('planning', 'pending'):
        info['status_label'] = '计划中'
        info['status_color'] = 'blue'
    elif plan and plan.status == 'completed':
        info['status_label'] = '已平仓'
        info['status_color'] = 'dim'
    elif plan and plan.status == 'active':
        # plan 是 active 但持仓数为 0 —— 数据不一致，标橙色提示
        info['holding'] = True   # 信任 plan
        info['status_label'] = '持仓中（无成交记录）'
        info['status_color'] = 'orange'
    # 其他情况默认 '未持仓' / dim
    return info


def _query_boards_and_caps(code: str):
    """从 industry_eastmoney 查该股票所属板块 + 市值。

    - 同一只股可能在多个板块（每个板块取它在该板块的最新一条记录）
    - 市值从所有记录里挑最新非空的一条（来源板块和日期一起返回）
    """
    eng = db.engines['quanttradingsystem']
    boards = []
    total_cap = None
    circ_cap = None
    cap_source = None
    try:
        with eng.connect() as conn:
            # 板块清单：按 (board_code) 分组取最新日期那条
            rows = conn.execute(text(
                '''
                SELECT t.board_code, t.board_name, t.date
                  FROM industry_eastmoney t
                  JOIN (SELECT board_code, MAX(date) AS mx
                          FROM industry_eastmoney
                         WHERE stock_code = :sc
                         GROUP BY board_code) m
                    ON t.board_code = m.board_code AND t.date = m.mx
                 WHERE t.stock_code = :sc
                 ORDER BY t.date DESC, t.board_name
                '''
            ), {'sc': code}).fetchall()
            for r in rows:
                boards.append({
                    'board_code': r.board_code,
                    'board_name': r.board_name,
                    'as_of': r.date.isoformat() if r.date else None,
                })

            # 最新一条 total_cap 非空的记录（顺带 circ_cap）
            cap_row = conn.execute(text(
                '''
                SELECT total_cap, circ_cap, date, board_name
                  FROM industry_eastmoney
                 WHERE stock_code = :sc AND total_cap IS NOT NULL
              ORDER BY date DESC
                 LIMIT 1
                '''
            ), {'sc': code}).fetchone()
            if cap_row:
                total_cap = float(cap_row.total_cap) if cap_row.total_cap is not None else None
                circ_cap = float(cap_row.circ_cap) if cap_row.circ_cap is not None else None
                cap_source = {
                    'date': cap_row.date.isoformat() if cap_row.date else None,
                    'board_name': cap_row.board_name,
                }
    except Exception:
        logger.exception('查 industry_eastmoney 失败')

    return boards, total_cap, circ_cap, cap_source


@stock_detail_bp.route('/api/<code>/overview', methods=['GET'])
def api_overview(code):
    """股票基础信息 + 最新价 + 基金持仓数 + 所属板块 + 市值"""
    # 用 find_stock 而不是 first()：处理同代码歧义（例如 000001 = 平安银行 vs 上证指数）
    info = StockInfo.find_stock(code)
    classification = StockClassification.query.filter_by(code=code).first()
    latest_daily = (StockDaily.query
                    .filter_by(stock_code=code)
                    .order_by(StockDaily.date.desc()).first())

    boards, total_cap, circ_cap, cap_source = _query_boards_and_caps(code)
    latest_close = latest_daily.close if latest_daily else None
    trade_info = _query_trade_info(code, latest_close)

    return jsonify({
        'success': True,
        'data': {
            'code': code,
            'name': info.name if info else None,
            'classification': classification.classification if classification else None,
            'es_code': info.EsCode if info else None,
            'market_code': info.MarketCode if info else None,
            'latest_close': latest_close,
            'latest_date': latest_daily.date.isoformat() if latest_daily and latest_daily.date else None,
            'fund_holdings_count': latest_daily.fund_holdings_count if latest_daily else None,
            'fund_holdings_ratio': latest_daily.fund_holdings_ratio if latest_daily else None,
            # ---- 新增 ----
            'boards': boards,
            'total_cap': total_cap,
            'circ_cap': circ_cap,
            'cap_source': cap_source,
            'trade_info': trade_info,
        }
    })


def _append_live_15m(historical_df: pd.DataFrame, code: str, day_ts: pd.Timestamp,
                     warmup_bars: int = 100):
    """把 day_ts 当天的 1m 拼接 + 重采样成 15m，append 到 historical 尾部，重算 MACD/Signal。

    返回: (拼好的 df, live_bars_count)。live_bars_count=0 表示当天没拿到 1m 数据。

    实现要点：
    - 历史 15m 截到 day_ts 00:00 之前（防止盘后回灌污染当日）
    - 取历史末尾 warmup_bars 根 + 当天重采样 15m → 在小段上跑
      compute_macd_signals_pipeline，足以让 EMA/DEA 收敛
    - live 行的 cycle 统计列（CycleAmplitudeMax/StartPrice 等）保留 NaN，
      因为 api_15m 不读这些
    """
    from App.codes.utils.data_processing import ResampleData
    from App.codes.Signals.StatisticsMacd import MacdSignalPipeline

    day_only = day_ts.normalize()

    # 1) 拉当天的 1m（盘中临时 + 历史归档合并）
    df_1m = StockData1m.load_1m_for_day(code, day_only)
    if df_1m is None or df_1m.empty:
        return historical_df, 0

    # 2) 1m → 15m
    df_1m = df_1m[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
    df_1m['money'] = df_1m.get('money', 0) if 'money' in df_1m.columns else 0.0
    live_15m = ResampleData.resample_1m_data(df_1m, '15m')
    if live_15m.empty:
        return historical_df, 0
    live_15m = live_15m[live_15m['date'].dt.date == day_only.date()].reset_index(drop=True)
    if live_15m.empty:
        return historical_df, 0

    # 3) 历史截到当天 00:00 之前（防止盘后回灌的当日数据和 live 重复）
    pre_today = historical_df[historical_df['date'] < day_only].copy()
    if pre_today.empty:
        # 极端情况：历史空，没法 warmup
        logger.warning(f'{code} 历史 15m 为空，无法给 live 提供 warmup')
        return historical_df, 0

    # 4) warmup tail + live → 跑信号流水线
    warmup = pre_today.tail(warmup_bars).copy()
    # 给 live 行补齐 warmup 里有但 live 没有的列（数值列填 NaN，避免 concat 时报错）
    for c in warmup.columns:
        if c not in live_15m.columns:
            live_15m[c] = pd.NA
    live_15m = live_15m[warmup.columns]  # 列顺序对齐

    combined = pd.concat([warmup, live_15m], ignore_index=True)
    # 清空信号列让 pipeline 自己重新算（避免 ffill 后的旧值干扰）
    signal_cols = ['Signal', 'SignalChoice', 'SignalId', 'SignalStartIndex']
    for c in signal_cols:
        if c in combined.columns:
            combined[c] = pd.NA
    try:
        # v2: MACD>0/<0 + 连续 7 根 low>MA30 / high<MA30
        # 注：parquet 历史数据也由 backfill_v2 重算成 v2 标签，保持一致
        from App.codes.Signals.MacdSignalV2 import compute_v2_signals_pipeline
        combined = compute_v2_signals_pipeline(combined, n=7)
    except Exception:
        logger.exception(f'{code} live 信号重算失败')
        return historical_df, 0

    # 5) 取重算后的 live 行
    live_recomputed = combined.tail(len(live_15m)).reset_index(drop=True)

    # 6) 用"方向"而非"时间戳"决定是否采纳 warmup 重算的趋势起点。
    #
    #    warmup 只有 100 根，看不到 100 根之前就开始的"长趋势"的真实起点 ——
    #    它会把一段延续中的同方向趋势的起点错误地截断到窗口内
    #    （例：延续自 5/28 的下跌被 warmup 重新标成 6/3 起点）。原先按时间戳
    #    "更晚就采纳"的规则恰好把这种被截断的假起点也采纳了，导致 live 当前趋势
    #    与离线 parquet（after_close）不一致、且被切成一小段。
    #
    #    新规则（按最终方向比较）：
    #      - 今天行重算方向 == 离线 last 方向 → 趋势延续，继承离线起点（更早更完整）
    #      - 方向不同              → 真的发生了翻转（今天新 flip / 离线漏判的近期 flip），
    #                                采纳 warmup 重算值（其起点必在近期、落在窗口内，准确）
    #    这样既消除"长趋势被截断"的 bug，又保留 warmup 发现近期翻转的能力。
    last_hist_signal = pre_today.iloc[-1].get('Signal') if 'Signal' in pre_today.columns else None
    last_hist_start = pre_today.iloc[-1].get('SignalStartIndex') if 'SignalStartIndex' in pre_today.columns else None
    last_hist_id = pre_today.iloc[-1].get('SignalId') if 'SignalId' in pre_today.columns else None
    hist_sig_num = pd.to_numeric(pd.Series([last_hist_signal]), errors='coerce').iloc[0]
    fresh_sig = (pd.to_numeric(live_recomputed.get('Signal'), errors='coerce')
                 if 'Signal' in live_recomputed.columns else None)

    for i in range(len(live_recomputed)):
        fi = fresh_sig.iloc[i] if (fresh_sig is not None and i < len(fresh_sig)) else None
        # 同向延续，或今天行没算出方向 → 继承离线（趋势从更早的真实起点延续至今）
        if fi is None or pd.isna(fi) or (pd.notna(hist_sig_num) and fi == hist_sig_num):
            live_recomputed.at[i, 'Signal'] = last_hist_signal
            live_recomputed.at[i, 'SignalStartIndex'] = last_hist_start
            if last_hist_id is not None and 'SignalId' in live_recomputed.columns:
                live_recomputed.at[i, 'SignalId'] = last_hist_id
        # else: 方向翻转 → 保留 warmup 算出的 fresh 值（近期真实翻转）

    # 7) 列对齐到 pre_today 的全集（pipeline 不写的列保持 NaN），concat 回去
    for c in pre_today.columns:
        if c not in live_recomputed.columns:
            live_recomputed[c] = pd.NA
    live_recomputed = live_recomputed[pre_today.columns]

    merged = pd.concat([pre_today, live_recomputed], ignore_index=True)
    return merged, len(live_recomputed)


def _signal_name(direction, signal_start_ts):
    """按 SignalStartIndex 时间 + 方向 给趋势段命名（同一段内稳定，趋势转变即换名）。

    例：↑涨#260306-1415 / ↓跌#260319-1000 / ·盘整#...
    名字本身唯一(精确到分钟的信号起点)，方便交易时识别"趋势是否已切换"。
    """
    if signal_start_ts is None or pd.isna(signal_start_ts):
        return None
    ts = pd.Timestamp(signal_start_ts)
    arrow = '↑' if direction > 0 else '↓' if direction < 0 else '·'
    word = '涨' if direction > 0 else '跌' if direction < 0 else '盘整'
    return f'{arrow}{word}#{ts.strftime("%y%m%d-%H%M")}'


@stock_detail_bp.route('/api/<code>/15m', methods=['GET'])
def api_15m(code):
    """15m K 线数据，可按时间范围过滤。

    三种视角：
    - 默认（收盘后）：直接读 15m parquet
    - mode=live：把今天的 1m（盘中临时 + 历史归档）重采样成 15m，append 到尾部
      并对尾部重算 MACD/Signal/SignalStartIndex，让 current_trend 能反映当下
    - as_of=YYYY-MM-DD[ HH:MM]：时光机回放，丢弃 as_of 之后的所有行；
      和 mode=live 互斥（as_of 优先）
    """
    start = request.args.get('start')
    end = request.args.get('end')
    mode = (request.args.get('mode') or '').strip().lower()
    period = (request.args.get('period') or '15m').strip().lower()
    as_of_ts, err = _parse_as_of(request.args.get('as_of'))
    if err:
        return jsonify({'success': False, 'message': err}), 400
    try:
        limit = max(50, min(int(request.args.get('limit', 2000)), 30000))
    except ValueError:
        limit = 2000

    from App.codes.utils.timeframe_resample import SUPPORTED_PERIODS
    if period not in SUPPORTED_PERIODS:
        period = '15m'

    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} 的 15m 数据'}), 404

    df['date'] = pd.to_datetime(df['date'])

    # live 模式：拼接当日 1m → 15m，重算尾部信号（始终先在 15m 上做 live merge）
    live_bars = 0
    if mode == 'live' and as_of_ts is None:
        today = pd.Timestamp.now().normalize()
        df, live_bars = _append_live_15m(df, code, today)

    # period != '15m'：把 15m DataFrame resample 到目标周期，然后重跑 v2 信号 +
    # cycle stats。注意 resample 会丢掉所有非 OHLCV 列，所以下面信号 / MACD 都
    # 是基于目标周期 fresh 算出来的。
    if period != '15m':
        from App.codes.utils.timeframe_resample import resample_15m
        from App.codes.Signals.MacdSignalV2 import compute_v2_signals_pipeline
        df = resample_15m(df, period)
        if df.empty:
            return jsonify({'success': False, 'message': f'resample 到 {period} 后无数据'}), 404
        df = compute_v2_signals_pipeline(df, n=7)

    if start:
        df = df[df['date'] >= pd.to_datetime(start)]
    if end:
        df = df[df['date'] <= pd.to_datetime(end)]
    if as_of_ts is not None:
        df = df[df['date'] <= as_of_ts]
        if df.empty:
            return jsonify({'success': False,
                            'message': f'as_of={as_of_ts.strftime("%Y-%m-%d %H:%M")} 之前无 {period} 数据'}), 404

    # 取最后 limit 行（避免一次返回几万条）
    df = df.tail(limit).reset_index(drop=True)

    has_signal = 'Signal' in df.columns
    has_sidx = 'SignalStartIndex' in df.columns
    sig_num = pd.to_numeric(df['Signal'], errors='coerce') if has_signal else None
    sidx_col = df['SignalStartIndex'] if has_sidx else None

    # MACD 三件套：parquet 里都有，前端 MACD 子图直接用
    dif_col = pd.to_numeric(df['Dif'], errors='coerce') if 'Dif' in df.columns else None
    dea_col = pd.to_numeric(df['Dea'], errors='coerce') if 'Dea' in df.columns else None
    macd_col = pd.to_numeric(df['MACD'], errors='coerce') if 'MACD' in df.columns else None
    # 三均线：EmaShort(12)/EmaMid(20)/EmaLong(30)，与 MACD 同源（MacdSignal.py:14-16）
    es_col = pd.to_numeric(df['EmaShort'], errors='coerce') if 'EmaShort' in df.columns else None
    em_col = pd.to_numeric(df['EmaMid'], errors='coerce') if 'EmaMid' in df.columns else None
    el_col = pd.to_numeric(df['EmaLong'], errors='coerce') if 'EmaLong' in df.columns else None

    def _f(series, i):
        if series is None: return None
        v = series.iloc[i]
        return None if pd.isna(v) else float(v)

    records = []
    for i, row in df.iterrows():
        direction = 0
        if has_signal and pd.notna(sig_num.iloc[i]):
            v = sig_num.iloc[i]
            direction = 1 if v > 0 else -1 if v < 0 else 0
        sstart = sidx_col.iloc[i] if has_sidx else None
        records.append({
            'date': row['date'].strftime('%Y-%m-%d %H:%M:%S'),
            'open': float(row['open']),
            'close': float(row['close']),
            'high': float(row['high']),
            'low': float(row['low']),
            'volume': int(row['volume']) if pd.notna(row.get('volume')) else 0,
            'direction': direction,
            'signal_name': _signal_name(direction, sstart),
            'dif': _f(dif_col, i),
            'dea': _f(dea_col, i),
            'macd': _f(macd_col, i),
            'ema_short': _f(es_col, i),
            'ema_mid': _f(em_col, i),
            'ema_long': _f(el_col, i),
        })

    # 把连续同一周期(SignalStartIndex)的 bar 合并成区段，便于在 K 线上画涨跌底色
    segments = []
    if has_sidx and len(df):
        key = df['SignalStartIndex'].astype(str).fillna('NA')
        seg_start = 0
        for i in range(1, len(df) + 1):
            if i == len(df) or key.iloc[i] != key.iloc[seg_start]:
                d = records[seg_start]['direction']
                if d != 0:
                    segments.append({
                        'start_idx': seg_start,
                        'end_idx': i - 1,
                        'direction': d,
                        'signal_name': records[seg_start]['signal_name'],
                        'start_date': records[seg_start]['date'],
                        'end_date': records[i - 1]['date'],
                        'bars': i - seg_start,
                    })
                seg_start = i

    # 当前趋势（最后一段）—— 交易时用：趋势没变名字不变，变了就该止盈/止损
    current_trend = None
    if segments:
        last = segments[-1]
        # 趋势内涨跌幅：以该段起点收盘价为基准，到段末收盘价的百分比
        start_price = records[last['start_idx']]['close']
        last_price = records[last['end_idx']]['close']
        change_pct = (round((last_price - start_price) / start_price * 100, 2)
                      if start_price else None)
        seg_rows = records[last['start_idx']:last['end_idx'] + 1]
        seg_high = max(r['high'] for r in seg_rows)
        seg_low = min(r['low'] for r in seg_rows)

        # 振幅口径与「周期振幅分布」完全统一 —— 极值口径：
        #   (本段极值 − 周期起点价) / 周期起点价；上涨取段内最高、下跌取段内最低。
        # 周期起点价 = 本段 StartPrice 的首个非空值（= flip 行起点，与 cycles 同源）。
        start_ref = None
        if 'StartPrice' in df.columns:
            _sp = pd.to_numeric(
                df['StartPrice'].iloc[last['start_idx']:last['end_idx'] + 1],
                errors='coerce').dropna()
            if len(_sp):
                start_ref = float(_sp.iloc[0])
        seg_extreme = seg_high if last['direction'] > 0 else seg_low
        if start_ref and start_ref != 0:
            amplitude_pct = round(abs(seg_extreme - start_ref) / start_ref * 100, 2)
        else:
            # 无 StartPrice 兜底：退回段内高低口径
            start_ref = seg_low if last['direction'] > 0 else seg_high
            amplitude_pct = (round((seg_high - seg_low) / seg_high * 100, 2)
                             if seg_high else None)

        current_trend = {
            'signal_name': last['signal_name'],
            'direction': last['direction'],
            'started_at': last['start_date'],
            'bars_in_trend': last['bars'],
            'is_active': last['end_idx'] == len(records) - 1,
            'start_price': round(start_price, 2),
            'last_price': round(last_price, 2),
            'change_pct': change_pct,
            'high_price': round(seg_high, 2),
            'low_price': round(seg_low, 2),
            'amplitude_pct': amplitude_pct,
            'amp_start': round(start_ref, 2) if start_ref else None,  # 周期起点价
            'amp_to': round(seg_extreme, 2),                          # 本段极值
        }

    return jsonify({
        'success': True,
        'count': len(records),
        'segments': segments,
        'current_trend': current_trend,
        'first_date': records[0]['date'] if records else None,
        'last_date': records[-1]['date'] if records else None,
        'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S') if as_of_ts is not None else None,
        'mode': mode if mode in ('live',) else 'after_close',
        'period': period,
        'live_bars': live_bars,
        'data': records,
    })


@stock_detail_bp.route('/api/<code>/1m/refresh', methods=['POST'])
def api_1m_refresh(code):
    """盘中拉一次当天的 1m 数据，写到 data/quarters_live/<today>/<code>.parquet。

    前端 live 模式每 15min 调一次。复用现有 download_1m_by_type（pytdx → 东方财富 → selenium
    三级策略），覆盖式写入临时目录，不污染历史 quarters。

    返回：
      - written:    本次写入的 1m 根数
      - last_ts:    最新 1m 时间戳
      - bars_15m:   重采样能拼出几根 15m（前端可据此知道有没有新整根）
      - path:       临时文件相对路径
    """
    if not StockData1m._validate_stock_code(code):
        return jsonify({'success': False, 'message': f'非法股票代码: {code}'}), 400

    try:
        from App.codes.downloads.DlStockData import download_1m_by_type, StockType
    except Exception as e:
        return jsonify({'success': False, 'message': f'下载模块不可用: {e}'}), 500

    today = pd.Timestamp.now().normalize()
    try:
        df, _end = download_1m_by_type(code, days=1, stock_type=StockType.STOCK_1M)
    except Exception:
        logger.exception(f'{code} 1m refresh 下载失败')
        return jsonify({'success': False, 'message': '下载异常，看后端日志'}), 500

    if df is None or df.empty:
        return jsonify({'success': False, 'message': '下载结果为空（非交易时段或代码无效）'}), 404

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df[df['date'].dt.date == today.date()]
    if df.empty:
        return jsonify({'success': False,
                        'message': f'下载到数据但没有今天 {today.date()} 的行（盘前/非交易日？）'}), 404

    df = df.sort_values('date').drop_duplicates(subset=['date']).reset_index(drop=True)

    # 写到 quarters_live/<today>/<code>.parquet（覆盖式：盘中数据 append-only，全量覆写最简单）
    root = _project_root()
    out_dir = root / 'data' / 'quarters_live' / today.strftime('%Y-%m-%d')
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{code}.parquet'
    try:
        df.to_parquet(out_path, index=False)
    except Exception:
        logger.exception(f'{code} 写 live parquet 失败 {out_path}')
        return jsonify({'success': False, 'message': '写文件失败，看后端日志'}), 500

    # 顺便算下能拼出几根 15m（前端可据此判断是否值得重画）
    try:
        from App.codes.utils.data_processing import ResampleData
        ohlcv = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
        ohlcv['money'] = df.get('money', 0) if 'money' in df.columns else 0.0
        bars_15m = len(ResampleData.resample_1m_data(ohlcv, '15m'))
    except Exception:
        bars_15m = None

    return jsonify({
        'success': True,
        'code': code,
        'date': today.strftime('%Y-%m-%d'),
        'written': len(df),
        'last_ts': df['date'].iloc[-1].strftime('%Y-%m-%d %H:%M:%S'),
        'bars_15m': bars_15m,
        'path': str(out_path.relative_to(root)).replace('\\', '/'),
    })


@stock_detail_bp.route('/api/<code>/sl_tp', methods=['POST'])
def api_set_sl_tp(code):
    """保存某股票的止损/止盈到独立的 MANUAL-<code> TradePlan。

    body(JSON): {stop_loss_price, take_profit_price}  —— 任一可为空/null
    用 MANUAL-<code> 作 plan_id，独立于成交导入推断的 INFER-<code>（后者每次导入会被
    wipe 重建），所以手动设的止损止盈不会丢。entry_price 取当前持仓成本/最新价（非空约束）。
    """
    try:
        from App.models.trade.trade_plan import TradePlan
    except Exception as e:
        return jsonify({'success': False, 'message': f'交易模型不可用: {e}'}), 500

    body = request.get_json(silent=True) or {}

    def _f(v):
        if v is None or v == '':
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    sl = _f(body.get('stop_loss_price'))
    tp = _f(body.get('take_profit_price'))

    # entry_price 非空：优先持仓成本，否则最新日线收盘，再否则 0
    latest_daily = (StockDaily.query
                    .filter_by(stock_code=code)
                    .order_by(StockDaily.date.desc()).first())
    latest_close = latest_daily.close if latest_daily else None
    trade_info = _query_trade_info(code, latest_close)
    entry = trade_info.get('avg_cost') or latest_close or 0

    info = StockInfo.find_stock(code)
    name = info.name if info else code

    plan_id = f'MANUAL-{code}'
    plan = TradePlan.query.filter_by(plan_id=plan_id).first()
    if plan is None:
        plan = TradePlan(
            plan_id=plan_id, stock_code=code, stock_name=name,
            trade_mode=TradePlan.MODE_REAL, direction=TradePlan.DIRECTION_LONG,
            status=TradePlan.STATUS_ACTIVE, entry_price=entry,
        )
        db.session.add(plan)
    plan.stock_name = name
    plan.entry_price = entry
    plan.stop_loss_price = sl
    plan.take_profit_price = tp
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f'{code} 保存止损止盈失败')
        return jsonify({'success': False, 'message': str(e)}), 500

    return jsonify({'success': True, 'data': {
        'plan_id': plan_id,
        'entry_price': float(entry) if entry is not None else None,
        'stop_loss_price': sl,
        'take_profit_price': tp,
    }})


@stock_detail_bp.route('/api/<code>/trades', methods=['GET'])
def api_trades(code):
    """该股票的历史买入/卖出成交记录，用于在 15m K 线上标注。

    返回已成交（executed/partial）的 buy/sell：time/side/price/quantity。
    """
    try:
        from App.models.trade.trade_records import TradeRecord
    except Exception as e:
        return jsonify({'success': False, 'message': f'交易模型不可用: {e}'}), 500

    try:
        rows = (TradeRecord.query
                .filter(TradeRecord.stock_code == code)
                .order_by(TradeRecord.order_time.asc())
                .all())
    except Exception as e:
        logger.exception('查询交易记录失败')
        return jsonify({'success': False, 'message': str(e)}), 500

    # 交易类型 → 买/卖方向（A 股常见只有 buy/sell；其余做兼容映射）
    BUY_TYPES = {'buy', 'buy_back', 'cover'}
    SELL_TYPES = {'sell', 'short'}
    out = []
    for t in rows:
        if t.status not in ('executed', 'partial'):
            continue
        tt = (t.trade_type or '').lower()
        side = 'buy' if tt in BUY_TYPES else 'sell' if tt in SELL_TYPES else None
        if side is None:
            continue
        ts = t.execute_time or t.order_time
        if ts is None:
            continue
        out.append({
            'time': ts.strftime('%Y-%m-%d %H:%M:%S'),
            'side': side,
            'trade_type': tt,
            'price': float(t.price) if t.price is not None else None,
            'quantity': int(t.quantity) if t.quantity is not None else None,
            'stock_name': t.stock_name,
        })

    return jsonify({'success': True, 'count': len(out), 'data': out})


@stock_detail_bp.route('/api/<code>/15m/schema', methods=['GET'])
def api_15m_schema(code):
    """查看 15m 数据有哪些字段：列名 / 类型 / 非空数 / 最新一行示例值。"""
    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} 的 15m 数据'}), 404

    total = len(df)
    last_row = df.iloc[-1] if total else None
    cols = []
    for name in df.columns:
        s = df[name]
        non_null = int(s.notna().sum())
        sample = last_row[name] if last_row is not None else None
        cols.append({
            'name': str(name),
            'dtype': str(s.dtype),
            'non_null': non_null,
            'null': total - non_null,
            'sample': _safe_jsonable(sample),
        })
    return jsonify({
        'success': True,
        'total_rows': total,
        'column_count': len(cols),
        'columns': cols,
    })


@stock_detail_bp.route('/api/<code>/cycles', methods=['GET'])
def api_cycles(code):
    """每个 MACD 信号周期(cycle)的 CycleLengthMax 等量化指标。

    一个 cycle = 一个 SignalStartIndex 分组。逐周期返回：
      方向 / 起止时间 / CycleLengthMax(周期K线根数) / 实际根数 /
      CycleAmplitudeMax(周期振幅) / 是否已完成(有 SignalChoice 标注)

    时光机视角：传 ?as_of=YYYY-MM-DD 会丢弃 as_of 之后的所有行；
    最后一个周期会被强制标为"进行中"（因为那天之后是否结束在当时还未知）。
    """
    as_of_ts, err = _parse_as_of(request.args.get('as_of'))
    if err:
        return jsonify({'success': False, 'message': err}), 400
    mode = (request.args.get('mode') or '').strip().lower()
    period = (request.args.get('period') or '15m').strip().lower()
    from App.codes.utils.timeframe_resample import SUPPORTED_PERIODS
    if period not in SUPPORTED_PERIODS:
        period = '15m'

    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} 的 15m 数据'}), 404

    df['date'] = pd.to_datetime(df['date'])

    # live 模式：先在 15m 层做 live merge（拼上今天的 1m），再走后面的 period 处理
    live_merged = False
    if mode == 'live' and as_of_ts is None:
        today = pd.Timestamp.now().normalize()
        df, _live_bars = _append_live_15m(df, code, today)
        live_merged = True

    # period != '15m' OR live 合并过：resample（如果需要）后重跑 v2 + 下游 cycle stats
    need_recompute = (period != '15m') or live_merged
    if need_recompute:
        from App.codes.utils.timeframe_resample import resample_15m
        from App.codes.Signals.MacdSignalV2 import compute_v2_signals_pipeline
        from App.codes.Signals.StatisticsMacd import StatisticsMACD
        if period != '15m':
            df = resample_15m(df, period)
            if df.empty:
                return jsonify({'success': False, 'message': f'resample 到 {period} 后无数据'}), 404
        df = compute_v2_signals_pipeline(df, n=7)
        # 补 Daily1mVolMax5 占位（StatisticsMacd 内部会访问，60m / live 视角下没这数据）
        if 'Daily1mVolMax5' not in df.columns:
            df['Daily1mVolMax5'] = pd.NA
        df = StatisticsMACD.s_StartEndIndex(df)
        df = StatisticsMACD.s_CycleAmplitude(df)
        df = StatisticsMACD.s_CycleLength(df)

    required = {'SignalStartIndex', 'CycleLengthMax'}
    missing = required - set(df.columns)
    if missing:
        return jsonify({'success': False,
                        'message': f'数据缺少周期字段: {sorted(missing)}（需先跑信号处理生成）'}), 400

    # 时光机视角：找出在 as_of 那天还没结束的周期 —— 这些周期的
    # CycleLengthMax / CycleAmplitudeMax 是结束后回填的"最终值"，
    # 直接用会泄漏未来。下面对这些周期单独重算。
    # live 模式：最后一段是"今天进行中"的周期，强制标为 in-progress。
    df = df[df['SignalStartIndex'].notna()].sort_values('date').reset_index(drop=True)
    if df.empty:
        return jsonify({'success': True, 'count': 0,
                        'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S') if as_of_ts is not None else None,
                        'data': []})

    has_amp = 'CycleAmplitudeMax' in df.columns
    has_choice = 'SignalChoice' in df.columns
    has_signal = 'Signal' in df.columns
    has_v5 = 'Cycle1mVolMax5' in df.columns
    has_close = 'close' in df.columns
    has_sp = 'StartPrice' in df.columns
    has_high = 'high' in df.columns
    has_low = 'low' in df.columns

    # 按「时间上连续的同 SignalStartIndex」切分成段，而不是 groupby(SSI 值)。
    # 信号管线在一次反向小周期后可能把新趋势段又赋成旧的 SignalStartIndex（SSI 复用、
    # 非单调）。groupby(值) 会把两段不连续的趋势合并成一个超长周期，且"最新周期"按 SSI
    # 值排序会选错——导致详情页"分布图当前周期"方向与"当前趋势"相反。连续段切分保证：
    # 每段 = 一个真实连续趋势，按时间排序，最后一段 = 当下进行中的周期（与 api_15m 一致）。
    ssi_key = df['SignalStartIndex'].astype(str)
    seg_bounds = []
    s = 0
    for i in range(1, len(df) + 1):
        if i == len(df) or ssi_key.iloc[i] != ssi_key.iloc[s]:
            seg_bounds.append((s, i - 1))
            s = i

    # 进行中段：as_of 模式下"末根晚于 as_of"的段；live 模式下时间最后一段。
    inprogress_idx = set()
    if as_of_ts is not None:
        for k, (a, b) in enumerate(seg_bounds):
            if df['date'].iloc[b] > as_of_ts:
                inprogress_idx.add(k)
    elif live_merged and seg_bounds:
        inprogress_idx.add(len(seg_bounds) - 1)

    cycles = []
    for k, (a, b) in enumerate(seg_bounds):
        g = df.iloc[a:b + 1]
        if as_of_ts is not None:
            g = g[g['date'] <= as_of_ts]
            if g.empty:
                continue
        sidx = df['SignalStartIndex'].iloc[a]
        first, last = g['date'].iloc[0], g['date'].iloc[-1]
        is_inprogress = k in inprogress_idx
        direction = 0
        if has_signal:
            sv = pd.to_numeric(g['Signal'], errors='coerce').dropna()
            if len(sv):
                direction = int(1 if sv.iloc[-1] > 0 else -1 if sv.iloc[-1] < 0 else 0)

        # CycleLengthMax 在源数据里是周期结束后回填的最终值。
        # as-of / 进行中的周期改回"截止当下已走过几根" = len(g) - 1，避免泄漏未来。
        if is_inprogress:
            clen = max(0, len(g) - 1)
        else:
            clen = pd.to_numeric(g['CycleLengthMax'], errors='coerce').max()

        # 周期起点价：用 flip 行（组首行）的 StartPrice，不要用 iloc[-1]。
        # 关键 bug 修复：周期边界处 StartPrice/EndPrice/CycleAmplitudeMax 会被相邻周期的
        # ffill 值污染，iloc[-1] / abs().max() 会抓到下一周期的值，导致振幅算错
        # （例如 156 根的下跌段算出 1% / 29% 都不对，真值是 21.7%）。
        # g 已按 date 升序，dropna().iloc[0] = flip 行的 StartPrice = 本周期真正起点。
        sp_at_flip = None
        if has_sp:
            _sp = pd.to_numeric(g['StartPrice'], errors='coerce').dropna()
            if len(_sp):
                sp_at_flip = float(_sp.iloc[0])

        # 真振幅（极值口径）：周期内 high.max(上涨) / low.min(下跌) 对比起点。
        # 用组内真实 high/low（非 ffill 字段，不会被污染）；对完成/进行中周期都正确。
        amp_extreme = None
        if sp_at_flip and sp_at_flip != 0 and has_high and has_low:
            hi = float(pd.to_numeric(g['high'], errors='coerce').max())
            lo = float(pd.to_numeric(g['low'], errors='coerce').min())
            if direction > 0:
                amp_extreme = abs(round((hi - sp_at_flip) / sp_at_flip, 4))
            elif direction < 0:
                amp_extreme = abs(round((lo - sp_at_flip) / sp_at_flip, 4))
            else:
                amp_extreme = round(max(abs((hi - sp_at_flip) / sp_at_flip),
                                        abs((lo - sp_at_flip) / sp_at_flip)), 4)

        # close 口径（周期涨跌幅）：周期末根 close 对比起点（用于参考，非主指标）
        amp_close_signed = None
        if sp_at_flip and sp_at_flip != 0 and has_close:
            close_series = pd.to_numeric(g['close'], errors='coerce').dropna()
            if len(close_series):
                amp_close_signed = round((float(close_series.iloc[-1]) - sp_at_flip) / sp_at_flip, 4)
        amp_close = abs(amp_close_signed) if amp_close_signed is not None else None

        v5 = None
        if has_v5:
            vv = pd.to_numeric(g['Cycle1mVolMax5'], errors='coerce').dropna()
            v5 = float(vv.max()) if len(vv) else None
        # as-of 模式下，进行中的周期强制标为"未完成"（哪怕源数据已有 SignalChoice）
        if is_inprogress:
            completed = False
        elif has_choice:
            completed = bool(g['SignalChoice'].notna().any())
        else:
            completed = None
        cycles.append({
            'signal_name': _signal_name(direction, sidx),
            'signal_start': pd.Timestamp(sidx).strftime('%Y-%m-%d %H:%M'),
            'first_bar': first.strftime('%Y-%m-%d %H:%M'),
            'last_bar': last.strftime('%Y-%m-%d %H:%M'),
            'direction': direction,                       # 1 上 / -1 下 / 0 未知
            'cycle_length_max': None if pd.isna(clen) else int(clen),
            'bar_count': int(len(g)),
            # 周期振幅：默认用 extreme 口径（周期内极值摆动 = 真振幅），回退到 close 口径
            'amplitude_max': amp_extreme if amp_extreme is not None else amp_close,
            'amplitude_close': amp_close,                 # close 口径绝对值（周期涨跌幅，参考）
            'amplitude_close_signed': amp_close_signed,   # close 口径带符号（上涨正/下跌负）
            'amplitude_extreme': amp_extreme,             # extreme 口径（真·周期最大摆动）
            'cycle_1m_vol_max5': v5,
            'completed': completed,
        })

    lens = [c['cycle_length_max'] for c in cycles if c['cycle_length_max'] is not None]
    stats = {}
    if lens:
        s = pd.Series(lens)
        stats = {
            'count': len(lens),
            'min': int(s.min()), 'max': int(s.max()),
            'mean': round(float(s.mean()), 1), 'median': float(s.median()),
            # 总体标准差（ddof=0）；单样本时 std 为 0，供前端画正态分布用
            'std': round(float(s.std(ddof=0)), 2) if len(lens) > 1 else 0.0,
        }
    return jsonify({
        'success': True,
        'count': len(cycles),
        'stats': stats,
        'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S') if as_of_ts is not None else None,
        'period': period,
        'mode': 'live' if live_merged else 'after_close',
        'data': cycles,
    })


@stock_detail_bp.route('/api/<code>/15m/day', methods=['GET'])
def api_15m_day(code):
    """某一交易日的全部 15m 行（含所有字段），用于明细查看。"""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'success': False, 'message': '需要 date 参数 (YYYY-MM-DD)'}), 400
    try:
        target = pd.to_datetime(date_str).date()
    except Exception:
        return jsonify({'success': False, 'message': f'date 格式错误: {date_str}'}), 400

    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} 的 15m 数据'}), 404

    df['date'] = pd.to_datetime(df['date'])
    day_df = df[df['date'].dt.date == target].sort_values('date')
    if day_df.empty:
        return jsonify({'success': True, 'date': date_str, 'columns': list(df.columns),
                        'count': 0, 'data': []})

    columns = list(day_df.columns)
    records = []
    for _, row in day_df.iterrows():
        rec = {}
        for col in columns:
            val = row[col]
            if col == 'date' and pd.notna(val):
                rec[col] = pd.Timestamp(val).strftime('%H:%M:%S')
            else:
                rec[col] = _safe_jsonable(val)
        records.append(rec)
    return jsonify({
        'success': True,
        'date': date_str,
        'columns': columns,
        'count': len(records),
        'data': records,
    })


# A 股一个完整交易日的 16 根 15m K 线收盘时间
_EXPECTED_15M_TIMES = [
    '10:00', '10:15', '10:30', '10:45', '11:00', '11:15', '11:30',
    '13:15', '13:30', '13:45', '14:00', '14:15', '14:30', '14:45', '15:00',
    '09:45',
]
_EXPECTED_15M_SET = set(_EXPECTED_15M_TIMES)


@stock_detail_bp.route('/api/<code>/15m/daily_summary', methods=['GET'])
def api_15m_daily_summary(code):
    """按交易日统计 15m 数据完整性：每天应有 16 根 K 线。

    返回每个交易日的实际条数、缺失的时间点，并标出完全缺失的交易日。
    query:
      only_issues: 1 时只返回不完整/缺失的日期
      limit:       返回最近多少个交易日（默认 250）
    """
    only_issues = request.args.get('only_issues') in ('1', 'true', 'yes')
    try:
        limit = max(10, min(int(request.args.get('limit', 250)), 2000))
    except ValueError:
        limit = 250

    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} 的 15m 数据'}), 404

    df['date'] = pd.to_datetime(df['date'])
    df['day'] = df['date'].dt.date
    df['hm'] = df['date'].dt.strftime('%H:%M')

    expected_n = len(_EXPECTED_15M_SET)
    first_day = df['day'].min()
    last_day = df['day'].max()

    # 实际有数据的天 -> 该天出现的时间点集合
    by_day = df.groupby('day')['hm'].apply(set).to_dict()

    # 该区间内应有的交易日（用日历找出完全缺失的整天）
    try:
        from App.routes.data.download_data_route import get_trading_dates
        cal = get_trading_dates()
    except Exception:
        cal = None

    if cal:
        all_days = sorted(d for d in cal if first_day <= d <= last_day)
    else:
        # 回退：用工作日（周一~周五）粗略估算
        all_days = []
        cur = first_day
        while cur <= last_day:
            if cur.weekday() < 5:
                all_days.append(cur)
            cur += timedelta(days=1)

    rows = []
    n_complete = n_incomplete = n_missing = 0
    for d in all_days:
        times = by_day.get(d)
        if not times:
            status = 'missing'
            n_missing += 1
            rows.append({
                'date': d.isoformat(), 'count': 0, 'expected': expected_n,
                'status': status, 'first': None, 'last': None,
                'missing_times': sorted(_EXPECTED_15M_SET),
            })
            continue
        miss = sorted(_EXPECTED_15M_SET - times)
        if miss:
            status = 'incomplete'
            n_incomplete += 1
        else:
            status = 'complete'
            n_complete += 1
        rows.append({
            'date': d.isoformat(),
            'count': len(times),
            'expected': expected_n,
            'status': status,
            'first': min(times),
            'last': max(times),
            'missing_times': miss,
        })

    rows.sort(key=lambda r: r['date'], reverse=True)
    if only_issues:
        rows = [r for r in rows if r['status'] != 'complete']
    rows = rows[:limit]

    return jsonify({
        'success': True,
        'calendar_source': 'akshare' if cal else 'weekday_fallback',
        'expected_per_day': expected_n,
        'range': {'first': first_day.isoformat(), 'last': last_day.isoformat()},
        'summary': {
            'trading_days': len(all_days),
            'complete': n_complete,
            'incomplete': n_incomplete,
            'missing': n_missing,
        },
        'count': len(rows),
        'data': rows,
    })


def _list_1m_dates(code: str):
    """扫描 data/data/quarters 和 data/quarters，返回该股票所有可用 1m 日期"""
    root = _project_root()
    candidates = [
        root / 'data' / 'data' / 'quarters',
        root / 'data' / 'quarters',
    ]
    dates = set()
    for base in candidates:
        if not base.exists():
            continue
        # parquet 一个文件 = 一个季度，得读出来才知道日期
        for parquet in base.rglob(f'{code}.parquet'):
            try:
                df = pd.read_parquet(parquet, columns=['date'])
                df['date'] = pd.to_datetime(df['date'])
                for d in df['date'].dt.date.unique():
                    dates.add(d.isoformat())
            except Exception as e:
                logger.warning(f'读 {parquet} 失败: {e}')
    return sorted(dates, reverse=True)


@stock_detail_bp.route('/api/<code>/1m/dates', methods=['GET'])
def api_1m_dates(code):
    as_of_ts, err = _parse_as_of(request.args.get('as_of'))
    if err:
        return jsonify({'success': False, 'message': err}), 400

    dates = _list_1m_dates(code)
    if as_of_ts is not None:
        cutoff_str = as_of_ts.strftime('%Y-%m-%d')
        dates = [d for d in dates if d <= cutoff_str]
    return jsonify({
        'success': True,
        'count': len(dates),
        'dates': dates[:200],     # 返回最近 200 天，避免一次太多
        'earliest': dates[-1] if dates else None,
        'latest': dates[0] if dates else None,
        'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S') if as_of_ts is not None else None,
    })


@stock_detail_bp.route('/api/<code>/1m', methods=['GET'])
def api_1m(code):
    """单日的 1m 数据"""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'success': False, 'message': '需要 date 参数 (YYYY-MM-DD)'}), 400

    try:
        target = pd.to_datetime(date_str).date()
    except Exception:
        return jsonify({'success': False, 'message': f'date 格式错误: {date_str}'}), 400

    # 找包含该日期的季度 parquet
    quarter = (target.month - 1) // 3 + 1
    year = target.year
    root = _project_root()
    candidates = [
        root / 'data' / 'data' / 'quarters' / str(year) / f'Q{quarter}' / f'{code}.parquet',
        root / 'data' / 'quarters' / str(year) / f'Q{quarter}' / f'{code}.parquet',
        root / 'data' / 'data' / 'quarters' / str(year) / f'Q{quarter}' / f'{code}.csv',
    ]
    df = None
    for path in candidates:
        if path.exists():
            try:
                df = pd.read_parquet(path) if path.suffix == '.parquet' else pd.read_csv(path)
                break
            except Exception:
                continue

    if df is None or df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} {date_str} 的 1m 数据'}), 404

    df['date'] = pd.to_datetime(df['date'])
    df = df[df['date'].dt.date == target]
    if df.empty:
        return jsonify({'success': False, 'message': f'当日无数据: {date_str}'}), 404

    records = []
    for _, row in df.iterrows():
        records.append({
            'time': row['date'].strftime('%H:%M'),
            'open': float(row['open']),
            'close': float(row['close']),
            'high': float(row['high']),
            'low': float(row['low']),
            'volume': int(row['volume']) if pd.notna(row.get('volume')) else 0,
        })
    return jsonify({
        'success': True,
        'date': date_str,
        'count': len(records),
        'data': records,
    })


# ==================== 1m 完整性检测（按年份） ====================
_ONEMIN_FULL_BARS = 240      # A 股整日 1m 根数（9:30-11:30 + 13:00-15:00）
_ONEMIN_FULL_MIN = 238       # ≥ 此值视为"完整"（容忍个别缺分钟）


def _onemin_quarter_bases():
    root = _project_root()
    return [root / 'data' / 'data' / 'quarters', root / 'data' / 'quarters']


def _onemin_years(code: str):
    """有历史 1m 数据的年份 → {year: [季度...]}（只查文件存在，不读内容）。"""
    out = {}
    for base in _onemin_quarter_bases():
        if not base.exists():
            continue
        for ydir in base.iterdir():
            if not ydir.is_dir() or not ydir.name.isdigit():
                continue
            qs = []
            for q in ('Q1', 'Q2', 'Q3', 'Q4'):
                if (ydir / q / f'{code}.parquet').exists() or (ydir / q / f'{code}.csv').exists():
                    qs.append(q)
            if qs:
                out.setdefault(ydir.name, set()).update(qs)
    return {y: sorted(v) for y, v in out.items()}


def _onemin_day_counts(code: str, year: int):
    """读该年各季度 parquet 的 date 列 → {date(iso): bar_count}。"""
    counts = {}
    for base in _onemin_quarter_bases():
        ydir = base / str(year)
        if not ydir.exists():
            continue
        for q in ('Q1', 'Q2', 'Q3', 'Q4'):
            for ext in ('parquet', 'csv'):
                fp = ydir / q / f'{code}.{ext}'
                if not fp.exists():
                    continue
                try:
                    df = (pd.read_parquet(fp, columns=['date']) if ext == 'parquet'
                          else pd.read_csv(fp, usecols=['date']))
                    df['date'] = pd.to_datetime(df['date'])
                    vc = df['date'].dt.date.value_counts()
                    for d, n in vc.items():
                        iso = d.isoformat()
                        counts[iso] = counts.get(iso, 0) + int(n)
                except Exception as e:
                    logger.warning(f'读 {fp} 失败: {e}')
    return counts


def _onemin_completeness(code: str, year: int):
    """对比"应有交易日(data_stock_daily)"与"实际 1m 覆盖"，逐日定状态 + 汇总。"""
    day_counts = _onemin_day_counts(code, year)

    # 应有交易日（该股票该年在日 K 里出现过的日期）
    rows = (db.session.query(StockDaily.date)
            .filter(StockDaily.stock_code == code)
            .filter(StockDaily.date >= datetime(year, 1, 1))
            .filter(StockDaily.date < datetime(year + 1, 1, 1))
            .order_by(StockDaily.date.asc()).all())
    trading_days = [r[0].isoformat() for r in rows]

    days, full, short, missing = [], 0, 0, 0
    missing_list, short_list = [], []
    for d in trading_days:
        n = day_counts.get(d, 0)
        if n == 0:
            status = 'missing'; missing += 1; missing_list.append(d)
        elif n >= _ONEMIN_FULL_MIN:
            status = 'full'; full += 1
        else:
            status = 'short'; short += 1; short_list.append({'date': d, 'bars': n})
        days.append({'date': d, 'bars': n, 'status': status})

    # 1m 有、daily 无的"多余日"（信息项）
    extra = sorted([{'date': d, 'bars': n} for d, n in day_counts.items()
                    if d not in set(trading_days)], key=lambda x: x['date'])

    # 按季度细分
    quarters = {}
    for it in days:
        mo = int(it['date'][5:7]); q = f'Q{(mo - 1) // 3 + 1}'
        b = quarters.setdefault(q, {'expected': 0, 'full': 0, 'short': 0, 'missing': 0})
        b['expected'] += 1; b[it['status']] += 1

    one_dates = sorted(day_counts.keys())
    expected = len(trading_days)
    return {
        'code': code, 'year': year,
        'expected_days': expected,
        'full': full, 'short': short, 'missing': missing,
        'complete_rate': round(full / expected * 100, 1) if expected else None,
        'has_1m': bool(day_counts),
        'onemin_date_range': [one_dates[0], one_dates[-1]] if one_dates else None,
        'onemin_day_count': len(day_counts),
        'quarters': quarters,
        'days': days,
        'missing_list': missing_list,
        'short_list': short_list,
        'extra_list': extra,
        'full_bars': _ONEMIN_FULL_BARS,
    }


@stock_detail_bp.route('/api/<code>/1m/years', methods=['GET'])
def api_1m_years(code):
    y = _onemin_years(code)
    years = sorted(y.keys(), reverse=True)
    return jsonify({'success': True, 'years': years, 'quarters': y,
                    'latest': years[0] if years else None})


@stock_detail_bp.route('/api/<code>/1m/completeness', methods=['GET'])
def api_1m_completeness(code):
    year_s = (request.args.get('year') or '').strip()
    if not year_s.isdigit():
        return jsonify({'success': False, 'message': '需要 year 参数 (YYYY)'}), 400
    try:
        data = _onemin_completeness(code, int(year_s))
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.exception('1m 完整性检测失败')
        return jsonify({'success': False, 'message': str(e)}), 500


def _list_param_months(code: str):
    """扫描 data/RnnData/<month>/json/<code>.json，返回有该股票参数的月份列表"""
    root = _project_root() / 'data' / 'RnnData'
    months = []
    if not root.exists():
        return months
    for sub in root.iterdir():
        if not sub.is_dir() or sub.name == 'CommonFile':
            continue
        if (sub / 'json' / f'{code}.json').exists():
            months.append(sub.name)
    months.sort(reverse=True)
    return months


@stock_detail_bp.route('/api/<code>/params/months', methods=['GET'])
def api_param_months(code):
    months = _list_param_months(code)
    return jsonify({'success': True, 'count': len(months), 'months': months})


@stock_detail_bp.route('/api/<code>/params', methods=['GET'])
def api_params(code):
    """指定月份的标准化参数 + 所有非字典字段一并返回"""
    month = request.args.get('month')
    if not month:
        # 默认最新
        ms = _list_param_months(code)
        if not ms:
            return jsonify({'success': False, 'message': f'未找到 {code} 任何月份的标准化参数'}), 404
        month = ms[0]

    json_path = StockDataPath.json_data_path(month, code)
    if not os.path.exists(json_path):
        return jsonify({'success': False, 'message': f'未找到参数文件: {json_path}'}), 404

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 拆分：归一化字段（含 num_max/num_min） vs 标量字段
    normalized = {}
    scalars = {}
    for k, v in data.items():
        if isinstance(v, dict) and 'num_max' in v:
            normalized[k] = v
        else:
            scalars[k] = v

    return jsonify({
        'success': True,
        'month': month,
        'normalized_fields_count': len(normalized),
        'normalized_fields': normalized,
        'other_fields': scalars,
    })


@stock_detail_bp.route('/api/<code>/metrics', methods=['GET'])
def api_metrics(code):
    """关键指标摘要：从最近月份参数 + 15m 行情 综合"""
    as_of_ts, err = _parse_as_of(request.args.get('as_of'))
    if err:
        return jsonify({'success': False, 'message': err}), 400

    # 1) 最近月份参数
    months = _list_param_months(code)
    # 时光机视角：只用 ≤ as_of 月份的参数（YYYY-MM 字符串比较即可）
    if as_of_ts is not None:
        cutoff_month = as_of_ts.strftime('%Y-%m')
        months = [m for m in months if m <= cutoff_month]
    params = {}
    if months:
        try:
            with open(StockDataPath.json_data_path(months[0], code), 'r', encoding='utf-8') as f:
                params = json.load(f)
        except Exception:
            params = {}

    # 2) 15m 数据计算近期统计
    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': True, 'message': '无 15m 数据', 'params_month': months[0] if months else None, 'metrics': {}})

    df['date'] = pd.to_datetime(df['date'])
    if as_of_ts is not None:
        df = df[df['date'] <= as_of_ts]
        if df.empty:
            return jsonify({'success': True, 'message': f'as_of 之前无 15m 数据',
                            'params_month': months[0] if months else None,
                            'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S'),
                            'metrics': {}})
    df['amplitude'] = (df['close'] - df['close'].shift(1)) / df['close'].shift(1)

    # 近 30 / 90 个交易日（约）—— 锚点是 as_of 或当下
    anchor = as_of_ts if as_of_ts is not None else pd.Timestamp.now()
    recent_30d = df[df['date'] >= anchor - pd.Timedelta(days=45)]
    recent_90d = df[df['date'] >= anchor - pd.Timedelta(days=130)]

    def _pick(p, key, sub):
        try:
            return p[key][sub]
        except (KeyError, TypeError):
            return None

    metrics = {
        # 来自 JSON 参数（标准化 max/min，反映模型见过的范围）
        'volume_param_max': _pick(params, 'volume', 'num_max'),
        'volume_param_min': _pick(params, 'volume', 'num_min'),
        'cycle_length_max_param': _pick(params, 'CycleLengthMax', 'num_max'),
        'cycle_amplitude_max_param': _pick(params, 'CycleAmplitudeMax', 'num_max'),
        'cycle_amplitude_min_param': _pick(params, 'CycleAmplitudeMax', 'num_min'),
        'daily_vol_ema_param': params.get('DailyVolEma'),
        # 来自实时 15m 计算
        'volume_max_recent_30d': float(recent_30d['volume'].max()) if not recent_30d.empty else None,
        'volume_mean_recent_30d': float(recent_30d['volume'].mean()) if not recent_30d.empty else None,
        'amplitude_max_recent_90d': float(recent_90d['amplitude'].max()) if not recent_90d.empty else None,
        'amplitude_min_recent_90d': float(recent_90d['amplitude'].min()) if not recent_90d.empty else None,
        'close_latest': float(df['close'].iloc[-1]),
        'close_max_recent_90d': float(recent_90d['close'].max()) if not recent_90d.empty else None,
        'close_min_recent_90d': float(recent_90d['close'].min()) if not recent_90d.empty else None,
        'rows_15m': len(df),
        'first_date_15m': df['date'].min().strftime('%Y-%m-%d'),
        'last_date_15m': df['date'].max().strftime('%Y-%m-%d'),
    }
    # 清理 None / NaN
    metrics = {k: _safe_jsonable(v) for k, v in metrics.items()}

    return jsonify({
        'success': True,
        'params_month': months[0] if months else None,
        'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S') if as_of_ts is not None else None,
        'metrics': metrics,
    })


@stock_detail_bp.route('/api/<code>/predictions', methods=['GET'])
def api_predictions(code):
    as_of_ts, err = _parse_as_of(request.args.get('as_of'))
    if err:
        return jsonify({'success': False, 'message': err}), 400
    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 500))
    except ValueError:
        limit = 50

    q = RnnRunningRecord.query.filter_by(code=code)
    if as_of_ts is not None:
        q = q.filter(RnnRunningRecord.created_at <= as_of_ts.to_pydatetime())
    rows = q.order_by(RnnRunningRecord.id.desc()).limit(limit).all()
    return jsonify({
        'success': True,
        'count': len(rows),
        'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S') if as_of_ts is not None else None,
        'data': [r.to_dict() for r in rows],
    })


@stock_detail_bp.route('/api/<code>/fund_holdings', methods=['GET'])
def api_fund_holdings(code):
    """基金持仓数随时间的变化（用 StockDaily 表的 fund_holdings_count 字段）"""
    as_of_ts, err = _parse_as_of(request.args.get('as_of'))
    if err:
        return jsonify({'success': False, 'message': err}), 400
    try:
        days = max(1, min(int(request.args.get('days', 365)), 3650))
    except ValueError:
        days = 365

    anchor_date = as_of_ts.date() if as_of_ts is not None else datetime.now().date()
    cutoff = anchor_date - timedelta(days=days)
    q = (StockDaily.query
         .filter(StockDaily.stock_code == code,
                 StockDaily.date >= cutoff,
                 StockDaily.fund_holdings_count > 0))
    if as_of_ts is not None:
        q = q.filter(StockDaily.date <= anchor_date)
    rows = q.order_by(StockDaily.date.asc()).all()

    series = [{
        'date': r.date.isoformat(),
        'fund_count': r.fund_holdings_count,
        'ratio_sum': r.fund_holdings_ratio,
        'ratio_avg': r.fund_holdings_avg_ratio,
        'ratio_max': r.fund_holdings_max_ratio,
        'close': r.close,
    } for r in rows]

    return jsonify({
        'success': True,
        'count': len(series),
        'as_of': as_of_ts.strftime('%Y-%m-%d %H:%M:%S') if as_of_ts is not None else None,
        'data': series,
    })
