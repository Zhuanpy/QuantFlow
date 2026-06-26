#!/usr/bin/env python3
"""从 ZIP 1m 数据补充股票分时数据 + 重生成 15m

源数据：E:\\User\\Documents\\stock\\<MARKET>.<CODE>.zip
        zip 内为 UTF-8-BOM 的 CSV：时间,开盘价,最高价,最低价,收盘价,成交量,成交额

目标：
  1m  → data/quarters/<year>/Q<n>/<code>.parquet  （按 date 去重，zip 优先）
  15m → data/15m/<code>.parquet                   （重采样 + 重算 MACD 信号）

用法：
    python scripts/import_minute_zips.py                       # 默认 ~292 只
    python scripts/import_minute_zips.py --code 002812         # 单只测试
    python scripts/import_minute_zips.py --codes 002812,000001
    python scripts/import_minute_zips.py --skip-15m            # 只导 1m
    python scripts/import_minute_zips.py --zip-dir D:/other    # 改源目录
    python scripts/import_minute_zips.py --dry-run             # 只解析不写文件
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
# 静音掉一些下游冗长日志
for noisy in ('App.codes.MySql.DataBaseStockData15m',):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger('import_minute_zips')

DEFAULT_ZIP_DIR = r'E:\User\Documents\stock'
# 1m parquet 标准字段顺序（与现有文件对齐）
_M1_COLS = ['open', 'close', 'high', 'low', 'volume', 'money',
            'year', 'month', 'day', 'hour', 'minute', 'date']


def _read_zip_csv(zip_path: Path) -> pd.DataFrame | None:
    """解压 zip 拿到 1 个 CSV，转成标准 schema 的 DataFrame。"""
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = next((n for n in zf.namelist() if n.lower().endswith('.csv')), None)
        if not csv_name:
            return None
        with zf.open(csv_name) as f:
            df = pd.read_csv(f, encoding='utf-8-sig')

    df = df.rename(columns={
        '时间': 'date', '开盘价': 'open', '最高价': 'high', '最低价': 'low',
        '收盘价': 'close', '成交量': 'volume', '成交额': 'money',
    })
    df['date'] = pd.to_datetime(df['date'])
    df['year'] = df['date'].dt.year
    df['month'] = df['date'].dt.month
    df['day'] = df['date'].dt.day
    df['hour'] = df['date'].dt.hour
    df['minute'] = df['date'].dt.minute
    # 数值列强制 float（zip CSV 里 volume 是 float64 即可）
    for c in ('open', 'close', 'high', 'low', 'volume', 'money'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df[_M1_COLS]


def _write_quarter_parquet(df: pd.DataFrame, out_path: Path, dry_run: bool) -> dict:
    """合并并写一个季度的 parquet。

    Returns: {'added': 新增分钟数, 'kept': 保留(已存在)分钟数, 'total': 写入后总数}
    """
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing['date'] = pd.to_datetime(existing['date'])
        existing_cols = [c for c in _M1_COLS if c in existing.columns]
        existing = existing[existing_cols]
        before = set(existing['date'])
        # concat 后 keep='last' → zip 数据排后面 → 重复时以 zip 为准
        merged = pd.concat([existing, df], ignore_index=True)
        merged = merged.drop_duplicates(subset=['date'], keep='last')
        merged = merged.sort_values('date').reset_index(drop=True)
        new_dates = set(merged['date']) - before
        added = len(new_dates)
        kept = len(before & set(merged['date']))
    else:
        merged = df.sort_values('date').reset_index(drop=True)
        added = len(merged)
        kept = 0

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(out_path, index=False)
    return {'added': added, 'kept': kept, 'total': len(merged)}


def _regen_15m(code: str, dq_root: Path, d15_root: Path, dry_run: bool,
               ResampleData, SignalMethod) -> dict | None:
    """重采样所有季度的 1m → 15m + 信号，合并写 data/15m/<code>.parquet。"""
    # 读所有季度的 1m
    parts = sorted(dq_root.rglob(f'{code}.parquet'),
                   key=lambda p: (p.parents[1].name, p.parent.name))  # 按 year/quarter
    if not parts:
        return None
    pieces = []
    for p in parts:
        d = pd.read_parquet(p)
        d['date'] = pd.to_datetime(d['date'])
        pieces.append(d)
    df_1m_full = (pd.concat(pieces, ignore_index=True)
                  .drop_duplicates(subset=['date'], keep='last')
                  .sort_values('date').reset_index(drop=True))

    # 重采样 + 信号
    df_15m = ResampleData.resample_1m_data(df_1m_full, '15m')
    if df_15m is None or df_15m.empty:
        return None
    df_15m = SignalMethod.signal_by_MACD_3ema(df_15m, df_1m_full)

    out_15m = d15_root / f'{code}.parquet'
    # 与现有 15m 合并去重（保护下游已存在的人工字段，如 SignalChoice）
    if out_15m.exists():
        existing = pd.read_parquet(out_15m)
        existing['date'] = pd.to_datetime(existing['date'])
        before = set(existing['date'])
        merged = pd.concat([existing, df_15m], ignore_index=True)
        merged = merged.drop_duplicates(subset=['date'], keep='last')
        merged = merged.sort_values('date').reset_index(drop=True)
        added = len(set(merged['date']) - before)
    else:
        merged = df_15m
        added = len(merged)

    if not dry_run:
        out_15m.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(out_15m, index=False)
    return {'added_15m': added, 'total_15m': len(merged),
            'first': str(merged['date'].min()), 'last': str(merged['date'].max())}


def main():
    parser = argparse.ArgumentParser(description='从 zip 1m 数据补充 + 重生成 15m')
    parser.add_argument('--zip-dir', type=str, default=DEFAULT_ZIP_DIR,
                        help='zip 文件根目录')
    parser.add_argument('--code', type=str, default=None, help='只跑单只')
    parser.add_argument('--codes', type=str, default=None, help='只跑这几只，逗号分隔')
    parser.add_argument('--skip-15m', action='store_true', help='只导 1m，不重生成 15m')
    parser.add_argument('--dry-run', action='store_true', help='只解析不写文件')
    args = parser.parse_args()

    from App import create_app
    from config import Config
    try:
        from App.codes.utils.Normal import ResampleData
        from App.codes.Signals.StatisticsMacd import SignalMethod
    except ImportError as e:
        logger.error(f'15m 重采样模块导入失败: {e}；将强制 --skip-15m')
        ResampleData = SignalMethod = None
        args.skip_15m = True

    app = create_app()
    with app.app_context():
        zip_dir = Path(args.zip_dir)
        if not zip_dir.exists():
            print(f'ERROR: zip 目录不存在: {zip_dir}'); return
        root = Path(Config.get_project_root())
        d15 = root / 'data' / '15m'
        dq = root / 'data' / 'quarters'

        # 1) 建立 code → zip 映射（命名 <MARKET>.<CODE>.zip）
        zip_map = {}
        for p in zip_dir.glob('*.zip'):
            stem = p.stem
            if '.' in stem:
                _, c = stem.split('.', 1)
                zip_map[c] = p
            else:
                zip_map[stem] = p
        print(f'[scan] {zip_dir} 找到 {len(zip_map)} 个 zip')

        # 2) 决定要导哪些 code
        if args.codes:
            codes = [c.strip() for c in args.codes.split(',') if c.strip()]
        elif args.code:
            codes = [args.code.strip()]
        else:
            # 默认：data/15m/ 下"我收集的"个股，排除 BK 板块
            codes = sorted(p.stem for p in d15.glob('*.parquet')
                           if not p.stem.upper().startswith('BK'))
            print(f'[scan] data/15m/ 下找到 {len(codes)} 只目标个股')

        if not codes:
            print('没有要处理的股票'); return

        # 3) 逐只处理
        PROGRESS_EVERY = 20 if len(codes) > 20 else 5
        t0 = time.time()
        ok = no_zip = fail = 0
        total_added_1m = total_added_15m = 0
        failed = []
        no_zip_list = []

        for i, code in enumerate(codes, 1):
            zip_path = zip_map.get(code)
            if not zip_path:
                no_zip += 1
                no_zip_list.append(code)
                continue
            try:
                df = _read_zip_csv(zip_path)
                if df is None or df.empty:
                    no_zip += 1; no_zip_list.append(code); continue

                # 按季度切分 → 每季度合并写 parquet
                quarters_written = []
                for (y, q), g in df.assign(_q=((df['month'] - 1) // 3 + 1)).groupby(['year', '_q']):
                    g = g.drop(columns=['_q'])
                    out = dq / str(int(y)) / f'Q{int(q)}' / f'{code}.parquet'
                    info = _write_quarter_parquet(g, out, args.dry_run)
                    quarters_written.append((y, q, info['added']))
                    total_added_1m += info['added']

                # 重生成 15m
                info_15m = None
                if not args.skip_15m and ResampleData and SignalMethod:
                    info_15m = _regen_15m(code, dq, d15, args.dry_run, ResampleData, SignalMethod)
                    if info_15m:
                        total_added_15m += info_15m['added_15m']

                ok += 1
                if i <= 5 or i % PROGRESS_EVERY == 0 or i == len(codes):
                    qstr = ', '.join(f'{y}Q{q}+{n}' for y, q, n in quarters_written if n > 0) or 'no new'
                    msg = f'  [{i:4d}/{len(codes)}] {code}  1m: {qstr}'
                    if info_15m:
                        msg += f'  15m: +{info_15m["added_15m"]} (now {info_15m["total_15m"]})'
                    print(msg)

                if i % PROGRESS_EVERY == 0 or i == len(codes):
                    rate = i / max(time.time() - t0, 0.001)
                    eta = (len(codes) - i) / rate
                    print(f'  --- progress {i}/{len(codes)}  ok={ok}  no_zip={no_zip}  '
                          f'fail={fail}  {rate:.1f}/s  eta={eta:.0f}s')
            except Exception as e:
                fail += 1; failed.append((code, str(e)))
                logger.warning(f'  [{i:4d}/{len(codes)}] {code}  [fail] {str(e)[:120]}')

        elapsed = time.time() - t0
        print(f'\ndone. ok={ok}  no_zip={no_zip}  fail={fail}  total={len(codes)}  elapsed={elapsed:.1f}s')
        print(f'  +{total_added_1m} 1m 分钟  +{total_added_15m} 15m 根')
        if no_zip_list[:5]:
            print(f'\nno zip ({len(no_zip_list)} total, first 5): {", ".join(no_zip_list[:5])}')
        if failed:
            print(f'\nfailed ({len(failed)} total, first 5):')
            for c, m in failed[:5]:
                print(f'  {c}: {m[:120]}')
        if args.dry_run:
            print('\n[dry-run] 没有写任何文件')


if __name__ == '__main__':
    main()
