# -*- coding: utf-8 -*-
"""批量把所有 15m parquet 从 v1 信号改写成 v2 信号 + 重算下游 cycle stats。

执行：
  python -m App.codes.Signals.backfill_v2          # 全量
  python -m App.codes.Signals.backfill_v2 002812   # 单只测试

规则：
  - 首次运行会把 data/15m/<code>.parquet 备份成 data/15m/<code>.v1.bak.parquet
  - 重算列：Signal / SignalChoice / SignalId / SignalStartIndex
            StartPrice / EndPrice / StartPriceIndex / EndPriceIndex
            CycleAmplitudeMax / CycleAmplitudePerBar
            CycleLengthMax / CycleLengthPerBar
  - 不动列：MACD / Dif / Dea / EmaShort/Mid/Long / DifSm / DifMl（数学结果一致）
            Daily1mVolMax* / Cycle1mVolMax* / EndDaily1mVolMax5（需 1m 数据，下次盘后跑）
            next/preCycle*（依赖 SignalId 分组，但和老 v1 的 grouping 已经对不上 —— 标记待清理）
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
import pandas as pd

from config import Config
from App.codes.Signals.MacdSignalV2 import compute_v2_signals_pipeline
from App.codes.Signals.StatisticsMacd import StatisticsMACD
from App.codes.parsers.MacdParser import (
    Signal, SignalChoice, SignalId, SignalStartIndex,
    StartPrice, EndPrice, StartPriceIndex, EndPriceIndex,
    CycleAmplitudeMax, CycleLengthMax,
)


def _root() -> Path:
    return Path(Config.get_project_root())


def _process_one(parquet_path: Path, dry_run: bool = False) -> dict:
    code = parquet_path.stem
    stat = {'code': code, 'rows': 0, 'flips_v1': 0, 'flips_v2': 0,
            'last_trend_v1': None, 'last_trend_v2': None}
    try:
        df = pd.read_parquet(parquet_path)
        df['date'] = pd.to_datetime(df['date'])
        stat['rows'] = len(df)
        if df.empty:
            return {**stat, 'status': 'skip-empty'}

        # 统计 v1 flip 数 / 最后趋势
        if SignalChoice in df.columns:
            stat['flips_v1'] = df[SignalChoice].notna().sum()
        if Signal in df.columns and not df.empty:
            last = pd.to_numeric(df[Signal], errors='coerce').dropna()
            if len(last):
                stat['last_trend_v1'] = '↑' if last.iloc[-1] > 0 else '↓' if last.iloc[-1] < 0 else '?'

        # ---- 跑 v2 信号 ----
        # compute_v2_signals_pipeline 会自己重算 MACD（确保和 v2 用的 MA30 一致）
        df = compute_v2_signals_pipeline(df, n=7)

        # ---- 重算 cycle stats（需要 Signal/SignalChoice 已就位，date 在列里）----
        # s_StartEndIndex / s_CycleAmplitude / s_CycleLength 都假定 date 为列
        try:
            df = StatisticsMACD.s_StartEndIndex(df)
            df = StatisticsMACD.s_CycleAmplitude(df)
            df = StatisticsMACD.s_CycleLength(df)
        except Exception as e:
            # 这里失败一般是缺 Daily1mVolMax5 列 —— s_StartEndIndex 内部会访问它。
            # 退化处理：手动加个 NaN 占位再重试
            if 'Daily1mVolMax5' not in df.columns:
                df['Daily1mVolMax5'] = pd.NA
                df = StatisticsMACD.s_StartEndIndex(df)
                df = StatisticsMACD.s_CycleAmplitude(df)
                df = StatisticsMACD.s_CycleLength(df)
            else:
                raise

        # 统计 v2
        if SignalChoice in df.columns:
            stat['flips_v2'] = df[SignalChoice].notna().sum()
        if Signal in df.columns:
            last = pd.to_numeric(df[Signal], errors='coerce').dropna()
            if len(last):
                stat['last_trend_v2'] = '↑' if last.iloc[-1] > 0 else '↓' if last.iloc[-1] < 0 else '?'

        if dry_run:
            return {**stat, 'status': 'dry-run'}

        # ---- 备份 + 写回 ----
        bak_path = parquet_path.with_suffix('.v1.bak.parquet')
        if not bak_path.exists():
            # 首次运行：备份原始 v1 版
            import shutil
            shutil.copy2(parquet_path, bak_path)

        df.to_parquet(parquet_path, index=False)
        return {**stat, 'status': 'ok'}
    except Exception as e:
        return {**stat, 'status': f'ERR: {type(e).__name__}: {e}'}


def main(codes=None, dry_run=False):
    base = _root() / 'data' / '15m'
    if not base.exists():
        print(f'目录不存在: {base}')
        return

    if codes:
        files = [base / f'{c}.parquet' for c in codes]
        files = [f for f in files if f.exists()]
    else:
        # 全量：不算 .v1.bak.parquet
        files = sorted(p for p in base.glob('*.parquet') if not p.name.endswith('.v1.bak.parquet'))

    print(f"找到 {len(files)} 个 parquet（dry_run={dry_run}）\n")
    t0 = time.time()
    results = []
    for i, p in enumerate(files, 1):
        r = _process_one(p, dry_run=dry_run)
        results.append(r)
        if i <= 5 or i % 25 == 0 or i == len(files):
            print(f"[{i:>3}/{len(files)}] {r['code']:>8} rows={r['rows']:>5} "
                  f"v1 flips={r['flips_v1']:>3} 最末={r['last_trend_v1'] or '?'} | "
                  f"v2 flips={r['flips_v2']:>3} 最末={r['last_trend_v2'] or '?'} | "
                  f"{r['status']}")
    elapsed = time.time() - t0

    # 汇总
    ok = sum(1 for r in results if r['status'].startswith('ok') or r['status'] == 'dry-run')
    err = sum(1 for r in results if r['status'].startswith('ERR'))
    skip = sum(1 for r in results if r['status'].startswith('skip'))
    changed_trend = sum(1 for r in results
                        if r['last_trend_v1'] and r['last_trend_v2']
                        and r['last_trend_v1'] != r['last_trend_v2'])

    avg_flip_v1 = sum(r['flips_v1'] for r in results) / max(1, len(results))
    avg_flip_v2 = sum(r['flips_v2'] for r in results) / max(1, len(results))

    print()
    print('=' * 70)
    print(f"完成：成功 {ok}，失败 {err}，跳过 {skip}，用时 {elapsed:.1f}s")
    print(f"平均 flip 数: v1 {avg_flip_v1:.1f} → v2 {avg_flip_v2:.1f}（+{(avg_flip_v2-avg_flip_v1)/max(1,avg_flip_v1)*100:.0f}%）")
    print(f"最末趋势翻转的票数: {changed_trend} / {len(results)}")


if __name__ == '__main__':
    from App import create_app
    app = create_app()
    args = sys.argv[1:]
    dry = '--dry' in args
    codes = [a for a in args if not a.startswith('--')] or None
    with app.app_context():
        main(codes, dry_run=dry)
