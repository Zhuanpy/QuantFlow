# -*- coding: utf-8 -*-
"""
股票数据仓库

提供股票数据的访问和操作
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, date

import pandas as pd
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import SQLAlchemyError

from App.repositories.base_repository import BaseRepository
from App.models import db
from App.utils.path_manager import get_path_manager

logger = logging.getLogger(__name__)


class StockRepository(BaseRepository):
    """
    股票数据仓库

    提供股票数据的 CRUD 操作和业务查询
    """

    def __init__(self, model_class=None):
        """
        初始化股票数据仓库

        Args:
            model_class: 股票模型类，默认延迟导入
        """
        if model_class is None:
            # 延迟导入避免循环依赖
            from App.models.data.Stock1m import Stock1m
            model_class = Stock1m
        super().__init__(model_class)
        self.path_manager = get_path_manager()

    # ==================== 股票基础查询 ====================

    def get_by_code(self, stock_code: str) -> List:
        """
        根据股票代码获取记录

        Args:
            stock_code: 股票代码

        Returns:
            List: 股票记录列表
        """
        return self.get_by_filter(stock_code=stock_code)

    def get_by_code_and_date(self, stock_code: str, trade_date: date) -> List:
        """
        根据股票代码和日期获取记录

        Args:
            stock_code: 股票代码
            trade_date: 交易日期

        Returns:
            List: 股票记录列表
        """
        try:
            return self.model_class.query.filter(
                and_(
                    self.model_class.stock_code == stock_code,
                    func.date(self.model_class.date) == trade_date
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"查询股票数据失败: {e}")
            return []

    def get_by_date_range(self, stock_code: str, start_date: datetime,
                          end_date: datetime) -> List:
        """
        获取日期范围内的股票数据

        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            List: 股票记录列表
        """
        try:
            return self.model_class.query.filter(
                and_(
                    self.model_class.stock_code == stock_code,
                    self.model_class.date >= start_date,
                    self.model_class.date <= end_date
                )
            ).order_by(self.model_class.date.asc()).all()
        except SQLAlchemyError as e:
            logger.error(f"查询股票数据失败: {e}")
            return []

    def get_latest(self, stock_code: str, limit: int = 1) -> List:
        """
        获取最新的股票数据

        Args:
            stock_code: 股票代码
            limit: 返回数量

        Returns:
            List: 股票记录列表
        """
        try:
            return self.model_class.query.filter_by(
                stock_code=stock_code
            ).order_by(
                self.model_class.date.desc()
            ).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"查询最新股票数据失败: {e}")
            return []

    def get_latest_date(self, stock_code: str) -> Optional[datetime]:
        """
        获取股票最新数据的日期

        Args:
            stock_code: 股票代码

        Returns:
            Optional[datetime]: 最新日期或 None
        """
        try:
            result = db.session.query(
                func.max(self.model_class.date)
            ).filter(
                self.model_class.stock_code == stock_code
            ).scalar()
            return result
        except SQLAlchemyError as e:
            logger.error(f"查询最新日期失败: {e}")
            return None

    # ==================== 数据转换 ====================

    def to_dataframe(self, records: List) -> pd.DataFrame:
        """
        将记录转换为 DataFrame

        Args:
            records: 股票记录列表

        Returns:
            pd.DataFrame: 数据框
        """
        if not records:
            return pd.DataFrame()

        try:
            data = []
            for record in records:
                data.append({
                    'date': record.date,
                    'open': record.open,
                    'close': record.close,
                    'high': record.high,
                    'low': record.low,
                    'volume': record.volume,
                    'money': getattr(record, 'money', None),
                })
            return pd.DataFrame(data)
        except Exception as e:
            logger.error(f"转换 DataFrame 失败: {e}")
            return pd.DataFrame()

    def from_dataframe(self, df: pd.DataFrame, stock_code: str) -> List[Dict[str, Any]]:
        """
        从 DataFrame 创建记录字典列表

        Args:
            df: 数据框
            stock_code: 股票代码

        Returns:
            List[Dict]: 记录字典列表
        """
        records = []
        for _, row in df.iterrows():
            records.append({
                'stock_code': stock_code,
                'date': row['date'],
                'open': row['open'],
                'close': row['close'],
                'high': row['high'],
                'low': row['low'],
                'volume': row['volume'],
                'money': row.get('money', 0),
            })
        return records

    # ==================== 批量操作 ====================

    def bulk_insert(self, df: pd.DataFrame, stock_code: str) -> int:
        """
        批量插入股票数据

        Args:
            df: 数据框
            stock_code: 股票代码

        Returns:
            int: 插入的记录数
        """
        try:
            records = self.from_dataframe(df, stock_code)
            instances = self.create_many(records)
            return len(instances)
        except Exception as e:
            logger.error(f"批量插入失败: {e}")
            return 0

    def bulk_upsert(self, df: pd.DataFrame, stock_code: str) -> int:
        """
        批量更新或插入股票数据

        Args:
            df: 数据框
            stock_code: 股票代码

        Returns:
            int: 处理的记录数
        """
        count = 0
        records = self.from_dataframe(df, stock_code)

        for record in records:
            result = self.upsert(['stock_code', 'date'], **record)
            if result:
                count += 1

        return count

    def delete_by_code(self, stock_code: str) -> int:
        """
        删除指定股票的所有数据

        Args:
            stock_code: 股票代码

        Returns:
            int: 删除的记录数
        """
        return self.delete_by_filter(stock_code=stock_code)

    def delete_by_date_range(self, stock_code: str, start_date: datetime,
                              end_date: datetime) -> int:
        """
        删除日期范围内的股票数据

        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            int: 删除的记录数
        """
        try:
            with self.session_scope():
                count = self.model_class.query.filter(
                    and_(
                        self.model_class.stock_code == stock_code,
                        self.model_class.date >= start_date,
                        self.model_class.date <= end_date
                    )
                ).delete()
                return count
        except SQLAlchemyError as e:
            logger.error(f"删除股票数据失败: {e}")
            return 0

    # ==================== CSV 文件操作 ====================

    def save_to_csv(self, stock_code: str, data_type: str = '1m',
                    year: str = None, quarter: str = None) -> bool:
        """
        将股票数据保存到 CSV 文件

        Args:
            stock_code: 股票代码
            data_type: 数据类型
            year: 年份
            quarter: 季度

        Returns:
            bool: 是否保存成功
        """
        try:
            records = self.get_by_code(stock_code)
            if not records:
                logger.warning(f"没有找到股票 {stock_code} 的数据")
                return False

            df = self.to_dataframe(records)
            file_path = self.path_manager.get_stock_data_path(
                stock_code, data_type, year, quarter
            )

            df.to_csv(file_path, index=False)
            logger.info(f"股票数据已保存到: {file_path}")
            return True

        except Exception as e:
            logger.error(f"保存 CSV 失败: {e}")
            return False

    def load_from_csv(self, stock_code: str, data_type: str = '1m',
                      year: str = None, quarter: str = None) -> pd.DataFrame:
        """
        从 CSV 文件加载股票数据

        Args:
            stock_code: 股票代码
            data_type: 数据类型
            year: 年份
            quarter: 季度

        Returns:
            pd.DataFrame: 数据框
        """
        try:
            file_path = self.path_manager.get_stock_data_path(
                stock_code, data_type, year, quarter, create=False
            )

            import os
            if not os.path.exists(file_path):
                logger.warning(f"文件不存在: {file_path}")
                return pd.DataFrame()

            df = pd.read_csv(file_path)
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])

            logger.info(f"从 {file_path} 加载了 {len(df)} 条记录")
            return df

        except Exception as e:
            logger.error(f"加载 CSV 失败: {e}")
            return pd.DataFrame()

    # ==================== 统计查询 ====================

    def get_stock_codes(self) -> List[str]:
        """
        获取所有股票代码

        Returns:
            List[str]: 股票代码列表
        """
        try:
            result = db.session.query(
                self.model_class.stock_code
            ).distinct().all()
            return [r[0] for r in result]
        except SQLAlchemyError as e:
            logger.error(f"查询股票代码失败: {e}")
            return []

    def get_date_range(self, stock_code: str) -> Optional[Dict[str, datetime]]:
        """
        获取股票数据的日期范围

        Args:
            stock_code: 股票代码

        Returns:
            Optional[Dict]: 包含 min_date 和 max_date 的字典
        """
        try:
            result = db.session.query(
                func.min(self.model_class.date),
                func.max(self.model_class.date)
            ).filter(
                self.model_class.stock_code == stock_code
            ).first()

            if result and result[0]:
                return {
                    'min_date': result[0],
                    'max_date': result[1]
                }
            return None
        except SQLAlchemyError as e:
            logger.error(f"查询日期范围失败: {e}")
            return None
