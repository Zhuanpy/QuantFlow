# -*- coding: utf-8 -*-
"""
股票池数据库访问模块
提供对 record_stock_pool 表的访问接口
"""
import os
import pandas as pd
import pymysql
import logging
from typing import Optional, Tuple, Any
from config import Config

logger = logging.getLogger(__name__)


class TableStockPool:
    """股票池表操作类"""
    
    # 表名
    TABLE_NAME = 'record_stock_pool'
    
    # 数据库配置（从 config.py 读取）
    # 注意：record_stock_pool 表可能在 mystockrecord 数据库中
    # 可以通过环境变量 STOCK_POOL_DB 指定，默认为 mystockrecord
    STOCK_POOL_DB = os.getenv('STOCK_POOL_DB', 'mystockrecord')
    
    @classmethod
    def _get_db_config(cls, database: Optional[str] = None) -> dict:
        """
        获取数据库配置
        
        Args:
            database: 数据库名称，如果为None则使用 STOCK_POOL_DB 或 Config.DB_NAME
        """
        # 优先使用传入的数据库名，其次使用环境变量，最后使用配置的数据库名
        db_name = database or cls.STOCK_POOL_DB or Config.DB_NAME
        
        return {
            'host': Config.DB_HOST,
            'port': int(Config.DB_PORT),
            'user': Config.DB_USER,
            'password': Config.DB_PASSWORD,
            'database': db_name,
            'charset': 'utf8mb4'
        }
    
    @classmethod
    def _get_connection(cls, database: Optional[str] = None):
        """
        获取数据库连接
        
        Args:
            database: 数据库名称，如果为None则使用默认配置
        """
        try:
            config = cls._get_db_config(database)
            connection = pymysql.connect(**config)
            return connection
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            raise
    
    @classmethod
    def load_StockPool(cls) -> pd.DataFrame:
        """
        加载股票池数据
        
        Returns:
            pd.DataFrame: 股票池数据，包含所有列
        """
        try:
            connection = cls._get_connection()
            
            # 查询所有数据
            query = f"SELECT * FROM {cls.TABLE_NAME}"
            df = pd.read_sql(query, connection)
            
            connection.close()
            
            if df.empty:
                logger.warning("股票池数据为空")
            else:
                logger.info(f"成功加载股票池数据: {len(df)} 条记录")
            
            return df
            
        except Exception as e:
            logger.error(f"加载股票池数据失败: {e}")
            raise
    
    @classmethod
    def set_table_to_pool(cls, sql: str, params: Optional[Tuple] = None) -> bool:
        """
        更新股票池表数据
        
        Args:
            sql: SQL更新语句（不包含UPDATE关键字，只包含SET和WHERE部分）
            params: SQL参数元组
            
        Returns:
            bool: 操作是否成功
            
        Example:
            sql = "Position = 1, TradeMethod = 1, PositionNum = %s where id = %s"
            params = (100, 123)
        """
        try:
            connection = cls._get_connection()
            cursor = connection.cursor()
            
            # 构建完整的UPDATE语句
            update_sql = f"UPDATE {cls.TABLE_NAME} SET {sql}"
            
            if params:
                cursor.execute(update_sql, params)
            else:
                cursor.execute(update_sql)
            
            connection.commit()
            affected_rows = cursor.rowcount
            
            cursor.close()
            connection.close()
            
            logger.info(f"更新股票池数据成功，影响行数: {affected_rows}")
            return True
            
        except Exception as e:
            logger.error(f"更新股票池数据失败: {e}")
            if 'connection' in locals():
                try:
                    connection.rollback()
                    connection.close()
                except:
                    pass
            raise
    
    @classmethod
    def query_pool(cls, where_clause: str = "", params: Optional[Tuple] = None) -> pd.DataFrame:
        """
        查询股票池数据
        
        Args:
            where_clause: WHERE子句（不包含WHERE关键字）
            params: SQL参数元组
            
        Returns:
            pd.DataFrame: 查询结果
        """
        try:
            connection = cls._get_connection()
            
            if where_clause:
                query = f"SELECT * FROM {cls.TABLE_NAME} WHERE {where_clause}"
            else:
                query = f"SELECT * FROM {cls.TABLE_NAME}"
            
            if params:
                df = pd.read_sql(query, connection, params=params)
            else:
                df = pd.read_sql(query, connection)
            
            connection.close()
            
            return df
            
        except Exception as e:
            logger.error(f"查询股票池数据失败: {e}")
            raise
    
    @classmethod
    def insert_to_pool(cls, data: dict) -> int:
        """
        插入股票池数据
        
        Args:
            data: 要插入的数据字典
            
        Returns:
            int: 插入的记录ID
        """
        try:
            connection = cls._get_connection()
            cursor = connection.cursor()
            
            # 构建INSERT语句
            columns = ', '.join(data.keys())
            placeholders = ', '.join(['%s'] * len(data))
            insert_sql = f"INSERT INTO {cls.TABLE_NAME} ({columns}) VALUES ({placeholders})"
            
            cursor.execute(insert_sql, tuple(data.values()))
            connection.commit()
            
            record_id = cursor.lastrowid
            
            cursor.close()
            connection.close()
            
            logger.info(f"插入股票池数据成功，ID: {record_id}")
            return record_id
            
        except Exception as e:
            logger.error(f"插入股票池数据失败: {e}")
            if 'connection' in locals():
                try:
                    connection.rollback()
                    connection.close()
                except:
                    pass
            raise
    
    @classmethod
    def delete_from_pool(cls, where_clause: str, params: Optional[Tuple] = None) -> int:
        """
        删除股票池数据
        
        Args:
            where_clause: WHERE子句（不包含WHERE关键字）
            params: SQL参数元组
            
        Returns:
            int: 删除的记录数
        """
        try:
            connection = cls._get_connection()
            cursor = connection.cursor()
            
            delete_sql = f"DELETE FROM {cls.TABLE_NAME} WHERE {where_clause}"
            
            if params:
                cursor.execute(delete_sql, params)
            else:
                cursor.execute(delete_sql)
            
            connection.commit()
            affected_rows = cursor.rowcount
            
            cursor.close()
            connection.close()
            
            logger.info(f"删除股票池数据成功，影响行数: {affected_rows}")
            return affected_rows
            
        except Exception as e:
            logger.error(f"删除股票池数据失败: {e}")
            if 'connection' in locals():
                try:
                    connection.rollback()
                    connection.close()
                except:
                    pass
            raise


class TableStockBoard:
    """板块表操作类"""
    
    TABLE_NAME = 'stock_board'  # 假设表名，可能需要根据实际情况调整
    STOCK_POOL_DB = os.getenv('STOCK_POOL_DB', 'mystockrecord')
    
    @classmethod
    def _get_connection(cls, database: Optional[str] = None):
        """获取数据库连接"""
        try:
            config = TableStockPool._get_db_config(database or cls.STOCK_POOL_DB)
            connection = pymysql.connect(**config)
            return connection
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            raise
    
    @classmethod
    def load_board(cls) -> pd.DataFrame:
        """
        加载板块数据
        
        Returns:
            pd.DataFrame: 板块数据
        """
        try:
            connection = cls._get_connection()
            query = f"SELECT * FROM {cls.TABLE_NAME}"
            df = pd.read_sql(query, connection)
            connection.close()
            
            if df.empty:
                logger.warning("板块数据为空")
            else:
                logger.info(f"成功加载板块数据: {len(df)} 条记录")
            
            return df
        except Exception as e:
            logger.error(f"加载板块数据失败: {e}")
            return pd.DataFrame()
    
    @classmethod
    def set_table_to_board(cls, sql: str, params: Optional[Tuple] = None) -> bool:
        """
        更新板块表数据
        
        Args:
            sql: SQL更新语句（不包含UPDATE关键字，只包含SET和WHERE部分）
            params: SQL参数元组
            
        Returns:
            bool: 操作是否成功
        """
        try:
            connection = cls._get_connection()
            cursor = connection.cursor()
            
            update_sql = f"UPDATE {cls.TABLE_NAME} SET {sql}"
            
            if params:
                cursor.execute(update_sql, params)
            else:
                cursor.execute(update_sql)
            
            connection.commit()
            cursor.close()
            connection.close()
            
            logger.info(f"更新板块数据成功")
            return True
        except Exception as e:
            logger.error(f"更新板块数据失败: {e}")
            if 'connection' in locals():
                try:
                    connection.rollback()
                    connection.close()
                except:
                    pass
            return False


class TableStockPoolCount:
    """股票池统计表操作类"""
    
    TABLE_NAME = 'stock_pool_count'  # 假设表名，可能需要根据实际情况调整
    STOCK_POOL_DB = os.getenv('STOCK_POOL_DB', 'mystockrecord')
    
    @classmethod
    def _get_connection(cls, database: Optional[str] = None):
        """获取数据库连接"""
        try:
            config = TableStockPool._get_db_config(database or cls.STOCK_POOL_DB)
            connection = pymysql.connect(**config)
            return connection
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            raise
    
    @classmethod
    def load_poolCount(cls) -> pd.DataFrame:
        """
        加载股票池统计数据
        
        Returns:
            pd.DataFrame: 统计数据
        """
        try:
            connection = cls._get_connection()
            query = f"SELECT * FROM {cls.TABLE_NAME} ORDER BY date DESC"
            df = pd.read_sql(query, connection)
            connection.close()
            
            if df.empty:
                logger.warning("股票池统计数据为空")
            else:
                logger.info(f"成功加载股票池统计数据: {len(df)} 条记录")
            
            return df
        except Exception as e:
            logger.error(f"加载股票池统计数据失败: {e}")
            return pd.DataFrame()
    
    @classmethod
    def set_table_poolCount(cls, sql: str, params: Optional[Tuple] = None) -> bool:
        """
        更新股票池统计表数据
        
        Args:
            sql: SQL更新语句（不包含UPDATE关键字，只包含SET和WHERE部分）
            params: SQL参数元组
            
        Returns:
            bool: 操作是否成功
        """
        try:
            connection = cls._get_connection()
            cursor = connection.cursor()
            
            update_sql = f"UPDATE {cls.TABLE_NAME} SET {sql}"
            
            if params:
                cursor.execute(update_sql, params)
            else:
                cursor.execute(update_sql)
            
            connection.commit()
            cursor.close()
            connection.close()
            
            logger.info(f"更新股票池统计数据成功")
            return True
        except Exception as e:
            logger.error(f"更新股票池统计数据失败: {e}")
            if 'connection' in locals():
                try:
                    connection.rollback()
                    connection.close()
                except:
                    pass
            return False
    
    @classmethod
    def append_poolCount(cls, data: pd.DataFrame) -> bool:
        """
        追加股票池统计数据
        
        Args:
            data: 要追加的DataFrame
            
        Returns:
            bool: 操作是否成功
        """
        try:
            connection = cls._get_connection()
            
            # 使用 pandas 的 to_sql 方法追加数据
            data.to_sql(cls.TABLE_NAME, connection, if_exists='append', index=False)
            
            connection.close()
            
            logger.info(f"追加股票池统计数据成功: {len(data)} 条记录")
            return True
        except Exception as e:
            logger.error(f"追加股票池统计数据失败: {e}")
            if 'connection' in locals():
                try:
                    connection.rollback()
                    connection.close()
                except:
                    pass
            return False


class TableTradeRecord:
    """交易记录表操作类"""
    
    TABLE_NAME = 'trade_record'  # 假设表名，可能需要根据实际情况调整
    STOCK_POOL_DB = os.getenv('STOCK_POOL_DB', 'mystockrecord')
    
    @classmethod
    def _get_connection(cls, database: Optional[str] = None):
        """获取数据库连接"""
        try:
            config = TableStockPool._get_db_config(database or cls.STOCK_POOL_DB)
            connection = pymysql.connect(**config)
            return connection
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            raise
    
    @classmethod
    def load_trade_record(cls) -> pd.DataFrame:
        """
        加载交易记录数据
        
        Returns:
            pd.DataFrame: 交易记录数据
        """
        try:
            connection = cls._get_connection()
            query = f"SELECT * FROM {cls.TABLE_NAME}"
            df = pd.read_sql(query, connection)
            connection.close()
            
            if df.empty:
                logger.warning("交易记录数据为空")
            else:
                logger.info(f"成功加载交易记录数据: {len(df)} 条记录")
            
            return df
        except Exception as e:
            logger.error(f"加载交易记录数据失败: {e}")
            return pd.DataFrame()

