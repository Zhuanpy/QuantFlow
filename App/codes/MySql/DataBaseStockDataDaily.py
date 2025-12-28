# -*- coding: utf-8 -*-
"""
股票日线数据访问层
提供从数据库或文件加载日线数据的功能
"""
import pandas as pd
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试导入 Flask 相关模块（如果可用）
try:
    from App.models.data.StockDaily import get_daily_stock_data, save_daily_stock_data_to_sql
    from App.codes.RnnDataFile.stock_path import StockDataPath
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.warning("Flask 模块不可用，DataBaseStockDataDaily 功能可能受限")


class StockDataDaily:
    """
    股票日线数据访问类
    提供从数据库或CSV文件加载数据的功能
    """
    
    @staticmethod
    def load_daily(stock_code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        加载股票日线数据
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期（可选，格式：'YYYY-MM-DD'）
            end_date: 结束日期（可选，格式：'YYYY-MM-DD'）
            
        Returns:
            pd.DataFrame: 包含日线数据的DataFrame，包含列：date, open, close, high, low, volume, money
        """
        # 优先尝试从数据库加载
        if FLASK_AVAILABLE:
            try:
                from App.exts import db
                with db.session.begin():
                    data = get_daily_stock_data(stock_code, start_date, end_date)
                    if not data.empty:
                        logger.info(f"成功从数据库加载股票 {stock_code} 日线数据，共 {len(data)} 条记录")
                        return data
            except Exception as e:
                logger.warning(f"从数据库加载失败，尝试从文件加载: {e}")
        
        # 如果数据库加载失败，尝试从CSV文件加载
        try:
            if FLASK_AVAILABLE:
                file_path = StockDataPath.get_stock_data_directory() / 'daily' / f'{stock_code}.csv'
            else:
                from config import Config
                project_root = Path(Config.get_project_root())
                file_path = project_root / 'data' / 'data' / 'daily' / f'{stock_code}.csv'
            
            if file_path.exists():
                data = pd.read_csv(file_path, parse_dates=['date'])
                
                # 如果指定了日期范围，进行过滤
                if start_date:
                    data = data[data['date'] >= pd.to_datetime(start_date)]
                if end_date:
                    data = data[data['date'] <= pd.to_datetime(end_date)]
                
                logger.info(f"成功从文件加载股票 {stock_code} 日线数据，共 {len(data)} 条记录")
                return data
            else:
                logger.warning(f"文件不存在: {file_path}")
        except Exception as e:
            logger.error(f"从文件加载失败: {e}")
        
        logger.error(f"无法加载股票 {stock_code} 日线数据")
        return pd.DataFrame()
    
    @staticmethod
    def save_daily(stock_code: str, data: pd.DataFrame) -> bool:
        """
        保存股票日线数据到数据库
        
        Args:
            stock_code: 股票代码
            data: 日线数据DataFrame
            
        Returns:
            bool: 保存是否成功
        """
        if not FLASK_AVAILABLE:
            logger.error("Flask 模块不可用，无法保存数据")
            return False
        
        try:
            from App.exts import db
            with db.session.begin():
                success = save_daily_stock_data_to_sql(stock_code, data)
                if success:
                    logger.info(f"成功保存股票 {stock_code} 日线数据到数据库，共 {len(data)} 条记录")
                return success
        except Exception as e:
            logger.error(f"保存股票 {stock_code} 日线数据失败: {e}")
            return False


if __name__ == '__main__':
    # 测试代码
    data = StockDataDaily.load_daily('000001')
    print(f"加载数据: {len(data)} 条记录")
    print(data.head() if not data.empty else "数据为空")

