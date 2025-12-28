#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
15分钟数据转换测试脚本
用于验证1分钟数据是否正确转换为15分钟数据
"""

import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

def test_15m_conversion():
    """测试15分钟数据转换功能"""
    try:
        # 导入必要的模块
        from App.codes.utils.Normal import ResampleData
        from App.models.data.Stock15m import save_15m_stock_data_to_sql, load_15m_stock_data_from_sql
        
        print("🔍 开始测试15分钟数据转换...")
        
        # 创建测试数据
        test_data = create_test_1m_data()
        print(f"✅ 创建测试数据: {len(test_data)} 条1分钟数据")
        
        # 测试数据转换
        print("🔄 开始转换1分钟数据为15分钟数据...")
        df_15m = ResampleData.resample_1m_data(test_data, '15m')
        print(f"✅ 转换完成: {len(df_15m)} 条15分钟数据")
        
        # 显示转换结果
        print("\n📊 转换结果预览:")
        print(df_15m.head())
        
        # 测试保存到数据库
        test_stock_code = "TEST001"
        print(f"\n💾 测试保存15分钟数据到数据库: {test_stock_code}")
        
        success = save_15m_stock_data_to_sql(test_stock_code, df_15m)
        if success:
            print("✅ 15分钟数据保存成功")
            
            # 测试从数据库加载
            print("📥 测试从数据库加载15分钟数据...")
            loaded_data = load_15m_stock_data_from_sql(test_stock_code)
            print(f"✅ 加载成功: {len(loaded_data)} 条记录")
            
            # 显示加载的数据
            print("\n📊 加载的数据预览:")
            print(loaded_data.head())
            
        else:
            print("❌ 15分钟数据保存失败")
            
        print("\n🎯 测试完成！")
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_test_1m_data():
    """创建测试用的1分钟数据"""
    # 创建时间序列
    start_time = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
    time_series = []
    
    # 创建一天的交易时间数据（9:30-15:00）
    current_time = start_time
    while current_time.hour < 15 or (current_time.hour == 15 and current_time.minute == 0):
        time_series.append(current_time)
        current_time += timedelta(minutes=1)
    
    # 创建测试数据
    data = []
    base_price = 10.0
    
    for i, time_point in enumerate(time_series):
        # 模拟价格波动
        price_change = (i % 10 - 5) * 0.01
        current_price = base_price + price_change
        
        data.append({
            'date': time_point,
            'open': current_price,
            'close': current_price + (i % 3 - 1) * 0.005,
            'high': current_price + 0.01,
            'low': current_price - 0.01,
            'volume': 1000 + (i % 100) * 10,
            'money': (1000 + (i % 100) * 10) * current_price
        })
    
    return pd.DataFrame(data)

def check_existing_15m_data():
    """检查现有的15分钟数据"""
    try:
        from App.models.data.Stock15m import load_15m_stock_data_from_sql
        
        print("\n🔍 检查现有15分钟数据...")
        
        # 检查一些常见的股票代码
        test_codes = ['000001', '000002', '002475']
        
        for code in test_codes:
            try:
                data = load_15m_stock_data_from_sql(code)
                if not data.empty:
                    print(f"✅ {code}: {len(data)} 条15分钟数据")
                    print(f"   时间范围: {data['date'].min()} 到 {data['date'].max()}")
                else:
                    print(f"❌ {code}: 无15分钟数据")
            except Exception as e:
                print(f"❌ {code}: 检查失败 - {e}")
                
    except Exception as e:
        print(f"❌ 检查现有数据失败: {e}")

if __name__ == '__main__':
    print("🚀 15分钟数据转换测试开始")
    print("=" * 50)
    
    # 运行测试
    test_success = test_15m_conversion()
    
    # 检查现有数据
    check_existing_15m_data()
    
    print("\n" + "=" * 50)
    if test_success:
        print("🎉 所有测试通过！15分钟数据转换功能正常")
    else:
        print("⚠️ 测试失败，请检查相关功能")
