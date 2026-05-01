# -*- coding: utf-8 -*-
"""
全市场股票评分引擎 v1

输入：data_stock_daily 里有数据的所有股票（按规则过滤后）
输出：每只股票的综合分 + 各维度子分

设计原则：
- 不依赖外部数据源（只用 data_stock_daily 已有的字段）
- 缺失的维度用"中性 50"代替而不是 0，避免拖累综合分
- 拆模型评分为振幅 / 周期 / 成交量 3 个子分（只对训过模型的股票计算）
- 健康度做"质量门控"：DEGENERATE/UNTRAINED 的模型维度不计分母
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ========== 权重配置（可在 UI 里调）==========
DEFAULT_WEIGHTS = {
    # 趋势 / 价格类
    'market_trend':   10,   # 大盘多头 — 暂用固定中性分
    'sector_trend':   10,   # 板块多头 — 暂用固定中性分
    'stock_trend':    10,   # 个股 MA20/MA60 多头排列
    'rps_60d':        10,   # 60 日相对强度（vs 全市场中位数）
    # 量能 / 流动性
    'liquidity':       5,   # 近 60 日日均成交额
    'volume_trend':    5,   # 近 5 日 / 60 日均量比（放量加分）
    # 价格位置
    'price_52w':       2,   # 52 周价格位置
    # 基金信号
    'fund_count':     15,   # 基金持仓数
    'fund_momentum':  10,   # 基金主动加仓变化
    # 模型 3 维度（只对训过的股票算）
    'model_amplitude':  8,  # CycleChange4 → 下一波段振幅
    'model_length':     5,  # CycleLength4 → 下一波段长度
    'model_volume':     5,  # BarVolume4 → 下一 Bar 量
    # 风险
    'risk_drawdown':   -3,  # 最大回撤超过阈值扣分
    'risk_st':         -3,  # ST 扣分（应该已被前置过滤掉，但兜底）
    'risk_smallcap':   -2,  # 市值过小扣分（仅当数据可用）
}


# ========== 工具函数 ==========
def _clip_pct_to_score(value: float, low: float, high: float) -> float:
    """把数值映射到 0-100 分（线性，超过区间截断）"""
    if value is None or pd.isna(value):
        return 50.0  # 缺失给中性
    if high == low:
        return 50.0
    s = (value - low) / (high - low) * 100
    return float(max(0, min(100, s)))


def _rank_to_percentile(values: pd.Series) -> pd.Series:
    """把数值转成百分位 (0-100)，缺失给 50"""
    valid = values.dropna()
    if len(valid) == 0:
        return pd.Series(50.0, index=values.index)
    ranks = values.rank(pct=True) * 100
    return ranks.fillna(50.0)


# ========== 主入口 ==========
def compute_market_scores(
    weights: Optional[Dict[str, float]] = None,
    exclude_st: bool = True,
    exclude_chinext: bool = True,
    exclude_kechuang: bool = False,
    exclude_beijiao: bool = False,
    days_back: int = 90,
    top_n: Optional[int] = None,
) -> List[Dict]:
    """
    扫描全市场（实际上是 data_stock_daily 里有数据的股票）做评分。

    Returns:
        list of dict: 每只股票一条，包含 stock_code, stock_name, total_score, sub_scores
    """
    from App.exts import db
    from App.models.data.StockDaily import StockDaily
    from App.models.data.basic_info import StockInfo
    from sqlalchemy import text

    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    cutoff = date.today() - timedelta(days=days_back)

    # 1) 拉股票名单（含名称用于 ST 判断）
    info_rows = StockInfo.query.all()
    info_map = {r.code: (r.name or '') for r in info_rows if r.code}

    # 2) 拉所有有 daily 数据的股票最近 N 天行情（一次 SQL）
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT stock_code, date, close, high, low, volume, money
            FROM data_stock_daily
            WHERE date >= :cutoff
            ORDER BY stock_code, date
        """), {'cutoff': cutoff}).fetchall()
        # 基金持仓数单独查（写入频率低，需要更长窗口找到非零值）
        fund_rows = conn.execute(text("""
            SELECT stock_code, date, fund_holdings_count
            FROM data_stock_daily
            WHERE date >= :fund_cutoff AND fund_holdings_count > 0
            ORDER BY stock_code, date
        """), {'fund_cutoff': date.today() - timedelta(days=365)}).fetchall()
    if not rows:
        return []

    df = pd.DataFrame(rows, columns=[
        'stock_code', 'date', 'close', 'high', 'low', 'volume', 'money'
    ])
    df['date'] = pd.to_datetime(df['date'])

    # 基金数据单独 DataFrame
    fund_df = pd.DataFrame(fund_rows, columns=['stock_code', 'date', 'fund_holdings_count'])
    fund_df['date'] = pd.to_datetime(fund_df['date'])

    # 3) 按股票分组聚合
    eligible_codes = []
    for code in df['stock_code'].unique():
        # 应用过滤规则
        name = info_map.get(code, '')
        if exclude_st and ('ST' in name.upper()):
            continue
        if exclude_chinext and (code.startswith('300') or code.startswith('301')):
            continue
        if exclude_kechuang and code.startswith('688'):
            continue
        if exclude_beijiao and (code.startswith('8') or code.startswith('4')):
            continue
        if code.startswith('BK'):  # 板块指数永远跳过
            continue
        eligible_codes.append(code)

    df = df[df['stock_code'].isin(eligible_codes)]
    if df.empty:
        return []

    # 4) 算每只股票的各维度原始指标
    raw = []
    for code, g in df.groupby('stock_code'):
        g = g.sort_values('date').reset_index(drop=True)
        n = len(g)
        if n < 20:
            continue  # 数据太少，跳过

        close = g['close'].astype(float)
        high = g['high'].astype(float)
        low = g['low'].astype(float)
        volume = g['volume'].astype(float)
        money = g['money'].astype(float)

        # 个股趋势：MA20 / MA60 多头排列 + 价高于 MA20
        ma20 = close.rolling(20).mean().iloc[-1] if n >= 20 else None
        ma60 = close.rolling(60).mean().iloc[-1] if n >= 60 else None
        cur = close.iloc[-1]
        trend_score = 50.0
        if ma20 is not None and ma60 is not None:
            bull = (ma20 > ma60) and (cur > ma20)
            trend_score = 80.0 if bull else 30.0
        elif ma20 is not None:
            trend_score = 65.0 if cur > ma20 else 35.0

        # 60 日涨跌幅（用于 RPS 排名）
        if n >= 60:
            ret_60d = (close.iloc[-1] / close.iloc[-60] - 1) * 100
        elif n >= 2:
            ret_60d = (close.iloc[-1] / close.iloc[0] - 1) * 100
        else:
            ret_60d = 0

        # 流动性：近 60 日日均成交额
        liq = float(money.tail(60).mean())

        # 量能趋势：近 5 日 / 近 60 日均量比
        vt_ratio = None
        if n >= 60:
            v5 = volume.tail(5).mean()
            v60 = volume.tail(60).mean()
            if v60 > 0:
                vt_ratio = v5 / v60

        # 52 周位置（按现有数据近似，days_back<252 就用全部）
        if n >= 2:
            high_52w = high.max()
            low_52w = low.min()
            if high_52w > low_52w:
                pos_52w = (cur - low_52w) / (high_52w - low_52w)
            else:
                pos_52w = 0.5
        else:
            pos_52w = 0.5

        # 最大回撤
        rolling_max = close.cummax()
        drawdown = ((close - rolling_max) / rolling_max).min()  # 负数
        max_drawdown_pct = float(drawdown) * 100  # eg -25.3

        # 基金持仓数（从 fund_df 单独查，最近 365 天里所有非零更新）
        fund_g = fund_df[fund_df['stock_code'] == code].sort_values('date')
        if len(fund_g) > 0:
            latest_fc = int(fund_g.iloc[-1]['fund_holdings_count'])
            if len(fund_g) >= 2:
                fund_momentum_raw = int(fund_g.iloc[-1]['fund_holdings_count'] - fund_g.iloc[0]['fund_holdings_count'])
            else:
                fund_momentum_raw = 0
        else:
            latest_fc = 0
            fund_momentum_raw = 0

        raw.append({
            'stock_code': code,
            'stock_name': info_map.get(code, ''),
            'cur_price': float(cur),
            'data_rows': n,
            'trend_score': trend_score,
            'ret_60d': float(ret_60d),
            'liquidity': liq,
            'vol_trend_ratio': vt_ratio,
            'pos_52w': float(pos_52w),
            'max_drawdown_pct': max_drawdown_pct,
            'latest_fc': latest_fc,
            'fund_momentum_raw': fund_momentum_raw,
        })

    if not raw:
        return []

    raw_df = pd.DataFrame(raw)

    # 5) 各维度归一化为 0-100 子分
    sub_scores = pd.DataFrame(index=raw_df.index)
    sub_scores['stock_trend'] = raw_df['trend_score']
    # RPS：60 日涨跌幅在全市场的百分位
    sub_scores['rps_60d'] = _rank_to_percentile(raw_df['ret_60d'])
    # 流动性：log 化后排名（量级差异太大，直接排名稳）
    sub_scores['liquidity'] = _rank_to_percentile(np.log1p(raw_df['liquidity'].clip(lower=1)))
    # 量能趋势：v5/v60 比；1.5 = 强放量 → 高分；0.5 = 缩量 → 低分
    sub_scores['volume_trend'] = raw_df['vol_trend_ratio'].apply(
        lambda v: _clip_pct_to_score(v, low=0.5, high=2.0) if v is not None and not pd.isna(v) else 50.0
    )
    # 52 周位置（这里给"接近高位略加分"，因为对趋势策略友好）
    sub_scores['price_52w'] = raw_df['pos_52w'] * 100
    # 基金持仓：log 化排名
    sub_scores['fund_count'] = _rank_to_percentile(np.log1p(raw_df['latest_fc'].clip(lower=0)))
    # 基金动量：原始增量做 z-score 后映射
    sub_scores['fund_momentum'] = _rank_to_percentile(raw_df['fund_momentum_raw'])
    # 风险：最大回撤
    # 回撤 -10% 内 → 100；-30% → 50；超过 -50% → 0
    sub_scores['risk_drawdown'] = raw_df['max_drawdown_pct'].apply(
        lambda d: _clip_pct_to_score(d, low=-50, high=-5)
    )
    # 大盘 / 板块趋势：暂用固定中性分
    sub_scores['market_trend'] = 50.0
    sub_scores['sector_trend'] = 50.0
    # 模型 3 维度：v1 全部留空（=不计分母），后续可接入
    for k in ('model_amplitude', 'model_length', 'model_volume'):
        sub_scores[k] = np.nan
    # ST / 小市值：v1 简化（已在前置过滤里排除 ST，这里补 0）
    sub_scores['risk_st'] = np.nan
    sub_scores['risk_smallcap'] = np.nan

    # 6) 加权综合分（动态权重：跳过 NaN 的维度，剩余权重按比例放大）
    abs_w = {k: abs(v) for k, v in weights.items()}  # 用绝对值算分母
    sub_scores_arr = sub_scores.copy()

    final_scores = []
    breakdown = []
    for idx in sub_scores_arr.index:
        row = sub_scores_arr.loc[idx]
        valid_w_sum = 0.0
        weighted_sum = 0.0
        per_dim = {}
        for k, w in weights.items():
            v = row.get(k)
            if v is None or pd.isna(v):
                per_dim[k] = None
                continue
            # 风险维度的权重是负的：分高 = 风险低 = 加分；分低 = 加分小
            # 我们已经把回撤映射成 0-100（100=回撤小）了，所以直接 +
            valid_w_sum += abs(w)
            contribution = (v if w > 0 else (100 - v)) * abs(w)
            weighted_sum += contribution
            per_dim[k] = round(float(v), 1)

        if valid_w_sum > 0:
            final = weighted_sum / valid_w_sum
        else:
            final = 0
        final_scores.append(round(float(final), 2))
        breakdown.append(per_dim)

    raw_df['total_score'] = final_scores
    raw_df['sub_scores'] = breakdown

    # 7) 排序 + 切 top_n
    raw_df = raw_df.sort_values('total_score', ascending=False).reset_index(drop=True)
    if top_n:
        raw_df = raw_df.head(top_n)

    # 8) 转成响应字典
    result = []
    for _, r in raw_df.iterrows():
        result.append({
            'stock_code': r['stock_code'],
            'stock_name': r['stock_name'],
            'total_score': r['total_score'],
            'sub_scores': r['sub_scores'],
            'cur_price': r['cur_price'],
            'ret_60d': round(r['ret_60d'], 2),
            'max_drawdown_pct': round(r['max_drawdown_pct'], 2),
            'latest_fc': r['latest_fc'],
            'fund_momentum_raw': r['fund_momentum_raw'],
            'data_rows': r['data_rows'],
        })
    return result
