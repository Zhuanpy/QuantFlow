#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查daily_stock_data表结构
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def main():
    try:
        from App import create_app
        from App.exts import db
        
        app = create_app()
        with app.app_context():
            print("检查daily_stock_data表结构...")
            
            result = db.engine.execute("DESCRIBE quanttradingsystem.daily_stock_data")
            columns = list(result)
            
            print("表结构:")
            for col in columns:
                field, type_, null, key, default, extra = col
                print(f"  {field:<20} {type_:<15} {'NULL' if null == 'YES' else 'NOT NULL':<8} {'PRI' if key == 'PRI' else '':<4} {default or '':<10} {extra or ''}")
            
            # 检查是否有stock_code字段
            has_stock_code = any(col[0] == 'stock_code' for col in columns)
            print(f"\n是否有stock_code字段: {has_stock_code}")
            
            # 检查主键
            pk_fields = [col[0] for col in columns if col[3] == 'PRI']
            print(f"主键字段: {pk_fields}")
            
            # 测试查询
            if has_stock_code:
                print("\n测试查询...")
                try:
                    result = db.engine.execute("SELECT COUNT(*) FROM quanttradingsystem.daily_stock_data")
                    count = result.fetchone()[0]
                    print(f"表中记录数: {count}")
                    
                    result = db.engine.execute("SELECT DISTINCT stock_code FROM quanttradingsystem.daily_stock_data LIMIT 5")
                    stock_codes = [row[0] for row in result]
                    print(f"股票代码示例: {stock_codes}")
                    
                except Exception as e:
                    print(f"查询测试失败: {e}")
            
    except Exception as e:
        print(f"检查失败: {e}")

if __name__ == '__main__':
    main()
