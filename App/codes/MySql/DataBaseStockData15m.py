# -*- coding: utf-8 -*-
"""
股票15分钟数据访问层
提供从数据库或文件加载15分钟数据的功能
"""
import pandas as pd
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试导入 Flask 相关模块（如果可用）
try:
    from App.models.data.Stock15m import load_15m_stock_data_from_sql, save_15m_stock_data_to_sql, get_15m_stock_data
    from App.codes.RnnDataFile.stock_path import StockDataPath
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.warning("Flask 模块不可用，DataBaseStockData15m 功能可能受限")


class StockData15m:
    """
    股票15分钟数据访问类
    提供从数据库或CSV文件加载数据的功能
    """
    
    @staticmethod
    def load_15m(stock_code: str) -> pd.DataFrame:
        """
        加载股票15分钟数据
        
        Args:
            stock_code: 股票代码
            
        Returns:
            pd.DataFrame: 包含15分钟数据的DataFrame，包含列：date, open, close, high, low, volume, money
        """
        # 优先尝试从数据库加载
        if FLASK_AVAILABLE:
            try:
                from App.exts import db
                with db.session.begin():
                    data = load_15m_stock_data_from_sql(stock_code)
                    if not data.empty:
                        logger.info(f"成功从数据库加载股票 {stock_code} 15分钟数据，共 {len(data)} 条记录")
                        return data
            except Exception as e:
                logger.warning(f"从数据库加载失败，尝试从文件加载: {e}")
        
        # 如果数据库加载失败，尝试从CSV文件加载
        try:
            if FLASK_AVAILABLE:
                file_path = StockDataPath.get_stock_data_directory() / '15m' / f'{stock_code}.csv'
            else:
                from config import Config
                project_root = Path(Config.get_project_root())
                file_path = project_root / 'data' / 'data' / '15m' / f'{stock_code}.csv'
            
            if file_path.exists():
                data = pd.read_csv(file_path, parse_dates=['date'])
                logger.info(f"成功从文件加载股票 {stock_code} 15分钟数据，共 {len(data)} 条记录")
                return data
            else:
                logger.warning(f"文件不存在: {file_path}")
        except Exception as e:
            logger.error(f"从文件加载失败: {e}")
        
        logger.error(f"无法加载股票 {stock_code} 15分钟数据")
        return pd.DataFrame()
    
    @staticmethod
    def save_15m(stock_code: str, data: pd.DataFrame) -> bool:
        """
        保存股票15分钟数据到数据库
        
        Args:
            stock_code: 股票代码
            data: 15分钟数据DataFrame
            
        Returns:
            bool: 保存是否成功
        """
        if not FLASK_AVAILABLE:
            logger.error("Flask 模块不可用，无法保存数据")
            return False
        
        try:
            from App.exts import db
            with db.session.begin():
                success = save_15m_stock_data_to_sql(stock_code, data)
                if success:
                    logger.info(f"成功保存股票 {stock_code} 15分钟数据到数据库，共 {len(data)} 条记录")
                return success
        except Exception as e:
            logger.error(f"保存股票 {stock_code} 15分钟数据失败: {e}")
            return False
    
    @staticmethod
    def replace_15m(stock_code: str, data: pd.DataFrame) -> bool:
        """
        替换股票15分钟数据（先删除旧数据，再插入新数据）
        
        Args:
            stock_code: 股票代码
            data: 15分钟数据DataFrame
            
        Returns:
            bool: 替换是否成功
        """
        # 先删除旧数据，再保存新数据
        if StockData15m.delete_15m(stock_code):
            return StockData15m.save_15m(stock_code, data)
        return False
    
    @staticmethod
    def append_15m(stock_code: str, data: pd.DataFrame) -> bool:
        """
        追加股票15分钟数据（不删除旧数据，直接插入新数据）
        
        Args:
            stock_code: 股票代码
            data: 15分钟数据DataFrame
            
        Returns:
            bool: 追加是否成功
        """
        return StockData15m.save_15m(stock_code, data)
    
    @staticmethod
    def delete_15m(stock_code: str) -> bool:
        """
        删除股票15分钟数据
        
        Args:
            stock_code: 股票代码
            
        Returns:
            bool: 删除是否成功
        """
        if not FLASK_AVAILABLE:
            logger.error("Flask 模块不可用，无法删除数据")
            return False
        
        try:
            from App.exts import db
            from App.models.data.Stock15m import create_15m_stock_model
            
            StockModel = create_15m_stock_model(stock_code)
            with db.session.begin():
                StockModel.query.delete()
                db.session.commit()
            logger.info(f"成功删除股票 {stock_code} 15分钟数据")
            return True
        except Exception as e:
            logger.error(f"删除股票 {stock_code} 15分钟数据失败: {e}")
            return False


if __name__ == '__main__':
    # 测试代码
    data = StockData15m.load_15m('000001')
    print(f"加载数据: {len(data)} 条记录")
    print(data.head() if not data.empty else "数据为空")

