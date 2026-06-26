#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证数据处理相关修复
"""

def test_template_variables():
    """测试模板变量是否正确定义"""
    print("检查success.html模板中的变量...")
    
    try:
        with open('App/templates/data/success.html', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查模板中使用的变量
        variables_to_check = [
            'daily_data_from_db',
            'file_path_1m',
            'file_path_15m', 
            'file_path_daily',
            'stock_code'
        ]
        
        missing_variables = []
        for var in variables_to_check:
            if f'{{{{ {var}' in content or f'{{% if {var}' in content:
                print(f"✅ 模板中使用变量: {var}")
            else:
                missing_variables.append(var)
        
        if missing_variables:
            print(f"⚠️ 模板中未使用的变量: {missing_variables}")
        
        return len(missing_variables) == 0
        
    except Exception as e:
        print(f"❌ 检查模板失败: {str(e)}")
        return False

def test_route_functions():
    """测试路由函数是否包含必要的变量传递"""
    print("\n检查路由函数...")
    
    try:
        with open('App/routes/data/download_data_route.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查是否包含必要的变量传递
        required_patterns = [
            'file_path_1m=',
            'file_path_15m=',
            'file_path_daily=',
            'daily_data_from_db=',
            'stock_code=stock_code'
        ]
        
        missing_patterns = []
        for pattern in required_patterns:
            if pattern in content:
                print(f"✅ 路由函数包含: {pattern}")
            else:
                missing_patterns.append(pattern)
        
        if missing_patterns:
            print(f"❌ 路由函数缺少: {missing_patterns}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 检查路由函数失败: {str(e)}")
        return False

def test_api_urls():
    """测试API URL是否正确"""
    print("\n检查API URL...")
    
    try:
        with open('App/templates/data/process_15m_data.html', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查API URL
        correct_urls = [
            '/process_data/api/process_15m_data',
            '/process_data/api/check_15m_data'
        ]
        
        wrong_urls = [
            '/api/process_15m_data',
            '/api/check_15m_data'
        ]
        
        for url in correct_urls:
            if url in content:
                print(f"✅ 正确的API URL: {url}")
            else:
                print(f"❌ 缺少正确的API URL: {url}")
        
        for url in wrong_urls:
            if url in content:
                print(f"❌ 发现错误的API URL: {url}")
                return False
            else:
                print(f"✅ 没有错误的API URL: {url}")
        
        return True
        
    except Exception as e:
        print(f"❌ 检查API URL失败: {str(e)}")
        return False

def test_homepage_links():
    """测试主页面的链接"""
    print("\n检查主页面链接...")
    
    try:
        with open('App/templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查链接
        if 'process_data.process_15m_data_page' in content:
            print("✅ 主页面包含15分钟数据处理链接")
        else:
            print("❌ 主页面缺少15分钟数据处理链接")
            return False
        
        if 'button disabled' not in content or content.count('button disabled') < 2:
            print("✅ 主页面数据处理按钮已启用")
        else:
            print("❌ 主页面仍有禁用的数据处理按钮")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 检查主页面失败: {str(e)}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("数据处理相关修复验证")
    print("=" * 60)
    
    # 运行所有测试
    tests = [
        ("模板变量检查", test_template_variables),
        ("路由函数检查", test_route_functions),
        ("API URL检查", test_api_urls),
        ("主页面链接检查", test_homepage_links)
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
        print("🎉 所有测试通过！修复成功！")
        print("\n现在可以正常使用:")
        print("1. 主页面 -> 数据处理 -> 15分钟数据处理")
        print("2. 数据下载页面 -> 15分钟数据处理")
        print("3. 股票数据下载功能")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        exit(1)
