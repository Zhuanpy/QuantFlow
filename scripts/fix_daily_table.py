#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复daily_stock_data表结构
添加stock_code字段并迁移数据
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def fix_table_structure():
    """修复表结构"""
    try:
        from App import create_app
        from App.exts import db
        
        app = create_app()
        with app.app_context():
            print("开始修复表结构...")
            
            # 1. 添加stock_code字段
            print("1. 添加stock_code字段...")
            try:
                db.engine.execute("ALTER TABLE datadaily.daily_stock_data ADD COLUMN stock_code VARCHAR(10) NOT NULL DEFAULT '000001'")
                print("  OK - stock_code字段添加成功")
            except Exception as e:
                print(f"  添加stock_code字段失败: {e}")
                return False
            
            # 2. 删除旧的主键约束
            print("2. 删除旧的主键约束...")
            try:
                db.engine.execute("ALTER TABLE datadaily.daily_stock_data DROP PRIMARY KEY")
                print("  OK - 旧主键约束删除成功")
            except Exception as e:
                print(f"  删除旧主键约束失败: {e}")
            
            # 3. 添加新的复合主键
            print("3. 添加新的复合主键...")
            try:
                db.engine.execute("ALTER TABLE datadaily.daily_stock_data ADD PRIMARY KEY (stock_code, date)")
                print("  OK - 新主键添加成功")
            except Exception as e:
                print(f"  添加新主键失败: {e}")
                return False
            
            print("表结构修复完成！")
            return True
            
    except Exception as e:
        print(f"修复表结构失败: {e}")
        return False

def migrate_sample_data():
    """迁移示例数据"""
    try:
        from App import create_app
        from App.exts import db
        
        app = create_app()
        with app.app_context():
            print("\n开始迁移示例数据...")
            
            # 获取所有股票表
            result = db.engine.execute("SHOW TABLES FROM datadaily")
            tables = [row[0] for row in result]
            
            # 过滤出股票代码表（排除daily_stock_data）
            stock_tables = [t for t in tables if t != 'daily_stock_data' and t.isdigit()]
            
            print(f"找到 {len(stock_tables)} 个股票表")
            
            # 迁移前3个股票的数据作为示例
            migrated_count = 0
            
            for stock_code in stock_tables[:3]:
                print(f"\n迁移股票 {stock_code}...")
                
                try:
                    # 获取原表数据
                    result = db.engine.execute(f"SELECT * FROM datadaily.`{stock_code}` LIMIT 5")
                    records = list(result)
                    
                    if records:
                        print(f"  找到 {len(records)} 条记录")
                        
                        # 迁移数据
                        for record in records:
                            date = record[0]
                            open_price = record[1] if len(record) > 1 else 0
                            close_price = record[2] if len(record) > 2 else 0
                            high = record[3] if len(record) > 3 else 0
                            low = record[4] if len(record) > 4 else 0
                            volume = record[5] if len(record) > 5 else 0
                            money = record[6] if len(record) > 6 else 0
                            
                            # 插入到daily_stock_data表
                            insert_sql = """
                            INSERT IGNORE INTO datadaily.daily_stock_data 
                            (stock_code, date, open, close, high, low, volume, money) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """
                            
                            db.engine.execute(insert_sql, (
                                stock_code, date, open_price, close_price, 
                                high, low, volume, money
                            ))
                            
                            migrated_count += 1
                        
                        print(f"  OK - 迁移完成")
                    else:
                        print(f"  无数据")
                        
                except Exception as e:
                    print(f"  迁移失败: {e}")
                    continue
            
            print(f"\n示例数据迁移完成！总共迁移了 {migrated_count} 条记录")
            return True
            
    except Exception as e:
        print(f"迁移示例数据失败: {e}")
        return False

def test_new_structure():
    """测试新结构"""
    try:
        from App.models.data import get_stock_list, get_daily_stock_data
        
        print("\n测试新结构...")
        
        # 获取股票列表
        stock_list = get_stock_list()
        print(f"OK - 获取股票列表成功，共 {len(stock_list)} 只股票")
        
        if stock_list:
            # 测试获取单只股票数据
            test_stock = stock_list[0]
            print(f"OK - 测试股票: {test_stock}")
            
            df = get_daily_stock_data(test_stock)
            print(f"OK - 获取股票数据成功，共 {len(df)} 条记录")
            
            if len(df) > 0:
                print(f"OK - 数据列: {list(df.columns)}")
                print(f"OK - 日期范围: {df['date'].min()} 到 {df['date'].max()}")
        
        return True
        
    except Exception as e:
        print(f"测试新结构失败: {e}")
        return False

def main():
    print("=" * 60)
    print("修复daily_stock_data表结构")
    print("=" * 60)
    
    # 修复表结构
    print("\n1. 修复表结构:")
    structure_ok = fix_table_structure()
    
    if structure_ok:
        # 迁移示例数据
        print("\n2. 迁移示例数据:")
        migrate_ok = migrate_sample_data()
        
        if migrate_ok:
            # 测试新结构
            print("\n3. 测试新结构:")
            test_ok = test_new_structure()
            
            if test_ok:
                print("\n" + "=" * 60)
                print("OK - 表结构修复成功！")
                print("OK - 新架构已就绪，可以使用单表多股票结构")
                print("=" * 60)
            else:
                print("\n✗ 测试失败")
        else:
            print("\n✗ 数据迁移失败")
    else:
        print("\n✗ 表结构修复失败")

if __name__ == '__main__':
    main()
