# -*- coding: utf-8 -*-
"""持仓股票当日 1m 自动拉取（交易时段每 5 分钟）。

思路参考盘中路由 /stock/live 的「定时拉下一分钟」，但改为**服务端后台 daemon 线程**，
不依赖任何页面打开：交易时段内每 interval 秒，为「当前持仓」（trade_records 净持仓>0）
逐只拉当日 1m 并落盘（复用 realtime_data_service.fetch_today_1m → pytdx →
data/realtime/1m/<日期>/<code>.csv，与 /trade/holdings/realtime 同源）。非交易时段空转跳过。

单例：一个进程内只跑一个线程（_state['running'] + _lock 守护）。控制/查看走
holdings_realtime_route 的 /api/trade/holdings/autofetch/{status,start,stop,run_once}。
与 minute_data_repair 的后台线程模式保持一致（项目未装任何调度器）。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dtime

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 300  # 5 分钟

# 进度/状态（单进程单例）
_state = {
    'running': False,
    'started': None,
    'interval_sec': DEFAULT_INTERVAL_SEC,
    'cycles': 0,
    'in_session': False,
    'last_run': None,
    'last_run_reason': None,
    'next_run': None,
    'last_codes': 0,
    'last_ok': 0,
    'last_fail': 0,
    'last_results': {},   # {code: rows | 'err: ...'}
    'last_error': None,
    # 板块 15m 盘中刷新（持仓+关注股所属板块）
    'last_board_codes': 0,
    'last_board_ok': 0,
    'last_board_fail': 0,
    'last_board_results': {},   # {board_code: rows | 'err: ...'}
    'last_summary_date': None,  # 每日收盘小结最近发送日期（YYYY-MM-DD），防当天重复发
}
_lock = threading.Lock()
_stop = threading.Event()
_thread = None
_ever_started = False   # 进程生命周期内是否启动过（供 ensure_started 只兜底一次）


# ---------------- 交易时段判断 ----------------
_AM_OPEN = dtime(9, 30)
_AM_CLOSE = dtime(11, 30)
_PM_OPEN = dtime(13, 0)
_PM_CLOSE = dtime(15, 1)   # 多留 1 分钟，确保能拿到 15:00 收盘那根


def in_trading_session(now: datetime = None) -> bool:
    """A 股交易时段：周一~周五 9:30-11:30 / 13:00-15:01。

    注意：不含法定节假日判断（项目内无交易日历），节假日会照常空拉，
    但 pytdx 当日无数据时 fetch_today_1m 返回空、幂等落盘、无副作用。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:   # 周六/周日
        return False
    t = now.time()
    return (_AM_OPEN <= t <= _AM_CLOSE) or (_PM_OPEN <= t <= _PM_CLOSE)


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# 每日收盘小结触发窗口：收盘（_PM_CLOSE 15:01）之后到当晚 23:00 之间，当天没发过就发一次
_SUMMARY_START = dtime(15, 1)
_SUMMARY_END = dtime(23, 0)


def _maybe_daily_summary(app):
    """收盘后触发一次「每只监测股当日小结」邮件（工作日、时段内、当天未发过）。

    失败则回滚当日标志，下一轮（5 分钟后）重试。整个逻辑放 not-in-session 分支调用，
    线程收盘后仍每 interval 空转，正好承载此定时任务（项目无独立调度器）。
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return
    if not (_SUMMARY_START <= now.time() <= _SUMMARY_END):
        return
    today = now.strftime('%Y-%m-%d')
    with _lock:
        if _state.get('last_summary_date') == today:
            return
        _state['last_summary_date'] = today   # 先占位，避免并发/重复发

    try:
        from App.services.watch_daily_summary import send_daily_summary
        res = send_daily_summary(app)
        logger.info(f'[autofetch] 每日盯盘小结已发: {res}')
    except Exception:
        with _lock:
            _state['last_summary_date'] = None   # 失败回滚，下一轮重试
        logger.exception('[autofetch] 每日盯盘小结发送失败')


# ---------------- 板块 15m 盘中刷新 ----------------
def _refresh_board_15m(board_code: str) -> int:
    """拉当日板块 1m 合成 15m 并并入 data/15m/{board}.parquet，返回并入的 15m 根数。

    与个股不同源：板块走 East Money（push2delay trends2，见 BoardDownloader），
    不能用 pytdx。落盘路径/合并方式与下载路由 STEP3 一致（按 date 去重 keep=last、升序），
    供 watch_detail_signal 的板块 15m 方向与盯盘图读取。无数据返回 0。
    """
    import os
    import pandas as pd
    from App.codes.downloads.eastmoney.board_downloader import BoardDownloader
    from App.codes.utils.Normal import ResampleData
    from App.utils.file_utils import get_stock_data_path

    df1 = BoardDownloader.board_1m_multiple(board_code, days=1)
    if df1 is None or df1.empty:
        return 0
    d15 = ResampleData.resample_1m_data(df1, '15m')
    if d15 is None or d15.empty:
        return 0
    d15['date'] = pd.to_datetime(d15['date'])

    fp = get_stock_data_path(board_code, data_type='15m_normal')   # data/15m/{code}.parquet
    if os.path.exists(fp):
        try:
            old = pd.read_parquet(fp)
            old['date'] = pd.to_datetime(old['date'])
            merged = pd.concat([old, d15], ignore_index=True)
            merged = (merged.drop_duplicates(subset=['date'], keep='last')
                            .sort_values('date').reset_index(drop=True))
        except Exception as e:   # 旧文件损坏则以新数据覆盖
            logger.debug(f'[autofetch] 板块 {board_code} 旧 15m 读取失败，改覆盖: {e}')
            merged = d15.sort_values('date').reset_index(drop=True)
    else:
        merged = d15.sort_values('date').reset_index(drop=True)

    os.makedirs(os.path.dirname(fp), exist_ok=True)
    merged.to_parquet(fp, index=False, engine='pyarrow')
    return len(d15)


# ---------------- 一次拉取 ----------------
def _fetch_once(app) -> dict:
    """为 持仓+关注 股票逐只拉当日 1m 落盘并合成当日 15m，并刷新其所属板块 15m，返回本轮摘要。

    覆盖 holding + watching（低吸目标多在 watching），15m 缓存供底部放量信号读取。
    板块 15m 与个股不同源、单独刷新（见 _refresh_board_15m）。
    """
    from App.services.realtime_data_service import (
        get_focus_stocks, fetch_today_1m, build_today_15m,
    )

    with app.app_context():
        focus = [x for x in get_focus_stocks() if x.get('kind') in ('holding', 'watching')]

        results = {}
        ok = fail = 0
        for item in focus:
            if _stop.is_set():
                break
            code = item['stock_code']
            try:
                df = fetch_today_1m(code, save=True)
                n = 0 if df is None else len(df)
                results[code] = n
                if n > 0:
                    ok += 1
                    try:
                        build_today_15m(code, df_1m=df, save=True)   # 供信号读缓存
                    except Exception as e:
                        logger.debug(f'[autofetch] {code} 合成 15m 失败: {e}')
                else:
                    fail += 1
            except Exception as e:   # 单只失败不影响其余
                results[code] = f'err: {e}'
                fail += 1
                logger.warning(f'[autofetch] {code} 拉取失败: {e}')

        # ---- 板块 15m：持仓+关注股所属板块去重后逐个刷新 ----
        board_results = {}
        board_ok = board_fail = 0
        try:
            from App.services.watch_detail_signal import _board_of
            boards = {}
            for item in focus:
                bc, _bn = _board_of(item['stock_code'])
                if bc:
                    boards[bc] = True
            for bc in boards:
                if _stop.is_set():
                    break
                try:
                    n = _refresh_board_15m(bc)
                    board_results[bc] = n
                    board_ok += 1
                except Exception as e:   # 单个板块失败不影响其余
                    board_results[bc] = f'err: {e}'
                    board_fail += 1
                    logger.warning(f'[autofetch] 板块 {bc} 15m 刷新失败: {e}')
        except Exception as e:
            logger.warning(f'[autofetch] 板块刷新调度失败: {e}')

    return {'codes': len(focus), 'ok': ok, 'fail': fail, 'results': results,
            'board_codes': len(board_results), 'board_ok': board_ok,
            'board_fail': board_fail, 'board_results': board_results}


# ---------------- 主循环 ----------------
def _sleep_interruptible(seconds: float):
    """可被 stop 打断的睡眠（每 1s 检查一次），保证停止响应及时。"""
    slept = 0.0
    while slept < seconds and not _stop.is_set():
        chunk = min(1.0, seconds - slept)
        time.sleep(chunk)
        slept += chunk


def _run(app, interval_sec: int):
    logger.info(f'[autofetch] 持仓 1m 自动拉取线程启动，interval={interval_sec}s')
    try:
        while not _stop.is_set():
            session = in_trading_session()
            with _lock:
                _state['in_session'] = session

            if session:
                try:
                    summary = _fetch_once(app)
                    with _lock:
                        _state['cycles'] += 1
                        cyc = _state['cycles']
                        _state['last_run'] = _now_str()
                        _state['last_run_reason'] = '交易时段拉取'
                        _state['last_codes'] = summary['codes']
                        _state['last_ok'] = summary['ok']
                        _state['last_fail'] = summary['fail']
                        _state['last_results'] = summary['results']
                        _state['last_board_codes'] = summary['board_codes']
                        _state['last_board_ok'] = summary['board_ok']
                        _state['last_board_fail'] = summary['board_fail']
                        _state['last_board_results'] = summary['board_results']
                        _state['last_error'] = None
                    logger.info(f"[autofetch] 第 {cyc} 轮：持仓+关注 {summary['codes']} 只，"
                                f"成功 {summary['ok']} / 失败 {summary['fail']}；"
                                f"板块 {summary['board_codes']} 个，"
                                f"成功 {summary['board_ok']} / 失败 {summary['board_fail']}")
                    # 数据到位后扫底部放量信号（命中即推送）
                    try:
                        from App.services.bottom_volume_signal import scan_focus
                        _res, _hits = scan_focus(app)
                        if _hits:
                            logger.info(f'[autofetch] 底部放量新命中 {len(_hits)} 只：'
                                        + ', '.join(h['code'] for h in _hits))
                    except Exception:
                        logger.exception('[autofetch] 底部放量扫描异常')
                except Exception as e:
                    with _lock:
                        _state['last_error'] = str(e)
                    logger.exception('[autofetch] 本轮拉取异常')
            else:
                with _lock:
                    _state['last_run_reason'] = '非交易时段，跳过'
                _maybe_daily_summary(app)   # 收盘后发每只监测股的当日小结（当天一次）

            with _lock:
                _state['next_run'] = datetime.fromtimestamp(
                    time.time() + interval_sec).strftime('%Y-%m-%d %H:%M:%S')
            _sleep_interruptible(interval_sec)
    finally:
        with _lock:
            _state['running'] = False
            _state['next_run'] = None
        logger.info('[autofetch] 持仓 1m 自动拉取线程退出')


# ---------------- 控制 ----------------
def start_autofetch(app, interval_sec: int = DEFAULT_INTERVAL_SEC):
    """启动后台线程（单例）。已在运行则直接返回。"""
    global _thread, _ever_started
    with _lock:
        if _state['running']:
            return False, '已在运行'
        _stop.clear()
        _state['running'] = True
        _state['started'] = _now_str()
        _state['interval_sec'] = interval_sec
        _state['cycles'] = 0
        _state['last_error'] = None
        _ever_started = True
    _thread = threading.Thread(target=_run, args=(app, interval_sec),
                               name='holdings-1m-autofetch', daemon=True)
    _thread.start()
    return True, '已启动'


def stop_autofetch():
    """请求停止（当前这轮处理完即停）。"""
    if not _state['running']:
        return False, '未在运行'
    _stop.set()
    return True, '已请求停止（当前这轮处理完即停）'


def ensure_started(app, interval_sec: int = DEFAULT_INTERVAL_SEC):
    """兜底自启：进程内从未启动过才启动（尊重用户手动停止，不反复重启）。

    用于 create_app 未能自启（如未走 Werkzeug reloader 子进程）时，
    首次访问持仓实时页即拉起线程。
    """
    if _ever_started or _state['running']:
        return False, '已启动过'
    return start_autofetch(app, interval_sec)


def run_once_now(app) -> dict:
    """立即同步拉一轮（不经线程，供手动触发/自测）。"""
    return _fetch_once(app)


def get_status() -> dict:
    with _lock:
        st = dict(_state)
    st['in_session_now'] = in_trading_session()
    st['ever_started'] = _ever_started
    return st