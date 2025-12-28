# -*- coding: utf-8 -*-
"""
股票数据统计管理模块
提供股票数据统计信息的更新和查询功能
"""
import os
import pandas as pd
import pymysql
import logging
from typing import Optional, Dict
from pathlib import Path
from config import Config
from App.codes.RnnDataFile.stock_path import StockDataPath, file_root

logger = logging.getLogger(__name__)

# 尝试导入 Flask 相关模块（如果可用）
try:
    from flask import current_app
    from App.exts import db
    from App.models.data.basic_info import StockCodes
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.warning("Flask 模块不可用，股票名称获取功能可能受限")


class StockStatsManager:
    """股票数据统计管理类"""
    
    TABLE_NAME = 'stock_data_stats'
    
    def __init__(self, database: Optional[str] = None):
        """
        初始化数据库连接
        
        Args:
            database: 数据库名称，如果为None则使用 Config.DB_NAME
        """
        self.database = database or Config.DB_NAME
        self.conn = None
        self._get_db_config()
        self.connect()
    
    def _get_db_config(self) -> dict:
        """获取数据库配置"""
        return {
            'host': Config.DB_HOST,
            'port': int(Config.DB_PORT),
            'user': Config.DB_USER,
            'password': Config.DB_PASSWORD,
            'database': self.database,
            'charset': 'utf8mb4'
        }
    
    def connect(self):
        """建立数据库连接"""
        try:
            self.conn = pymysql.connect(**self._get_db_config())
            logger.info(f"数据库连接成功: {self.database}")
        except pymysql.err.OperationalError as e:
            logger.error(f"数据库连接失败: {e}")
            raise
    
    def _get_stock_name(self, stock_code: str) -> str:
        """
        获取股票名称
        
        Args:
            stock_code: 股票代码
            
        Returns:
            str: 股票名称，如果找不到则返回股票代码
        """
        if FLASK_AVAILABLE:
            try:
                with current_app.app_context():
                    stock = StockCodes.query.filter_by(code=stock_code).first()
                    if stock and stock.name:
                        return stock.name
            except Exception as e:
                logger.warning(f"从数据库获取股票名称失败 {stock_code}: {e}")
        
        # 如果无法从数据库获取，返回代码本身
        return stock_code
    
    def update_stock_stats(self, data_dir: Optional[str] = None):
        """
        更新股票数据统计信息
        
        Args:
            data_dir: 数据目录路径，如果为None则使用默认路径
        """
        if not self.conn or not self.conn.is_connected():
            self.connect()
        
        # 使用默认数据目录或传入的目录
        if data_dir is None:
            data_dir = StockDataPath.get_stock_data_directory()
        
        data_dir_path = Path(data_dir)
        if not data_dir_path.exists():
            logger.error(f"数据目录不存在: {data_dir}")
            return
        
        cursor = self.conn.cursor()
        try:
            # 支持多种目录结构：
            # 1. data/data/1m/ (直接按类型存储)
            # 2. data/data/years/2024/1m/ (按年份和类型存储)
            # 3. data/data/quarters/2024/Q1/ (按季度存储)
            
            processed_count = 0
            
            # 方式1: 直接按类型存储 (data/data/1m/, data/data/15m/, data/data/daily/)
            for data_type_dir in ['1m', '15m', 'daily']:
                type_path = data_dir_path / data_type_dir
                if type_path.exists() and type_path.is_dir():
                    processed_count += self._process_data_type_directory(
                        cursor, type_path, data_type_dir
                    )
            
            # 方式2: 按年份和类型存储 (data/data/years/2024/1m/)
            years_path = data_dir_path / 'years'
            if years_path.exists() and years_path.is_dir():
                for year_dir in years_path.iterdir():
                    if year_dir.is_dir():
                        for data_type_dir in ['1m', '15m', 'daily']:
                            type_path = year_dir / data_type_dir
                            if type_path.exists() and type_path.is_dir():
                                processed_count += self._process_data_type_directory(
                                    cursor, type_path, data_type_dir
                                )
            
            # 方式3: 按季度存储 (data/data/quarters/2024/Q1/)
            quarters_path = data_dir_path / 'quarters'
            if quarters_path.exists() and quarters_path.is_dir():
                for year_dir in quarters_path.iterdir():
                    if year_dir.is_dir():
                        for quarter_dir in year_dir.iterdir():
                            if quarter_dir.is_dir():
                                processed_count += self._process_data_type_directory(
                                    cursor, quarter_dir, '1m'  # 季度目录通常存储1分钟数据
                                )
            
            self.conn.commit()
            logger.info(f"统计数据更新完成，共处理 {processed_count} 个文件")
        
        except Exception as e:
            logger.error(f"更新统计数据时出错: {e}", exc_info=True)
            if self.conn:
                self.conn.rollback()
        finally:
            cursor.close()
    
    def _process_data_type_directory(self, cursor, type_path: Path, data_type: str) -> int:
        """
        处理指定类型的数据目录
        
        Args:
            cursor: 数据库游标
            type_path: 数据目录路径
            data_type: 数据类型 (1m, 15m, daily)
            
        Returns:
            int: 处理的文件数量
        """
        processed_count = 0
        
        for file_path in type_path.glob('*.csv'):
            try:
                stock_code = file_path.stem  # 获取文件名（不含扩展名）
                
                # 读取CSV文件获取行数
                df = pd.read_csv(file_path)
                row_count = len(df)
                
                # 获取股票名称
                stock_name = self._get_stock_name(stock_code)
                
                # 更新数据库
                sql = """
                INSERT INTO {} 
                (stock_code, stock_name, data_type, data_exists, row_count)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                data_exists = VALUES(data_exists),
                row_count = VALUES(row_count),
                last_update = CURRENT_TIMESTAMP
                """.format(self.TABLE_NAME)
                
                cursor.execute(sql, (stock_code, stock_name, data_type, True, row_count))
                processed_count += 1
                
            except Exception as e:
                logger.error(f"处理文件 {file_path} 时出错: {e}")
        
        return processed_count
    
    def get_stats(self, stock_code: Optional[str] = None, data_type: Optional[str] = None) -> pd.DataFrame:
        """
        获取统计数据
        
        Args:
            stock_code: 股票代码，如果指定则只查询该股票
            data_type: 数据类型，如果指定则只查询该类型
            
        Returns:
            pd.DataFrame: 统计数据
        """
        if not self.conn or not self.conn.is_connected():
            self.connect()
        
        try:
            query = f"SELECT * FROM {self.TABLE_NAME}"
            conditions = []
            params = []
            
            if stock_code:
                conditions.append("stock_code = %s")
                params.append(stock_code)
            
            if data_type:
                conditions.append("data_type = %s")
                params.append(data_type)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY last_update DESC"
            
            df = pd.read_sql(query, self.conn, params=params if params else None)
            logger.info(f"成功获取统计数据: {len(df)} 条记录")
            return df
            
        except Exception as e:
            logger.error(f"获取统计数据时出错: {e}")
            raise
    
    def close(self):
        """关闭数据库连接"""
        if self.conn and self.conn.open:
            self.conn.close()
            logger.info("数据库连接已关闭")
    
    def __enter__(self):
        """上下文管理器入口"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()

if __name__ == "__main__":
    # 使用示例
    # 方式1: 使用上下文管理器（推荐）
    with StockStatsManager() as stats_manager:
        # 更新统计数据（使用默认数据目录）
        stats_manager.update_stock_stats()
        
        # 获取所有统计数据
        stats_df = stats_manager.get_stats()
        print(stats_df)
        
        # 获取特定股票的统计数据
        stock_stats = stats_manager.get_stats(stock_code='000001')
        print(stock_stats)
        
        # 获取特定类型的数据统计
        type_stats = stats_manager.get_stats(data_type='1m')
        print(type_stats)
    
    # 方式2: 手动管理连接
    # stats_manager = StockStatsManager()
    # try:
    #     stats_manager.update_stock_stats()
    #     stats_df = stats_manager.get_stats()
    #     print(stats_df)
    # finally:
    #     stats_manager.close() 