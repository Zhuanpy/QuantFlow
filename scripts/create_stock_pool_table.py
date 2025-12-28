#!/usr/bin/env python3
"""
创建股票池表的数据库迁移脚本
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App import create_app
from App.exts import db
from App.models.strategy.StockPool import StockPool

def create_stock_pool_table():
    """创建股票池表"""
    try:
        app = create_app()
        with app.app_context():
            # 创建表
            db.create_all()
            print("✅ 股票池表创建成功")
            
            # 验证表是否存在
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'stock_pool' in tables:
                print("✅ 验证: stock_pool 表已存在")
                
                # 显示表结构
                columns = inspector.get_columns('stock_pool')
                print("\n📋 表结构:")
                for col in columns:
                    print(f"  - {col['name']}: {col['type']}")
            else:
                print("❌ 验证失败: stock_pool 表不存在")
                
    except Exception as e:
        print(f"❌ 创建股票池表失败: {e}")
        return False
    
    return True

if __name__ == "__main__":
    print("🚀 开始创建股票池表...")
    success = create_stock_pool_table()
    if success:
        print("🎉 股票池表创建完成！")
    else:
        print("💥 股票池表创建失败！")
        sys.exit(1)
