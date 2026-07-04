# -*- coding: utf-8 -*-
"""板块成分股 1 分钟数据「修复」服务（后台运行 + 进度）。

对一批个股逐只执行：
  1) 本地 zip 全量导入：E:/User/Documents/stock/{SH|SZ|BJ}.{code}.zip
     → data/quarters/<年>/Q<季>/<code>.parquet（去重，覆盖到 ~2026-05）
  2) TDX(pytdx) 补近期：下近 ~90 天 1m → 合并进 quarters（补 5 月后缺口）
  3) 重生成 15m：data/15m/<code>.parquet（重采样 + MACD 信号）

全程幂等：按 date 去重，重复跑不会重复累加。复用 import_minute_zips 的三个 helper。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from App.exts import db

logger = logging.getLogger(__name__)

DEFAULT_ZIP_DIR = 'E:/User/Documents/stock'

# 进度状态（单任务串行；同一时间只允许一个修复任务）
_state = {
    'running': False, 'board': None, 'done': 0, 'total': 0, 'skipped': 0,
    'ok': 0, 'fail': 0, 'zip_added': 0, 'tdx_added': 0, 'daily_added': 0,
    'cur': None, 'error': None, 'failed': [], 'started': None,
}
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return dict(_state)


def _find_zip(code: str, zip_dir: Path):
    """zip 文件名是 {SH|SZ|BJ}.{code}.zip，逐个前缀试，命中即返回。"""
    for pfx in ('SH', 'SZ', 'BJ'):
        p = zip_dir / f'{pfx}.{code}.zip'
        if p.exists():
            return p
    return None


def _tdx_recent_df(api, code: str, m1_cols, max_batches: int = 40, since=None):
    """用 pytdx 翻页取近期 1m，转成 _M1_COLS schema 的 df。

    since: 已有数据的最新日期(date/Timestamp)。给定时只增量下载——翻到该日之前即停，
    最终只保留 >= since 当天的新数据，避免重复下载、减少对 TDX 服务器的流量。
    since=None 时取全部可回溯范围(约 90 交易日)。
    """
    from App.codes.downloads.DlPytdx import get_market
    market = get_market(code)
    since_ts = pd.Timestamp(since).normalize() if since is not None else None
    bars = {}
    prev_min = None
    for i in range(max_batches):
        try:
            data = api.get_security_bars(8, market, code, i * 800, 800)  # 8=1分钟
        except Exception:
            break
        if not data:
            break
        for b in data:
            bars[b['datetime']] = b
        cmin = min(b['datetime'] for b in data)
        # 已翻到 since 当天或更早 → 需要的增量都拿到了，停
        if since_ts is not None and pd.Timestamp(cmin) <= since_ts:
            break
        if prev_min is not None and cmin >= prev_min:
            break
        prev_min = cmin
        if len(data) < 800:
            break
        time.sleep(0.03)
    if not bars:
        return None
    df = pd.DataFrame(list(bars.values())).rename(
        columns={'datetime': 'date', 'vol': 'volume', 'amount': 'money'})
    df['date'] = pd.to_datetime(df['date'])
    if since_ts is not None:
        df = df[df['date'] >= since_ts]   # 只留 since 当天起的新数据(当天重抓以补全)
        if df.empty:
            return None
    df['year'] = df['date'].dt.year
    df['month'] = df['date'].dt.month
    df['day'] = df['date'].dt.day
    df['hour'] = df['date'].dt.hour
    df['minute'] = df['date'].dt.minute
    for c in ('open', 'close', 'high', 'low', 'volume', 'money'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df[m1_cols]


def _tdx_market_latest_date(api, codes, probe=5):
    """探一次「市场最新交易日」：取前 probe 只各自最新 1 根 1m，取其中最大日期。

    用于跳过本地已到该日的股票（不必再逐只翻页下载）。返回 Timestamp(归一到日) 或 None。
    只要有一只在正常交易，就能拿到市场最新交易日；多探几只是为兜底停牌股。
    """
    from App.codes.downloads.DlPytdx import get_market
    latest = None
    for code in codes[:probe]:
        try:
            data = api.get_security_bars(8, get_market(code), code, 0, 1)  # 8=1分钟，取最新1根
        except Exception:
            continue
        if data:
            d = pd.Timestamp(data[-1]['datetime']).normalize()
            if latest is None or d > latest:
                latest = d
    return latest


def _code_date_range(code, qdirs):
    """从已有 quarters 取该 code 的(最早, 最新)1m 日期。qdirs 为 year/Q 目录列表(新→旧)。"""
    latest = earliest = None
    for qd in qdirs:               # 新→旧：第一个含该 code 的季度即最新
        p = qd / f'{code}.parquet'
        if p.exists():
            try:
                latest = pd.to_datetime(pd.read_parquet(p, columns=['date'])['date']).max()
            except Exception:
                pass
            break
    for qd in reversed(qdirs):      # 旧→新：第一个含该 code 的季度即最早
        p = qd / f'{code}.parquet'
        if p.exists():
            try:
                earliest = pd.to_datetime(pd.read_parquet(p, columns=['date'])['date']).min()
            except Exception:
                pass
            break
    return earliest, latest


def _resample_daily(code: str, dq: Path):
    """读该 code 所有季度 1m → 按交易日聚合成日 K（OHLCV）。返回 df 或 None。"""
    parts = sorted(dq.rglob(f'{code}.parquet'))
    if not parts:
        return None
    pieces = []
    for p in parts:
        try:
            d = pd.read_parquet(p, columns=['date', 'open', 'high', 'low', 'close', 'volume', 'money'])
            pieces.append(d)
        except Exception:
            continue
    if not pieces:
        return None
    df = pd.concat(pieces, ignore_index=True)
    df['date'] = pd.to_datetime(df['date'])
    df = df.dropna(subset=['date']).sort_values('date')
    df['d'] = df['date'].dt.date
    g = df.groupby('d')
    daily = pd.DataFrame({
        'open': g['open'].first(), 'high': g['high'].max(),
        'low': g['low'].min(), 'close': g['close'].last(),
        'volume': g['volume'].sum(), 'money': g['money'].sum(),
    }).reset_index().rename(columns={'d': 'date'})
    return daily


def _upsert_daily(eng, code: str, daily) -> int:
    """把日 K 写入 data_stock_daily，仅插入缺失日期(不动已有行)。返回新增行数。"""
    if daily is None or daily.empty:
        return 0
    with eng.begin() as conn:
        existing = {r[0] for r in conn.execute(
            text('SELECT date FROM data_stock_daily WHERE stock_code = :c'), {'c': code}).fetchall()}
        new_rows = []
        for r in daily.itertuples(index=False):
            if r.date in existing:
                continue
            new_rows.append({'c': code, 'd': r.date,
                             'o': float(r.open), 'cl': float(r.close),
                             'h': float(r.high), 'l': float(r.low),
                             'v': float(r.volume) if pd.notna(r.volume) else None,
                             'm': float(r.money) if pd.notna(r.money) else None})
        if new_rows:
            conn.execute(text(
                'INSERT INTO data_stock_daily (stock_code,date,open,close,high,low,volume,money) '
                'VALUES (:c,:d,:o,:cl,:h,:l,:v,:m)'), new_rows)
    return len(new_rows)


def _write_quarters(df, code, dq, write_quarter):
    """按季度切分写 quarters parquet，返回新增分钟数。"""
    added = 0
    g = df.assign(_q=((df['month'] - 1) // 3 + 1))
    for (y, q), part in g.groupby(['year', '_q']):
        part = part.drop(columns=['_q'])
        out = dq / str(int(y)) / f'Q{int(q)}' / f'{code}.parquet'
        added += write_quarter(part, out, False)['added']
    return added


def _run(app, board_code, codes, zip_dir, tdx_days, then_trend=False):
    from config import Config
    # 复用 import_minute_zips 的解析/写入/重采样逻辑
    from scripts.Others.import_minute_zips import (
        _read_zip_csv, _write_quarter_parquet, _regen_15m, _M1_COLS)
    from App.codes.utils.Normal import ResampleData
    from App.codes.Signals.StatisticsMacd import SignalMethod
    from App.codes.downloads.DlPytdx import AVAILABLE_SERVERS
    from pytdx.hq import TdxHq_API

    root = Path(Config.get_project_root())
    dq = root / 'data' / 'quarters'
    d15 = root / 'data' / '15m'
    zdir = Path(zip_dir)
    max_batches = max(1, min(60, (tdx_days * 4 // 800) + 3))  # 天数→批次(每日~240根)
    # year/Q 目录(新→旧)，用于增量判断已有数据日期范围；只扫一次
    qdirs = sorted([d for y in dq.iterdir() if y.is_dir()
                    for d in y.iterdir() if d.is_dir()],
                   key=lambda d: (d.parent.name, d.name), reverse=True) if dq.exists() else []
    ZIP_COVERED_BEFORE = pd.Timestamp('2025-06-01')  # 已有数据早于此 → 视 zip 已导入，跳过

    # 连一个 TDX 服务器（整批共用）
    api = None
    for ip, port in AVAILABLE_SERVERS:
        try:
            a = TdxHq_API()
            if a.connect(ip, port, time_out=5):
                api = a
                break
        except Exception:
            continue

    # 探一次市场最新交易日：本地已到该日的股票整批筛掉，不进循环
    mkt_latest = _tdx_market_latest_date(api, codes) if api is not None else None
    if mkt_latest is not None:
        logger.info(f'[repair] 市场最新交易日 {mkt_latest:%Y-%m-%d}')

    # 预筛：逐只判断「到底有没有活要干」，没活的直接剔除（不计入 total、不进循环）。
    # 有活 = 缺早期历史待导 zip / 1m 落后市场最新日 / 15m 文件缺失。
    todo, skipped = [], []
    for code in codes:
        earliest, latest = _code_date_range(code, qdirs)
        need_zip = ((earliest is None or earliest > ZIP_COVERED_BEFORE)
                    and _find_zip(code, zdir) is not None)
        up_to_date = (mkt_latest is not None and latest is not None
                      and pd.Timestamp(latest).normalize() >= mkt_latest)
        need_tdx = (api is not None) and not up_to_date
        need_15m = not (d15 / f'{code}.parquet').exists()
        (todo if (need_zip or need_tdx or need_15m) else skipped).append((code, earliest, latest))
    with _lock:
        _state['total'] = len(todo)
        _state['skipped'] = len(skipped)
    logger.info(f'[repair] 预筛：{len(skipped)} 只已最新剔除，实修 {len(todo)} 只'
                + (f'（市场最新 {mkt_latest:%Y-%m-%d}）' if mkt_latest is not None else '（TDX 不可用，仅本地补全）'))

    try:
        with app.app_context():
            for code, earliest, latest in todo:
                with _lock:
                    _state['cur'] = code
                try:
                    added_any = False   # 本轮是否真有新 1m 落地（决定要不要重算 15m/日K）
                    # 1) 本地 zip —— 仅在缺早期历史时导(避免每次重读 5MB + 去重)
                    if earliest is None or earliest > ZIP_COVERED_BEFORE:
                        zp = _find_zip(code, zdir)
                        if zp:
                            zdf = _read_zip_csv(zp)
                            if zdf is not None and not zdf.empty:
                                n = _write_quarters(zdf, code, dq, _write_quarter_parquet)
                                zdates = pd.to_datetime(zdf['date'])
                                zmin, zmax = zdates.min(), zdates.max()
                                latest = zmax if latest is None else max(latest, zmax)
                                added_any = added_any or n > 0
                                logger.info(
                                    f'[repair] {code} 本地zip {zmin:%Y-%m-%d}~{zmax:%Y-%m-%d}，去重后新增 {n} 根 1m')
                                with _lock:
                                    _state['zip_added'] += n
                    # 2) TDX —— 本地已到市场最新交易日则跳过；否则只下 latest 之后的增量
                    if api is not None:
                        up_to_date = (mkt_latest is not None and latest is not None
                                      and pd.Timestamp(latest).normalize() >= mkt_latest)
                        if up_to_date:
                            logger.info(
                                f'[repair] {code} 跳过TDX：本地已最新到 {pd.Timestamp(latest):%Y-%m-%d}'
                                f'（=市场最新 {mkt_latest:%Y-%m-%d}）')
                        else:
                            tdf = _tdx_recent_df(api, code, _M1_COLS, max_batches, since=latest)
                            if tdf is not None and not tdf.empty:
                                n = _write_quarters(tdf, code, dq, _write_quarter_parquet)
                                tdates = pd.to_datetime(tdf['date'])
                                tmin, tmax = tdates.min(), tdates.max()
                                added_any = added_any or n > 0
                                logger.info(
                                    f'[repair] {code} TDX下载 {tmin:%Y-%m-%d}~{tmax:%Y-%m-%d}，去重后新增 {n} 根 1m'
                                    + (f'（此前最新到 {pd.Timestamp(latest):%Y-%m-%d}）' if latest is not None else '（全量回溯）'))
                                with _lock:
                                    _state['tdx_added'] += n
                            else:
                                logger.info(
                                    f'[repair] {code} TDX无新增，已最新到 '
                                    + (f'{pd.Timestamp(latest):%Y-%m-%d}' if latest is not None else '（无历史）'))
                    # 3/4) 无新 1m 且 15m 已存在 → 15m/日K 都是无操作，直接跳过省时
                    p15 = d15 / f'{code}.parquet'
                    if added_any or not p15.exists():
                        _regen_15m(code, dq, d15, False, ResampleData, SignalMethod)  # 重生成 15m
                        try:
                            eng = db.engines['quanttradingsystem']
                            n = _upsert_daily(eng, code, _resample_daily(code, dq))   # 补日K（仅插缺失日期）
                            with _lock:
                                _state['daily_added'] += n
                        except Exception:
                            logger.exception(f'[repair] {code} 补日K失败')
                    with _lock:
                        _state['ok'] += 1
                except Exception as e:
                    logger.exception(f'[repair] {code} 失败')
                    with _lock:
                        _state['fail'] += 1
                        _state['failed'].append(code)
                finally:
                    with _lock:
                        _state['done'] += 1
            # 修复完自动接力：重算趋势打分 + 分布快照（另起线程，前端接着轮询）
            if then_trend and codes:
                start_trend_recompute(app, board_code, codes)
    except Exception as e:
        logger.exception('[repair] 任务异常')
        with _lock:
            _state['error'] = str(e)
    finally:
        if api is not None:
            try:
                api.disconnect()
            except Exception:
                pass
        with _lock:
            _state['running'] = False
            _state['cur'] = None


# ============== 重算趋势阶段 / 分布快照（数据修复后用）==============
_tstate = {
    'running': False, 'board': None, 'phase': None, 'done': 0, 'total': 0,
    'ts_ok': 0, 'ts_fail': 0, 'snap_ok': 0, 'snap_fail': 0,
    'cur': None, 'error': None, 'started': None,
}
_tlock = threading.Lock()


def get_trend_status() -> dict:
    with _tlock:
        return dict(_tstate)


def _run_trend(app, board_code, codes):
    with app.app_context():
        from datetime import date as _date
        from App.services.stock_trend_score_service import compute_and_persist
        from App.services.dist_snapshot_service import compute_dist_snapshot
        today = _date.today()
        try:
            # 1) 个股趋势打分 StockTrendScore（成分股页的 趋势阶段/信号/分）— 批量一次
            with _tlock:
                _tstate['phase'] = '趋势打分(StockTrendScore)'
            res = compute_and_persist(codes, today)
            with _tlock:
                _tstate['ts_ok'] = res.get('ok', 0)
                _tstate['ts_fail'] = res.get('fail', 0)
                _tstate['phase'] = '分布快照(15m 趋势/目标价)'
            # 2) 分布快照 stock_dist_snapshot（screener/股票池的 15m 趋势 + 预估目标价）— 逐只
            for code in codes:
                with _tlock:
                    _tstate['cur'] = code
                try:
                    compute_dist_snapshot(code, snapshot_date=today, commit=True, merge_below=0)
                    with _tlock:
                        _tstate['snap_ok'] += 1
                except Exception:
                    logger.exception(f'[trend] {code} 分布快照失败')
                    with _tlock:
                        _tstate['snap_fail'] += 1
                finally:
                    with _tlock:
                        _tstate['done'] += 1
        except Exception as e:
            logger.exception('[trend] 重算任务异常')
            with _tlock:
                _tstate['error'] = str(e)
        finally:
            with _tlock:
                _tstate['running'] = False
                _tstate['cur'] = None


def start_trend_recompute(app, board_code, codes):
    """启动后台重算：个股趋势打分 + 分布快照。返回 (ok, message)。"""
    codes = [c for c in codes if c]
    with _tlock:
        if _tstate['running']:
            return False, '已有重算任务在进行中'
        _tstate.update({'running': True, 'board': board_code, 'phase': None,
                        'done': 0, 'total': len(codes), 'ts_ok': 0, 'ts_fail': 0,
                        'snap_ok': 0, 'snap_fail': 0, 'cur': None, 'error': None,
                        'started': time.strftime('%H:%M:%S')})
    if not codes:
        with _tlock:
            _tstate['running'] = False
        return False, '该板块没有成分股'
    threading.Thread(target=_run_trend, args=(app, board_code, codes), daemon=True).start()
    return True, f'已开始重算 {board_code} 的 {len(codes)} 只成分股趋势/分布'


def start_repair(app, board_code, codes, zip_dir=DEFAULT_ZIP_DIR, tdx_days=90, then_trend=False):
    """启动后台修复。返回 (ok, message)。已有任务在跑则拒绝。

    then_trend=True 时，修复循环跑完会自动接力 start_trend_recompute（趋势打分+分布快照），
    实现「下载更新最新数据 + 重算趋势/分布」一步到位。
    """
    codes = [c for c in codes if c]
    with _lock:
        if _state['running']:
            return False, '已有修复任务在进行中'
        _state.update({'running': True, 'board': board_code, 'done': 0,
                       'total': len(codes), 'skipped': 0, 'ok': 0, 'fail': 0,
                       'zip_added': 0, 'tdx_added': 0, 'daily_added': 0, 'cur': None,
                       'error': None, 'failed': [], 'started': time.strftime('%H:%M:%S')})
    if not codes:
        with _lock:
            _state['running'] = False
        return False, '该板块没有成分股可修复'
    threading.Thread(target=_run, args=(app, board_code, codes, zip_dir, tdx_days, then_trend),
                     daemon=True).start()
    tail = '（完成后自动重算趋势/分布）' if then_trend else ''
    return True, f'已开始修复 {board_code} 的 {len(codes)} 只成分股{tail}'
