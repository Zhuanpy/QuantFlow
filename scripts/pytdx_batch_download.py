#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pytdx 分批下载更多历史数据
一次最多800根，通过多次调用获取更长时间范围
"""

from pytdx.hq import TdxHq_API
import pandas as pd
from datetime import datetime, timedelta

# 已验证可用的服务器
servers = [
    ('60.191.117.167', 7709),
    ('115.238.56.198', 7709),
    ('115.238.90.165', 7709),
]


def get_market(stock_code):
    """判断市场"""
    if stock_code.startswith(('6', '5', '9')):
        return 1  # 上海
    else:
        return 0  # 深圳


def get_1m_data_batch(stock_code, total_bars=2000):
    """
    分批获取更多1分钟数据
    
    Parameters:
    stock_code: 股票代码
    total_bars: 总共需要的K线数量（会分批获取）
    
    Returns:
    DataFrame: 合并后的数据
    """
    market = get_market(stock_code)
    api = TdxHq_API()
    
    # 连接服务器
    connected = False
    for server_ip, server_port in servers:
        try:
            if api.connect(server_ip, server_port, time_out=10):
                print(f"✓ 连接成功: {server_ip}:{server_port}")
                connected = True
                break
        except:
            continue
    
    if not connected:
        print("❌ 无法连接到任何服务器")
        return pd.DataFrame()
    
    try:
        all_data = []
        batch_size = 800  # 每次最多800根
        batches_needed = (total_bars + batch_size - 1) // batch_size
        
        print(f"计划获取 {total_bars} 根K线，需要 {batches_needed} 批次")
        print()
        
        for i in range(batches_needed):
            start_pos = i * batch_size
            count = min(batch_size, total_bars - start_pos)
            
            print(f"批次 {i+1}/{batches_needed}: 从位置 {start_pos} 获取 {count} 根K线...")
            
            data = api.get_security_bars(
                8,  # 1分钟线
                market,
                stock_code,
                start_pos,  # 起始位置
                count
            )
            
            if data:
                df_batch = pd.DataFrame(data)
                all_data.append(df_batch)
                print(f"  ✓ 成功获取 {len(data)} 条")
            else:
                print(f"  ✗ 第 {i+1} 批次返回空数据")
                break
        
        if not all_data:
            print("❌ 未获取到任何数据")
            return pd.DataFrame()
        
        # 合并所有批次
        df = pd.concat(all_data, ignore_index=True)
        
        # 数据处理
        df = df.rename(columns={
            'datetime': 'date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'vol': 'volume',
            'amount': 'money'
        })
        
        df['date'] = pd.to_datetime(df['date'])
        
        if 'money' not in df.columns:
            df['money'] = 0
        
        # 去重并排序
        df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
        
        print()
        print(f"✅ 总共获取 {len(df)} 条数据")
        print(f"时间范围: {df['date'].min()} 至 {df['date'].max()}")
        
        return df
        
    except Exception as e:
        print(f"❌ 获取数据异常: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()
    finally:
        api.disconnect()


def get_1m_data_by_days(stock_code, days=10):
    """
    按天数获取数据（自动计算需要的K线数量）
    
    Parameters:
    stock_code: 股票代码
    days: 需要的天数
    
    Returns:
    DataFrame: 数据
    """
    # 每天约240根K线，但考虑到非交易日，多获取一些
    bars_needed = days * 300  # 预留余量
    
    print(f"获取 {stock_code} 最近 {days} 个交易日的数据")
    print(f"预计需要 {bars_needed} 根K线")
    print("=" * 80)
    print()
    
    df = get_1m_data_batch(stock_code, total_bars=bars_needed)
    
    if not df.empty:
        # 筛选出最近N天
        cutoff_date = datetime.now() - timedelta(days=days)
        df = df[df['date'] >= cutoff_date]
        print(f"筛选后剩余 {len(df)} 条数据")
    
    return df


# 测试代码
if __name__ == "__main__":
    print("=" * 80)
    print("pytdx 分批下载测试")
    print("=" * 80)
    print()
    
    # 测试1: 获取固定数量的K线
    print("测试 1: 获取 1500 根K线（需要2批次）")
    print("=" * 80)
    df1 = get_1m_data_batch('000001', total_bars=1500)
    if not df1.empty:
        print(f"\n数据预览（前5条）:")
        print(df1.head())
        print(f"\n数据预览（后5条）:")
        print(df1.tail())
    
    print()
    print()
    
    # 测试2: 按天数获取
    print("测试 2: 获取最近 10 个交易日的数据")
    print("=" * 80)
    df2 = get_1m_data_by_days('600519', days=10)
    if not df2.empty:
        print(f"\n数据统计:")
        print(f"  总记录数: {len(df2)}")
        print(f"  时间跨度: {(df2['date'].max() - df2['date'].min()).days} 天")
        print(f"\n最近5条数据:")
        print(df2.tail())
    
    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)
    print()
    print("💡 说明:")
    print("  - pytdx 单次最多获取 800 根K线")
    print("  - 通过改变 start_pos 参数可以获取更早的数据")
    print("  - 可以循环调用获取任意长度的历史数据")
    print("  - 建议每次获取后添加小延迟，避免请求过快")


