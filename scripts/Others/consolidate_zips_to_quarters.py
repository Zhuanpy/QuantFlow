"""
把 E:\\User\\Documents\\stock 下的 1m 压缩包(<市场>.<代码>.zip)整合进新路径 data/quarters，
整合(数据已齐全)后删除该 zip，避免重复占用磁盘。

逻辑（对每个 zip）：
  1. 解析 zip 的 1m 数据；
  2. 按季度把「data/quarters 里缺的分钟」合并进对应 parquet（union 去重，无损）；
  3. 复查 zip 的每一根 1m 时间戳都已在 data/quarters；
  4. 全部覆盖 → 删除该 zip；未覆盖(异常) → 保留 zip 不删。

安全：
  · 只处理文件名形如 <市场>.<数字代码>.zip 的股票压缩包；PDF/PSD 等个人文件一律不碰。
  · 默认 dry-run 只统计（要补多少、能删多少），--commit 才写入+删除。
  · 只有确认数据已进 data/quarters 才删 zip。

用法:
  python scripts/Others/consolidate_zips_to_quarters.py            # dry-run
  python scripts/Others/consolidate_zips_to_quarters.py --commit   # 合并 + 删 zip
  python scripts/Others/consolidate_zips_to_quarters.py --zip-dir "E:/User/Documents/stock"
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from scripts.Others.import_minute_zips import _read_zip_csv, _write_quarter_parquet, _M1_COLS

DEFAULT_ZIP_DIR = r'E:\User\Documents\stock'


def _q(dt_series):
    return ((dt_series.dt.month - 1) // 3 + 1)


def _quarter_missing(part, out_path):
    """返回 part 中不在 out_path parquet 里的时间戳集合（out 不存在则全部缺）。"""
    want = set(part['date'])
    if not out_path.exists():
        return want
    have = set(pd.to_datetime(pd.read_parquet(out_path, columns=['date'])['date']))
    return want - have


def process_zip(zip_path: Path, dq: Path, commit: bool):
    code = zip_path.stem.split('.')[-1]
    if not code.isdigit():
        return ('skip_nonstock', 0)
    try:
        zdf = _read_zip_csv(zip_path)
    except Exception as e:
        return ('error:' + str(e)[:40], 0)
    if zdf is None or zdf.empty:
        return ('empty', 0)
    zdf['date'] = pd.to_datetime(zdf['date'])
    g = zdf.assign(_y=zdf['date'].dt.year, _q=_q(zdf['date']))

    added = 0
    for (y, q), part in g.groupby(['_y', '_q']):
        part = part.drop(columns=['_y', '_q'])
        out = dq / str(int(y)) / f'Q{int(q)}' / f'{code}.parquet'
        missing = _quarter_missing(part, out)
        if not missing:
            continue                       # 该季度已齐全，跳过
        added += len(missing)
        # 合并写入（dry-run 时 _write_quarter_parquet 不落盘）
        _write_quarter_parquet(part[_M1_COLS], out, dry_run=not commit)

    if not commit:
        return ('would_delete' if added == 0 else 'would_merge', added)

    # commit：复查 zip 全部时间戳是否已入库
    covered = True
    for (y, q), part in g.groupby(['_y', '_q']):
        out = dq / str(int(y)) / f'Q{int(q)}' / f'{code}.parquet'
        if _quarter_missing(part.drop(columns=['_y', '_q'], errors='ignore'), out):
            covered = False
            break
    if covered:
        zip_path.unlink()
        return ('deleted', added)
    return ('kept_uncovered', added)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--commit', action='store_true')
    ap.add_argument('--zip-dir', default=DEFAULT_ZIP_DIR)
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    dq = root / 'data' / 'quarters'
    zdir = Path(args.zip_dir)
    zips = sorted(zdir.glob('*.zip'))
    print(f'zip 目录: {zdir}   股票 zip 数(*.zip): {len(zips)}   模式: {"COMMIT" if args.commit else "dry-run"}')

    from collections import Counter
    cat = Counter()
    total_added = 0
    for i, zp in enumerate(zips, 1):
        status, added = process_zip(zp, dq, args.commit)
        key = status.split(':')[0]
        cat[key] += 1
        total_added += added
        if key.startswith('error') or status == 'kept_uncovered':
            print(f'  [{key}] {zp.name} (+{added})')
        if i % 300 == 0:
            print(f'  ...{i}/{len(zips)}  累计新增 {total_added} 根 1m  分类 {dict(cat)}', flush=True)

    print('\n===== 汇总 =====')
    print(dict(cat))
    print(f'累计合并进 data/quarters 的 1m 根数: {total_added}')
    if not args.commit:
        print('\n[dry-run] 未写入/未删除。加 --commit 执行。')
        print('  would_delete=已齐全(直接删)  would_merge=需补数据后删  skip_nonstock=非股票文件跳过')
    else:
        print('  deleted=已删  kept_uncovered=数据没进库故保留  已删的 zip 数据均已在 data/quarters')


if __name__ == '__main__':
    main()
