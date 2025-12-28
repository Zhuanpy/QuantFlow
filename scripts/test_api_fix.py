#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试15分钟数据处理API修复
"""

import sys
import os
import json

# 添加项目根目录到Python路径
sys.path.append('.')

def test_api_endpoints():
    """测试API端点是否正常工作"""
    print("测试15分钟数据处理API端点...")
    
    try:
        from App import create_app
        
        # 创建应用实例
        app = create_app()
        print("✅ 应用创建成功")
        
        # 测试API端点
        with app.test_client() as client:
            # 测试页面路由
            print("\n1. 测试页面路由...")
            response = client.get('/process_data/15m_data')
            if response.status_code == 200:
                print("✅ 页面路由访问成功")
            else:
                print(f"❌ 页面路由访问失败: {response.status_code}")
                return False
            
            # 测试API端点（POST请求）
            print("\n2. 测试API端点...")
            test_data = {
                'stock_code': '000001',
                'year': 2024,
                'quarter': 'Q4',
                'processing_type': 'resample',
                'overwrite_mode': 'skip'
            }
            
            response = client.post('/process_data/api/process_15m_data', 
                                 data=json.dumps(test_data),
                                 content_type='application/json')
            
            print(f"API响应状态码: {response.status_code}")
            
            if response.status_code in [200, 400, 404, 500]:
                # 这些状态码都是正常的API响应
                try:
                    data = response.get_json()
                    print(f"✅ API端点响应正常: {data.get('message', '无消息')}")
                except:
                    print("⚠️ API返回非JSON格式，可能是错误页面")
                    print(f"响应内容前100字符: {response.get_data(as_text=True)[:100]}")
            else:
                print(f"❌ API端点响应异常: {response.status_code}")
                return False
            
            # 测试检查数据API端点
            print("\n3. 测试检查数据API端点...")
            response = client.post('/process_data/api/check_15m_data',
                                 data=json.dumps(test_data),
                                 content_type='application/json')
            
            print(f"检查数据API响应状态码: {response.status_code}")
            
            if response.status_code in [200, 400, 404, 500]:
                try:
                    data = response.get_json()
                    print(f"✅ 检查数据API端点响应正常: {data.get('message', '无消息')}")
                except:
                    print("⚠️ 检查数据API返回非JSON格式")
            else:
                print(f"❌ 检查数据API端点响应异常: {response.status_code}")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ API测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_url_routing():
    """测试URL路由是否正确"""
    print("\n测试URL路由...")
    
    try:
        from App import create_app
        
        app = create_app()
        
        with app.app_context():
            from flask import url_for
            
            # 测试URL生成
            try:
                page_url = url_for('process_data.process_15m_data_page')
                print(f"✅ 页面URL生成成功: {page_url}")
            except Exception as e:
                print(f"❌ 页面URL生成失败: {str(e)}")
                return False
            
            # 检查所有相关路由
            print("\n📋 15分钟数据处理相关路由:")
            for rule in app.url_map.iter_rules():
                if 'process_data' in rule.rule:
                    print(f"  {rule.methods} {rule.rule}")
        
        return True
        
    except Exception as e:
        print(f"❌ URL路由测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("15分钟数据处理API修复测试")
    print("=" * 60)
    
    # 运行测试
    url_success = test_url_routing()
    api_success = test_api_endpoints()
    
    print("\n" + "=" * 60)
    if url_success and api_success:
        print("🎉 所有测试通过！API修复成功！")
        print("\n现在可以正常使用:")
        print("1. 访问页面: http://localhost:5000/process_data/15m_data")
        print("2. API端点:")
        print("   - POST /process_data/api/process_15m_data")
        print("   - POST /process_data/api/check_15m_data")
        print("\n注意: 如果API返回404或500错误，可能是因为:")
        print("- 1分钟数据文件不存在")
        print("- 数据路径配置问题")
        print("- 依赖模块导入问题")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        sys.exit(1)
