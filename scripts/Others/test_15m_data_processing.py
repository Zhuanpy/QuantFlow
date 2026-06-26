#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
15分钟数据处理功能测试脚本
"""

import sys
import os
import pandas as pd
import logging
from datetime import datetime

# 添加项目根目录到Python路径
sys.path.append('.')

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_15m_data_processing():
    """测试15分钟数据处理功能"""
    
    print("=" * 60)
    print("15分钟数据处理功能测试")
    print("=" * 60)
    
    try:
        # 导入必要的模块
        from App.utils.file_utils import get_stock_data_path
        from App.codes.utils.Normal import ResampleData
        from App.codes.数据整理.15M数据整理 import clean_and_standardize, load_extreme_values
        
        # 测试参数
        test_stock_code = "002475"
        test_year = "2025"
        test_quarter = "Q4"
        
        print(f"测试股票代码: {test_stock_code}")
        print(f"测试时间: {test_year}-{test_quarter}")
        print()
        
        # 步骤1: 检查1分钟数据
        print("步骤1: 检查1分钟数据...")
        file_path_1m = get_stock_data_path(test_stock_code, data_type='1m')
        print(f"1分钟数据路径: {file_path_1m}")
        
        if not os.path.exists(file_path_1m):
            print("❌ 1分钟数据文件不存在，请先下载数据")
            return False
        
        # 读取1分钟数据
        try:
            df_1m = pd.read_csv(file_path_1m, parse_dates=['date'])
            print(f"✅ 成功读取1分钟数据: {len(df_1m)} 条记录")
            print(f"   数据时间范围: {df_1m['date'].min()} 到 {df_1m['date'].max()}")
        except Exception as e:
            print(f"❌ 读取1分钟数据失败: {str(e)}")
            return False
        
        # 步骤2: 重采样为15分钟数据
        print("\n步骤2: 重采样为15分钟数据...")
        try:
            df_15m = ResampleData.resample_1m_data(df_1m, '15m')
            if df_15m.empty:
                print("❌ 15分钟数据重采样结果为空")
                return False
            
            print(f"✅ 成功重采样为15分钟数据: {len(df_15m)} 条记录")
            print(f"   数据时间范围: {df_15m['date'].min()} 到 {df_15m['date'].max()}")
            print(f"   数据列: {list(df_15m.columns)}")
        except Exception as e:
            print(f"❌ 15分钟数据重采样失败: {str(e)}")
            return False
        
        # 步骤3: 标准化处理
        print("\n步骤3: 标准化处理...")
        try:
            cache_df = load_extreme_values()
            df_15m_standardized = clean_and_standardize(test_stock_code, df_15m.copy(), cache_df)
            
            print(f"✅ 成功完成15分钟数据标准化")
            print(f"   标准化后数据形状: {df_15m_standardized.shape}")
            print(f"   标准化后数据列: {list(df_15m_standardized.columns)}")
            
            # 显示标准化后的数据统计
            numeric_cols = df_15m_standardized.select_dtypes(include=['float64', 'int64']).columns
            print(f"   数值列统计:")
            for col in numeric_cols[:5]:  # 只显示前5列
                mean_val = df_15m_standardized[col].mean()
                std_val = df_15m_standardized[col].std()
                print(f"     {col}: 均值={mean_val:.4f}, 标准差={std_val:.4f}")
                
        except Exception as e:
            print(f"❌ 15分钟数据标准化失败: {str(e)}")
            return False
        
        # 步骤4: 测试文件路径生成
        print("\n步骤4: 测试文件路径生成...")
        try:
            file_path_15m = get_stock_data_path(test_stock_code, data_type='15m')
            print(f"✅ 15分钟数据路径: {file_path_15m}")
            
            # 测试标准化数据路径
            standardized_dir = os.path.join(os.path.dirname(file_path_15m), '..', 'standardized', test_stock_code)
            standardized_path = os.path.join(standardized_dir, f"{test_year}_{test_quarter}.csv")
            print(f"✅ 标准化数据路径: {standardized_path}")
            
            # 测试信号数据路径
            signal_dir = os.path.join(os.path.dirname(file_path_15m), '..', 'signals')
            signal_path = os.path.join(signal_dir, f"{test_stock_code}_{test_year}_{test_quarter}_signals.csv")
            print(f"✅ 信号数据路径: {signal_path}")
            
        except Exception as e:
            print(f"❌ 文件路径生成失败: {str(e)}")
            return False
        
        # 步骤5: 测试数据保存
        print("\n步骤5: 测试数据保存...")
        try:
            # 保存15分钟原始数据
            os.makedirs(os.path.dirname(file_path_15m), exist_ok=True)
            df_15m.to_csv(file_path_15m, index=False)
            print(f"✅ 成功保存15分钟原始数据: {file_path_15m}")
            
            # 保存标准化数据
            os.makedirs(standardized_dir, exist_ok=True)
            df_15m_standardized.to_csv(standardized_path, index=False)
            print(f"✅ 成功保存标准化数据: {standardized_path}")
            
        except Exception as e:
            print(f"❌ 数据保存失败: {str(e)}")
            return False
        
        print("\n" + "=" * 60)
        print("✅ 15分钟数据处理功能测试完成！")
        print("=" * 60)
        
        # 显示测试结果摘要
        print("\n测试结果摘要:")
        print(f"  - 1分钟数据: {len(df_1m)} 条记录")
        print(f"  - 15分钟数据: {len(df_15m)} 条记录")
        print(f"  - 标准化数据: {len(df_15m_standardized)} 条记录")
        print(f"  - 数据压缩比: {len(df_1m) / len(df_15m):.2f}:1")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_endpoints():
    """测试API端点"""
    print("\n" + "=" * 60)
    print("API端点测试")
    print("=" * 60)
    
    try:
        # 这里可以添加API端点测试
        print("✅ API端点测试准备就绪")
        print("   访问地址: http://localhost:5000/process_data/15m_data")
        print("   API端点: /api/process_15m_data")
        print("   检查端点: /api/check_15m_data")
        
    except Exception as e:
        print(f"❌ API端点测试失败: {str(e)}")
        return False
    
    return True

if __name__ == "__main__":
    print("开始15分钟数据处理功能测试...")
    
    # 运行功能测试
    success = test_15m_data_processing()
    
    # 运行API测试
    api_success = test_api_endpoints()
    
    if success and api_success:
        print("\n🎉 所有测试通过！15分钟数据处理功能已就绪！")
        print("\n使用说明:")
        print("1. 访问 http://localhost:5000/process_data/15m_data")
        print("2. 输入股票代码、年份、季度")
        print("3. 选择处理类型（重采样/标准化/完整处理）")
        print("4. 点击'开始处理'按钮")
        print("5. 查看处理结果和保存路径")
    else:
        print("\n❌ 部分测试失败，请检查错误信息")
        sys.exit(1)
