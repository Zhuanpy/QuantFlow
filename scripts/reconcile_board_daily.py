# -*- coding: utf-8 -*-
"""
板块日K缺口补救脚本（reconcile）

背景见排查结论：板块日K的权威源 push2his(kline) 经常被东财整段 TCP RST 限流，
收盘下载时部分/整批板块没落库，于是 data_stock_daily 里板块尾部出现"参差缺口"
（例：06-17 缺 15 个、06-18 几乎全缺），而个股走 pytdx 不受影响。

本脚本逐个板块对比"最新已落库日期"与"参考交易日"，对落后的板块调用
BoardDownloader.board_daily() 整段回拉并 upsert，同时标记 DailyTaskStatus。

board_daily 内部数据源优先级：
  1) requests 直连 push2his（可达时一次性按 lmt 回补整段，能补齐 17 和 18）
  2) 本地 15m 聚合（绕开 push2his，能补 15m 文件已覆盖的日子，如 18）
  3) Selenium 兜底

因此：
  - push2his 被 RST 时（白天高峰常见）：本脚本能补到 15m 已覆盖的日子（通常是当天/18）。
  - push2his 恢复时（建议清晨/凌晨低峰跑）：能把更早的缺口（如 17）一次性补齐。

用法：
    python scripts/reconcile_board_daily.py            # 全部板块，写库
    python scripts/reconcile_board_daily.py --dry-run  # 只看会补什么，不写库
    python scripts/reconcile_board_daily.py --code BK1032   # 只处理指定板块
"""
import os
import sys
import time
import argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
from sqlalchemy import text

from App import create_app
from App.exts import db
from App.codes.downloads.eastmoney.board_downloader import BoardDownloader
from App.models.data.StockDaily import StockDaily, save_daily_stock_data_to_sql
from App.models.data.DailyTaskStatus import DailyTaskStatus


def _reference_trading_date(conn) -> date:
    """用个股（非板块）在 data_stock_daily 里的最大日期作为权威交易日。

    个股走 pytdx/akshare（稳定源），其最新日期天然就是最近一个交易日，
    且自动绕开节假日（如 06-19 端午节不会出现）。比硬编码/纯工作日推算更准。
    """
    row = conn.execute(text(
        "SELECT MAX(date) FROM data_stock_daily WHERE stock_code NOT LIKE 'BK%'"
    )).fetchone()
    if row and row[0]:
        return row[0]
    # 兜底：今天向前找首个工作日
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _trading_days(conn, ref: date, window: int = 12):
    """最近 window 天内的真实交易日集合（取自个股稳定源），用于精确判定缺口。"""
    start = ref - timedelta(days=window)
    rows = conn.execute(text(
        "SELECT DISTINCT date FROM data_stock_daily "
        "WHERE stock_code NOT LIKE 'BK%' AND date BETWEEN :s AND :e"
    ), {'s': start, 'e': ref}).fetchall()
    return sorted({r[0] for r in rows if r[0]})


def _board_dates(conn, code: str, start: date, ref: date):
    rows = conn.execute(text(
        "SELECT date FROM data_stock_daily "
        "WHERE stock_code = :c AND date BETWEEN :s AND :e"
    ), {'c': code, 's': start, 'e': ref}).fetchall()
    return {r[0] for r in rows if r[0]}


def _board_codes(conn, only_code=None):
    if only_code:
        return [only_code.strip().upper()]
    rows = conn.execute(text(
        "SELECT DISTINCT stock_code FROM data_stock_daily "
        "WHERE stock_code LIKE 'BK%' ORDER BY stock_code"
    )).fetchall()
    return [r[0].strip().upper() for r in rows if r[0]]


def reconcile_boards(dry_run=False, only_code=None, sleep=0.8, log=print):
    """板块日K缺口补救核心逻辑（**须在 app_context 内调用**）。

    逐个板块对比"最新已落库日期"与"参考交易日"，对落后的板块 board_daily() 整段
    回拉并 upsert，同时标记 DailyTaskStatus。可被收盘下载流水线/CLI 复用。

    Args:
        dry_run: True 时只检测会补什么，不写库。
        only_code: 只处理指定板块，如 'BK1032'；None 处理全部 BK%。
        sleep: 每个板块之间的间隔秒数（节流）。
        log: 进度输出回调（默认 print；从下载流水线调用可传 logging.info）。

    Returns:
        dict 汇总：{ref, total, up_to_date, filled, still_gap, failed, gap_detail}。
    """
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        ref = _reference_trading_date(conn)
        codes = _board_codes(conn, only_code)
        trading_days = _trading_days(conn, ref)
        # 各板块当前最新日期
        rows = conn.execute(text(
            "SELECT stock_code, MAX(date) FROM data_stock_daily "
            "WHERE stock_code LIKE 'BK%' GROUP BY stock_code"
        )).fetchall()
        latest_map = {r[0].strip().upper(): r[1] for r in rows if r[0]}

    win_start = trading_days[0] if trading_days else ref
    log('=' * 78)
    log(f'板块日K补救  参考交易日={ref}  板块数={len(codes)}  '
        f'近窗交易日={[str(d) for d in trading_days]}  '
        f'{"[DRY-RUN]" if dry_run else "[写库]"}')
    log('=' * 78)

    up_to_date = filled = still_gap = failed = 0
    gap_detail = []  # (code, 仍缺的日期)

    for i, code in enumerate(codes, 1):
        before = latest_map.get(code)
        if before is not None and before >= ref:
            up_to_date += 1
            continue

        # 计算要回拉的天数：覆盖缺口 + 余量
        if before is None:
            days = 60
        else:
            gap = (ref - before).days
            days = min(max(gap + 10, 30), 365)

        try:
            df = BoardDownloader.board_daily(code, days=days)
        except Exception as e:
            failed += 1
            log(f'[{i}/{len(codes)}] {code:<8} 异常: {type(e).__name__}: {str(e)[:80]}')
            continue

        if df is None or df.empty:
            failed += 1
            log(f'[{i}/{len(codes)}] {code:<8} 无数据（push2his RST + 15m无 + Selenium失败）')
            time.sleep(sleep)
            continue

        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        got_dates = {d.date() for d in df['date']}
        # 这次拉回能补上的、近窗内的真实交易日
        new_dates = sorted(d for d in trading_days
                           if d in got_dates and (before is None or d > before))

        if not dry_run and not df.empty:
            save_daily_stock_data_to_sql(code, df)
            for d in new_dates:
                DailyTaskStatus.mark_task(code, d, 'is_daily_processed')

        # 补完后仍缺的近窗交易日（含中间空洞，如有18缺17）
        with eng.connect() as conn:
            have = _board_dates(conn, code, win_start, ref) if not dry_run else None
        if have is None:
            have = {d for d in trading_days if d == before} | got_dates  # dry-run 预估
        missing = [d for d in trading_days if d not in have]

        if new_dates:
            filled += 1
        tag = ''
        if missing:
            still_gap += 1
            tag = f'  [!] 仍缺: {[str(d) for d in missing]}'
            gap_detail.append((code, missing))
        log(f'[{i}/{len(codes)}] {code:<8} {str(before):<12} -> 新增 '
            f'{[str(d) for d in new_dates] or "无"}{tag}')

        time.sleep(sleep)

    log('-' * 78)
    log(f'汇总：已最新 {up_to_date}，补到新数据 {filled}，仍有尾部缺口 {still_gap}，失败/无源 {failed}')
    if gap_detail:
        log('\n仍有近窗缺口的板块（通常是 push2his 被限流，需清晨/凌晨低峰重跑本脚本）：')
        for c, miss in gap_detail[:80]:
            log(f'  {c}: 缺 {[str(d) for d in miss]}')
    log('=' * 78)

    return {
        'ref': ref, 'total': len(codes), 'up_to_date': up_to_date,
        'filled': filled, 'still_gap': still_gap, 'failed': failed,
        'gap_detail': gap_detail,
    }


def main():
    parser = argparse.ArgumentParser(description='板块日K缺口补救')
    parser.add_argument('--dry-run', action='store_true', help='只检测会补什么，不写库')
    parser.add_argument('--code', default=None, help='只处理指定板块，如 BK1032')
    parser.add_argument('--sleep', type=float, default=0.8, help='每个板块之间的间隔秒数（节流）')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        reconcile_boards(dry_run=args.dry_run, only_code=args.code, sleep=args.sleep)


if __name__ == '__main__':
    main()
