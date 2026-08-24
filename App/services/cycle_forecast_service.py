"""周期预估的保存 / 状态刷新 / 复盘回填 / 转交易计划。

口径说明见 App/models/evaluation/CycleForecast.py 的 docstring。

段信息一律从本地 15m parquet 复算（列 `SignalStartIndex` = 段起始时间戳、
`StartPrice` = 段起点价），与 live 页 current_trend 同源 —— 不走 HTTP 回调自己的接口。

对外：
    current_segment(code)              -> dict 当前段信息（判断预估是否还成立）
    create(payload)                    -> dict 新建预估（同段旧的 pending 置 superseded）
    list_for_stock(code, limit)        -> list 该股预估（顺带刷新状态）
    refresh(fc, seg=None, price=None)  -> bool 是否有变更
    review(fc)                         -> bool 段已结束则回填实际值与误差
    convert_to_plan(fc_id, **kw)       -> dict 写一条 TradePlan
    refresh_all(limit)                 -> dict 批量刷新+复盘（给收盘流水线用）
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from App.exts import db
from App.models.evaluation.CycleForecast import (
    CycleForecast, ST_PENDING, ST_REACHED, ST_EXPIRED, ST_INVALIDATED,
    ST_SUPERSEDED, ST_CONVERTED, ST_CANCELLED, LIVE_STATUSES,
)

logger = logging.getLogger(__name__)


def _p15(code: str) -> Path:
    from config import Config
    return Path(Config.get_project_root()) / 'data' / '15m' / f'{code}.parquet'


def _load_15m(code: str) -> pd.DataFrame:
    fp = _p15(code)
    if not fp.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(fp)
    except Exception as e:
        logger.warning(f'[forecast] 读取 {fp} 失败: {e}')
        return pd.DataFrame()
    if 'date' not in df.columns:
        return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    return df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)


def _segment_of(df: pd.DataFrame, seg_start_at, since=None) -> dict:
    """取指定段（按 SignalStartIndex 匹配）的实际走势。找不到返回 {}。

    since：只统计该时刻**之后**的 bar 的极值（seg_low/seg_high/extreme）。
    触达判定必须带上它 —— 否则"段内最低 170"这种发生在下预估之前的历史极值，
    会让任何高于它的目标价一保存就秒变"已触达"。
    段起点价/总根数/is_active 仍按整段算，不受 since 影响。
    """
    if df.empty or 'SignalStartIndex' not in df.columns:
        return {}
    key = pd.to_datetime(df['SignalStartIndex'], errors='coerce')
    seg = df[key == pd.Timestamp(seg_start_at)]
    if seg.empty:
        return {}
    seg_full = seg
    if since is not None:
        seg_after = seg[seg['date'] >= pd.Timestamp(since)]
    else:
        seg_after = seg
    direction = 0
    if 'Signal' in df.columns:
        v = pd.to_numeric(seg['Signal'], errors='coerce').dropna()
        if len(v):
            direction = 1 if v.iloc[-1] > 0 else (-1 if v.iloc[-1] < 0 else 0)
    start_price = None
    if 'StartPrice' in seg.columns:
        sp = pd.to_numeric(seg['StartPrice'], errors='coerce').dropna()
        if len(sp):
            start_price = float(sp.iloc[0])
    up = direction > 0
    ext_col = 'high' if up else 'low'
    src = seg_after if len(seg_after) else seg_full
    idx = src[ext_col].astype(float).idxmax() if up else src[ext_col].astype(float).idxmin()
    extreme = float(src.loc[idx, ext_col])
    amp = (abs(extreme - start_price) / start_price * 100
           if start_price else None)
    return {
        'seg_start_at': seg_full['date'].iloc[0].to_pydatetime(),
        'direction': 'up' if up else 'down',
        'start_price': start_price,
        'bars': int(len(seg_full)),
        'extreme': round(extreme, 3),
        'extreme_at': src.loc[idx, 'date'].to_pydatetime(),
        'amp_pct': (round(amp, 2) if amp is not None else None),
        'last_close': float(seg_full['close'].iloc[-1]),
        'last_bar_at': seg_full['date'].iloc[-1].to_pydatetime(),
        # 该段是否仍是最后一段（末根就是全表末根）
        'is_active': bool(seg_full.index[-1] == df.index[-1]),
        'seg_low': float(src['low'].astype(float).min()),
        'seg_high': float(src['high'].astype(float).max()),
        'bars_after': int(len(seg_after)),
    }


def current_segment(code: str) -> dict:
    """当前（最后一段）的段信息。"""
    df = _load_15m(code)
    if df.empty or 'SignalStartIndex' not in df.columns:
        return {}
    last_key = pd.to_datetime(df['SignalStartIndex'], errors='coerce').iloc[-1]
    if pd.isna(last_key):
        return {}
    return _segment_of(df, last_key)


# ==================== 新建 ====================
def _f(v):
    if v in (None, '', '-'):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return int(round(f)) if f is not None else None


def _dt(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(v)[:19], fmt)
        except ValueError:
            continue
    return None


def create(payload: dict) -> dict:
    """保存一条预估。同一段里已有的 pending 预估会被置为 superseded（保留修改轨迹）。"""
    CycleForecast.ensure_table()
    code = (payload.get('stock_code') or '').strip()
    seg_start = _dt(payload.get('seg_start_at'))
    target = _f(payload.get('target_price'))
    direction = (payload.get('direction') or '').strip().lower()
    if not code or not seg_start or target is None or direction not in ('up', 'down'):
        raise ValueError('缺少必要字段：stock_code / seg_start_at / target_price / direction')

    # 同段旧的 pending/reached 置为 superseded —— 一段里只保留最新一条在跑，
    # 但历史不删：改过几次 P、每次目标价多少，都是校准 P 表的样本。
    old = (CycleForecast.query
           .filter(CycleForecast.stock_code == code,
                   CycleForecast.timeframe == (payload.get('timeframe') or '15m'),
                   CycleForecast.seg_start_at == seg_start,
                   CycleForecast.status.in_(LIVE_STATUSES))
           .all())
    for o in old:
        o.status = ST_SUPERSEDED
        o.status_note = '被同段新预估覆盖'
        o.updated_at = datetime.utcnow()

    fc = CycleForecast(
        stock_code=code,
        stock_name=(payload.get('stock_name') or '').strip() or None,
        timeframe=(payload.get('timeframe') or '15m'),
        direction=direction,
        signal_name=(payload.get('signal_name') or None),
        seg_start_at=seg_start,
        seg_start_price=_f(payload.get('seg_start_price')),
        forecast_at=datetime.now(),
        price_at=_f(payload.get('price_at')),
        bars_at=_i(payload.get('bars_at')),
        amp_walked_pct=_f(payload.get('amp_walked_pct')),
        amp_p=_i(payload.get('amp_p')),
        amp_p_source=(payload.get('amp_p_source') or 'manual'),
        proj_amp_pct=_f(payload.get('proj_amp_pct')),
        target_price=target,
        amp_fit_mean=_f(payload.get('amp_fit_mean')),
        amp_fit_std=_f(payload.get('amp_fit_std')),
        len_p=_i(payload.get('len_p')),
        len_p_source=(payload.get('len_p_source') or 'manual'),
        proj_len_bars=_i(payload.get('proj_len_bars')),
        remaining_bars=_i(payload.get('remaining_bars')),
        proj_end_at=_dt(payload.get('proj_end_at')),
        len_fit_mean=_f(payload.get('len_fit_mean')),
        len_fit_std=_f(payload.get('len_fit_std')),
        status=ST_PENDING,
        note=(payload.get('note') or None),
    )
    db.session.add(fc)
    db.session.commit()
    logger.info(f'[forecast] 新建 {code} {direction} 目标 {target} '
                f'(段 {seg_start}, P{fc.amp_p})')

    # 提醒：这个价位在本段里**已经走到过**（发生在下预估之前）。不改状态，
    # 但要让人知道 —— 否则会以为"还没到"，实际上是错过了一次。
    out = fc.to_dict()
    hist = _segment_of(_load_15m(code), seg_start)
    if hist:
        passed = ((hist['seg_low'] <= target) if direction == 'down'
                  else (hist['seg_high'] >= target))
        if passed:
            out['already_passed'] = {
                'extreme': hist['extreme'], 'at': hist['extreme_at'].strftime('%Y-%m-%d %H:%M'),
                'hint': f'本段此前{"最低" if direction == "down" else "最高"}到过 '
                        f'{hist["extreme"]}，已越过该目标价；触达判定只看下预估之后的走势。',
            }
    return out


# ==================== 状态刷新 ====================
def refresh(fc: CycleForecast, df: pd.DataFrame = None, live_price=None) -> bool:
    """刷新一条预估的状态。返回是否有变更。

    判定顺序（顺序不能换）：
      1. 段翻转 → invalidated：预估的前提没了，此时"价格恰好到过目标"也不算数
      2. 触达目标 → reached：下跌看最低价、上涨看最高价（用段内极值，不是收盘价）
      3. 过了预估结束时间还没触达 → expired
    """
    if fc.status not in LIVE_STATUSES:
        return False
    if df is None:
        df = _load_15m(fc.stock_code)
    # 触达只看下预估之后的 bar；段是否存活、总根数仍按整段判
    seg = (_segment_of(df, fc.seg_start_at, since=fc.forecast_at)
           if not df.empty else {})

    # 1) 段还在不在？
    if not seg or not seg.get('is_active'):
        if fc.status == ST_REACHED:
            # 已经触达过，段结束只是收尾，不改成作废
            return False
        # 段结束了也要先补判一次触达：如果系统停了几天、这条预估到现在才被检查，
        # 直接判作废就会把"其实到过我的价"的样本记成没到过，校准数据就偏了。
        if seg:
            _down = fc.direction == 'down'
            _ext = seg['seg_low'] if _down else seg['seg_high']
            if seg.get('bars_after') and ((_ext <= fc.target_price) if _down
                                          else (_ext >= fc.target_price)):
                fc.status = ST_REACHED
                fc.reached_price = round(float(_ext), 3)
                fc.reached_at = seg.get('extreme_at')
                fc.status_note = (f'段内曾{"跌到" if _down else "涨到"} {fc.reached_price}'
                                  f'（达目标 {fc.target_price}），段现已结束')
                fc.updated_at = datetime.utcnow()
                return True
        fc.status = ST_INVALIDATED
        # 找不到段有一种合理情形：该段收盘后净振幅不足 1%，被弱段合并吸收进了前一段，
        # 它的 SignalStartIndex 就此消失（见 MacdSignalV2._merge_weak_segments）。
        seg_label = fc.signal_name or (fc.seg_start_at.strftime('%m-%d %H:%M')
                                       if fc.seg_start_at else '?')
        fc.status_note = ('段已结束/翻转，全程未触达目标' if seg else
                          f'找不到该段（{seg_label}），可能是弱段被合并或 15m 数据重算')
        fc.updated_at = datetime.utcnow()
        return True

    # 2) 触达没有 —— 用段内极值判定：盘中冲到过就算到过，不必收盘价站上
    down = fc.direction == 'down'
    ext = seg['seg_low'] if down else seg['seg_high']
    if live_price is not None:
        ext = min(ext, float(live_price)) if down else max(ext, float(live_price))
    hit = (ext <= fc.target_price) if down else (ext >= fc.target_price)
    if hit and seg.get('bars_after') and fc.status != ST_REACHED:
        fc.status = ST_REACHED
        fc.reached_price = round(float(ext), 3)
        fc.reached_at = seg.get('extreme_at') or datetime.now()
        fc.status_note = (f'下预估后{"最低" if down else "最高"} {fc.reached_price} '
                          f'已达目标 {fc.target_price}')
        fc.updated_at = datetime.utcnow()
        return True

    # 3) 过期（段还活着，但已经走过了预估的结束时间）
    if (fc.status == ST_PENDING and fc.proj_end_at
            and seg['last_bar_at'] > fc.proj_end_at):
        fc.status = ST_EXPIRED
        fc.status_note = f'已过预估结束时间 {fc.proj_end_at:%Y-%m-%d %H:%M}，仍未触达'
        fc.updated_at = datetime.utcnow()
        return True
    return False


def review(fc: CycleForecast, df: pd.DataFrame = None) -> bool:
    """段结束后回填实际值与误差（**不管有没有触达都要回填** —— 没触达的样本
    同样是校准 P 表的证据，只统计成功的会把分位系统性调偏）。"""
    if fc.reviewed_at:
        return False
    if df is None:
        df = _load_15m(fc.stock_code)
    seg = _segment_of(df, fc.seg_start_at) if not df.empty else {}
    if not seg or seg.get('is_active'):
        return False        # 段还没走完，等下次

    fc.actual_extreme = seg['extreme']
    fc.actual_extreme_at = seg['extreme_at']
    fc.actual_amp_pct = seg['amp_pct']
    fc.actual_len_bars = seg['bars']
    if fc.actual_amp_pct is not None and fc.proj_amp_pct is not None:
        fc.amp_err_pct = round(fc.actual_amp_pct - fc.proj_amp_pct, 2)
    if fc.actual_len_bars is not None and fc.proj_len_bars is not None:
        fc.len_err_bars = int(fc.actual_len_bars - fc.proj_len_bars)
    fc.reviewed_at = datetime.utcnow()
    fc.updated_at = datetime.utcnow()
    return True


def list_for_stock(code: str, limit: int = 20, refresh_status: bool = True) -> list:
    """取某股票的预估列表（默认顺带刷新状态 + 回填已结束段的复盘）。"""
    CycleForecast.ensure_table()
    rows = (CycleForecast.query.filter(CycleForecast.stock_code == code)
            .order_by(CycleForecast.id.desc()).limit(limit).all())
    if refresh_status and rows:
        df = _load_15m(code)
        changed = False
        for fc in rows:
            changed |= refresh(fc, df=df)
            changed |= review(fc, df=df)
        if changed:
            db.session.commit()
    return [r.to_dict() for r in rows]


def refresh_all(limit: int = 500) -> dict:
    """批量刷新所有未结案的预估 + 回填可复盘的（给收盘流水线用）。"""
    CycleForecast.ensure_table()
    rows = (CycleForecast.query
            .filter(db.or_(CycleForecast.status.in_(LIVE_STATUSES),
                           CycleForecast.reviewed_at.is_(None)))
            .order_by(CycleForecast.id.desc()).limit(limit).all())
    by_code = {}
    for fc in rows:
        by_code.setdefault(fc.stock_code, []).append(fc)

    stat = {'checked': len(rows), 'status_changed': 0, 'reviewed': 0, 'stocks': len(by_code)}
    for code, lst in by_code.items():
        df = _load_15m(code)
        if df.empty:
            continue
        for fc in lst:
            if refresh(fc, df=df):
                stat['status_changed'] += 1
            if review(fc, df=df):
                stat['reviewed'] += 1
    db.session.commit()
    return stat


# ==================== 转交易计划 ====================
def convert_to_plan(fc_id: int, quantity: int = 100, trade_mode: str = 'simulate',
                    stop_loss_price=None, note: str = None) -> dict:
    """把预估转成一条 TradePlan：目标价即计划买入价。

    预估表管"我怎么判断"，TradePlan 管"我打算怎么交易"，两边各司其职、互不污染。
    """
    from App.models.trade.trade_plan import TradePlan
    fc = CycleForecast.query.get(fc_id)
    if not fc:
        raise ValueError(f'预估 {fc_id} 不存在')
    if fc.plan_id:
        return {'plan_id': fc.plan_id, 'already': True}

    plan_id = f'FC{fc.id}-{fc.stock_code}-{datetime.now():%Y%m%d%H%M%S}'
    reason = (f'周期预估：{fc.direction} 段（起 {fc.seg_start_at:%Y-%m-%d %H:%M}，'
              f'起点价 {fc.seg_start_price}），振幅 P{fc.amp_p} → 预估整段 '
              f'{fc.proj_amp_pct}%，目标 {fc.target_price}；'
              f'长度 P{fc.len_p} → 预估 {fc.proj_len_bars} 根，'
              f'预估结束 {fc.proj_end_at:%Y-%m-%d %H:%M}' if fc.proj_end_at else '')
    plan = TradePlan(
        plan_id=plan_id,
        stock_code=fc.stock_code,
        stock_name=fc.stock_name or fc.stock_code,
        trade_mode=trade_mode,
        direction='long',
        status=TradePlan.STATUS_PENDING,
        entry_price=fc.target_price,
        target_price=None,
        stop_loss_price=stop_loss_price,
        current_price=fc.price_at,
        quantity=quantity,
        plan_time=datetime.utcnow(),
        expire_time=fc.proj_end_at,
        entry_reason=reason,
        strategy_name='cycle_forecast',
        notes=note or fc.note,
    )
    db.session.add(plan)
    fc.plan_id = plan_id
    fc.status = ST_CONVERTED
    fc.status_note = f'已转为交易计划 {plan_id}'
    fc.updated_at = datetime.utcnow()
    db.session.commit()
    logger.info(f'[forecast] {fc.stock_code} 预估 {fc.id} → 交易计划 {plan_id}')
    return {'plan_id': plan_id, 'entry_price': float(fc.target_price)}


def delete(fc_id: int) -> dict:
    """彻底删除一条预估。

    与 cancel 的区别：cancel 只改状态、记录留着（仍是校准 P 表的样本），
    delete 是真删。所以只在"这条纯属误操作/没意义"时用，别拿它清理正常的历史。
    已转成的 TradePlan 不动 —— 那是独立的交易计划，可能正在执行中。
    """
    fc = CycleForecast.query.get(fc_id)
    if not fc:
        raise ValueError(f'预估 {fc_id} 不存在')
    info = {'id': fc.id, 'stock_code': fc.stock_code, 'signal_name': fc.signal_name,
            'target_price': fc.target_price, 'status': fc.status,
            'plan_id': fc.plan_id, 'reviewed': bool(fc.reviewed_at)}
    db.session.delete(fc)
    db.session.commit()
    logger.info(f'[forecast] 删除预估 {info}')
    return info


def cancel(fc_id: int, reason: str = None) -> dict:
    fc = CycleForecast.query.get(fc_id)
    if not fc:
        raise ValueError(f'预估 {fc_id} 不存在')
    fc.status = ST_CANCELLED
    fc.status_note = reason or '手工作废'
    fc.updated_at = datetime.utcnow()
    db.session.commit()
    return fc.to_dict()
