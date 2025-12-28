#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试StockDaily模型功能
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_import():
    """测试导入功能"""
    try:
        from App.models.data import StockDaily, create_daily_stock_model, save_daily_stock_data_to_sql, get_daily_stock_data
        print("OK - 所有导入成功")
        return True
    except Exception as e:
        print(f"ERROR - 导入失败: {e}")
        return False

def test_model_creation():
    """测试模型创建"""
    try:
        from App.models.data import create_daily_stock_model
        from App import create_app
        
        app = create_app()
        with app.app_context():
            # 测试创建000001模型
            model = create_daily_stock_model("000001")
            print(f"OK - 模型创建成功: {model.__name__}")
            print(f"OK - 表名: {model.__tablename__}")
            print(f"OK - 绑定数据库: {model.__bind_key__}")
            return True
    except Exception as e:
        print(f"ERROR - 模型创建失败: {e}")
        return False

def test_data_retrieval():
    """测试数据获取"""
    try:
        from App.models.data import get_daily_stock_data
        from App import create_app
        
        app = create_app()
        with app.app_context():
            # 测试获取000001数据
            df = get_daily_stock_data("000001", start_date="2025-01-01", end_date="2025-01-31")
            print(f"OK - 数据获取成功，共 {len(df)} 条记录")
            if len(df) > 0:
                print(f"OK - 数据列: {list(df.columns)}")
                print(f"OK - 日期范围: {df['date'].min()} 到 {df['date'].max()}")
            return True
    except Exception as e:
        print(f"ERROR - 数据获取失败: {e}")
        return False

def main():
    print("=" * 60)
    print("StockDaily 模型功能测试")
    print("=" * 60)
    
    # 测试导入
    print("\n1. 测试导入功能:")
    import_ok = test_import()
    
    if import_ok:
        # 测试模型创建
        print("\n2. 测试模型创建:")
        model_ok = test_model_creation()
        
        # 测试数据获取
        print("\n3. 测试数据获取:")
        data_ok = test_data_retrieval()
        
        if model_ok and data_ok:
            print("\n" + "=" * 60)
            print("所有测试通过！StockDaily模型功能正常")
            print("=" * 60)
        else:
            print("\n" + "=" * 60)
            print("部分测试失败")
            print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("导入测试失败")
        print("=" * 60)

if __name__ == '__main__':
    main()


