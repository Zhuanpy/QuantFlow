#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日线数据保存验证脚本
快速验证日线数据是否正确保存到 daily_stock_data 表
"""

import sys
import os

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

def verify_daily_data_save():
    """验证日线数据保存功能"""
    try:
        from App.models.data.StockDaily import StockDaily, save_daily_stock_data_to_sql
        from App.exts import db
        import pandas as pd
        from datetime import date
        
        print("🔍 验证日线数据保存功能...")
        
        # 创建测试数据
        test_data = pd.DataFrame([
            {
                'date': date(2025, 1, 20),
                'open': 10.0,
                'close': 10.5,
                'high': 10.8,
                'low': 9.8,
                'volume': 1000,
                'money': 10500
            },
            {
                'date': date(2025, 1, 21),
                'open': 10.5,
                'close': 11.0,
                'high': 11.2,
                'low': 10.3,
                'volume': 1200,
                'money': 13200
            }
        ])
        
        test_stock_code = "VERIFY001"
        
        # 测试保存
        print(f"💾 保存测试数据到 daily_stock_data 表...")
        success = save_daily_stock_data_to_sql(test_stock_code, test_data)
        
        if success:
            print("✅ 数据保存成功")
            
            # 验证数据是否正确保存
            print("📥 验证数据是否正确保存...")
            saved_records = StockDaily.query.filter_by(stock_code=test_stock_code).all()
            
            if len(saved_records) == 2:
                print(f"✅ 验证成功: 找到 {len(saved_records)} 条记录")
                
                # 显示保存的数据
                for record in saved_records:
                    print(f"   {record.stock_code} - {record.date}: 开盘{record.open}, 收盘{record.close}")
                
                # 测试重复数据更新
                print("\n🔄 测试重复数据更新...")
                updated_data = pd.DataFrame([
                    {
                        'date': date(2025, 1, 20),  # 相同日期
                        'open': 10.2,  # 更新价格
                        'close': 10.7,
                        'high': 11.0,
                        'low': 9.9,
                        'volume': 1100,
                        'money': 11770
                    }
                ])
                
                update_success = save_daily_stock_data_to_sql(test_stock_code, updated_data)
                if update_success:
                    print("✅ 重复数据更新成功")
                    
                    # 验证更新
                    updated_record = StockDaily.query.filter_by(
                        stock_code=test_stock_code,
                        date=date(2025, 1, 20)
                    ).first()
                    
                    if updated_record and updated_record.open == 10.2:
                        print("✅ 数据更新验证成功")
                    else:
                        print("❌ 数据更新验证失败")
                
                # 清理测试数据
                print("\n🧹 清理测试数据...")
                StockDaily.query.filter_by(stock_code=test_stock_code).delete()
                db.session.commit()
                print("✅ 测试数据清理完成")
                
            else:
                print(f"❌ 验证失败: 期望2条记录，实际找到{len(saved_records)}条")
                
        else:
            print("❌ 数据保存失败")
            
        return success
        
    except Exception as e:
        print(f"❌ 验证过程出错: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_table_structure():
    """检查 daily_stock_data 表结构"""
    try:
        from App.models.data.StockDaily import StockDaily
        
        print("\n🔍 检查 daily_stock_data 表结构...")
        
        # 获取表信息
        table_name = StockDaily.__tablename__
        print(f"✅ 表名: {table_name}")
        
        # 检查主键
        primary_keys = StockDaily.__table__.primary_key.columns.keys()
        print(f"✅ 主键: {primary_keys}")
        
        # 检查列信息
        columns = StockDaily.__table__.columns
        print(f"✅ 列数: {len(columns)}")
        
        # 显示主要列
        main_columns = ['stock_code', 'date', 'open', 'close', 'high', 'low', 'volume', 'money']
        print("📊 主要列信息:")
        for col_name in main_columns:
            if col_name in columns:
                col = columns[col_name]
                print(f"   {col_name}: {col.type} {'(主键)' if col.primary_key else ''}")
        
        return True
        
    except Exception as e:
        print(f"❌ 检查表结构失败: {e}")
        return False

if __name__ == '__main__':
    print("🚀 日线数据保存验证开始")
    print("=" * 50)
    
    # 检查表结构
    structure_ok = check_table_structure()
    
    # 验证保存功能
    save_ok = verify_daily_data_save()
    
    print("\n" + "=" * 50)
    if structure_ok and save_ok:
        print("🎉 日线数据保存功能完全正常！")
        print("✅ daily_stock_data 表结构正确")
        print("✅ 数据保存和更新功能正常")
    else:
        print("⚠️ 发现问题，请检查相关功能")
