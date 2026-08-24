"""时序热度分（heat_ts）计算 —— 见 App/models/strategy/BoardHeatTs.py 的口径说明。

    heat_ts = 50 × pct_self(涨跌幅, N) + 50 × pct_self(成交额, N)

pct_self = 当日值在**该板块自己**最近 N 个交易日（含当日）中的百分位，0-100。
不涉及任何横截面，所以：
  - 可以用历史日K整段回补（横截面热度做不到，东财只给当日快照）
  - 不受"当天参与排名的板块数"变化影响

日K来源（与趋势打分同一条链）：
  1. `data_stock_daily` 的 BKxxxx —— 东财板块日K（只有一级行业有，且只回溯到 2026-03）
  2. `board_synth_daily` 的 BKxxxxS —— 自建合成指数（概念板块靠它，回到 2021 年）

对外：
    compute_board(board_code, window=250, min_periods=40) -> DataFrame
    persist_board(board_code, ...)  -> dict 统计
    compute_pool(codes=None, ...)   -> dict 批量统计
"""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import text

from App.exts import db
from App.models.strategy.BoardHeatTs import (
    BoardHeatTs, HEAT_TS_VERSION, SRC_EM_DAILY, SRC_SYNTH,
)

logger = logging.getLogger(__name__)

WINDOW = 250        # 分位窗口（约一年交易日）
MIN_PERIODS = 40    # 少于这么多天不给分（样本太短的分位没有意义）


# 某源最新日期落后另一源超过这么多天，就判定为"过期源"，即使它更长也不用
STALE_GAP_DAYS = 7


def _load_daily(board_code: str, min_len: int = MIN_PERIODS + 1) -> tuple:
    """加载板块全历史日K。返回 (DataFrame[date, close, amount], source)。

    两个候选源：东财板块日K(BKxxxx) 与自建合成指数(BKxxxxS)。**不做拼接** ——
    两条序列的基点和口径不同（合成指数是自定基期 1000 的加权指数），接起来会在
    接缝处造出假的涨跌幅和成交额跳变。只能二选一。

    选源规则（缺一不可）：
      1. 谁明显更新鲜用谁 —— 只按"谁更长"选会踩坑：早期建的 7 个行业合成指数
         停在 2026-08-07，比东财日K旧半个月，但行数多十倍，按长度选就会让
         heat_ts 永远停在旧日期。
      2. 新鲜度相当时，选更长的（分位窗口越长越稳）。
      3. 被选中的那条必须够长（≥ min_len），否则退到另一条。
    """
    eng = db.engines['quanttradingsystem']
    code = (board_code or '').strip().upper()
    if not code:
        return pd.DataFrame(), None

    def _q(sql, params):
        with eng.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=['date', 'close', 'amount'])

    em = _q("SELECT date, close, money FROM data_stock_daily "
            "WHERE stock_code = :c ORDER BY date", {'c': code})
    syn = _q("SELECT date, close, money FROM board_synth_daily "
             "WHERE stock_code = :c ORDER BY date", {'c': f'{code}S'})

    if em.empty and syn.empty:
        return pd.DataFrame(), None
    if syn.empty:
        return em, SRC_EM_DAILY
    if em.empty:
        return syn, SRC_SYNTH

    em_last = pd.to_datetime(em['date']).max()
    syn_last = pd.to_datetime(syn['date']).max()
    gap = (em_last - syn_last).days

    if gap > STALE_GAP_DAYS and len(em) >= min_len:
        return em, SRC_EM_DAILY          # 合成指数过期
    if -gap > STALE_GAP_DAYS and len(syn) >= min_len:
        return syn, SRC_SYNTH            # 东财日K过期
    # 新鲜度相当 → 选更长的（够长才选）
    if len(syn) >= len(em) and len(syn) >= min_len:
        return syn, SRC_SYNTH
    if len(em) >= min_len:
        return em, SRC_EM_DAILY
    return (syn, SRC_SYNTH) if len(syn) >= len(em) else (em, SRC_EM_DAILY)


def compute_board(board_code: str, window: int = WINDOW,
                  min_periods: int = MIN_PERIODS) -> tuple:
    """算一个板块的全历史 heat_ts。返回 (DataFrame, source)。"""
    df, src = _load_daily(board_code, min_len=min_periods + 1)
    if df.empty or len(df) < min_periods + 1:
        return pd.DataFrame(), src

    df = df.copy()
    df['date'] = pd.to_datetime(df['date']).dt.date
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df = df.dropna(subset=['close']).sort_values('date').reset_index(drop=True)
    df = df[df['close'] > 0].reset_index(drop=True)
    if len(df) < min_periods + 1:
        return pd.DataFrame(), src

    df['change_pct'] = df['close'].pct_change() * 100.0

    # 滚动分位：rank(pct=True) 给的是"当日值在窗口内的名次占比"，正是要的分位
    def _pct(s):
        return (s.rolling(window, min_periods=min_periods)
                 .rank(pct=True) * 100.0)

    df['pct_chg'] = _pct(df['change_pct'])
    df['pct_amt'] = _pct(df['amount']) if df['amount'].notna().any() else np.nan

    if df['pct_amt'].isna().all():
        # 没有成交额（极少数旧数据）→ 退化为只用涨幅，口径要标出来
        df['heat_ts'] = df['pct_chg']
        logger.warning(f'[heat_ts] {board_code} 无成交额，heat_ts 退化为纯涨幅分位')
    else:
        df['heat_ts'] = 0.5 * df['pct_chg'] + 0.5 * df['pct_amt']

    df = df.dropna(subset=['heat_ts']).reset_index(drop=True)
    for c in ('pct_chg', 'pct_amt', 'heat_ts', 'change_pct'):
        df[c] = df[c].round(2)
    return df, src


def persist_board(board_code: str, board_name: str = None, window: int = WINDOW,
                  min_periods: int = MIN_PERIODS, incremental: bool = True) -> dict:
    """算并落库。incremental=True 时只补最新日期之后的行（默认）。"""
    BoardHeatTs.ensure_table()
    df, src = compute_board(board_code, window=window, min_periods=min_periods)
    if df.empty:
        return {'board_code': board_code, 'rows': 0, 'source': src,
                'error': '日K不足或缺失'}

    if incremental:
        last = BoardHeatTs.latest_date(board_code)
        if last:
            df = df[df['date'] > last]
    if df.empty:
        return {'board_code': board_code, 'rows': 0, 'source': src, 'skipped': '已最新'}

    if not incremental:
        (BoardHeatTs.query.filter(BoardHeatTs.board_code == board_code)
         .delete(synchronize_session=False))

    now = datetime.utcnow()
    db.session.bulk_insert_mappings(BoardHeatTs, [{
        'board_code': board_code, 'board_name': board_name,
        'date': r.date, 'close': float(r.close),
        'change_pct': (None if pd.isna(r.change_pct) else float(r.change_pct)),
        'amount': (None if pd.isna(r.amount) else float(r.amount)),
        'pct_chg': (None if pd.isna(r.pct_chg) else float(r.pct_chg)),
        'pct_amt': (None if pd.isna(r.pct_amt) else float(r.pct_amt)),
        'heat_ts': float(r.heat_ts),
        'window_n': window, 'data_source': src,
        'version': HEAT_TS_VERSION, 'created_at': now,
    } for r in df.itertuples(index=False)])
    db.session.commit()
    return {'board_code': board_code, 'rows': len(df), 'source': src,
            'range': [str(df['date'].iloc[0]), str(df['date'].iloc[-1])]}


def compute_pool(codes=None, window: int = WINDOW, min_periods: int = MIN_PERIODS,
                 incremental: bool = True) -> dict:
    """批量：默认给 L1 候选池全部板块算 heat_ts。"""
    from App.models.evaluation.Board import Board, TIER_L1
    BoardHeatTs.ensure_table()
    Board.ensure_table()

    if codes:
        targets = Board.query.filter(Board.board_code.in_(list(codes))).all()
    else:
        targets = Board.list_by_tier(TIER_L1)

    ok, empty, failed, total_rows = 0, [], [], 0
    for b in targets:
        try:
            res = persist_board(b.board_code, b.board_name, window=window,
                                min_periods=min_periods, incremental=incremental)
        except Exception as e:
            db.session.rollback()
            failed.append({'board_code': b.board_code, 'error': str(e)[:150]})
            continue
        if res.get('error'):
            empty.append({'board_code': b.board_code, 'board_name': b.board_name,
                          'error': res['error']})
        else:
            ok += 1
            total_rows += res.get('rows', 0)
    logger.info(f'[heat_ts] 完成 {ok}/{len(targets)}，写入 {total_rows} 行，'
                f'无数据 {len(empty)}，失败 {len(failed)}')
    return {'targets': len(targets), 'ok': ok, 'rows': total_rows,
            'empty': empty, 'failed': failed,
            'window': window, 'min_periods': min_periods}


def series(board_code: str, days: int = 250):
    """取单板块 heat_ts 序列（升序），给详情图用。"""
    BoardHeatTs.ensure_table()
    rows = (BoardHeatTs.query.filter(BoardHeatTs.board_code == board_code)
            .order_by(BoardHeatTs.date.desc()).limit(days).all())
    return [r.to_dict() for r in reversed(rows)]
