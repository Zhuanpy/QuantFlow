#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查datadaily数据库结构和数据
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from App import create_app
from App.exts import db

def main():
    app = create_app()
    with app.app_context():
        print("=" * 60)
        print("datadaily 数据库检查")
        print("=" * 60)
        
        # 1. 检查数据库是否存在
        try:
            result = db.engine.execute("SHOW TABLES FROM datadaily")
            tables = [row[0] for row in result]
            print(f"OK - datadaily 数据库存在")
            print(f"OK - 包含 {len(tables)} 个表")
            print(f"OK - 前10个表: {tables[:10]}")
            
            # 2. 检查000001表结构
            if '000001' in tables:
                print("\n" + "-" * 40)
                print("000001 表结构:")
                print("-" * 40)
                
                result = db.engine.execute("DESCRIBE datadaily.`000001`")
                columns = list(result)
                
                for col in columns:
                    field, type_, null, key, default, extra = col
                    print(f"  {field:<25} {type_:<15} {'NULL' if null == 'YES' else 'NOT NULL':<8} {'PRI' if key == 'PRI' else '':<4} {default or '':<10} {extra or ''}")
                
                # 3. 检查数据示例
                print("\n" + "-" * 40)
                print("000001 表数据示例 (前5条):")
                print("-" * 40)
                
                result = db.engine.execute("SELECT * FROM datadaily.`000001` LIMIT 5")
                rows = list(result)
                
                if rows:
                    # 获取列名
                    col_names = [desc[0] for desc in result.description]
                    print(f"列名: {col_names}")
                    print()
                    
                    for i, row in enumerate(rows, 1):
                        print(f"第{i}行: {dict(row)}")
                else:
                    print("表中没有数据")
                
                # 4. 检查数据总数
                result = db.engine.execute("SELECT COUNT(*) FROM datadaily.`000001`")
                count = result.fetchone()[0]
                print(f"\n数据总数: {count} 条")
                
                # 5. 检查日期范围
                result = db.engine.execute("SELECT MIN(date), MAX(date) FROM datadaily.`000001`")
                date_range = result.fetchone()
                print(f"日期范围: {date_range[0]} 到 {date_range[1]}")
                
            else:
                print("ERROR - 000001 表不存在")
                
        except Exception as e:
            print(f"ERROR - 错误: {e}")
        
        print("\n" + "=" * 60)
        print("检查完成")
        print("=" * 60)

if __name__ == '__main__':
    main()

