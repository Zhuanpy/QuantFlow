#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
季度数据处理模块
用于按照季度处理1分钟数据，生成训练数据
"""
import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed
import json

from App.codes.utils.Normal import ResampleData
from App.codes.Signals.StatisticsMacd import SignalMethod
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.models.strategy import RnnTrainingRecords
from App.exts import db

logger = logging.getLogger(__name__)

class QuarterlyDataProcessor:
    """季度数据处理器"""
    
    def __init__(self, base_data_path: str = None):
        """
        初始化季度数据处理器
        
        Args:
            base_data_path: 数据基础路径，默认为项目数据目录
        """
        if base_data_path is None:
            self.base_data_path = Path(StockDataPath.get_stock_data_directory())
        else:
            self.base_data_path = Path(base_data_path)
        
        # 季度映射
        self.quarter_months = {
            1: [1, 2, 3],    # Q1: 1-3月
            2: [4, 5, 6],    # Q2: 4-6月
            3: [7, 8, 9],    # Q3: 7-9月
            4: [10, 11, 12]  # Q4: 10-12月
        }
    
    def get_quarter_info(self, date: datetime) -> Tuple[int, int, str]:
        """
        获取日期的季度信息
        
        Args:
            date: 日期
            
        Returns:
            (year, quarter, quarter_str): 年份、季度、季度字符串
        """
        year = date.year
        month = date.month
        
        for quarter, months in self.quarter_months.items():
            if month in months:
                return year, quarter, f"Q{quarter}"
        
        raise ValueError(f"无法确定月份 {month} 的季度")
    
    def get_quarter_dates(self, year: int, quarter: int) -> Tuple[datetime, datetime]:
        """
        获取季度的开始和结束日期
        
        Args:
            year: 年份
            quarter: 季度 (1-4)
            
        Returns:
            (start_date, end_date): 季度开始和结束日期
        """
        months = self.quarter_months[quarter]
        start_date = datetime(year, months[0], 1)
        
        # 计算结束日期
        if quarter == 4:
            end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            next_quarter = quarter + 1
            next_months = self.quarter_months[next_quarter]
            end_date = datetime(year, next_months[0], 1) - timedelta(days=1)
        
        return start_date, end_date
    
    def collect_quarterly_1m_data(self, year: int, quarter: int, stock_codes: List[str] = None) -> Dict[str, pd.DataFrame]:
        """
        收集指定季度的1分钟数据
        
        Args:
            year: 年份
            quarter: 季度
            stock_codes: 股票代码列表，None表示处理所有股票
            
        Returns:
            Dict[str, pd.DataFrame]: 股票代码到数据的映射
        """
        logger.info(f"开始收集 {year}年Q{quarter} 季度的1分钟数据")
        
        # 构建季度数据路径
        quarter_path = self.base_data_path / "quarters" / f"{year}Q{quarter}"
        
        if not quarter_path.exists():
            logger.warning(f"季度数据目录不存在: {quarter_path}")
            return {}
        
        quarterly_data = {}
        
        # 获取所有CSV文件
        csv_files = list(quarter_path.glob("*.csv"))
        
        if stock_codes:
            # 只处理指定的股票
            csv_files = [f for f in csv_files if f.stem in stock_codes]
        
        logger.info(f"找到 {len(csv_files)} 个股票数据文件")
        
        for csv_file in csv_files:
            try:
                stock_code = csv_file.stem
                df = pd.read_csv(csv_file)
                
                if df.empty:
                    logger.warning(f"股票 {stock_code} 的数据为空")
                    continue
                
                # 确保日期列格式正确
                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'])
                    quarterly_data[stock_code] = df
                    logger.info(f"成功加载股票 {stock_code} 数据: {len(df)} 条记录")
                else:
                    logger.warning(f"股票 {stock_code} 数据缺少date列")
                    
            except Exception as e:
                logger.error(f"加载股票 {csv_file.stem} 数据失败: {e}")
                continue
        
        logger.info(f"季度数据收集完成，共 {len(quarterly_data)} 只股票")
        return quarterly_data
    
    def process_quarterly_15m_data(self, quarterly_data: Dict[str, pd.DataFrame], year: int, quarter: int) -> Dict[str, pd.DataFrame]:
        """
        处理季度数据为15分钟数据
        
        Args:
            quarterly_data: 季度1分钟数据
            year: 年份
            quarter: 季度
            
        Returns:
            Dict[str, pd.DataFrame]: 处理后的15分钟数据
        """
        logger.info(f"开始处理 {year}年Q{quarter} 季度的15分钟数据")
        
        processed_data = {}
        
        for stock_code, df_1m in quarterly_data.items():
            try:
                # 使用ResampleData转换为15分钟数据
                df_15m = ResampleData.resample_1m_data(df_1m, '15m')
                
                if not df_15m.empty:
                    processed_data[stock_code] = df_15m
                    logger.info(f"成功处理股票 {stock_code} 的15分钟数据: {len(df_15m)} 条记录")
                else:
                    logger.warning(f"股票 {stock_code} 的15分钟数据为空")
                    
            except Exception as e:
                logger.error(f"处理股票 {stock_code} 的15分钟数据失败: {e}")
                continue
        
        logger.info(f"15分钟数据处理完成，共 {len(processed_data)} 只股票")
        return processed_data
    
    def generate_training_features(self, df_15m: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        生成训练特征
        
        Args:
            df_15m: 15分钟数据
            stock_code: 股票代码
            
        Returns:
            pd.DataFrame: 包含特征的训练数据
        """
        try:
            # 确保数据按时间排序
            df_15m = df_15m.sort_values('date').reset_index(drop=True)
            
            # 计算技术指标
            df_features = df_15m.copy()
            
            # 计算MACD信号
            signal_method = SignalMethod()
            macd_signals = signal_method.calculate_macd_signals(df_15m)
            
            if not macd_signals.empty:
                # 合并MACD信号
                df_features = pd.merge(df_features, macd_signals, on='date', how='left')
            
            # 计算价格变化率
            df_features['price_change'] = df_features['close'].pct_change()
            df_features['price_change_5'] = df_features['close'].pct_change(5)
            df_features['price_change_10'] = df_features['close'].pct_change(10)
            
            # 计算成交量变化率
            df_features['volume_change'] = df_features['volume'].pct_change()
            df_features['volume_ma_5'] = df_features['volume'].rolling(5).mean()
            df_features['volume_ma_10'] = df_features['volume'].rolling(10).mean()
            
            # 计算价格位置（相对于最高最低价）
            df_features['high_low_ratio'] = (df_features['close'] - df_features['low']) / (df_features['high'] - df_features['low'])
            df_features['high_low_ratio'] = df_features['high_low_ratio'].fillna(0.5)
            
            # 计算波动率
            df_features['volatility'] = df_features['price_change'].rolling(20).std()
            
            # 添加股票代码
            df_features['stock_code'] = stock_code
            
            # 删除包含NaN的行
            df_features = df_features.dropna()
            
            return df_features
            
        except Exception as e:
            logger.error(f"生成股票 {stock_code} 的训练特征失败: {e}")
            return pd.DataFrame()
    
    def save_quarterly_training_data(self, training_data: Dict[str, pd.DataFrame], year: int, quarter: int) -> bool:
        """
        保存季度训练数据
        
        Args:
            training_data: 训练数据
            year: 年份
            quarter: 季度
            
        Returns:
            bool: 保存是否成功
        """
        try:
            # 构建保存路径
            save_path = self.base_data_path / "RnnData" / f"{year}-{quarter:02d}" / "train_data"
            save_path.mkdir(parents=True, exist_ok=True)
            
            # 保存每个股票的训练数据
            for stock_code, df in training_data.items():
                if not df.empty:
                    file_path = save_path / f"{stock_code}_training.csv"
                    df.to_csv(file_path, index=False)
                    logger.info(f"保存股票 {stock_code} 训练数据到: {file_path}")
            
            # 保存季度汇总信息
            summary = {
                'year': year,
                'quarter': quarter,
                'processed_stocks': len(training_data),
                'total_records': sum(len(df) for df in training_data.values()),
                'processed_time': datetime.now().isoformat(),
                'stocks': list(training_data.keys())
            }
            
            summary_path = save_path / "quarterly_summary.json"
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            
            logger.info(f"季度训练数据保存完成: {summary_path}")
            return True
            
        except Exception as e:
            logger.error(f"保存季度训练数据失败: {e}")
            return False
    
    def update_training_records(self, year: int, quarter: int, stock_codes: List[str], status: str = 'success') -> bool:
        """
        更新训练记录状态
        
        Args:
            year: 年份
            quarter: 季度
            stock_codes: 股票代码列表
            status: 处理状态
            
        Returns:
            bool: 更新是否成功
        """
        try:
            quarter_str = f"{year}Q{quarter}"
            
            for stock_code in stock_codes:
                # 查找或创建训练记录
                record = RnnTrainingRecords.query.filter_by(
                    code=stock_code,
                    parser_month=quarter_str
                ).first()
                
                if not record:
                    record = RnnTrainingRecords(
                        code=stock_code,
                        name=f"股票{stock_code}",
                        parser_month=quarter_str,
                        starting_date=datetime.now()
                    )
                    db.session.add(record)
                
                # 更新状态
                if status == 'success':
                    record.model_data = 'processed'
                    record.model_data_timing = datetime.now()
                    record.model_check = RnnTrainingRecords.STATUS_SUCCESS
                else:
                    record.model_check = RnnTrainingRecords.STATUS_FAILED
                    record.model_error = f"季度 {quarter_str} 处理失败"
                
                record.updated_at = datetime.now()
            
            db.session.commit()
            logger.info(f"更新训练记录完成: {len(stock_codes)} 只股票")
            return True
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新训练记录失败: {e}")
            return False
    
    def process_quarter(self, year: int, quarter: int, stock_codes: List[str] = None) -> bool:
        """
        处理整个季度的数据
        
        Args:
            year: 年份
            quarter: 季度
            stock_codes: 股票代码列表，None表示处理所有股票
            
        Returns:
            bool: 处理是否成功
        """
        try:
            logger.info(f"开始处理 {year}年Q{quarter} 季度的数据")
            
            # 1. 收集季度1分钟数据
            quarterly_data = self.collect_quarterly_1m_data(year, quarter, stock_codes)
            
            if not quarterly_data:
                logger.warning(f"没有找到 {year}年Q{quarter} 季度的数据")
                return False
            
            # 2. 处理为15分钟数据
            processed_15m = self.process_quarterly_15m_data(quarterly_data, year, quarter)
            
            if not processed_15m:
                logger.warning(f"15分钟数据处理失败")
                return False
            
            # 3. 生成训练特征
            training_data = {}
            for stock_code, df_15m in processed_15m.items():
                features = self.generate_training_features(df_15m, stock_code)
                if not features.empty:
                    training_data[stock_code] = features
            
            if not training_data:
                logger.warning(f"训练特征生成失败")
                return False
            
            # 4. 保存训练数据
            save_success = self.save_quarterly_training_data(training_data, year, quarter)
            
            if not save_success:
                logger.error(f"训练数据保存失败")
                return False
            
            # 5. 更新训练记录
            record_success = self.update_training_records(
                year, quarter, list(training_data.keys()), 'success'
            )
            
            if record_success:
                logger.info(f"{year}年Q{quarter} 季度数据处理完成")
                return True
            else:
                logger.error(f"训练记录更新失败")
                return False
                
        except Exception as e:
            logger.error(f"处理 {year}年Q{quarter} 季度数据失败: {e}")
            return False
    
    def process_multiple_quarters(self, year: int, quarters: List[int] = None, stock_codes: List[str] = None) -> Dict[str, bool]:
        """
        处理多个季度的数据
        
        Args:
            year: 年份
            quarters: 季度列表，None表示处理所有季度
            stock_codes: 股票代码列表
            
        Returns:
            Dict[str, bool]: 每个季度的处理结果
        """
        if quarters is None:
            quarters = [1, 2, 3, 4]
        
        results = {}
        
        for quarter in quarters:
            quarter_key = f"{year}Q{quarter}"
            logger.info(f"开始处理季度: {quarter_key}")
            
            success = self.process_quarter(year, quarter, stock_codes)
            results[quarter_key] = success
            
            if success:
                logger.info(f"季度 {quarter_key} 处理成功")
            else:
                logger.error(f"季度 {quarter_key} 处理失败")
        
        return results

# 使用示例
if __name__ == "__main__":
    # 初始化处理器
    processor = QuarterlyDataProcessor()
    
    # 处理2024年所有季度的数据
    results = processor.process_multiple_quarters(2024)
    
    # 打印结果
    for quarter, success in results.items():
        print(f"{quarter}: {'成功' if success else '失败'}")



