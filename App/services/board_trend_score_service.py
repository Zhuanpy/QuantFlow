# -*- coding: utf-8 -*-
"""
板块趋势打分服务（v1）

数据源：`datadaily.{board_code}`（每个板块一张日 K 表，列含 date/open/close/high/low/volume）。

输出：
- 6 个 0-100 子分（price_structure / ma / macd / volume / momentum / volatility）
- 趋势阶段（up_early / up_late / down_early / down_late / unknown）+ 置信度
- 趋势强度（strong / medium / weak / none）+ 交易信号（buy / hold / sell / wait / none）
- 综合分 total_score（加权平均）

公式以「看多即高分」为统一方向：分越高表示当前对多头越有利。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

FORMULA_VERSION = 'v1'

# 子分权重（合计 100，纯多头视角；置信度单独算，不进 total_score）
WEIGHTS = {
    'price_structure_score': 15,
    'ma_score':              20,
    'macd_score':            25,
    'volume_score':          15,
    'momentum_score':        15,
    'volatility_score':      10,
}

DAILY_DB = 'datadaily'   # 板块日 K 所在库
LOOKBACK_DAYS = 250      # 计算窗口


# ============== 工具函数 ==============
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False).mean()
    avg_dn = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_up / avg_dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([(high - low),
                    (high - prev).abs(),
                    (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


def _clip(v: float, lo: float = 0, hi: float = 100) -> float:
    return float(max(lo, min(hi, v)))


def _linmap(v: float, lo: float, hi: float, out_lo: float = 0, out_hi: float = 100) -> float:
    """把 [lo, hi] 线性映射到 [out_lo, out_hi]，超出截断"""
    if hi == lo:
        return (out_lo + out_hi) / 2
    return _clip(out_lo + (v - lo) / (hi - lo) * (out_hi - out_lo), 0, 100)


# ============== 数据加载 ==============
def _load_board_daily(board_code: str, end_date: date,
                      lookback: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """加载板块日 K（截至 end_date 的最近 lookback 行）。

    数据源优先级：
      1. quanttradingsystem.data_stock_daily（按 stock_code='BKxxxx' 过滤；最新数据在这里）
      2. datadaily.{board_code 小写}（旧的一表一板块，作 fallback）

    返回 DataFrame 按 date 升序，列：date/open/close/high/low/volume/money。
    """
    from App.exts import db
    code = board_code.strip()
    if not code:
        return pd.DataFrame()

    eng = db.engines['quanttradingsystem']
    df = pd.DataFrame()

    # 1) 先尝试 data_stock_daily（最新数据）
    try:
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT date, open, close, high, low, volume, money
                FROM data_stock_daily
                WHERE stock_code = :code AND date <= :end_date
                ORDER BY date DESC
                LIMIT :lim
            """), {'code': code, 'end_date': end_date, 'lim': lookback}).fetchall()
        if rows:
            df = pd.DataFrame(rows, columns=['date', 'open', 'close', 'high', 'low', 'volume', 'money'])
    except Exception as e:
        logger.debug(f"data_stock_daily 读取 {code} 失败: {e}")

    # 2) 回退到旧表 datadaily.{code 小写}
    if df.empty or len(df) < 20:
        try:
            with eng.connect() as conn:
                rows = conn.execute(text(f"""
                    SELECT date, open, close, high, low, volume, money
                    FROM {DAILY_DB}.{code.lower()}
                    WHERE date <= :end_date
                    ORDER BY date DESC
                    LIMIT :lim
                """), {'end_date': end_date, 'lim': lookback}).fetchall()
            if rows:
                df_old = pd.DataFrame(rows, columns=['date', 'open', 'close', 'high', 'low', 'volume', 'money'])
                # 合并去重，新数据优先
                if df.empty:
                    df = df_old
                else:
                    df = pd.concat([df, df_old]).drop_duplicates(subset=['date'])
        except Exception as e:
            logger.debug(f"{DAILY_DB}.{code.lower()} 读取失败: {e}")

    if df.empty:
        return df

    df = df.sort_values('date').reset_index(drop=True)
    for c in ('open', 'close', 'high', 'low', 'volume', 'money'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


# ============== 单维度子分 ==============
def _score_price_structure(df: pd.DataFrame) -> Tuple[float, dict]:
    """价格结构：相对 60 日高低区间的位置（0=底，100=顶），并对突破/破位做调整"""
    close = df['close']
    high60 = df['high'].rolling(60, min_periods=20).max().iloc[-1]
    low60 = df['low'].rolling(60, min_periods=20).min().iloc[-1]
    cur = close.iloc[-1]
    if pd.isna(high60) or pd.isna(low60) or high60 <= low60:
        return 50.0, {'pos_60d': None}
    pos = (cur - low60) / (high60 - low60) * 100

    # 突破/破位调整：如果今日创 20 日新高，加 5；20 日新低，减 5
    if len(df) >= 21:
        prev20_max = df['high'].iloc[-21:-1].max()
        prev20_min = df['low'].iloc[-21:-1].min()
        if cur > prev20_max:
            pos += 5
        if cur < prev20_min:
            pos -= 5

    return _clip(pos), {'pos_60d': round(float(pos), 1),
                        'high60': float(high60), 'low60': float(low60)}


def _score_ma(df: pd.DataFrame) -> Tuple[float, dict]:
    """均线系统：close vs MA20 vs MA60 + MA20 斜率"""
    close = df['close']
    if len(close) < 20:
        return 50.0, {}
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    cur = float(close.iloc[-1])
    m20 = float(ma20.iloc[-1]) if not pd.isna(ma20.iloc[-1]) else None
    m60 = float(ma60.iloc[-1]) if not pd.isna(ma60.iloc[-1]) else None

    # MA20 斜率（最近 5 日）
    slope20 = None
    if not pd.isna(ma20.iloc[-1]) and len(ma20) >= 6 and not pd.isna(ma20.iloc[-6]):
        slope20 = (ma20.iloc[-1] - ma20.iloc[-6]) / max(abs(ma20.iloc[-6]), 1e-9) * 100

    score = 50.0
    if m20 is not None and m60 is not None:
        if cur > m20 > m60:
            score = 80.0
            if slope20 and slope20 > 0:
                score = 95.0
        elif m20 > m60:
            score = 65.0
        elif cur > m20 and m20 < m60:
            score = 55.0
        elif cur < m20 < m60:
            score = 20.0
            if slope20 and slope20 < 0:
                score = 5.0
        else:
            score = 35.0
    elif m20 is not None:
        score = 65.0 if cur > m20 else 35.0

    return score, {
        'ma20': round(m20, 4) if m20 else None,
        'ma60': round(m60, 4) if m60 else None,
        'slope20_pct': round(slope20, 2) if slope20 is not None else None,
    }


def _score_macd(df: pd.DataFrame) -> Tuple[float, dict]:
    """MACD：DIF 零轴位置 + 柱体方向"""
    close = df['close']
    if len(close) < 30:
        return 50.0, {}
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    bar = (dif - dea) * 2

    cur = float(close.iloc[-1])
    d_dif = float(dif.iloc[-1])
    d_dea = float(dea.iloc[-1])
    d_bar = float(bar.iloc[-1])
    bar_prev = float(bar.iloc[-2]) if len(bar) >= 2 else 0.0

    # DIF 相对价格的位置：DIF/close*100 映射 [-2, 2] → [0, 100]
    dif_score = _linmap(d_dif / max(cur, 1e-9) * 100, -2, 2)
    # 柱体方向：在变化（向上）加分，向下减分
    bar_dir = 1 if d_bar > bar_prev else -1
    bar_score = 65 if bar_dir > 0 else 35
    if d_dif > 0 and d_dea > 0:
        bar_score += 15
    elif d_dif < 0 and d_dea < 0:
        bar_score -= 15

    score = 0.55 * dif_score + 0.45 * _clip(bar_score)
    return _clip(score), {
        'dif': round(d_dif, 4), 'dea': round(d_dea, 4),
        'bar': round(d_bar, 4), 'bar_dir': bar_dir,
    }


def _score_volume(df: pd.DataFrame) -> Tuple[float, dict]:
    """量能：v5 / v60 比例 + 当日量相对 5 日均的偏离"""
    vol = df['volume']
    if len(vol) < 60:
        return 50.0, {}
    v5 = vol.tail(5).mean()
    v60 = vol.tail(60).mean()
    if v60 <= 0:
        return 50.0, {}
    ratio_5_60 = v5 / v60
    # 1.5 → 90 分；1.0 → 60 分；0.5 → 30 分；2.0 以上截断
    score = _linmap(ratio_5_60, 0.5, 2.0)
    return score, {'v5_v60_ratio': round(float(ratio_5_60), 3)}


def _score_momentum(df: pd.DataFrame) -> Tuple[float, dict]:
    """动量：RSI(14)。50-65 健康看多区间最高分；超买/超卖做衰减"""
    close = df['close']
    if len(close) < 15:
        return 50.0, {}
    rsi = float(_rsi(close, 14).iloc[-1])
    # 健康看多区: RSI 50-65 → 80-95；下方衰减；70+ 超买减分
    if 50 <= rsi <= 65:
        score = _linmap(rsi, 50, 65, 80, 95)
    elif rsi > 65:
        score = _linmap(rsi, 65, 85, 80, 50)  # 越超买越扣
    elif 40 <= rsi < 50:
        score = _linmap(rsi, 40, 50, 50, 75)
    else:  # < 40
        score = _linmap(rsi, 20, 40, 30, 50)
    return score, {'rsi14': round(rsi, 2)}


def _score_volatility(df: pd.DataFrame) -> Tuple[float, dict]:
    """波动率：ATR(14) / close。1-3% 给最高分；过低无行情，过高风险大"""
    close = df['close']
    if len(close) < 15:
        return 50.0, {}
    atr14 = float(_atr(df['high'], df['low'], close, 14).iloc[-1])
    cur = float(close.iloc[-1])
    if cur <= 0:
        return 50.0, {}
    atr_pct = atr14 / cur * 100
    # 1.5% 最佳
    if atr_pct < 1:
        score = _linmap(atr_pct, 0, 1, 30, 60)
    elif atr_pct <= 3:
        score = _linmap(atr_pct, 1, 3, 60, 90)
    elif atr_pct <= 5:
        score = _linmap(atr_pct, 3, 5, 90, 60)
    else:
        score = _linmap(atr_pct, 5, 10, 60, 25)
    return score, {'atr14': round(atr14, 4), 'atr_pct': round(atr_pct, 2)}


# ============== 阶段判定 ==============
def _classify_stage(snapshot: dict) -> Tuple[str, str, str, float]:
    """根据指标快照判阶段、强度、信号、置信度"""
    dif = snapshot.get('dif', 0) or 0
    bar = snapshot.get('bar', 0) or 0
    bar_dir = snapshot.get('bar_dir', 0) or 0
    slope20 = snapshot.get('slope20_pct')
    ma20 = snapshot.get('ma20')
    ma60 = snapshot.get('ma60')
    cur = snapshot.get('close')
    pos_60d = snapshot.get('pos_60d')
    rsi = snapshot.get('rsi14', 50)

    # ---- 阶段 ----
    # 判定优先级：先看 DIF 在零轴的相对位置，再细分
    stage = 'unknown'
    s = slope20 or 0
    if cur is not None and ma20 is not None:
        ma_bull = (ma60 is not None and ma20 > ma60)  # 多头排列
        if dif < 0:
            # 零轴下方
            if cur > ma20 and (bar > 0 or bar_dir > 0):
                stage = 'up_early'              # 价已突破 MA20，柱体上行 → 反弹/筑底突破
            elif cur < ma20:
                stage = 'down_late' if (s <= 0) else 'down_early'
            else:
                stage = 'down_early'
        else:
            # 零轴上方
            if cur > ma20 and s > 0:
                # 顶部预警条件
                if rsi >= 70 or (pos_60d is not None and pos_60d >= 90):
                    stage = 'up_late'
                else:
                    stage = 'up_late' if ma_bull else 'up_early'
            elif cur > ma20:
                # 价在 MA20 上但 MA20 走平/下行 → 上涨末期/顶部
                stage = 'up_late'
            else:
                # 价跌破 MA20，但 DIF 还在零轴上 → 刚开始下跌
                stage = 'down_early'

    # ---- 信号 ----
    signal = 'none'
    if stage == 'up_early':
        signal = 'buy'
    elif stage == 'up_late':
        signal = 'hold' if rsi < 75 else 'sell'
    elif stage == 'down_early':
        signal = 'sell'
    elif stage == 'down_late':
        signal = 'wait'

    # 强度由 total 决定，外面填
    return stage, signal


def _confidence_from_subscores(sub: dict) -> float:
    """各子分相对 50 的方向一致性。一致 → 高置信"""
    keys = ('ma_score', 'macd_score', 'volume_score', 'momentum_score',
            'price_structure_score')
    signs = []
    for k in keys:
        v = sub.get(k)
        if v is None or pd.isna(v):
            continue
        signs.append(1 if v >= 50 else -1)
    if not signs:
        return 0.0
    bias = abs(sum(signs)) / len(signs)  # 0~1
    return _clip(bias * 100)


def _strength_from_total(total: float) -> str:
    if total >= 75:
        return 'strong'
    if total >= 60:
        return 'medium'
    if total >= 40:
        return 'weak'
    return 'none'


# ============== 主入口 ==============
@dataclass
class BoardScoreResult:
    board_code: str
    record_date: date
    sub_scores: Dict[str, float]
    total_score: float
    trend_stage: str
    trend_stage_confidence: float
    trend_strength: str
    signal: str
    snapshot: Dict[str, float]   # close / ma20 / ma60 / dif / dea / bar / atr 等
    formula_version: str = FORMULA_VERSION
    error: Optional[str] = None


def compute_one(board_code: str, record_date: date) -> BoardScoreResult:
    """单板块打分。失败时 error 字段非空，sub_scores 给中性分。"""
    df = _load_board_daily(board_code, record_date)
    if df.empty or len(df) < 20:
        return BoardScoreResult(
            board_code=board_code, record_date=record_date,
            sub_scores={k: None for k in WEIGHTS.keys()},
            total_score=None, trend_stage='unknown',
            trend_stage_confidence=0.0, trend_strength='none', signal='none',
            snapshot={}, error=f'数据不足（rows={len(df)}）',
        )

    actual_date = df['date'].iloc[-1]  # 实际用的最新一日（≤ record_date）

    ps_s, ps_d = _score_price_structure(df)
    ma_s, ma_d = _score_ma(df)
    macd_s, macd_d = _score_macd(df)
    vol_s, vol_d = _score_volume(df)
    mom_s, mom_d = _score_momentum(df)
    vola_s, vola_d = _score_volatility(df)

    sub = {
        'price_structure_score': ps_s,
        'ma_score':              ma_s,
        'macd_score':            macd_s,
        'volume_score':          vol_s,
        'momentum_score':        mom_s,
        'volatility_score':      vola_s,
    }
    # 综合分（加权平均）
    wsum = sum(WEIGHTS.values())
    total = sum(sub[k] * WEIGHTS[k] for k in sub) / wsum

    snap = {
        'close': float(df['close'].iloc[-1]),
        'change_pct': float((df['close'].iloc[-1] / df['close'].iloc[-2] - 1) * 100)
                      if len(df) >= 2 else None,
        'ma20': ma_d.get('ma20'),
        'ma60': ma_d.get('ma60'),
        'macd_dif': macd_d.get('dif'),
        'macd_dea': macd_d.get('dea'),
        'macd_bar': macd_d.get('bar'),
        'atr': vola_d.get('atr14'),
        # 用于阶段分类的辅助字段
        'slope20_pct': ma_d.get('slope20_pct'),
        'pos_60d': ps_d.get('pos_60d'),
        'rsi14': mom_d.get('rsi14'),
        'bar_dir': macd_d.get('bar_dir'),
        'dif': macd_d.get('dif'),
        'bar': macd_d.get('bar'),
    }

    stage, signal = _classify_stage(snap)
    confidence = _confidence_from_subscores(sub)
    strength = _strength_from_total(total)

    return BoardScoreResult(
        board_code=board_code, record_date=actual_date,
        sub_scores={k: round(v, 2) for k, v in sub.items()},
        total_score=round(total, 2),
        trend_stage=stage, trend_stage_confidence=round(confidence, 1),
        trend_strength=strength, signal=signal,
        snapshot={k: (round(v, 4) if isinstance(v, (int, float)) else v)
                  for k, v in snap.items() if v is not None},
    )


def compute_and_persist(board_codes: List[str], record_date: date) -> dict:
    """批量计算并写回 BoardTrendScore 表。已有同 (board_code, record_date) 的记录会被覆盖。

    Returns:
        {ok: int, fail: int, errors: [...], updated: [...]}
    """
    from App.exts import db
    from App.models.evaluation.BoardTrendScore import BoardTrendScore

    ok, fail = 0, 0
    errors, updated = [], []

    for code in board_codes:
        try:
            r = compute_one(code, record_date)
            if r.error:
                fail += 1
                errors.append({'board_code': code, 'error': r.error})
                # 仍然 upsert 一条 stage='unknown' 的占位
                BoardTrendScore.upsert(
                    board_code=code, record_date=record_date,
                    trend_stage='unknown', trend_stage_confidence=0.0,
                    trend_strength='none', signal='none',
                    formula_version=FORMULA_VERSION, is_manual=False,
                    notes=f'[{FORMULA_VERSION}] {r.error}',
                )
                continue

            BoardTrendScore.upsert(
                board_code=code, record_date=record_date,
                trend_stage=r.trend_stage,
                trend_stage_confidence=r.trend_stage_confidence,
                trend_strength=r.trend_strength,
                signal=r.signal,
                **r.sub_scores,
                total_score=r.total_score,
                close=r.snapshot.get('close'),
                change_pct=r.snapshot.get('change_pct'),
                ma20=r.snapshot.get('ma20'),
                ma60=r.snapshot.get('ma60'),
                macd_dif=r.snapshot.get('macd_dif'),
                macd_dea=r.snapshot.get('macd_dea'),
                macd_bar=r.snapshot.get('macd_bar'),
                atr=r.snapshot.get('atr'),
                formula_version=FORMULA_VERSION,
                is_manual=False,
            )
            ok += 1
            updated.append(code)
        except Exception as e:
            db.session.rollback()
            fail += 1
            errors.append({'board_code': code, 'error': str(e)})
            logger.exception(f'板块 {code} 打分失败')

    return {'ok': ok, 'fail': fail, 'errors': errors, 'updated': updated}
