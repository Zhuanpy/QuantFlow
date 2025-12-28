#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试导入修复是否成功
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.append('.')

def test_imports():
    """测试导入是否正常"""
    print("测试导入修复...")
    
    try:
        # 测试基本导入
        from App.routes.data.process_15m_data_route import process_data_bp
        print("✅ 成功导入 process_data_bp")
        
        # 测试MySql模块导入
        from App.codes.MySql.sql_utils import Stocks
        print("✅ 成功导入 Stocks")
        
        from App.codes.MySql.LoadMysql import LoadBasicInform
        print("✅ 成功导入 LoadBasicInform")
        
        from App.codes.MySql.DB_MySql import MysqlAlchemy
        print("✅ 成功导入 MysqlAlchemy")
        
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
    print("导入修复测试")
    print("=" * 60)
    
    # 运行测试
    import_success = test_imports()
    app_success = test_app_creation()
    
    print("\n" + "=" * 60)
    if import_success and app_success:
        print("🎉 所有测试通过！导入问题已修复！")
        print("\n现在可以正常启动应用:")
        print("python run.py")
        print("\n然后访问:")
        print("http://localhost:5000/process_data/15m_data")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        sys.exit(1)
