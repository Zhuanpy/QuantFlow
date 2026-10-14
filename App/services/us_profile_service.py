# -*- coding: utf-8 -*-
"""美股档案页（方案 B）：日K、事件时间线（含事件后价格反应）、季度 KPI。

日K 不另存：从 data/us/quarters/**/<SYM>.parquet 的 1m 现聚合，与 us_data_futu
「parquet 是唯一真相源」一致。标的只有几只，几个月 1m 聚合是毫秒级。

事件反应口径：t0 = 事件日当天或之后的第一个交易日；
  当日% = close[t0] / close[t0-1] - 1；5日% = close[t0+4] / close[t0-1] - 1。
盘后发布的披露（8-K/10-Q 多在 16:00 后）真正的反应在下一交易日，所以 5 日比当日更可信。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

# KPI 指标定义：(key, 显示名)。未列出的标的用通用组；表里出现的其它 key 也会显示。
KPI_DEFS = {
    'SPCX': [
        ('revenue', '营收($B)'), ('starlink_rev', 'Starlink营收($B)'),
        ('starlink_subs', 'Starlink用户(万)'), ('gross_margin', '毛利率%'),
        ('net_income', '净利润($B)'), ('capex', '资本开支($B)'),
        ('fcf', '自由现金流($B)'), ('launches', '发射次数'),
    ],
}
_GENERIC_KPI = [('revenue', '营收($B)'), ('gross_margin', '毛利率%'),
                ('net_income', '净利润($B)'), ('fcf', '自由现金流($B)')]


def kpi_defs(symbol: str) -> list:
    return KPI_DEFS.get(symbol.upper(), _GENERIC_KPI)


# ------------------------------------------------------------------ 日K

def load_daily(symbol: str) -> pd.DataFrame:
    from App.services.us_data_futu import us_quarters_dir
    parts = sorted(us_quarters_dir().rglob(f'{symbol.upper()}.parquet'))
    frames = []
    for p in parts:
        try:
            frames.append(pd.read_parquet(p, columns=['date', 'open', 'high', 'low', 'close', 'volume']))
        except Exception:
            logger.exception(f'[us_profile] 读取失败 {p}')
    if not frames:
        return pd.DataFrame(columns=['day', 'open', 'high', 'low', 'close', 'volume'])
    m = pd.concat(frames, ignore_index=True)
    m['date'] = pd.to_datetime(m['date'])
    m = m.sort_values('date').drop_duplicates('date')
    m['day'] = m['date'].dt.date
    d = m.groupby('day').agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                             close=('close', 'last'), volume=('volume', 'sum')).reset_index()
    return d


def daily_payload(symbol: str) -> dict:
    d = load_daily(symbol)
    if d.empty:
        return {'bars': [], 'summary': None}
    bars = [[r.day.isoformat(), r.open, r.close, r.low, r.high, r.volume] for r in d.itertuples()]
    last = d.iloc[-1]
    prev = d.iloc[-2] if len(d) > 1 else None
    summary = {
        'last_day': last.day.isoformat(), 'close': float(last.close),
        'chg_pct': (float(last.close / prev.close - 1) * 100) if prev is not None else None,
        'high_all': float(d.high.max()), 'low_all': float(d.low.min()),
        'first_day': d.iloc[0].day.isoformat(), 'days': int(len(d)),
    }
    return {'bars': bars, 'summary': summary}


def _reactions(daily: pd.DataFrame, event_dates) -> dict:
    """{event_date: {t0, ret_d0, ret_5d}}。"""
    if daily.empty:
        return {}
    days = list(daily['day'])
    closes = list(daily['close'])
    out = {}
    import bisect
    for ed in set(event_dates):
        i = bisect.bisect_left(days, ed)
        if i >= len(days) or i == 0:
            out[ed] = {'t0': days[i].isoformat() if i < len(days) else None, 'ret_d0': None, 'ret_5d': None}
            continue
        base = closes[i - 1]
        r5 = closes[i + 4] / base - 1 if i + 4 < len(days) else None
        out[ed] = {'t0': days[i].isoformat(), 'ret_d0': (closes[i] / base - 1) * 100,
                   'ret_5d': r5 * 100 if r5 is not None else None}
    return out


# ------------------------------------------------------------------ 事件

def list_events(symbol: str) -> list:
    from App.models.data.UsWatch import UsStockEvent
    evs = (UsStockEvent.query.filter_by(symbol=symbol.upper())
           .order_by(UsStockEvent.event_date.desc(), UsStockEvent.id.desc()).all())
    reac = _reactions(load_daily(symbol), [e.event_date for e in evs])
    out = []
    for e in evs:
        d = e.to_dict()
        d.update(reac.get(e.event_date) or {})
        out.append(d)
    return out


_EVENT_FIELDS = ('event_date', 'category', 'title', 'detail', 'expectation', 'review',
                 'importance', 'source_url')


def save_event(symbol: str, payload: dict) -> dict:
    from App.exts import db
    from App.models.data.UsWatch import UsStockEvent, EVENT_CATEGORIES
    eid = payload.get('id')
    e = UsStockEvent.query.get(eid) if eid else UsStockEvent(symbol=symbol.upper(), source='manual')
    if e is None:
        raise ValueError('事件不存在')
    title = (payload.get('title') or '').strip()
    if not title:
        raise ValueError('标题不能为空')
    try:
        ev_date = datetime.strptime(payload.get('event_date') or '', '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('日期格式应为 YYYY-MM-DD')
    cat = payload.get('category') or 'other'
    if cat not in EVENT_CATEGORIES:
        raise ValueError(f'未知分类 {cat}')
    e.event_date, e.category, e.title = ev_date, cat, title[:200]
    for k in ('detail', 'expectation', 'review', 'source_url'):
        if k in payload:
            setattr(e, k, (payload.get(k) or '').strip() or None)
    e.importance = max(1, min(3, int(payload.get('importance') or 2)))
    if not eid:
        db.session.add(e)
    db.session.commit()
    return e.to_dict()


def delete_event(eid: int):
    from App.exts import db
    from App.models.data.UsWatch import UsStockEvent
    e = UsStockEvent.query.get(eid)
    if e is None:
        raise ValueError('事件不存在')
    db.session.delete(e)
    db.session.commit()


# ------------------------------------------------------------------ KPI

def list_kpis(symbol: str) -> dict:
    from App.models.data.UsWatch import UsStockKpi
    rows = UsStockKpi.query.filter_by(symbol=symbol.upper()).order_by(UsStockKpi.period.desc()).all()
    defs = kpi_defs(symbol)
    keys = [k for k, _ in defs]
    for r in rows:
        for k in r.get_metrics():
            if k not in keys:
                keys.append(k)
                defs = defs + [(k, k)]
    return {'defs': [{'key': k, 'label': lbl} for k, lbl in defs], 'rows': [r.to_dict() for r in rows]}


def save_kpi(symbol: str, payload: dict) -> dict:
    from App.exts import db
    from App.models.data.UsWatch import UsStockKpi
    period = (payload.get('period') or '').strip().upper()
    if not period:
        raise ValueError('期间不能为空，如 2026Q3')
    metrics = {}
    for k, v in (payload.get('metrics') or {}).items():
        if v in (None, ''):
            continue
        try:
            metrics[k] = float(v)
        except (TypeError, ValueError):
            raise ValueError(f'{k} 不是数字：{v}')
    kid = payload.get('id')
    r = UsStockKpi.query.get(kid) if kid else UsStockKpi.query.filter_by(symbol=symbol.upper(), period=period).first()
    if r is None:
        r = UsStockKpi(symbol=symbol.upper())
        db.session.add(r)
    r.period = period
    pe = payload.get('period_end')
    r.period_end = datetime.strptime(pe, '%Y-%m-%d').date() if pe else None
    r.metrics = json.dumps(metrics, ensure_ascii=False)
    r.notes = (payload.get('notes') or '').strip() or None
    db.session.commit()
    return r.to_dict()


def delete_kpi(kid: int):
    from App.exts import db
    from App.models.data.UsWatch import UsStockKpi
    r = UsStockKpi.query.get(kid)
    if r is None:
        raise ValueError('记录不存在')
    db.session.delete(r)
    db.session.commit()


def list_filings(symbol: str, limit: int = 200) -> list:
    from App.models.data.UsWatch import UsSecFiling
    from App.services.us_sec_watch import describe_items
    rows = (UsSecFiling.query.filter_by(symbol=symbol.upper())
            .order_by(UsSecFiling.filed_date.desc(), UsSecFiling.id.desc()).limit(limit).all())
    out = []
    for r in rows:
        d = r.to_dict()
        d['items_desc'] = describe_items(r.items)
        out.append(d)
    return out
