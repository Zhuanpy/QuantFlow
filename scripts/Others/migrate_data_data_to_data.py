"""
路径统一：把旧路径 data/data/* 的物理数据合并进新路径 data/*。

规则：
  data/data/years/*    → data/years/*      （新路径空，全部搬）
  data/data/quarters/* → data/quarters/*   （只搬新路径不存在的文件，不覆盖 live 数据）
其它 data/data 下子目录（若有）同理：新路径缺则搬，已存在则跳过（保留新路径为准）。

安全：默认 dry-run 只统计；--commit 才移动；移动用 shutil.move（同盘即改名，快）。
移动后旧目录留空壳，验证无误后可手动删 data/data。

用法： python scripts/Others/migrate_data_data_to_data.py [--commit]
"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(commit: bool):
    root = Path(__file__).resolve().parents[2]   # 仓库根，避免导入 config 的循环依赖
    old_base = root / 'data' / 'data'
    new_base = root / 'data'
    if not old_base.is_dir():
        print('无 data/data 目录，无需迁移。')
        return

    to_move = []      # (src, dst)
    conflict = []     # 新路径已存在(跳过)
    for src in old_base.rglob('*'):
        if src.is_dir():
            continue
        rel = src.relative_to(old_base)          # 如 years/2024/xxx.parquet
        dst = new_base / rel                      # data/years/2024/xxx.parquet
        if dst.exists():
            conflict.append(rel)
        else:
            to_move.append((src, dst))

    # 按顶层子目录统计
    from collections import Counter
    mv_by = Counter(str(Path(r[0]).relative_to(old_base)).split('\\')[0].split('/')[0] for r in to_move)
    cf_by = Counter(str(r).split('\\')[0].split('/')[0] for r in conflict)
    print(f'旧 data/data 下待处理文件：搬移 {len(to_move)}  跳过(新路径已存在) {len(conflict)}')
    print('  待搬移 按子目录:', dict(mv_by))
    print('  跳过   按子目录:', dict(cf_by))
    print('  样例待搬移:', [str(s.relative_to(old_base)) for s, _ in to_move[:5]])

    if not commit:
        print('\n[dry-run] 未移动任何文件。加 --commit 执行。')
        return

    moved = 0
    for src, dst in to_move:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1
        if moved % 1000 == 0:
            print(f'   已搬 {moved}/{len(to_move)}')
    print(f'[OK] 已搬移 {moved} 个文件到 data/。')
    print('旧 data/data 现在应只剩空目录，验证无误后可删除。')


if __name__ == '__main__':
    main(commit=('--commit' in sys.argv))
