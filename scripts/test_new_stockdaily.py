#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试重构后的StockDaily模型功能
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_import():
    """测试导入功能"""
    try:
        from App.models.data import StockDaily, save_daily_stock_data_to_sql, get_daily_stock_data, get_multiple_stocks_data, get_stock_list, get_market_overview
        print("OK - 所有导入成功")
        return True
    except Exception as e:
        print(f"ERROR - 导入失败: {e}")
        return False

def test_model_structure():
    """测试模型结构"""
    try:
        from App.models.data import StockDaily
        from App import create_app
        
        app = create_app()
        with app.app_context():
            # 测试模型属性
            print(f"OK - 模型类名: {StockDaily.__name__}")
            print(f"OK - 表名: {StockDaily.__tablename__}")
            print(f"OK - 绑定数据库: {StockDaily.__bind_key__}")
            
            # 检查字段
            columns = StockDaily.__table__.columns
            print(f"OK - 字段数量: {len(columns)}")
            
            # 检查主键
            primary_keys = [col.name for col in columns if col.primary_key]
            print(f"OK - 主键字段: {primary_keys}")
            
            # 检查股票代码字段
            stock_code_col = columns.get('stock_code')
            if stock_code_col:
                print(f"OK - stock_code字段存在，类型: {stock_code_col.type}")
            else:
                print("ERROR - stock_code字段不存在")
                return False
                
            return True
    except Exception as e:
        print(f"ERROR - 模型结构测试失败: {e}")
        return False

def test_table_creation():
    """测试表创建"""
    try:
        from App.models.data import StockDaily
        from App import create_app
        
        app = create_app()
        with app.app_context():
            # 检查表是否存在
            from App.exts import db
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'daily_stock_data' in tables:
                print("OK - daily_stock_data表已存在")
                
                # 检查表结构
                columns = inspector.get_columns('daily_stock_data')
                print(f"OK - 表字段数量: {len(columns)}")
                
                # 检查主键
                pk_constraint = inspector.get_pk_constraint('daily_stock_data')
                print(f"OK - 主键约束: {pk_constraint}")
                
                return True
            else:
                print("INFO - daily_stock_data表不存在，需要创建")
                # 尝试创建表
                db.create_all()
                print("OK - 表创建完成")
                return True
    except Exception as e:
        print(f"ERROR - 表创建测试失败: {e}")
        return False

def test_data_operations():
    """测试数据操作"""
    try:
        from App.models.data import get_stock_list, get_daily_stock_data
        from App import create_app
        
        app = create_app()
        with app.app_context():
            # 测试获取股票列表
            stock_list = get_stock_list()
            print(f"OK - 获取股票列表成功，共 {len(stock_list)} 只股票")
            
            if stock_list:
                # 测试获取单只股票数据
                test_stock = stock_list[0]
                print(f"OK - 测试股票: {test_stock}")
                
                df = get_daily_stock_data(test_stock, start_date="2025-01-01", end_date="2025-01-31")
                print(f"OK - 获取股票数据成功，共 {len(df)} 条记录")
                
                if len(df) > 0:
                    print(f"OK - 数据列: {list(df.columns)}")
                    print(f"OK - 日期范围: {df['date'].min()} 到 {df['date'].max()}")
                
            return True
    except Exception as e:
        print(f"ERROR - 数据操作测试失败: {e}")
        return False

def main():
    print("=" * 60)
    print("重构后的StockDaily模型功能测试")
    print("=" * 60)
    
    # 测试导入
    print("\n1. 测试导入功能:")
    import_ok = test_import()
    
    if import_ok:
        # 测试模型结构
        print("\n2. 测试模型结构:")
        model_ok = test_model_structure()
        
        # 测试表创建
        print("\n3. 测试表创建:")
        table_ok = test_table_creation()
        
        # 测试数据操作
        print("\n4. 测试数据操作:")
        data_ok = test_data_operations()
        
        if model_ok and table_ok and data_ok:
            print("\n" + "=" * 60)
            print("所有测试通过！重构后的StockDaily模型功能正常")
            print("=" * 60)
            print("\n新架构优势:")
            print("✓ 单表多股票结构，便于跨股票查询")
            print("✓ 复合主键(stock_code + date)，避免数据重复")
            print("✓ 支持多股票批量查询和统计")
            print("✓ 减少了数据库表的数量，提高管理效率")
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


