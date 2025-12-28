#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试数据处理按钮修复
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.append('.')

def test_homepage():
    """测试主页面的数据处理按钮"""
    print("测试主页面数据处理按钮...")
    
    try:
        from App import create_app
        
        # 创建应用实例
        app = create_app()
        print("✅ 应用创建成功")
        
        # 测试主页面访问
        with app.test_client() as client:
            response = client.get('/')
            if response.status_code == 200:
                print("✅ 主页面访问成功")
                
                # 检查页面内容是否包含正确的链接
                content = response.get_data(as_text=True)
                if 'process_data_bp.process_15m_data_page' in content:
                    print("✅ 15分钟数据处理按钮链接正确")
                else:
                    print("❌ 15分钟数据处理按钮链接缺失")
                    return False
                
                if 'dl_bp.dl' in content:
                    print("✅ 数据整理按钮链接正确")
                else:
                    print("❌ 数据整理按钮链接缺失")
                    return False
                
                return True
            else:
                print(f"❌ 主页面访问失败: {response.status_code}")
                return False
        
    except Exception as e:
        print(f"❌ 主页面测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_process_page():
    """测试15分钟数据处理页面"""
    print("\n测试15分钟数据处理页面...")
    
    try:
        from App import create_app
        
        # 创建应用实例
        app = create_app()
        
        # 测试15分钟数据处理页面访问
        with app.test_client() as client:
            response = client.get('/process_data/15m_data')
            if response.status_code == 200:
                print("✅ 15分钟数据处理页面访问成功")
                return True
            else:
                print(f"❌ 15分钟数据处理页面访问失败: {response.status_code}")
                print(f"响应内容: {response.get_data(as_text=True)[:200]}...")
                return False
        
    except Exception as e:
        print(f"❌ 15分钟数据处理页面测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("数据处理按钮修复测试")
    print("=" * 60)
    
    # 运行测试
    homepage_success = test_homepage()
    process_page_success = test_process_page()
    
    print("\n" + "=" * 60)
    if homepage_success and process_page_success:
        print("🎉 所有测试通过！数据处理按钮已修复！")
        print("\n现在可以正常使用:")
        print("1. 访问主页: http://localhost:5000/")
        print("2. 点击'数据处理' -> '15分钟数据处理'")
        print("3. 或者直接访问: http://localhost:5000/process_data/15m_data")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        sys.exit(1)
