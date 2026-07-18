"""
统一数据路径收尾：对比旧路径 data/data 与新路径 data/，
  · 新路径已齐全的文件 → 无需处理；
  · 新路径缺数据的文件 → 把旧路径的数据合并进新路径（union 去重，无损）；
确认旧路径每个文件的数据都已在新路径后 → 删除整个 data/data。

对比是「分钟级」（完整时间戳），比天级更严谨：即使某天新路径已有、但少了几根，也会补齐。
只往新 parquet 合并（parquet 为权威格式）；旧的 csv 也统一并入对应的新 parquet。

用法:
  python scripts/Others/consolidate_old_data.py            # dry-run：只报告，不改动
  python scripts/Others/consolidate_old_data.py --commit   # 合并缺失 + 删除 data/data
"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd


def _read(p):
    return pd.read_parquet(p) if p.suffix == '.parquet' else pd.read_csv(p)


def _dts(p):
    """该文件的全部 1m 时间戳集合（分钟级）。"""
    df = pd.read_parquet(p, columns=['date']) if p.suffix == '.parquet' \
        else pd.read_csv(p, usecols=['date'])
    return set(pd.to_datetime(df['date']))


def main(commit: bool):
    root = Path(__file__).resolve().parents[2]
    old_base = root / 'data' / 'data'
    new_base = root / 'data'
    if not old_base.is_dir():
        print('无 data/data 目录，无需处理。')
        return

    files = [p for p in old_base.rglob('*') if p.is_file()]
    print(f'旧路径 data/data 文件数: {len(files)}')

    covered, to_merge, errors = 0, [], []
    for p in files:
        rel = p.relative_to(old_base)
        npq = new_base / rel.with_suffix('.parquet')   # 一律与新 parquet 比/并
        try:
            if not npq.exists():
                to_merge.append((p, npq, 'NEW'))        # 新路径完全没有 → 整份搬
                continue
            missing = _dts(p) - _dts(npq)
            if missing:
                to_merge.append((p, npq, len(missing)))
            else:
                covered += 1
        except Exception as e:
            errors.append((str(rel), str(e)[:40]))

    print(f'新路径已齐全: {covered}   需从旧路径补: {len(to_merge)}   读取出错: {len(errors)}')
    for p, npq, tag in to_merge[:25]:
        rel = p.relative_to(old_base)
        print(f'  待合并 {rel}: ' + ('新路径无, 整份复制' if tag == 'NEW' else f'缺 {tag} 根 1m'))
    for rel, e in errors[:10]:
        print(f'  出错 {rel}: {e}')

    if not commit:
        print('\n[dry-run] 未改动任何文件。加 --commit 执行合并 + 删除。')
        return

    if errors:
        print('\n⚠ 有文件读取失败，为安全起见先不删除 data/data，请人工检查上面「出错」项。')
        return

    # 1) 合并缺失数据到新路径
    for p, npq, _ in to_merge:
        odf = _read(p)
        if npq.exists():
            merged = pd.concat([_read(npq), odf], ignore_index=True)
        else:
            npq.parent.mkdir(parents=True, exist_ok=True)
            merged = odf
        merged['date'] = pd.to_datetime(merged['date'])
        merged = merged.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
        merged.to_parquet(npq, index=False)
    if to_merge:
        print(f'已把 {len(to_merge)} 个文件的缺失数据合并进新路径。')

    # 2) 复查：旧路径每个文件的时间戳是否都已在新 parquet
    still = []
    for p in files:
        rel = p.relative_to(old_base)
        npq = new_base / rel.with_suffix('.parquet')
        try:
            if (not npq.exists()) or (_dts(p) - _dts(npq)):
                still.append(str(rel))
        except Exception as e:
            still.append(f'{rel} (读取失败:{e})')
    if still:
        print(f'⚠ 复查仍有 {len(still)} 个文件未被新路径覆盖，未删除 data/data：')
        for s in still[:10]:
            print('   ', s)
        return

    # 3) 全部覆盖 → 删除旧路径
    shutil.rmtree(old_base)
    print('[OK] 新路径已完整覆盖旧路径，data/data 已删除。')


if __name__ == '__main__':
    main(commit=('--commit' in sys.argv))
