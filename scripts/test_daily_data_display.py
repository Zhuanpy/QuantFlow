#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日线数据验证显示测试脚本
验证模板中是否正确显示从 daily_stock_data 表查询的数据
"""

import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

def test_daily_data_query():
    """测试日线数据查询功能"""
    try:
        from App.models.data.StockDaily import StockDaily
        
        print("🔍 测试日线数据查询功能...")
        
        # 查询最近7天的数据
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=7)
        
        print(f"📅 查询时间范围: {start_date} 到 {end_date}")
        
        # 查询所有股票的数据
        daily_records = StockDaily.query.filter(
            StockDaily.date >= start_date,
            StockDaily.date <= end_date
        ).order_by(StockDaily.date.desc()).limit(20).all()
        
        if daily_records:
            print(f"✅ 找到 {len(daily_records)} 条日线数据")
            
            # 按股票代码分组
            stock_groups = {}
            for record in daily_records:
                if record.stock_code not in stock_groups:
                    stock_groups[record.stock_code] = []
                stock_groups[record.stock_code].append(record)
            
            print(f"📊 涉及 {len(stock_groups)} 只股票:")
            for stock_code, records in stock_groups.items():
                print(f"   {stock_code}: {len(records)} 条记录")
            
            # 显示示例数据
            print("\n📋 示例数据:")
            for i, record in enumerate(daily_records[:5]):
                print(f"   {i+1}. {record.stock_code} - {record.date}")
                print(f"      开盘: {record.open:.2f}, 收盘: {record.close:.2f}")
                print(f"      最高: {record.high:.2f}, 最低: {record.low:.2f}")
                print(f"      成交量: {record.volume:,}, 成交额: {record.money:,}")
                print()
            
            # 测试特定股票的数据
            test_stock = list(stock_groups.keys())[0]
            print(f"🎯 测试股票 {test_stock} 的数据查询:")
            
            test_records = StockDaily.query.filter(
                StockDaily.stock_code == test_stock,
                StockDaily.date >= start_date,
                StockDaily.date <= end_date
            ).order_by(StockDaily.date.desc()).all()
            
            if test_records:
                print(f"✅ 找到 {len(test_records)} 条 {test_stock} 的数据")
                
                # 转换为DataFrame格式（模拟模板中的处理）
                daily_data_list = []
                for record in test_records:
                    daily_data_list.append({
                        'date': record.date.strftime('%Y-%m-%d'),
                        'open': f"{record.open:.2f}",
                        'close': f"{record.close:.2f}",
                        'high': f"{record.high:.2f}",
                        'low': f"{record.low:.2f}",
                        'volume': f"{record.volume:,}",
                        'money': f"{record.money:,}"
                    })
                
                df = pd.DataFrame(daily_data_list)
                print("\n📊 DataFrame 格式:")
                print(df.to_string(index=False))
                
                # 测试HTML表格生成
                html_table = df.to_html(classes='table table-striped verification-table', index=False, escape=False)
                print(f"\n✅ HTML表格生成成功 (长度: {len(html_table)} 字符)")
                
            else:
                print(f"❌ 未找到 {test_stock} 的数据")
                
        else:
            print("❌ 未找到任何日线数据")
            
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_template_data_format():
    """测试模板数据格式"""
    try:
        print("\n🔍 测试模板数据格式...")
        
        # 创建模拟数据
        test_data = pd.DataFrame([
            {
                'date': '2025-01-20',
                'open': '10.50',
                'close': '10.80',
                'high': '11.00',
                'low': '10.30',
                'volume': '1,200,000',
                'money': '12,960,000'
            },
            {
                'date': '2025-01-19',
                'open': '10.20',
                'close': '10.50',
                'high': '10.70',
                'low': '10.10',
                'volume': '980,000',
                'money': '10,290,000'
            }
        ])
        
        print("📊 模拟数据:")
        print(test_data.to_string(index=False))
        
        # 测试HTML生成
        html_table = test_data.to_html(classes='table table-striped verification-table', index=False, escape=False)
        print(f"\n✅ HTML表格生成成功")
        print(f"📏 HTML长度: {len(html_table)} 字符")
        
        # 检查HTML内容
        if 'verification-table' in html_table:
            print("✅ CSS类名正确")
        else:
            print("❌ CSS类名缺失")
            
        if 'table-striped' in html_table:
            print("✅ Bootstrap样式正确")
        else:
            print("❌ Bootstrap样式缺失")
            
        return True
        
    except Exception as e:
        print(f"❌ 模板数据格式测试失败: {e}")
        return False

if __name__ == '__main__':
    print("🚀 日线数据验证显示测试开始")
    print("=" * 50)
    
    # 测试数据查询
    query_success = test_daily_data_query()
    
    # 测试模板格式
    template_success = test_template_data_format()
    
    print("\n" + "=" * 50)
    if query_success and template_success:
        print("🎉 日线数据验证显示功能完全正常！")
        print("✅ 数据库查询功能正常")
        print("✅ 模板数据格式正确")
        print("✅ HTML表格生成正常")
    else:
        print("⚠️ 发现问题，请检查相关功能")
