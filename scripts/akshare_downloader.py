#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用 akshare 获取股票数据
更稳定、更可靠的替代方案
"""

import pandas as pd
from datetime import datetime, timedelta

def get_stock_1m_data_akshare(stock_code, days=5):
    """
    使用 akshare 获取股票1分钟数据
    
    Parameters:
    stock_code: 股票代码，如 '000001' 或 '600000'
    days: 获取天数（akshare支持最近几个交易日）
    
    Returns:
    DataFrame: 包含 date, open, close, high, low, volume, money 列
    """
    try:
        import akshare as ak
        
        print(f"正在获取 {stock_code} 的1分钟数据...")
        
        # akshare 获取分钟数据
        # period 参数: "1", "5", "15", "30", "60"
        df = ak.stock_zh_a_hist_min_em(
            symbol=stock_code, 
            period="1",  # 1分钟
            adjust=""    # 不复权
        )
        
        if df.empty:
            print(f"✗ 未获取到数据")
            return pd.DataFrame()
        
        # 重命名列以匹配东方财富格式
        df = df.rename(columns={
            '时间': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'money'
        })
        
        # 转换日期格式
        df['date'] = pd.to_datetime(df['date'])
        
        # 只保留需要的列
        columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
        df = df[columns]
        
        # 按时间排序
        df = df.sort_values('date').reset_index(drop=True)
        
        # 只保留最近N天的数据
        if days < 100:  # 如果指定了天数限制
            cutoff_date = datetime.now() - timedelta(days=days)
            df = df[df['date'] >= cutoff_date]
        
        print(f"✓ 成功获取 {len(df)} 条数据")
        return df
        
    except ImportError:
        print("❌ akshare 未安装")
        print("请安装: pip install akshare")
        return pd.DataFrame()
    except Exception as e:
        print(f"❌ 获取数据失败: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()


def get_stock_daily_data_akshare(stock_code, days=365):
    """
    使用 akshare 获取股票日线数据
    
    Parameters:
    stock_code: 股票代码
    days: 获取天数
    
    Returns:
    DataFrame: 日线数据
    """
    try:
        import akshare as ak
        
        print(f"正在获取 {stock_code} 的日线数据...")
        
        # 计算日期范围
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        
        # 获取日线数据
        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=""
        )
        
        if df.empty:
            print(f"✗ 未获取到数据")
            return pd.DataFrame()
        
        # 重命名列
        df = df.rename(columns={
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'money'
        })
        
        df['date'] = pd.to_datetime(df['date'])
        
        print(f"✓ 成功获取 {len(df)} 条数据")
        return df
        
    except Exception as e:
        print(f"❌ 获取数据失败: {e}")
        return pd.DataFrame()


# 测试代码
if __name__ == "__main__":
    print("=" * 80)
    print("akshare 股票数据获取测试")
    print("=" * 80)
    print()
    
    # 测试1: 获取1分钟数据
    print("测试 1: 获取平安银行(000001)的1分钟数据")
    print("-" * 80)
    df_1m = get_stock_1m_data_akshare('000001', days=1)
    if not df_1m.empty:
        print(f"数据列: {df_1m.columns.tolist()}")
        print(f"数据预览:")
        print(df_1m.head())
        print(f"最新数据时间: {df_1m['date'].max()}")
    
    print()
    print("=" * 80)
    
    # 测试2: 获取日线数据
    print("测试 2: 获取贵州茅台(600519)的日线数据")
    print("-" * 80)
    df_daily = get_stock_daily_data_akshare('600519', days=30)
    if not df_daily.empty:
        print(f"数据预览:")
        print(df_daily.head())
    
    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)
    print()
    print("💡 akshare 优势:")
    print("  ✅ 无需连接特定服务器")
    print("  ✅ 数据来源可靠（东方财富）")
    print("  ✅ 接口简单易用")
    print("  ✅ 更新及时")


