# -*- coding: utf-8 -*-
"""
持仓股票实时数据服务
- 持仓查询：从 trade_records 推算（buy 数量 - sell 数量 > 0 视为持仓）
- 当日 1m：用 pytdx 拉取，落到 data/realtime/1m/<YYYY-MM-DD>/<code>.csv
- 当日 15m：1m 重采样为 15m，落到 data/realtime/15m/<YYYY-MM-DD>/<code>.csv
- 历史 15m：复用 data/15m/<code>.parquet（与 viewer_15m / 个股趋势同源）
"""
import os
import logging
from typing import List, Dict, Optional
from datetime import datetime, date

import pandas as pd

from App.exts import db
from App.utils.path_manager import get_path_manager

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 路径
# ------------------------------------------------------------------
def get_realtime_dir() -> str:
    """data/realtime 根目录"""
    pm = get_path_manager()
    p = str(pm.data_base / 'realtime')
    os.makedirs(p, exist_ok=True)
    return p


def _intraday_path(period: str, trade_date: date, code: str) -> str:
    """data/realtime/<period>/<YYYY-MM-DD>/<code>.csv"""
    if period not in ('1m', '15m'):
        raise ValueError(f"period must be '1m' or '15m', got {period}")
    folder = os.path.join(get_realtime_dir(), period, trade_date.strftime('%Y-%m-%d'))
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f'{code}.csv')


# ------------------------------------------------------------------
# 持仓查询
# ------------------------------------------------------------------
def get_current_holdings() -> List[Dict]:
    """从 trade_records 推算当前持仓（buy 数量 - sell 数量 > 0）

    Returns:
        List[{kind:'holding', stock_code, stock_name, net_quantity, avg_cost}]，按代码升序
    """
    try:
        from App.models.trade.trade_records import TradeRecord
        from sqlalchemy import func, case

        rows = (db.session.query(
                    TradeRecord.stock_code,
                    TradeRecord.stock_name,
                    func.sum(case(
                        (TradeRecord.trade_type == 'buy', TradeRecord.quantity),
                        (TradeRecord.trade_type == 'sell', -TradeRecord.quantity),
                        else_=0,
                    )).label('net_qty'),
                    func.sum(case(
                        (TradeRecord.trade_type == 'buy', TradeRecord.price * TradeRecord.quantity),
                        else_=0,
                    )).label('total_buy_amt'),
                    func.sum(case(
                        (TradeRecord.trade_type == 'buy', TradeRecord.quantity),
                        else_=0,
                    )).label('total_buy_qty'),
                )
                .filter(TradeRecord.status.in_(['executed', 'partial']))
                .group_by(TradeRecord.stock_code, TradeRecord.stock_name)
                .all())

        out = []
        for r in rows:
            net_qty = int(r.net_qty or 0)
            if net_qty <= 0:
                continue
            buy_amt = float(r.total_buy_amt or 0)
            buy_qty = int(r.total_buy_qty or 0)
            avg_cost = (buy_amt / buy_qty) if buy_qty > 0 else None
            out.append({
                'kind': 'holding',
                'stock_code': r.stock_code,
                'stock_name': r.stock_name,
                'net_quantity': net_qty,
                'avg_cost': round(avg_cost, 4) if avg_cost is not None else None,
            })

        out.sort(key=lambda x: x['stock_code'])
        return out

    except Exception as e:
        logger.exception(f'查询当前持仓失败: {e}')
        return []


def get_watching_stocks() -> List[Dict]:
    """从 trade_plans 取等待入场的股票（planning/pending + direction=long）

    同一代码可能有多条计划（如不同入场价位的分批计划），按 stock_code 去重，
    保留最新的（updated_at 倒序），其余可合并显示为加仓计划候选。

    Returns:
        List[{kind:'watching', stock_code, stock_name,
              target_entry_price, target_quantity,
              stop_loss_price, take_profit_price,
              plan_id, trade_mode, status}]
    """
    try:
        from App.models.trade.trade_plan import TradePlan

        rows = (TradePlan.query
                .filter(TradePlan.status.in_(['planning', 'pending']))
                .filter(TradePlan.direction == 'long')
                .order_by(TradePlan.updated_at.desc())
                .all())

        seen = set()
        out = []
        for r in rows:
            if not r.stock_code or r.stock_code in seen:
                continue
            seen.add(r.stock_code)
            out.append({
                'kind': 'watching',
                'stock_code': r.stock_code,
                'stock_name': r.stock_name,
                'target_entry_price': float(r.entry_price) if r.entry_price is not None else None,
                'target_quantity': int(r.quantity) if r.quantity is not None else None,
                'stop_loss_price': float(r.stop_loss_price) if r.stop_loss_price is not None else None,
                'take_profit_price': float(r.take_profit_price) if r.take_profit_price is not None else None,
                'plan_id': r.plan_id,
                'trade_mode': r.trade_mode,
                'status': r.status,
            })

        out.sort(key=lambda x: x['stock_code'])
        return out

    except Exception as e:
        logger.exception(f'查询等待入场股票失败: {e}')
        return []


def get_tracked_boards() -> List[Dict]:
    """纳入持仓跟踪的板块（来自 eval_board_holding）

    Returns:
        List[{kind:'board', stock_code, stock_name, etf_code, etf_name, note}]，按代码升序
    """
    try:
        from App.models.evaluation.BoardHolding import BoardHolding
        BoardHolding.ensure_table()
        rows = BoardHolding.list_all()
        return [{
            'kind': 'board',
            'stock_code': r.board_code,
            'stock_name': r.board_name or r.board_code,
            'etf_code': r.etf_code,
            'etf_name': r.etf_name,
            'note': r.note,
        } for r in rows]
    except Exception as e:
        logger.exception(f'查询跟踪板块失败: {e}')
        return []


def get_focus_stocks() -> List[Dict]:
    """关注列表：已持仓 + 等待入场 + 跟踪板块

    - 已持仓优先：若 trade_plans 里有同代码 planning/pending 计划，仅显示持仓行（避免重复）
    - 仅未持仓的 watching 计划单独显示
    - 跟踪板块（BK 代码）追加在最后；与已有持仓/关注同代码则跳过
    - 持仓行在前，关注行其次，板块行最后；组内按代码升序
    """
    holdings = get_current_holdings()
    watching_all = get_watching_stocks()
    held_codes = {h['stock_code'] for h in holdings}
    watching = [w for w in watching_all if w['stock_code'] not in held_codes]

    seen = held_codes | {w['stock_code'] for w in watching}
    boards = [b for b in get_tracked_boards() if b['stock_code'] not in seen]
    return holdings + watching + boards


# ------------------------------------------------------------------
# pytdx 实时拉取 + 落盘
# ------------------------------------------------------------------
def fetch_today_1m(code: str, save: bool = True) -> pd.DataFrame:
    """拉取当日 1m 数据并落到临时文件夹

    Args:
        code: 股票代码
        save: 是否落盘（False 时仅返回 DataFrame）

    Returns:
        pd.DataFrame  cols: date, open, close, high, low, volume, money
    """
    try:
        from App.codes.downloads.DlPytdx import download_stock_1m_pytdx

        df, _end = download_stock_1m_pytdx(code, days=1)
        if df is None or df.empty:
            logger.warning(f'pytdx 当日 1m 返回空: {code}')
            return pd.DataFrame()

        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        today = pd.Timestamp.now().normalize()
        df = df[df['date'] >= today].reset_index(drop=True)

        keep = [c for c in ('date', 'open', 'close', 'high', 'low', 'volume', 'money') if c in df.columns]
        df = df[keep]

        # 滤掉 pytdx 已知的 14:59 占位行：volume<1 且 H==L==O==C
        if not df.empty:
            try:
                placeholder = (df['volume'] < 1) & (df['high'] == df['low']) & (df['open'] == df['close'])
                dropped = int(placeholder.sum())
                if dropped:
                    df = df[~placeholder].reset_index(drop=True)
                    logger.debug(f'{code}: 过滤 {dropped} 条 pytdx 占位行')
            except Exception:
                pass

        if save and not df.empty:
            trade_date = df['date'].max().date()
            path = _intraday_path('1m', trade_date, code)
            df.to_csv(path, index=False)
            logger.info(f'{code}: 当日 1m 已保存 {len(df)} 行 → {path}')

        return df

    except Exception as e:
        logger.exception(f'pytdx 拉取当日 1m 失败 {code}: {e}')
        return pd.DataFrame()


def build_today_15m(code: str, df_1m: Optional[pd.DataFrame] = None,
                    save: bool = True) -> pd.DataFrame:
    """将当日 1m 聚合为 15m"""
    from App.codes.utils.data_processing import ResampleData

    if df_1m is None:
        df_1m = fetch_today_1m(code, save=save)
    if df_1m is None or df_1m.empty:
        return pd.DataFrame()

    try:
        df_15m = ResampleData.resample_1m_data(df_1m, freq='15m')
    except Exception as e:
        logger.warning(f'{code}: 1m → 15m 聚合失败 {e}')
        return pd.DataFrame()

    if df_15m is None or df_15m.empty:
        return pd.DataFrame()

    if save:
        trade_date = pd.to_datetime(df_15m['date']).max().date()
        path = _intraday_path('15m', trade_date, code)
        df_15m.to_csv(path, index=False)
        logger.info(f'{code}: 当日 15m 已保存 {len(df_15m)} 行 → {path}')

    return df_15m


# ------------------------------------------------------------------
# 1m 合并：历史 quarters + 盘中 realtime + 本次现拉
# ------------------------------------------------------------------
_1M_MERGE_DAYS = 8   # 合并后只保留最近 N 天 1m（够算 5m/15m 的 MA20+确认，且省内存）
_1M_COLS = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']


def load_history_1m(code: str, quarters: int = 2) -> pd.DataFrame:
    """读历史 1m（data/quarters/<年>/<季>/<code>.parquet），取最近 `quarters` 个季度文件。

    与个股 1m 下载/RNN 同源。返回按时间升序、仅含标准 7 列的 DataFrame（无则空表）。
    """
    try:
        pm = get_path_manager()
        qroot = pm.data_base / 'quarters'
        if not qroot.exists():
            return pd.DataFrame()
        # data/quarters/<年>/<季>/<code>.parquet；路径按 年/季 字典序即时间序
        files = sorted(qroot.glob(f'*/*/{code}.parquet'))
        if not files:
            return pd.DataFrame()
        frames = []
        for fp in files[-max(1, quarters):]:
            try:
                d = pd.read_parquet(fp)
                if d is not None and not d.empty:
                    frames.append(d)
            except Exception as e:
                logger.debug(f'{code}: 读历史 1m 分片失败 {fp}: {e}')
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df['date'] = pd.to_datetime(df['date'])
        keep = [c for c in _1M_COLS if c in df.columns]
        return df[keep].sort_values('date').reset_index(drop=True)
    except Exception as e:
        logger.warning(f'{code}: 读历史 1m 失败 {e}')
        return pd.DataFrame()


def load_persisted_realtime_1m(code: str, days: int = 10) -> pd.DataFrame:
    """读已落盘的盘中 1m（data/realtime/1m/<日期>/<code>.csv），扫最近 `days` 个日期目录。

    这些文件由 holdings_1m_autofetch 后台线程 / fetch_today_1m 累积，是本次现拉失败时的兜底。
    """
    try:
        root = os.path.join(get_realtime_dir(), '1m')
        if not os.path.isdir(root):
            return pd.DataFrame()
        date_dirs = sorted(d for d in os.listdir(root)
                           if os.path.isdir(os.path.join(root, d)))
        frames = []
        for d in date_dirs[-max(1, days):]:
            fp = os.path.join(root, d, f'{code}.csv')
            if os.path.exists(fp):
                try:
                    frames.append(pd.read_csv(fp, parse_dates=['date']))
                except Exception as e:
                    logger.debug(f'{code}: 读盘中 1m 失败 {fp}: {e}')
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df['date'] = pd.to_datetime(df['date'])
        keep = [c for c in _1M_COLS if c in df.columns]
        return df[keep].sort_values('date').reset_index(drop=True)
    except Exception as e:
        logger.warning(f'{code}: 读盘中 1m 失败 {e}')
        return pd.DataFrame()


def load_merged_1m(code: str, refresh: bool = True) -> Dict:
    """合并「历史 1m + 已落盘盘中 1m + 本次现拉当日 1m」为一份连续 1m。

    Args:
        code: 股票代码
        refresh: True 则现拉一次 pytdx 当日 1m（成功即落盘 realtime/1m/<今日>/），
                 并把是否成功透传给调用方；False 只读已落盘数据（不触发网络）。

    Returns:
        {df, fetch_ok, fetch_attempted, hist_rows, persisted_rows, fetched_rows, total_rows}
        - fetch_ok: 本次现拉是否成功拿到当日 1m（refresh=False 时为 None）
        - df: 合并去重后的 1m（升序，仅最近 _1M_MERGE_DAYS 天，标准 7 列；无则空表）
    """
    hist = load_history_1m(code)
    persisted = load_persisted_realtime_1m(code)

    fetch_ok = None
    fetched = pd.DataFrame()
    if refresh:
        try:
            fetched = fetch_today_1m(code, save=True)   # 成功则落到今日 realtime csv
        except Exception as e:
            logger.warning(f'{code}: 本次现拉 1m 异常 {e}')
            fetched = pd.DataFrame()
        fetch_ok = fetched is not None and not fetched.empty

    parts = [p for p in (hist, persisted, fetched) if p is not None and not p.empty]
    if not parts:
        return {'df': pd.DataFrame(), 'fetch_ok': fetch_ok,
                'fetch_attempted': refresh, 'hist_rows': 0,
                'persisted_rows': 0, 'fetched_rows': 0, 'total_rows': 0}

    merged = pd.concat(parts, ignore_index=True)
    merged['date'] = pd.to_datetime(merged['date'])
    merged = (merged.drop_duplicates(subset=['date'], keep='last')
                    .sort_values('date').reset_index(drop=True))

    # 只保留最近 N 天，够算 5m/15m 的均线+确认，省内存
    cutoff = merged['date'].max().normalize() - pd.Timedelta(days=_1M_MERGE_DAYS)
    merged = merged[merged['date'] >= cutoff].reset_index(drop=True)

    return {
        'df': merged,
        'fetch_ok': fetch_ok,
        'fetch_attempted': refresh,
        'hist_rows': len(hist),
        'persisted_rows': len(persisted),
        'fetched_rows': 0 if fetched is None else len(fetched),
        'total_rows': len(merged),
    }


# ------------------------------------------------------------------
# 历史 + 当日合并（15m）
# ------------------------------------------------------------------
def load_history_15m(code: str) -> pd.DataFrame:
    """读历史 15m（与 viewer_15m / stock_trend 同源）"""
    try:
        from App.routes.data.viewer_15m_route import (
            _get_15m_dir, _find_15m_file, _read_15m_file,
        )
        fpath = _find_15m_file(_get_15m_dir(), code)
        if not fpath:
            return pd.DataFrame()
        df = _read_15m_file(fpath)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        return df.sort_values('date').reset_index(drop=True)
    except Exception as e:
        logger.warning(f'{code}: 读历史 15m 失败 {e}')
        return pd.DataFrame()


def merge_history_and_today_15m(code: str, refresh: bool = True) -> Dict:
    """合并历史 15m + 当日实时 15m

    Args:
        code: 股票代码
        refresh: True 则现拉 pytdx 当日 1m 重新聚合并落盘；False 则直接读临时缓存

    Returns:
        {df: DataFrame, intraday_rows: int, total_rows: int, has_intraday: bool}
    """
    hist = load_history_15m(code)
    today = pd.Timestamp.now().normalize().date()

    if refresh:
        today_15m = build_today_15m(code)
    else:
        path = _intraday_path('15m', today, code)
        if os.path.exists(path):
            try:
                today_15m = pd.read_csv(path, parse_dates=['date'])
            except Exception as e:
                logger.warning(f'{code}: 读临时 15m 失败 {e}')
                today_15m = pd.DataFrame()
        else:
            today_15m = pd.DataFrame()

    if today_15m is None or today_15m.empty:
        return {
            'df': hist,
            'intraday_rows': 0,
            'total_rows': len(hist),
            'has_intraday': False,
        }

    if hist.empty:
        merged = today_15m.copy()
    else:
        # 历史 15m 切到今日 00:00 之前，避免重叠
        cutoff = pd.Timestamp(today)
        hist_clean = hist[hist['date'] < cutoff]
        merged = pd.concat([hist_clean, today_15m], ignore_index=True)

    merged = (merged
              .drop_duplicates(subset=['date'], keep='last')
              .sort_values('date')
              .reset_index(drop=True))

    return {
        'df': merged,
        'intraday_rows': len(today_15m),
        'total_rows': len(merged),
        'has_intraday': True,
    }
