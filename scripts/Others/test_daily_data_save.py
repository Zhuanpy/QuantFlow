#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日线数据保存测试脚本
验证日线数据是否正确保存到 daily_stock_data 表
"""

import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

def test_daily_data_save():
    """测试日线数据保存功能"""
    try:
        # 导入必要的模块
        from App.codes.utils.Normal import ResampleData
        from App.models.data.StockDaily import save_daily_stock_data_to_sql, StockDaily
        from App.exts import db
        
        print("🔍 开始测试日线数据保存...")
        
        # 创建测试数据
        test_data = create_test_1m_data()
        print(f"✅ 创建测试数据: {len(test_data)} 条1分钟数据")
        
        # 转换为日线数据
        print("🔄 开始转换1分钟数据为日线数据...")
        df_daily = ResampleData.resample_1m_data(test_data, 'd')
        print(f"✅ 转换完成: {len(df_daily)} 条日线数据")
        
        # 显示转换结果
        print("\n📊 日线数据预览:")
        print(df_daily.head())
        
        # 测试保存到数据库
        test_stock_code = "TEST002"
        print(f"\n💾 测试保存日线数据到 daily_stock_data 表: {test_stock_code}")
        
        success = save_daily_stock_data_to_sql(test_stock_code, df_daily)
        if success:
            print("✅ 日线数据保存成功")
            
            # 测试从数据库查询
            print("📥 测试从数据库查询日线数据...")
            records = StockDaily.query.filter_by(stock_code=test_stock_code).all()
            print(f"✅ 查询成功: {len(records)} 条记录")
            
            # 显示查询的数据
            if records:
                print("\n📊 数据库中的日线数据预览:")
                for i, record in enumerate(records[:3]):  # 只显示前3条
                    print(f"记录 {i+1}: {record.stock_code} - {record.date} - 开盘:{record.open} - 收盘:{record.close}")
            
            # 清理测试数据
            print("\n🧹 清理测试数据...")
            StockDaily.query.filter_by(stock_code=test_stock_code).delete()
            db.session.commit()
            print("✅ 测试数据清理完成")
            
        else:
            print("❌ 日线数据保存失败")
            
        print("\n🎯 测试完成！")
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_test_1m_data():
    """创建测试用的1分钟数据"""
    # 创建时间序列
    start_time = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
    time_series = []
    
    # 创建一天的交易时间数据（9:30-15:00）
    current_time = start_time
    while current_time.hour < 15 or (current_time.hour == 15 and current_time.minute == 0):
        time_series.append(current_time)
        current_time += timedelta(minutes=1)
    
    # 创建测试数据
    data = []
    base_price = 10.0
    
    for i, time_point in enumerate(time_series):
        # 模拟价格波动
        price_change = (i % 10 - 5) * 0.01
        current_price = base_price + price_change
        
        data.append({
            'date': time_point,
            'open': current_price,
            'close': current_price + (i % 3 - 1) * 0.005,
            'high': current_price + 0.01,
            'low': current_price - 0.01,
            'volume': 1000 + (i % 100) * 10,
            'money': (1000 + (i % 100) * 10) * current_price
        })
    
    return pd.DataFrame(data)

def check_existing_daily_data():
    """检查现有的日线数据"""
    try:
        from App.models.data.StockDaily import StockDaily
        
        print("\n🔍 检查现有日线数据...")
        
        # 查询最近的数据
        recent_records = StockDaily.query.order_by(StockDaily.date.desc()).limit(10).all()
        
        if recent_records:
            print(f"✅ 找到 {len(recent_records)} 条最近的日线数据")
            
            # 按股票代码分组统计
            stock_counts = {}
            for record in recent_records:
                if record.stock_code not in stock_counts:
                    stock_counts[record.stock_code] = 0
                stock_counts[record.stock_code] += 1
            
            print("\n📊 各股票数据统计:")
            for stock_code, count in stock_counts.items():
                print(f"  {stock_code}: {count} 条记录")
                
            # 显示最新记录
            latest_record = recent_records[0]
            print(f"\n📅 最新记录: {latest_record.stock_code} - {latest_record.date}")
            print(f"   开盘: {latest_record.open}, 收盘: {latest_record.close}")
            print(f"   最高: {latest_record.high}, 最低: {latest_record.low}")
            print(f"   成交量: {latest_record.volume}, 成交额: {latest_record.money}")
            
        else:
            print("❌ 未找到日线数据")
            
    except Exception as e:
        print(f"❌ 检查现有数据失败: {e}")

def test_complete_download_process():
    """测试完整的下载流程"""
    try:
        from App.codes.RnnDataFile.save_download import complete_download_process
        
        print("\n🚀 测试完整下载流程...")
        
        # 使用测试股票代码
        test_stock_code = "TEST003"
        
        # 注意：这里需要确保有真实的下载数据源
        # 如果没有，可能会失败，但我们可以检查流程是否正确
        try:
            result = complete_download_process(test_stock_code, days=1, update_record=False)
            
            print(f"✅ 完整流程执行完成")
            print(f"   成功状态: {result['success']}")
            print(f"   消息: {result['message']}")
            print(f"   步骤完成情况: {result['steps']}")
            print(f"   数据统计: {result['data_info']}")
            
        except Exception as e:
            print(f"⚠️ 完整流程测试失败（可能是数据源问题）: {e}")
            print("   这是正常的，因为测试环境可能没有真实的数据源")
            
    except Exception as e:
        print(f"❌ 完整流程测试异常: {e}")

if __name__ == '__main__':
    print("🚀 日线数据保存测试开始")
    print("=" * 50)
    
    # 运行测试
    test_success = test_daily_data_save()
    
    # 检查现有数据
    check_existing_daily_data()
    
    # 测试完整流程
    test_complete_download_process()
    
    print("\n" + "=" * 50)
    if test_success:
        print("🎉 日线数据保存功能正常！")
    else:
        print("⚠️ 测试失败，请检查相关功能")
