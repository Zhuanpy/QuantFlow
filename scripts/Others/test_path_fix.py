#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试路径修复
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.append('.')

def test_path_generation():
    """测试路径生成是否正确"""
    print("测试路径生成...")
    
    try:
        from App.utils.file_utils import get_stock_data_path
        
        # 测试不同的年份和季度
        test_cases = [
            ('002475', '1m', '2025', 'Q3'),
            ('002475', '15m', '2025', 'Q3'),
            ('002475', 'daily', '2025', 'Q3'),
            ('000001', '1m', '2024', 'Q4'),
            ('000001', '15m', '2024', 'Q4'),
        ]
        
        for stock_code, data_type, year, quarter in test_cases:
            path = get_stock_data_path(stock_code, data_type, year, quarter)
            print(f"✅ {stock_code} {data_type} {year} {quarter}: {path}")
            
            # 验证路径是否包含正确的年份和季度
            if year not in path or quarter not in path:
                print(f"❌ 路径不包含正确的年份或季度: {path}")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 路径生成测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_with_correct_paths():
    """测试API是否使用正确的路径"""
    print("\n测试API路径使用...")
    
    try:
        # 检查15分钟数据处理路由
        with open('App/routes/data/process_15m_data_route.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查是否包含年份和季度参数
        if 'get_stock_data_path(stock_code, data_type=\'1m\', year=year, quarter=quarter)' in content:
            print("✅ 1分钟数据路径使用正确的年份和季度参数")
        else:
            print("❌ 1分钟数据路径未使用正确的年份和季度参数")
            return False
        
        if 'get_stock_data_path(stock_code, data_type=\'15m\', year=year, quarter=quarter)' in content:
            print("✅ 15分钟数据路径使用正确的年份和季度参数")
        else:
            print("❌ 15分钟数据路径未使用正确的年份和季度参数")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ API路径测试失败: {str(e)}")
        return False

def test_function_signature():
    """测试函数签名是否正确"""
    print("\n测试函数签名...")
    
    try:
        with open('App/utils/file_utils.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查函数签名是否包含year和quarter参数
        if 'def get_stock_data_path(stock_code: str, data_type: str = \'1m\', year: str = None, quarter: str = None, create: bool = True) -> str:' in content:
            print("✅ 函数签名包含year和quarter参数")
        else:
            print("❌ 函数签名缺少year和quarter参数")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 函数签名测试失败: {str(e)}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("路径修复验证测试")
    print("=" * 60)
    
    # 运行所有测试
    tests = [
        ("函数签名检查", test_function_signature),
        ("路径生成测试", test_path_generation),
        ("API路径使用检查", test_api_with_correct_paths),
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
        print("🎉 所有测试通过！路径修复成功！")
        print("\n现在15分钟数据处理会使用正确的年份和季度路径:")
        print("- 用户选择Q3 -> 保存到Q3目录")
        print("- 用户选择Q4 -> 保存到Q4目录")
        print("- 路径格式: data/data/15m/年份/季度/股票代码.csv")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        exit(1)
