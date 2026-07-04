# -*- coding: utf-8 -*-
"""用板块成分股数据合成「自己的板块指数」，与东财板块数据并存、可切换对比。

思路（加权收益率链式指数，贴近东财行业指数口径）：
  · 权重 w_i = 成分股流通市值占比（circ / total / equal 可选）；
  · 每根 bar：各股收益率 r_i = close_i,t / close_i,t-1 − 1，
    对「当根有数据且有权重」的成分股重新归一化后加权求和 R_t = Σ w_i·r_i；
  · 链式累乘得指数收盘 Index_t = Index_{t-1}·(1+R_t)；
    open/high/low 用各股对应价对「前收」的收益率同法合成；
  · 锚定：整条序列缩放，使最新收盘 == 东财同板块最新真实收盘（图上视觉连续）；
    无东财数据则最新收盘设为 1000。

存储：写到**独立的合成代码** synth_code = <board_code> + 'S'，
  · 日K → data_stock_daily(stock_code='BK1036S')
  · 15m → data/15m/BK1036S.parquet
东财原始数据（BK1036）完全不动。图表/评分接口对 code 是通用的，
前端切到 BK1036S 即读合成板块，切回 BK1036 即读东财板块。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from App.exts import db

logger = logging.getLogger(__name__)

SYNTH_SUFFIX = 'S'
_OHLC_COLS = ['open', 'high', 'low', 'close', 'volume', 'money']


def synth_code(board_code: str) -> str:
    """板块代码 → 合成板块代码（BK1036 → BK1036S）。"""
    bc = (board_code or '').strip().upper()
    return bc if bc.endswith(SYNTH_SUFFIX) else bc + SYNTH_SUFFIX


def is_synth_code(code: str) -> bool:
    return (code or '').strip().upper().endswith(SYNTH_SUFFIX)


def synth_exists(board_code: str) -> dict:
    """该板块是否已合成过（日K 行数 / 15m 文件）。供前端切源前判断。"""
    from config import Config
    scode = synth_code(board_code)
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        n = conn.execute(text('SELECT COUNT(*) FROM data_stock_daily WHERE stock_code=:c'),
                         {'c': scode}).scalar()
    has15 = (Path(Config.get_project_root()) / 'data' / '15m' / f'{scode}.parquet').exists()
    return {'synth_code': scode, 'daily_rows': int(n or 0),
            'has_15m': has15, 'exists': bool(n) or has15}


# ================= 权重 =================
def _members_and_weights(board_code: str, weighting: str = 'circ'):
    """最新名单日期的成分股 + 权重。weighting: circ/total/equal。

    返回 (codes:list, weights:dict[code->float])。circ/total 只含有市值的成分股，
    equal 则所有成分股等权。
    """
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT ie.stock_code, ie.total_cap, ie.circ_cap
            FROM industry_eastmoney ie
            JOIN (SELECT MAX(date) d FROM industry_eastmoney WHERE board_code=:c) m
              ON ie.date = m.d
            WHERE ie.board_code = :c
            """), {'c': board_code}).fetchall()
    codes = [r[0] for r in rows]
    if weighting == 'equal':
        weights = {r[0]: 1.0 for r in rows}
    else:
        col = 2 if weighting == 'circ' else 1  # circ_cap / total_cap
        weights = {r[0]: float(r[col]) for r in rows if r[col]}
    return codes, weights


# ================= 合成核心 =================
def _synthesize(frames: dict, weights: dict, anchor_close=None, base=1000.0) -> pd.DataFrame:
    """frames: {code: df(index=date, 含 open/high/low/close/volume/money)} → 合成指数 df。

    返回列：date/open/high/low/close/volume/money（date 为列）。
    """
    frames = {k: v for k, v in frames.items() if v is not None and not v.empty}
    if not frames:
        return pd.DataFrame()

    close_p = pd.DataFrame({k: v['close'] for k, v in frames.items()}).sort_index()
    open_p = pd.DataFrame({k: v['open'] for k, v in frames.items()}).reindex_like(close_p)
    high_p = pd.DataFrame({k: v['high'] for k, v in frames.items()}).reindex_like(close_p)
    low_p = pd.DataFrame({k: v['low'] for k, v in frames.items()}).reindex_like(close_p)
    money_p = pd.DataFrame({k: v.get('money') for k, v in frames.items()}).reindex_like(close_p)
    vol_p = pd.DataFrame({k: v.get('volume') for k, v in frames.items()}).reindex_like(close_p)

    prev = close_p.shift(1)
    r_close = close_p / prev - 1
    r_open = open_p / prev - 1
    r_high = high_p / prev - 1
    r_low = low_p / prev - 1

    wser = pd.Series(weights, dtype='float64')
    cols_w = close_p.columns.intersection(wser.index)  # 有权重的成分股列

    def _wr(r_row):
        valid = r_row[cols_w].dropna().index
        if len(valid) == 0:
            return np.nan
        ww = wser[valid]
        ww = ww / ww.sum()
        return float((r_row[valid] * ww).sum())

    R_close = r_close.apply(_wr, axis=1)
    R_open = r_open.apply(_wr, axis=1)
    R_high = r_high.apply(_wr, axis=1)
    R_low = r_low.apply(_wr, axis=1)

    idx_close = (1 + R_close.fillna(0)).cumprod()          # 基=1
    prev_idx = idx_close.shift(1).fillna(1.0)
    idx_open = prev_idx * (1 + R_open.fillna(0))
    idx_high = prev_idx * (1 + R_high.fillna(0))
    idx_low = prev_idx * (1 + R_low.fillna(0))

    out = pd.DataFrame({
        'date': idx_close.index,
        'open': idx_open.values, 'high': idx_high.values,
        'low': idx_low.values, 'close': idx_close.values,
        # 指数量能：成分股成交额/量在当根求和（NaN 视为 0）
        'volume': vol_p.sum(axis=1, min_count=1).values,
        'money': money_p.sum(axis=1, min_count=1).values,
    })
    # high/low 兜底：合成的极值须包住 open/close
    out['high'] = out[['high', 'open', 'close']].max(axis=1)
    out['low'] = out[['low', 'open', 'close']].min(axis=1)

    # 锚定：缩放使最新收盘对齐东财最新真实收盘（无则 base）
    last = out['close'].iloc[-1]
    target = anchor_close if (anchor_close and anchor_close > 0) else base
    if last and last > 0:
        scale = target / last
        for c in ('open', 'high', 'low', 'close'):
            out[c] = out[c] * scale
    return out


def _add_macd(df: pd.DataFrame) -> pd.DataFrame:
    """在合成 close 上算 Dif/Dea/MACD（供 15m/60m 图表画 MACD）。"""
    c = df['close']
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['Dif'] = ema12 - ema26
    df['Dea'] = df['Dif'].ewm(span=9, adjust=False).mean()
    df['MACD'] = (df['Dif'] - df['Dea']) * 2
    return df


# ================= 数据加载 =================
def _load_members_daily(codes) -> dict:
    eng = db.engines['quanttradingsystem']
    frames = {}
    with eng.connect() as conn:
        for code in codes:
            r = conn.execute(text(
                'SELECT date,open,high,low,close,volume,money FROM data_stock_daily '
                'WHERE stock_code=:c ORDER BY date'), {'c': code}).fetchall()
            if r:
                d = pd.DataFrame(r, columns=['date', 'open', 'high', 'low', 'close', 'volume', 'money'])
                d['date'] = pd.to_datetime(d['date'])
                frames[code] = d.set_index('date')
    return frames


def _load_members_15m(codes, d15: Path) -> dict:
    frames = {}
    for code in codes:
        p = d15 / f'{code}.parquet'
        if not p.exists():
            continue
        try:
            d = pd.read_parquet(p, columns=['date', 'open', 'high', 'low', 'close', 'volume', 'money'])
            d['date'] = pd.to_datetime(d['date'])
            frames[code] = d.set_index('date').sort_index()
        except Exception:
            logger.debug(f'读取 15m 失败 {code}')
    return frames


def _em_last_close_daily(board_code: str):
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        r = conn.execute(text(
            'SELECT close FROM data_stock_daily WHERE stock_code=:c ORDER BY date DESC LIMIT 1'),
            {'c': board_code}).fetchone()
    return float(r[0]) if r and r[0] else None


def _em_last_close_15m(board_code: str, d15: Path):
    p = d15 / f'{board_code}.parquet'
    if not p.exists():
        return None
    try:
        d = pd.read_parquet(p, columns=['date', 'close'])
        return float(pd.to_numeric(d['close'], errors='coerce').dropna().iloc[-1])
    except Exception:
        return None


# ================= 写入（写到合成代码，东财数据不动）=================
def _write_daily(scode: str, df: pd.DataFrame) -> int:
    """整段替换 data_stock_daily 里该合成代码的行。返回写入行数。"""
    if df is None or df.empty:
        return 0
    eng = db.engines['quanttradingsystem']
    with eng.begin() as conn:
        conn.execute(text('DELETE FROM data_stock_daily WHERE stock_code=:c'), {'c': scode})
        rows = [{'c': scode, 'd': r.date.date() if hasattr(r.date, 'date') else r.date,
                 'o': float(r.open), 'cl': float(r.close), 'h': float(r.high),
                 'l': float(r.low), 'v': float(r.volume) if pd.notna(r.volume) else None,
                 'm': float(r.money) if pd.notna(r.money) else None}
                for r in df.itertuples(index=False)]
        conn.execute(text(
            'INSERT INTO data_stock_daily (stock_code,date,open,close,high,low,volume,money) '
            'VALUES (:c,:d,:o,:cl,:h,:l,:v,:m)'), rows)
    return len(rows)


def _write_15m(scode: str, df: pd.DataFrame, d15: Path) -> int:
    if df is None or df.empty:
        return 0
    d15.mkdir(parents=True, exist_ok=True)
    df.to_parquet(d15 / f'{scode}.parquet', index=False)
    return len(df)


# ================= 对外：构建 =================
def build_synthetic_board(board_code: str, weighting: str = 'circ',
                          timeframes=('daily', '15m')) -> dict:
    """合成某板块的自有指数并写入合成代码。返回统计 dict。"""
    from config import Config
    d15 = Path(Config.get_project_root()) / 'data' / '15m'
    scode = synth_code(board_code)
    codes, weights = _members_and_weights(board_code, weighting)
    res = {'board_code': board_code, 'synth_code': scode, 'weighting': weighting,
           'members': len(codes), 'weighted': len(weights)}

    if 'daily' in timeframes:
        frames = _load_members_daily(codes)
        idf = _synthesize(frames, weights, anchor_close=_em_last_close_daily(board_code))
        res['daily_rows'] = _write_daily(scode, idf)
        res['daily_range'] = ([idf['date'].min().date().isoformat(),
                               idf['date'].max().date().isoformat()] if not idf.empty else None)

    if '15m' in timeframes:
        frames = _load_members_15m(codes, d15)
        idf = _synthesize(frames, weights, anchor_close=_em_last_close_15m(board_code, d15))
        if not idf.empty:
            idf = _add_macd(idf)
        res['m15_rows'] = _write_15m(scode, idf, d15)
        res['m15_range'] = ([idf['date'].min().isoformat(), idf['date'].max().isoformat()]
                            if not idf.empty else None)
    return res


# ================= 后台任务 + 进度 =================
_state = {'running': False, 'board': None, 'phase': None, 'result': None,
          'error': None, 'started': None}
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return dict(_state)


def _run(app, board_code, weighting, timeframes):
    with app.app_context():
        try:
            with _lock:
                _state['phase'] = '合成中'
            res = build_synthetic_board(board_code, weighting, timeframes)
            with _lock:
                _state['result'] = res
                _state['phase'] = '完成'
        except Exception as e:
            logger.exception('[synth] 合成板块失败')
            with _lock:
                _state['error'] = str(e)
        finally:
            with _lock:
                _state['running'] = False


def start_build(app, board_code, weighting='circ', timeframes=('daily', '15m')):
    """启动后台合成。返回 (ok, message)。"""
    with _lock:
        if _state['running']:
            return False, '已有合成任务在进行中'
        _state.update({'running': True, 'board': board_code, 'phase': '排队',
                       'result': None, 'error': None, 'started': time.strftime('%H:%M:%S')})
    threading.Thread(target=_run, args=(app, board_code, weighting, tuple(timeframes)),
                     daemon=True).start()
    return True, f'已开始合成 {synth_code(board_code)}'
