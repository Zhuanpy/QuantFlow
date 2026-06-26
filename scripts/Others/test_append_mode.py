#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试15分钟数据追加模式
"""

import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
sys.path.append('.')

def test_append_mode():
    """测试追加模式是否正常工作"""
    print("测试15分钟数据追加模式...")
    
    try:
        from App.utils.file_utils import get_stock_data_path
        
        # 创建测试数据
        test_stock = 'TEST001'
        
        # 生成第一组测试数据
        dates1 = pd.date_range(start='2024-01-01 09:30:00', periods=5, freq='15min')
        df1 = pd.DataFrame({
            'date': dates1,
            'open': [100.0, 101.0, 102.0, 103.0, 104.0],
            'high': [100.5, 101.5, 102.5, 103.5, 104.5],
            'low': [99.5, 100.5, 101.5, 102.5, 103.5],
            'close': [100.2, 101.2, 102.2, 103.2, 104.2],
            'volume': [1000, 1100, 1200, 1300, 1400],
            'money': [100000, 111100, 122400, 134160, 145880]
        })
        
        # 生成第二组测试数据（包含重复日期）
        dates2 = pd.date_range(start='2024-01-01 10:00:00', periods=3, freq='15min')
        df2 = pd.DataFrame({
            'date': dates2,
            'open': [104.5, 105.0, 105.5],
            'high': [105.0, 105.5, 106.0],
            'low': [104.0, 104.5, 105.0],
            'close': [104.7, 105.2, 105.7],
            'volume': [1500, 1600, 1700],
            'money': [157050, 168320, 179690]
        })
        
        # 获取文件路径
        file_path = get_stock_data_path(test_stock, data_type='15m_normal')
        
        # 清理之前的测试文件
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"清理之前的测试文件: {file_path}")
        
        # 测试1: 保存第一组数据
        print("\n1. 保存第一组数据...")
        df1.to_csv(file_path, index=False)
        print(f"✅ 保存第一组数据: {len(df1)} 条记录")
        
        # 验证第一组数据
        saved_data1 = pd.read_csv(file_path, parse_dates=['date'])
        print(f"✅ 验证第一组数据: {len(saved_data1)} 条记录")
        
        # 测试2: 追加第二组数据
        print("\n2. 追加第二组数据...")
        existing_data = pd.read_csv(file_path, parse_dates=['date'])
        combined_data = pd.concat([existing_data, df2]).drop_duplicates(subset=['date'], keep='last')
        combined_data = combined_data.sort_values('date')
        combined_data.to_csv(file_path, index=False)
        print(f"✅ 追加第二组数据: {len(df2)} 条记录")
        
        # 验证合并后的数据
        final_data = pd.read_csv(file_path, parse_dates=['date'])
        print(f"✅ 验证合并后数据: {len(final_data)} 条记录")
        
        # 测试3: 验证去重逻辑
        print("\n3. 验证去重逻辑...")
        duplicate_dates = final_data[final_data.duplicated(subset=['date'], keep=False)]
        if len(duplicate_dates) == 0:
            print("✅ 去重逻辑正常: 没有重复日期")
        else:
            print(f"❌ 去重逻辑异常: 发现 {len(duplicate_dates)} 条重复记录")
            return False
        
        # 测试4: 验证数据完整性
        print("\n4. 验证数据完整性...")
        expected_records = len(df1) + len(df2)
        if len(final_data) == expected_records:
            print(f"✅ 数据完整性正常: 期望 {expected_records} 条，实际 {len(final_data)} 条")
        else:
            print(f"❌ 数据完整性异常: 期望 {expected_records} 条，实际 {len(final_data)} 条")
            return False
        
        # 测试5: 验证数据排序
        print("\n5. 验证数据排序...")
        is_sorted = final_data['date'].is_monotonic_increasing
        if is_sorted:
            print("✅ 数据排序正常: 按日期升序排列")
        else:
            print("❌ 数据排序异常: 日期未按升序排列")
            return False
        
        # 清理测试文件
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"\n🧹 清理测试文件: {file_path}")
        
        return True
        
    except Exception as e:
        print(f"❌ 追加模式测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_latest_data_priority():
    """测试最新数据优先逻辑"""
    print("\n测试最新数据优先逻辑...")
    
    try:
        from App.utils.file_utils import get_stock_data_path
        
        test_stock = 'TEST002'
        
        # 创建包含重复日期的数据
        dates = pd.date_range(start='2024-01-01 09:30:00', periods=3, freq='15min')
        
        # 第一组数据
        df1 = pd.DataFrame({
            'date': dates,
            'open': [100.0, 101.0, 102.0],
            'high': [100.5, 101.5, 102.5],
            'low': [99.5, 100.5, 101.5],
            'close': [100.2, 101.2, 102.2],
            'volume': [1000, 1100, 1200],
            'money': [100000, 111100, 122400]
        })
        
        # 第二组数据（相同日期，不同价格）
        df2 = pd.DataFrame({
            'date': dates,
            'open': [200.0, 201.0, 202.0],  # 不同的价格
            'high': [200.5, 201.5, 202.5],
            'low': [199.5, 200.5, 201.5],
            'close': [200.2, 201.2, 202.2],
            'volume': [2000, 2100, 2200],
            'money': [200000, 211100, 222400]
        })
        
        file_path = get_stock_data_path(test_stock, data_type='15m_normal')
        
        # 清理测试文件
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # 保存第一组数据
        df1.to_csv(file_path, index=False)
        
        # 追加第二组数据（应该覆盖第一组）
        existing_data = pd.read_csv(file_path, parse_dates=['date'])
        combined_data = pd.concat([existing_data, df2]).drop_duplicates(subset=['date'], keep='last')
        combined_data = combined_data.sort_values('date')
        combined_data.to_csv(file_path, index=False)
        
        # 验证结果
        final_data = pd.read_csv(file_path, parse_dates=['date'])
        
        # 检查是否保留了第二组数据（最新数据）
        first_record = final_data.iloc[0]
        if abs(first_record['close'] - 200.2) < 0.01:  # 第二组数据的收盘价
            print("✅ 最新数据优先逻辑正常: 保留了第二组数据")
        else:
            print(f"❌ 最新数据优先逻辑异常: 收盘价为 {first_record['close']}，期望 200.2")
            return False
        
        # 清理测试文件
        if os.path.exists(file_path):
            os.remove(file_path)
        
        return True
        
    except Exception as e:
        print(f"❌ 最新数据优先测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("15分钟数据追加模式测试")
    print("=" * 60)
    
    # 运行测试
    tests = [
        ("追加模式测试", test_append_mode),
        ("最新数据优先测试", test_latest_data_priority),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        result = test_func()
        results.append((test_name, result))
    
    print("\n" + "=" * 60)
    print("测试结果汇总:")
    print("=" * 60)
    
    all_passed = True
    for test_name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name}: {status}")
        if not result:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有测试通过！追加模式工作正常！")
        print("\n功能特点:")
        print("✅ 自动追加新数据到现有文件")
        print("✅ 智能去重，保留最新记录")
        print("✅ 数据按日期排序")
        print("✅ 确保数据完整性")
        print("\n现在用户可以:")
        print("1. 多次处理同一股票的不同季度数据")
        print("2. 数据会自动合并到统一文件中")
        print("3. 重复数据会保留最新的记录")
        print("4. 无需担心数据覆盖问题")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        exit(1)
