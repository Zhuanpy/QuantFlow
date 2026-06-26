#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试15分钟数据存储结构改进
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.append('.')

def test_new_storage_structure():
    """测试新的存储结构"""
    print("测试15分钟数据存储结构...")
    
    try:
        from App.utils.file_utils import get_stock_data_path
        
        # 测试不同的数据类型
        test_cases = [
            ('002475', '15m_normal', 'data/data/15m/002475.csv'),
            ('002475', '15m_standardized', 'data/data/15m_standardized/002475.csv'),
            ('000001', '15m_normal', 'data/data/15m/000001.csv'),
            ('000001', '15m_standardized', 'data/data/15m_standardized/000001.csv'),
        ]
        
        for stock_code, data_type, expected_path in test_cases:
            path = get_stock_data_path(stock_code, data_type)
            print(f"✅ {stock_code} {data_type}: {path}")
            
            # 验证路径是否正确
            if expected_path not in path:
                print(f"❌ 路径不符合预期: {path}")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 存储结构测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_backward_compatibility():
    """测试向后兼容性"""
    print("\n测试向后兼容性...")
    
    try:
        from App.utils.file_utils import get_stock_data_path
        
        # 测试旧的15m类型是否仍然工作
        path_old = get_stock_data_path('002475', '15m')
        path_new = get_stock_data_path('002475', '15m_normal')
        
        if path_old == path_new:
            print("✅ 向后兼容性正常: '15m' 等同于 '15m_normal'")
        else:
            print(f"❌ 向后兼容性失败: {path_old} != {path_new}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 向后兼容性测试失败: {str(e)}")
        return False

def test_directory_structure():
    """测试目录结构"""
    print("\n测试目录结构...")
    
    try:
        from App.utils.file_utils import get_stock_data_path
        
        # 测试目录创建
        test_path = get_stock_data_path('TEST001', '15m_normal', create=True)
        test_std_path = get_stock_data_path('TEST001', '15m_standardized', create=True)
        
        # 检查目录是否存在
        normal_dir = os.path.dirname(test_path)
        std_dir = os.path.dirname(test_std_path)
        
        if os.path.exists(normal_dir):
            print(f"✅ 15分钟原始数据目录存在: {normal_dir}")
        else:
            print(f"❌ 15分钟原始数据目录不存在: {normal_dir}")
            return False
        
        if os.path.exists(std_dir):
            print(f"✅ 15分钟标准化数据目录存在: {std_dir}")
        else:
            print(f"❌ 15分钟标准化数据目录不存在: {std_dir}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 目录结构测试失败: {str(e)}")
        return False

def test_data_types():
    """测试所有支持的数据类型"""
    print("\n测试支持的数据类型...")
    
    try:
        from App.utils.file_utils import get_stock_data_path
        
        # 测试所有支持的数据类型
        data_types = [
            '1m',           # 1分钟数据（按季度）
            '15m',          # 15分钟数据（兼容性）
            '15m_normal',   # 15分钟原始数据（统一文件）
            '15m_standardized', # 15分钟标准化数据（统一文件）
            'daily',        # 日线数据（按季度）
        ]
        
        for data_type in data_types:
            try:
                path = get_stock_data_path('TEST001', data_type)
                print(f"✅ {data_type}: {path}")
            except Exception as e:
                print(f"❌ {data_type}: {str(e)}")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 数据类型测试失败: {str(e)}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("15分钟数据存储结构改进测试")
    print("=" * 60)
    
    # 运行所有测试
    tests = [
        ("存储结构测试", test_new_storage_structure),
        ("向后兼容性测试", test_backward_compatibility),
        ("目录结构测试", test_directory_structure),
        ("数据类型测试", test_data_types),
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
        print("🎉 所有测试通过！15分钟数据存储结构改进成功！")
        print("\n新的存储结构:")
        print("📁 data/data/15m/股票代码.csv          - 15分钟原始数据（统一文件）")
        print("📁 data/data/15m_standardized/股票代码.csv - 15分钟标准化数据（统一文件）")
        print("\n优势:")
        print("✅ 每只股票一个15分钟文件，便于管理")
        print("✅ 区分原始数据和标准化数据")
        print("✅ 支持数据追加和覆盖模式")
        print("✅ 保持向后兼容性")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        exit(1)
