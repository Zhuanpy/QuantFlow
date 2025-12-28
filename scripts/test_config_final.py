#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试配置是否正确工作
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.append('.')

def test_config():
    """测试配置是否正确"""
    print("测试配置...")
    
    try:
        # 测试config.py中的配置
        from config import Config
        columns = Config.STOCK_COLUMNS
        print("✅ 成功从config.py读取配置")
        
        # 检查必要的配置项
        required_sections = ['Basic', 'Macd', 'Boll', 'Signal', 'cycle', 'Recycle', 'Signal30m', 'Signal120m', 'SignalDaily']
        for section in required_sections:
            if section in columns:
                print(f"✅ {section} 配置存在")
            else:
                print(f"❌ {section} 配置缺失")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 配置测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_parser_utils():
    """测试parser_utils是否正确工作"""
    print("\n测试parser_utils...")
    
    try:
        from App.codes.parsers.parser_utils import read_columns
        columns = read_columns()
        print("✅ 成功从parser_utils读取配置")
        
        # 检查配置内容
        if 'Basic' in columns and '1' in columns['Basic']:
            print(f"✅ Basic配置正确: {columns['Basic']['1']}")
        else:
            print("❌ Basic配置不正确")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ parser_utils测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_parsers():
    """测试所有parser模块"""
    print("\n测试parser模块...")
    
    try:
        # 测试MacdParser
        from App.codes.parsers.MacdParser import Signal, SignalTimes, EmaShort, EmaMid
        print("✅ MacdParser导入成功")
        
        # 测试BollingerParser
        from App.codes.parsers.BollingerParser import BollMid, BollStd, BollUp, BollDn
        print("✅ BollingerParser导入成功")
        
        # 测试RnnParser
        from App.codes.parsers.RnnParser import ModelName
        print("✅ RnnParser导入成功")
        
        return True
        
    except Exception as e:
        print(f"❌ parser模块测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_signals():
    """测试Signals模块"""
    print("\n测试Signals模块...")
    
    try:
        # 测试StatisticsMacd
        from App.codes.Signals.StatisticsMacd import SignalMethod
        print("✅ StatisticsMacd导入成功")
        
        # 测试MacdSignal
        from App.codes.Signals.MacdSignal import calculate_MACD
        print("✅ MacdSignal导入成功")
        
        # 测试BollingerSignal
        from App.codes.Signals.BollingerSignal import Bollinger
        print("✅ BollingerSignal导入成功")
        
        return True
        
    except Exception as e:
        print(f"❌ Signals模块测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_app():
    """测试应用创建"""
    print("\n测试应用创建...")
    
    try:
        from App import create_app
        app = create_app()
        print("✅ 应用创建成功")
        
        # 检查蓝图注册
        blueprint_names = [bp.name for bp in app.blueprints.values()]
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
    print("配置和模块测试")
    print("=" * 60)
    
    # 运行所有测试
    tests = [
        ("配置测试", test_config),
        ("parser_utils测试", test_parser_utils),
        ("parser模块测试", test_parsers),
        ("Signals模块测试", test_signals),
        ("应用创建测试", test_app)
    ]
    
    all_passed = True
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        if not test_func():
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有测试通过！配置和模块都正常工作！")
        print("\n现在可以正常启动应用:")
        print("python run.py")
        print("\n然后访问:")
        print("http://localhost:5000/process_data/15m_data")
    else:
        print("❌ 部分测试失败，请检查错误信息")
        sys.exit(1)
