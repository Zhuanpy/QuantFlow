#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化的15分钟数据处理功能测试
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.append('.')

def test_imports():
    """测试导入是否正常"""
    print("测试导入...")
    
    try:
        # 测试基本导入
        from App.routes.data.process_15m_data_route import process_data_bp
        print("✅ 成功导入 process_data_bp")
        
        # 测试函数导入
        from App.routes.data.process_15m_data_route import clean_and_standardize_data, load_extreme_values_cache
        print("✅ 成功导入标准化函数")
        
        # 测试其他必要模块
        from App.utils.file_utils import get_stock_data_path
        print("✅ 成功导入文件工具")
        
        from App.codes.utils.Normal import ResampleData
        print("✅ 成功导入重采样工具")
        
        return True
        
    except Exception as e:
        print(f"❌ 导入失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_basic_functions():
    """测试基本函数"""
    print("\n测试基本函数...")
    
    try:
        from App.routes.data.process_15m_data_route import clean_and_standardize_data, load_extreme_values_cache
        
        # 测试缓存加载
        cache = load_extreme_values_cache()
        print(f"✅ 缓存加载成功: {type(cache)}")
        
        # 测试路径生成
        from App.utils.file_utils import get_stock_data_path
        test_path = get_stock_data_path("002475", data_type='15m')
        print(f"✅ 路径生成成功: {test_path}")
        
        return True
        
    except Exception as e:
        print(f"❌ 函数测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_app_creation():
    """测试应用创建"""
    print("\n测试应用创建...")
    
    try:
        from App import create_app
        
        # 创建应用实例
        app = create_app()
        print("✅ 应用创建成功")
        
        # 检查蓝图注册
        blueprint_names = [bp.name for bp in app.blueprints.values()]
        print(f"✅ 已注册蓝图: {blueprint_names}")
        
        if 'process_data' in blueprint_names:
            print("✅ process_data 蓝图已注册")
        else:
            print("❌ process_data 蓝图未注册")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 应用创建失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("15分钟数据处理功能 - 简化测试")
    print("=" * 60)
    
    # 运行测试
    tests = [
        ("导入测试", test_imports),
        ("函数测试", test_basic_functions),
        ("应用创建测试", test_app_creation)
    ]
    
    all_passed = True
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        if not test_func():
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有测试通过！15分钟数据处理功能已就绪！")
        print("\n使用说明:")
        print("1. 启动Flask应用: python run.py")
        print("2. 访问: http://localhost:5000/process_data/15m_data")
        print("3. 输入股票代码、年份、季度")
        print("4. 选择处理类型并开始处理")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        sys.exit(1)
