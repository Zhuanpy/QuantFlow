#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复主键约束
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
            print("修复主键约束...")
            
            # 删除旧主键
            try:
                db.engine.execute("ALTER TABLE datadaily.daily_stock_data DROP PRIMARY KEY")
                print("OK - 旧主键删除成功")
            except Exception as e:
                print(f"删除旧主键失败: {e}")
            
            # 添加新复合主键
            try:
                db.engine.execute("ALTER TABLE datadaily.daily_stock_data ADD PRIMARY KEY (stock_code, date)")
                print("OK - 新复合主键添加成功")
            except Exception as e:
                print(f"添加新主键失败: {e}")
                return False
            
            print("主键修复完成！")
            return True
            
    except Exception as e:
        print(f"修复失败: {e}")
        return False

if __name__ == '__main__':
    main()




