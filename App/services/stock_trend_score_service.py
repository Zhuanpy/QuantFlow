# -*- coding: utf-8 -*-
"""
个股趋势打分服务（v1）

数据源：`quanttradingsystem.data_stock_daily`（按 stock_code 过滤，排除板块 BK*）。
打分公式与板块趋势完全一致——直接复用 board_trend_score_service 中的 _score_*、
_classify_stage、_confidence_from_subscores、_strength_from_total 等函数。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from App.services.board_trend_score_service import (
    WEIGHTS, FORMULA_VERSION,
    _score_price_structure, _score_ma, _score_macd,
    _score_volume, _score_momentum, _score_volatility,
    _classify_stage, _confidence_from_subscores, _strength_from_total,
)

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 250


# ============== 数据加载 ==============
def _load_stock_daily(stock_code: str, end_date: date,
                      lookback: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """加载个股日 K（截至 end_date 的最近 lookback 行）。

    返回 DataFrame 按 date 升序，列：date/open/close/high/low/volume/money。
    """
    from App.exts import db
    code = stock_code.strip()
    if not code:
        return pd.DataFrame()

    eng = db.engines['quanttradingsystem']
    df = pd.DataFrame()
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

    if df.empty:
        return df

    df = df.sort_values('date').reset_index(drop=True)
    for c in ('open', 'close', 'high', 'low', 'volume', 'money'):
        df[c] = pd.to_numeric(df[c], errors='coerce')

    ohlc = ['open', 'high', 'low', 'close']
    bad_mask = df[ohlc].isna().any(axis=1) | (df[ohlc] <= 0).any(axis=1)
    if bad_mask.any():
        dropped = int(bad_mask.sum())
        df = df.loc[~bad_mask].reset_index(drop=True)
        logger.info(f"{stock_code}: 清理 {dropped} 行无效 OHLC（0 或 NaN）")
    return df


# ============== 主入口 ==============
@dataclass
class StockScoreResult:
    stock_code: str
    record_date: date
    sub_scores: Dict[str, float]
    total_score: float
    trend_stage: str
    trend_stage_confidence: float
    trend_strength: str
    signal: str
    snapshot: Dict[str, float]
    formula_version: str = FORMULA_VERSION
    error: Optional[str] = None


def compute_one(stock_code: str, record_date: date) -> StockScoreResult:
    """单只个股打分。失败时 error 字段非空。"""
    df = _load_stock_daily(stock_code, record_date)
    if df.empty or len(df) < 20:
        return StockScoreResult(
            stock_code=stock_code, record_date=record_date,
            sub_scores={k: None for k in WEIGHTS.keys()},
            total_score=None, trend_stage='unknown',
            trend_stage_confidence=0.0, trend_strength='none', signal='none',
            snapshot={}, error=f'数据不足（rows={len(df)}）',
        )

    actual_date = df['date'].iloc[-1]

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

    return StockScoreResult(
        stock_code=stock_code, record_date=actual_date,
        sub_scores={k: round(v, 2) for k, v in sub.items()},
        total_score=round(total, 2),
        trend_stage=stage, trend_stage_confidence=round(confidence, 1),
        trend_strength=strength, signal=signal,
        snapshot={k: (round(v, 4) if isinstance(v, (int, float)) else v)
                  for k, v in snap.items() if v is not None},
    )


def _sync_score_to_pool(stock_code: str, total_score: float, record_date: date) -> None:
    """把最新一日的 total_score 同步回 StockPool.score / score_trend。

    score_trend = 今日 total_score - 上一日 total_score（若有，否则 0）。
    pool 行不存在则跳过（不主动插入；股票池由人工维护）。
    """
    from App.exts import db
    from App.models.strategy.StockPool import StockPool
    from App.models.evaluation.StockTrendScore import StockTrendScore

    pool = StockPool.query.filter_by(stock_code=stock_code).first()
    if pool is None or total_score is None:
        return

    prev = (StockTrendScore.query
            .filter(StockTrendScore.stock_code == stock_code,
                    StockTrendScore.record_date < record_date,
                    StockTrendScore.total_score.isnot(None))
            .order_by(StockTrendScore.record_date.desc())
            .first())
    trend = (total_score - prev.total_score) if prev else 0.0

    pool.score = float(total_score)
    pool.score_trend = float(trend)
    pool.score_updated_at = datetime.utcnow()
    pool.updated_at = datetime.utcnow()
    db.session.commit()


def compute_and_persist(stock_codes: List[str], record_date: date) -> dict:
    """批量计算并写回 StockTrendScore 表。已有同 (stock_code, record_date) 的记录会被覆盖。

    成功的记录会同步把最新 total_score 写回 strategy_stock_pool.score（若该股票在池中）。
    """
    from App.exts import db
    from App.models.evaluation.StockTrendScore import StockTrendScore

    ok, fail = 0, 0
    errors, updated = [], []

    for code in stock_codes:
        try:
            r = compute_one(code, record_date)
            if r.error:
                fail += 1
                errors.append({'stock_code': code, 'error': r.error})
                StockTrendScore.upsert(
                    stock_code=code, record_date=record_date,
                    trend_stage='unknown', trend_stage_confidence=0.0,
                    trend_strength='none', signal='none',
                    formula_version=FORMULA_VERSION, is_manual=False,
                    notes=f'[{FORMULA_VERSION}] {r.error}',
                )
                continue

            StockTrendScore.upsert(
                stock_code=code, record_date=record_date,
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
            try:
                _sync_score_to_pool(code, r.total_score, record_date)
            except Exception as e:
                logger.warning(f'同步 {code} 评分到 StockPool 失败: {e}')
            ok += 1
            updated.append(code)
        except Exception as e:
            db.session.rollback()
            fail += 1
            errors.append({'stock_code': code, 'error': str(e)})
            logger.exception(f'个股 {code} 打分失败')

    return {'ok': ok, 'fail': fail, 'errors': errors, 'updated': updated}
