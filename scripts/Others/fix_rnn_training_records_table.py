#!/usr/bin/env python3
"""
修复rnn_training_records表，添加缺失的created_at和updated_at字段
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App import create_app
from App.exts import db

def fix_rnn_training_records_table():
    """修复rnn_training_records表"""
    try:
        app = create_app()
        with app.app_context():
            # 检查表是否存在
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'rnn_training_records' not in tables:
                print("❌ rnn_training_records 表不存在")
                return False
            
            # 检查字段是否存在
            columns = inspector.get_columns('rnn_training_records')
            column_names = [col['name'] for col in columns]
            
            print(f"📋 当前表字段: {column_names}")
            
            # 添加缺失的字段
            if 'created_at' not in column_names:
                print("➕ 添加 created_at 字段...")
                db.engine.execute("ALTER TABLE rnn_training_records ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
                print("✅ created_at 字段添加成功")
            else:
                print("✅ created_at 字段已存在")
            
            if 'updated_at' not in column_names:
                print("➕ 添加 updated_at 字段...")
                db.engine.execute("ALTER TABLE rnn_training_records ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
                print("✅ updated_at 字段添加成功")
            else:
                print("✅ updated_at 字段已存在")
            
            # 验证字段是否添加成功
            columns_after = inspector.get_columns('rnn_training_records')
            column_names_after = [col['name'] for col in columns_after]
            
            print(f"📋 修复后表字段: {column_names_after}")
            
            if 'created_at' in column_names_after and 'updated_at' in column_names_after:
                print("🎉 rnn_training_records 表修复成功！")
                return True
            else:
                print("❌ 表修复失败")
                return False
                
    except Exception as e:
        print(f"❌ 修复表失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 开始修复 rnn_training_records 表...")
    success = fix_rnn_training_records_table()
    if success:
        print("🎉 表修复完成！")
    else:
        print("💥 表修复失败！")
        sys.exit(1)



