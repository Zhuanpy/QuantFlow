# -*- coding: utf-8 -*-
"""
MySQL数据访问层
提供RNN模型相关的数据库操作
"""
import pandas as pd
import pymysql
import logging
from typing import Optional
from config import Config

logger = logging.getLogger(__name__)

# 尝试导入 Flask 相关模块（如果可用）
try:
    from App.exts import db
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.warning("Flask 模块不可用，LoadMysql 功能可能受限")


class LoadRnnModel:
    """
    RNN模型数据访问类
    提供RNN模型训练和运行记录的数据库操作
    """
    
    # 数据库和表名配置
    db_rnn = 'rnn_model'
    tb_train_record = 'TrainRecord'
    tb_run_record = 'RunRecord'
    
    @staticmethod
    def _get_db_connection(database: str = None):
        """获取数据库连接"""
        try:
            conn = pymysql.connect(
                host=Config.DB_HOST,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=database or LoadRnnModel.db_rnn,
                charset=Config.DB_CONFIG['charset'],
                cursorclass=pymysql.cursors.DictCursor
            )
            return conn
        except pymysql.err.OperationalError as e:
            logger.error(f"数据库连接失败: {e}")
            raise
    
    @classmethod
    def load_run_record(cls) -> pd.DataFrame:
        """
        加载运行记录
        
        Returns:
            pd.DataFrame: 运行记录数据
        """
        sql = f"SELECT * FROM {cls.tb_run_record}"
        conn = None
        try:
            conn = cls._get_db_connection()
            df = pd.read_sql(sql, conn)
            logger.info(f"成功加载运行记录，共 {len(df)} 条")
            return df
        except Exception as e:
            logger.error(f"加载运行记录失败: {e}")
            return pd.DataFrame()
        finally:
            if conn:
                conn.close()
    
    @classmethod
    def load_train_record(cls) -> pd.DataFrame:
        """
        加载训练记录
        
        Returns:
            pd.DataFrame: 训练记录数据
        """
        sql = f"SELECT * FROM {cls.tb_train_record}"
        conn = None
        try:
            conn = cls._get_db_connection()
            df = pd.read_sql(sql, conn)
            logger.info(f"成功加载训练记录，共 {len(df)} 条")
            return df
        except Exception as e:
            logger.error(f"加载训练记录失败: {e}")
            return pd.DataFrame()
        finally:
            if conn:
                conn.close()
    
    @classmethod
    def set_table_run_record(cls, sql: str, params: tuple = None) -> bool:
        """
        更新运行记录表
        
        Args:
            sql: SQL UPDATE 语句的 SET 和 WHERE 部分
            params: 参数元组
            
        Returns:
            bool: 更新是否成功
        """
        # 构建完整的SQL语句
        full_sql = f"UPDATE {cls.tb_run_record} SET {sql}"
        conn = None
        try:
            conn = cls._get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(full_sql, params)
            conn.commit()
            logger.info(f"成功更新运行记录表。SQL: {full_sql}, Params: {params}")
            return True
        except Exception as e:
            logger.error(f"更新运行记录表失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()
    
    @classmethod
    def set_table_train_record(cls, sql: str, params: tuple = None) -> bool:
        """
        更新训练记录表
        
        Args:
            sql: SQL UPDATE 语句的 SET 和 WHERE 部分（可以包含表名占位符 {table}）
            params: 参数元组
            
        Returns:
            bool: 更新是否成功
        """
        # 如果SQL中包含 {table} 占位符，替换为实际表名
        if '{table}' in sql:
            full_sql = sql.format(table=cls.tb_train_record)
        else:
            # 否则，假设SQL已经包含表名，或者需要添加
            if cls.tb_train_record not in sql:
                full_sql = f"UPDATE {cls.tb_train_record} SET {sql}"
            else:
                full_sql = sql
        
        conn = None
        try:
            conn = cls._get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(full_sql, params)
            conn.commit()
            logger.info(f"成功更新训练记录表。SQL: {full_sql}, Params: {params}")
            return True
        except Exception as e:
            logger.error(f"更新训练记录表失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()


class LoadBasicInform:
    """
    基础信息加载类
    提供股票基础信息的数据库操作
    """
    
    @staticmethod
    def load_minute() -> pd.DataFrame:
        """
        加载分钟数据相关的股票基础信息
        
        Returns:
            pd.DataFrame: 包含股票代码、名称、分类等信息的DataFrame
        """
        if not FLASK_AVAILABLE:
            logger.error("Flask 模块不可用，无法加载基础信息")
            return pd.DataFrame()
        
        try:
            from App.models.data.basic_info import StockCodes, StockClassification
            
            # 查询所有股票代码
            stocks = StockCodes.query.all()
            
            # 转换为DataFrame
            data = []
            for stock in stocks:
                # 尝试获取分类信息
                classification = '股票'  # 默认值
                try:
                    stock_class = StockClassification.query.filter_by(code=stock.code).first()
                    if stock_class:
                        classification = stock_class.classification
                except:
                    pass
                
                data.append({
                    'id': stock.id,
                    'name': stock.name,
                    'code': stock.code,
                    'EsCode': stock.EsCode or stock.code,
                    'Classification': classification
                })
            
            df = pd.DataFrame(data)
            logger.info(f"成功加载基础信息: {len(df)} 条记录")
            return df
            
        except Exception as e:
            logger.error(f"加载基础信息失败: {e}")
            return pd.DataFrame()


class LoadNortFunds:
    """
    北向资金数据加载类
    """
    
    @staticmethod
    def load_north_funds() -> pd.DataFrame:
        """
        加载北向资金数据
        
        Returns:
            pd.DataFrame: 北向资金数据
        """
        # 这是一个占位实现，实际功能需要根据具体的数据源实现
        logger.warning("LoadNortFunds.load_north_funds() 方法尚未实现")
        return pd.DataFrame()


class LoadFundsAwkward:
    """
    基金重仓数据加载类
    """
    
    @staticmethod
    def load_funds_awkward() -> pd.DataFrame:
        """
        加载基金重仓数据
        
        Returns:
            pd.DataFrame: 基金重仓数据
        """
        # 这是一个占位实现，实际功能需要根据具体的数据源实现
        logger.warning("LoadFundsAwkward.load_funds_awkward() 方法尚未实现")
        return pd.DataFrame()


if __name__ == '__main__':
    # 测试代码
    records = LoadRnnModel.load_run_record()
    print(f"运行记录: {len(records)} 条")
    
    train_records = LoadRnnModel.load_train_record()
    print(f"训练记录: {len(train_records)} 条")

