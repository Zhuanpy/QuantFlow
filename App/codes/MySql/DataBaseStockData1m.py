# -*- coding: utf-8 -*-
"""
股票1分钟数据访问层
提供从数据库或文件加载1分钟数据的功能
"""
import pandas as pd
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 尝试导入 Flask 相关模块（如果可用）
try:
    from App.models.data.Stock1m import get_1m_stock_data
    from App.codes.RnnDataFile.stock_path import StockDataPath
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.warning("Flask 模块不可用，DataBaseStockData1m 功能可能受限")


class StockData1m:
    """
    股票1分钟数据访问类
    提供从数据库或CSV文件加载数据的功能
    """

    # 股票代码格式：6位数字（如 000001, 600000）
    STOCK_CODE_PATTERN = re.compile(r'^\d{6}$')

    @classmethod
    def _validate_stock_code(cls, stock_code: str) -> bool:
        """验证股票代码格式"""
        return bool(cls.STOCK_CODE_PATTERN.match(stock_code))

    @staticmethod
    def load_1m(stock_code: str, year: str) -> pd.DataFrame:
        """
        加载股票1分钟数据
        
        Args:
            stock_code: 股票代码
            year: 年份（字符串，如 "2024"）
            
        Returns:
            pd.DataFrame: 包含1分钟数据的DataFrame，包含列：date, open, close, high, low, volume, money
        """
        # 验证股票代码格式
        if not StockData1m._validate_stock_code(stock_code):
            logger.error(f"无效的股票代码格式: {stock_code}")
            return pd.DataFrame()

        try:
            year_int = int(year)
        except ValueError:
            logger.error(f"无效的年份格式: {year}")
            return pd.DataFrame()

        # 优先尝试从数据库加载
        if FLASK_AVAILABLE:
            try:
                data = get_1m_stock_data(stock_code, year_int)
                if not data.empty:
                    logger.info(f"成功从数据库加载股票 {stock_code} {year}年1分钟数据，共 {len(data)} 条记录")
                    return data
            except Exception as e:
                logger.warning(f"从数据库加载失败，尝试从文件加载: {e}")
        
        # 如果数据库加载失败，尝试从CSV文件加载
        try:
            if FLASK_AVAILABLE:
                # 使用 StockDataPath 获取文件路径
                file_path = StockDataPath.get_stock_data_directory() / '1m' / f'{stock_code}.csv'
            else:
                # 如果没有 Flask，尝试从默认路径加载
                from config import Config
                project_root = Path(Config.get_project_root())
                file_path = project_root / 'data' / 'data' / '1m' / f'{stock_code}.csv'
            
            if file_path.exists():
                data = pd.read_csv(file_path, parse_dates=['date'])
                # 过滤指定年份的数据
                data = data[data['date'].dt.year == year_int]
                logger.info(f"成功从文件加载股票 {stock_code} {year}年1分钟数据，共 {len(data)} 条记录")
                return data
            else:
                logger.warning(f"文件不存在: {file_path}")
        except Exception as e:
            logger.error(f"从文件加载失败: {e}")
        
        logger.error(f"无法加载股票 {stock_code} {year}年1分钟数据")
        return pd.DataFrame()
    
    @staticmethod
    def load_1m_by_date_range(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        按日期范围加载股票1分钟数据
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            pd.DataFrame: 包含1分钟数据的DataFrame
        """
        try:
            start_year = pd.to_datetime(start_date).year
            end_year = pd.to_datetime(end_date).year
            
            all_data = []
            for year in range(start_year, end_year + 1):
                year_data = StockData1m.load_1m(stock_code, str(year))
                if not year_data.empty:
                    all_data.append(year_data)
            
            if not all_data:
                return pd.DataFrame()
            
            # 合并所有年份的数据
            combined_data = pd.concat(all_data, ignore_index=True)
            
            # 过滤日期范围
            combined_data = combined_data[
                (combined_data['date'] >= pd.to_datetime(start_date)) &
                (combined_data['date'] <= pd.to_datetime(end_date))
            ]
            
            logger.info(f"成功加载股票 {stock_code} {start_date} 至 {end_date} 的1分钟数据，共 {len(combined_data)} 条记录")
            return combined_data.sort_values('date').reset_index(drop=True)
            
        except Exception as e:
            logger.error(f"按日期范围加载数据失败: {e}")
            return pd.DataFrame()


if __name__ == '__main__':
    # 测试代码
    data = StockData1m.load_1m('000001', '2024')
    print(f"加载数据: {len(data)} 条记录")
    print(data.head() if not data.empty else "数据为空")

