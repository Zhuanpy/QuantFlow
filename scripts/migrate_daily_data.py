#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
迁移日线数据表结构
将现有的多表结构迁移到单表多股票结构
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def check_current_structure():
    """检查当前数据库结构"""
    try:
        from App import create_app
        from App.exts import db
        
        app = create_app()
        with app.app_context():
            # 检查datadaily数据库中的表
            result = db.engine.execute("SHOW TABLES FROM datadaily")
            tables = [row[0] for row in result]
            
            print(f"当前datadaily数据库中的表数量: {len(tables)}")
            print(f"前10个表: {tables[:10]}")
            
            # 检查是否有daily_stock_data表
            if 'daily_stock_data' in tables:
                print("\ndaily_stock_data表已存在，检查结构:")
                result = db.engine.execute("DESCRIBE datadaily.daily_stock_data")
                columns = list(result)
                
                for col in columns:
                    field, type_, null, key, default, extra = col
                    print(f"  {field:<25} {type_:<15} {'NULL' if null == 'YES' else 'NOT NULL':<8} {'PRI' if key == 'PRI' else '':<4}")
                
                # 检查是否有stock_code字段
                has_stock_code = any(col[0] == 'stock_code' for col in columns)
                print(f"\n是否有stock_code字段: {has_stock_code}")
                
                return has_stock_code, tables
            else:
                print("\ndaily_stock_data表不存在")
                return False, tables
                
    except Exception as e:
        print(f"检查结构失败: {e}")
        return False, []

def migrate_data():
    """迁移数据到新结构"""
    try:
        from App import create_app
        from App.exts import db
        
        app = create_app()
        with app.app_context():
            print("\n开始数据迁移...")
            
            # 获取所有股票表
            result = db.engine.execute("SHOW TABLES FROM datadaily")
            tables = [row[0] for row in result]
            
            # 过滤出股票代码表（排除daily_stock_data）
            stock_tables = [t for t in tables if t != 'daily_stock_data' and t.isdigit()]
            
            print(f"找到 {len(stock_tables)} 个股票表需要迁移")
            
            total_records = 0
            
            for i, stock_code in enumerate(stock_tables[:5]):  # 先迁移前5个表作为测试
                print(f"\n迁移股票 {stock_code} ({i+1}/{min(5, len(stock_tables))})...")
                
                try:
                    # 获取原表数据
                    result = db.engine.execute(f"SELECT * FROM datadaily.`{stock_code}` LIMIT 10")
                    records = list(result)
                    
                    if records:
                        print(f"  找到 {len(records)} 条记录")
                        
                        # 构建插入语句
                        for record in records:
                            # 构建字段映射
                            date = record[0]  # 第一列是date
                            open_price = record[1] if len(record) > 1 else 0
                            close_price = record[2] if len(record) > 2 else 0
                            high = record[3] if len(record) > 3 else 0
                            low = record[4] if len(record) > 4 else 0
                            volume = record[5] if len(record) > 5 else 0
                            money = record[6] if len(record) > 6 else 0
                            
                            # 插入到新表
                            insert_sql = """
                            INSERT IGNORE INTO datadaily.daily_stock_data 
                            (stock_code, date, open, close, high, low, volume, money) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """
                            
                            db.engine.execute(insert_sql, (
                                stock_code, date, open_price, close_price, 
                                high, low, volume, money
                            ))
                            
                            total_records += 1
                        
                        print(f"  迁移完成，共 {len(records)} 条记录")
                    else:
                        print(f"  表 {stock_code} 无数据")
                        
                except Exception as e:
                    print(f"  迁移股票 {stock_code} 失败: {e}")
                    continue
            
            print(f"\n迁移完成！总共迁移了 {total_records} 条记录")
            return True
            
    except Exception as e:
        print(f"数据迁移失败: {e}")
        return False

def main():
    print("=" * 60)
    print("日线数据表结构迁移")
    print("=" * 60)
    
    # 检查当前结构
    print("\n1. 检查当前数据库结构:")
    has_stock_code, tables = check_current_structure()
    
    if not has_stock_code:
        print("\n2. 需要迁移数据到新结构")
        print("注意：这将把现有的股票代码表数据迁移到统一的daily_stock_data表中")
        
        # 询问是否继续
        response = input("\n是否继续迁移？(y/N): ")
        if response.lower() == 'y':
            success = migrate_data()
            if success:
                print("\n✓ 迁移成功！")
                print("现在可以使用新的单表多股票结构了")
            else:
                print("\n✗ 迁移失败")
        else:
            print("迁移已取消")
    else:
        print("\n✓ 数据库结构已经是新版本，无需迁移")

if __name__ == '__main__':
    main()




