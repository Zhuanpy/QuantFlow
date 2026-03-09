"""
将现有的1分钟和15分钟CSV文件迁移为Parquet格式

用法:
    python scripts/migrate_csv_to_parquet.py                # 迁移全部
    python scripts/migrate_csv_to_parquet.py --dry-run      # 预览不写入
    python scripts/migrate_csv_to_parquet.py --delete-csv   # 迁移后删除旧CSV
"""
import sys
import os
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


def migrate_directory(directory: Path, dry_run: bool, delete_csv: bool) -> tuple:
    """迁移一个目录下的所有CSV为Parquet"""
    converted = 0
    skipped = 0
    failed = 0

    if not directory.exists():
        return converted, skipped, failed

    for csv_file in sorted(directory.glob('*.csv')):
        parquet_file = csv_file.with_suffix('.parquet')

        # 如果parquet已存在，跳过
        if parquet_file.exists():
            skipped += 1
            continue

        if dry_run:
            size_mb = csv_file.stat().st_size / 1024 / 1024
            print(f"  [预览] {csv_file.name} -> .parquet  ({size_mb:.1f}MB)")
            converted += 1
            continue

        try:
            df = pd.read_csv(csv_file, encoding='utf-8-sig')
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
            df.to_parquet(parquet_file, index=False, engine='pyarrow')

            csv_size = csv_file.stat().st_size / 1024 / 1024
            parquet_size = parquet_file.stat().st_size / 1024 / 1024
            ratio = (1 - parquet_size / csv_size) * 100 if csv_size > 0 else 0
            print(f"  {csv_file.name} -> .parquet  "
                  f"({csv_size:.1f}MB -> {parquet_size:.1f}MB, 压缩{ratio:.0f}%)")

            if delete_csv:
                csv_file.unlink()

            converted += 1
        except Exception as e:
            print(f"  [失败] {csv_file.name}: {e}")
            failed += 1

    return converted, skipped, failed


def main():
    parser = argparse.ArgumentParser(description='CSV转Parquet迁移工具')
    parser.add_argument('--dry-run', action='store_true', help='预览模式')
    parser.add_argument('--delete-csv', action='store_true', help='迁移后删除旧CSV')
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    data_base = project_root / 'data' / 'data'

    total_converted = 0
    total_skipped = 0
    total_failed = 0

    # 1. 迁移季度目录下的1m数据
    quarters_dir = data_base / 'quarters'
    if quarters_dir.exists():
        print("=" * 60)
        print("迁移1分钟数据 (quarters/)")
        print("=" * 60)
        for year_dir in sorted(quarters_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            for quarter_dir in sorted(year_dir.iterdir()):
                if not quarter_dir.is_dir():
                    continue
                csv_count = len(list(quarter_dir.glob('*.csv')))
                if csv_count == 0:
                    continue
                print(f"\n{year_dir.name}/{quarter_dir.name}/ ({csv_count} 个CSV文件)")
                c, s, f = migrate_directory(quarter_dir, args.dry_run, args.delete_csv)
                total_converted += c
                total_skipped += s
                total_failed += f

    # 2. 迁移15m目录
    dir_15m = data_base / '15m'
    if dir_15m.exists():
        csv_count = len(list(dir_15m.glob('*.csv')))
        if csv_count > 0:
            print(f"\n{'=' * 60}")
            print(f"迁移15分钟数据 (15m/) ({csv_count} 个CSV文件)")
            print("=" * 60)
            c, s, f = migrate_directory(dir_15m, args.dry_run, args.delete_csv)
            total_converted += c
            total_skipped += s
            total_failed += f

    # 3. 迁移15m_standardized目录
    dir_15m_std = data_base / '15m_standardized'
    if dir_15m_std.exists():
        csv_count = len(list(dir_15m_std.glob('*.csv')))
        if csv_count > 0:
            print(f"\n{'=' * 60}")
            print(f"迁移15分钟标准化数据 (15m_standardized/) ({csv_count} 个CSV文件)")
            print("=" * 60)
            c, s, f = migrate_directory(dir_15m_std, args.dry_run, args.delete_csv)
            total_converted += c
            total_skipped += s
            total_failed += f

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"迁移完成！")
    print(f"  转换: {total_converted}  跳过: {total_skipped}  失败: {total_failed}")
    if args.dry_run:
        print(f"  (预览模式，未实际写入)")
    if args.delete_csv:
        print(f"  (已删除旧CSV文件)")
    print("=" * 60)


if __name__ == '__main__':
    main()
