# -*- coding: utf-8 -*-
"""临时脚本：分析1m数据存储结构"""
import os
from pathlib import Path
import pandas as pd

base = Path('data/data/quarters')

print('=== 目录结构分析 ===')
total_files = 0
total_size = 0

for item in sorted(base.iterdir()):
    if item.is_dir():
        csv_files = list(item.rglob('*.csv'))
        dir_size = sum(f.stat().st_size for f in csv_files)
        total_files += len(csv_files)
        total_size += dir_size
        print(f'{item.name}: {len(csv_files)} 个文件, {dir_size/1024/1024:.1f} MB')

print(f'\n总计: {total_files} 个文件, {total_size/1024/1024:.1f} MB')

# 分析单个文件
print('\n=== 单文件分析 (2025/Q4) ===')
q4_path = base / '2025' / 'Q4'
if q4_path.exists():
    files = list(q4_path.glob('*.csv'))
    sizes = [(f.name, f.stat().st_size) for f in files]
    sizes.sort(key=lambda x: x[1], reverse=True)
    print(f'总文件数: {len(files)}')
    print(f'最大文件: {sizes[0][0]} ({sizes[0][1]/1024:.1f} KB)')
    print(f'最小文件: {sizes[-1][0]} ({sizes[-1][1]/1024:.1f} KB)')
    avg_size = sum(s[1] for s in sizes) / len(sizes)
    print(f'平均大小: {avg_size/1024:.1f} KB')
    total_q4 = sum(s[1] for s in sizes)
    print(f'Q4总大小: {total_q4/1024/1024:.1f} MB')

# 分析CSV文件结构
print('\n=== CSV文件结构分析 ===')
sample_file = q4_path / '000938.csv'
if sample_file.exists():
    df = pd.read_csv(sample_file)
    print(f'列名: {list(df.columns)}')
    print(f'行数: {len(df)}')
    print(f'日期范围: {df["date"].min()} ~ {df["date"].max()}')

    # 计算每行大小
    file_size = sample_file.stat().st_size
    print(f'文件大小: {file_size/1024:.1f} KB')
    print(f'每行平均大小: {file_size/len(df):.1f} 字节')

# 估算全市场数据量
print('\n=== 全市场数据估算 ===')
print('假设: 5000只股票, 每只每季度约 15000 条1分钟数据')
estimated_rows_per_stock_year = 15000 * 4  # 4个季度
estimated_size_per_row = 80  # 字节
estimated_total_per_year = 5000 * estimated_rows_per_stock_year * estimated_size_per_row
print(f'每年预估数据量: {estimated_total_per_year/1024/1024/1024:.1f} GB')
