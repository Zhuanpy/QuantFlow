#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试15分钟数据处理路由
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.append('.')

def test_routes():
    """测试路由是否正常注册"""
    print("测试路由注册...")
    
    try:
        from App import create_app
        
        # 创建应用实例
        app = create_app()
        print("✅ 应用创建成功")
        
        # 检查所有注册的路由
        print("\n📋 所有注册的路由:")
        with app.app_context():
            for rule in app.url_map.iter_rules():
                if 'process_data' in rule.rule:
                    print(f"  {rule.methods} {rule.rule}")
        
        # 检查15分钟数据处理相关路由
        print("\n🔍 15分钟数据处理相关路由:")
        process_routes = []
        with app.app_context():
            for rule in app.url_map.iter_rules():
                if 'process_data' in rule.rule or '15m' in rule.rule:
                    process_routes.append(f"  {rule.methods} {rule.rule}")
                    print(f"  {rule.methods} {rule.rule}")
        
        if not process_routes:
            print("❌ 没有找到15分钟数据处理相关路由")
            return False
        
        # 测试路由访问
        print("\n🧪 测试路由访问...")
        with app.test_client() as client:
            # 测试页面路由
            response = client.get('/process_data/15m_data')
            if response.status_code == 200:
                print("✅ 页面路由访问成功")
            else:
                print(f"❌ 页面路由访问失败: {response.status_code}")
                print(f"响应内容: {response.get_data(as_text=True)[:200]}...")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 路由测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_template():
    """测试模板文件是否存在"""
    print("\n测试模板文件...")
    
    template_path = "App/templates/data/process_15m_data.html"
    if os.path.exists(template_path):
        print(f"✅ 模板文件存在: {template_path}")
        return True
    else:
        print(f"❌ 模板文件不存在: {template_path}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("15分钟数据处理路由测试")
    print("=" * 60)
    
    # 运行测试
    template_success = test_template()
    route_success = test_routes()
    
    print("\n" + "=" * 60)
    if template_success and route_success:
        print("🎉 所有测试通过！路由正常工作！")
        print("\n访问地址:")
        print("http://localhost:5000/process_data/15m_data")
        print("\nAPI端点:")
        print("POST http://localhost:5000/process_data/api/process_15m_data")
        print("POST http://localhost:5000/process_data/api/check_15m_data")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        sys.exit(1)
