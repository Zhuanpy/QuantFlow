# -*- coding: utf-8 -*-
"""
统一路径管理模块

提供项目中所有数据路径的统一管理，包括：
- 股票数据路径（1m, 15m, daily）
- 模型文件路径
- 临时文件路径
- 目录结构管理
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)


class PathManager:
    """
    统一路径管理器

    提供项目中所有路径的获取和管理功能
    """

    # 数据类型常量
    DATA_1M = '1m'
    DATA_15M = '15m'
    DATA_15M_STANDARDIZED = '15m_standardized'
    DATA_DAILY = 'daily'
    DATA_FUNDS = 'funds_awkward'

    # 支持的数据类型列表
    SUPPORTED_DATA_TYPES = [DATA_1M, DATA_15M, DATA_15M_STANDARDIZED, DATA_DAILY, DATA_FUNDS]

    def __init__(self, project_root: str = None):
        """
        初始化路径管理器

        Args:
            project_root: 项目根目录，不传则自动获取
        """
        if project_root:
            self._project_root = Path(project_root)
        else:
            try:
                from config import Config
                self._project_root = Path(Config.get_project_root())
            except ImportError:
                # 回退到基于当前文件的路径推断
                self._project_root = Path(__file__).parent.parent.parent

        # 基础路径
        self._data_base = self._project_root / 'data' / 'data'
        self._models_base = self._project_root / 'data' / 'models'
        self._temp_base = self._project_root / 'data' / 'temp'
        self._processed_base = self._project_root / 'data' / 'processed'

    @property
    def project_root(self) -> Path:
        """获取项目根目录"""
        return self._project_root

    @property
    def data_base(self) -> Path:
        """获取数据基础目录"""
        return self._data_base

    # ==================== 季度相关 ====================

    @staticmethod
    def get_current_quarter() -> str:
        """
        获取当前季度

        Returns:
            str: 季度字符串，格式 'Q1', 'Q2', 'Q3', 'Q4'
        """
        now = datetime.now()
        quarter_num = (now.month - 1) // 3 + 1
        return f"Q{quarter_num}"

    @staticmethod
    def get_current_year() -> str:
        """获取当前年份"""
        return str(datetime.now().year)

    @staticmethod
    def get_quarter_str(year: int = None, quarter: int = None) -> str:
        """
        获取季度完整字符串

        Args:
            year: 年份
            quarter: 季度 (1-4)

        Returns:
            str: 格式 '2024Q1'
        """
        year = year or datetime.now().year
        if quarter is None:
            quarter = (datetime.now().month - 1) // 3 + 1
        return f"{year}Q{quarter}"

    # ==================== 股票数据路径 ====================

    def get_stock_data_path(self, stock_code: str, data_type: str = DATA_1M,
                             year: str = None, quarter: str = None,
                             create: bool = True) -> str:
        """
        获取股票数据的存储路径

        Args:
            stock_code: 股票代码
            data_type: 数据类型 ('1m', '15m', '15m_standardized', 'daily')
            year: 年份，默认当前年
            quarter: 季度 ('Q1'-'Q4')，默认当前季度
            create: 是否自动创建目录

        Returns:
            str: 完整的文件路径
        """
        year = year or self.get_current_year()
        quarter = quarter or self.get_current_quarter()

        # 根据数据类型确定基础目录
        if data_type == self.DATA_1M:
            base_dir = self._data_base / 'quarters' / year / quarter
        elif data_type in (self.DATA_15M, '15m_normal'):
            base_dir = self._data_base / '15m'
        elif data_type == self.DATA_15M_STANDARDIZED:
            base_dir = self._data_base / '15m_standardized'
        elif data_type == self.DATA_DAILY:
            base_dir = self._data_base / 'daily' / year / quarter
        else:
            base_dir = self._data_base / 'quarters' / year / quarter

        if create and not base_dir.exists():
            base_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"创建目录: {base_dir}")

        return str(base_dir / f"{stock_code}.csv")

    def get_stock_1m_path(self, stock_code: str, year: str = None,
                           quarter: str = None, create: bool = True) -> str:
        """获取1分钟数据路径"""
        return self.get_stock_data_path(stock_code, self.DATA_1M, year, quarter, create)

    def get_stock_15m_path(self, stock_code: str, create: bool = True) -> str:
        """获取15分钟数据路径"""
        return self.get_stock_data_path(stock_code, self.DATA_15M, create=create)

    def get_stock_daily_path(self, stock_code: str, year: str = None,
                              quarter: str = None, create: bool = True) -> str:
        """获取日线数据路径"""
        return self.get_stock_data_path(stock_code, self.DATA_DAILY, year, quarter, create)

    # ==================== 处理后数据路径 ====================

    def get_processed_data_path(self, data_type: str, filename: str,
                                 create: bool = True) -> str:
        """
        获取处理后数据的存储路径

        Args:
            data_type: 数据类型 ('features', 'signals', 'indicators')
            filename: 文件名
            create: 是否自动创建目录

        Returns:
            str: 完整的文件路径
        """
        base_dir = self._processed_base / data_type

        if create and not base_dir.exists():
            base_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"创建目录: {base_dir}")

        return str(base_dir / filename)

    # ==================== 模型文件路径 ====================

    def get_model_path(self, model_type: str, filename: str,
                        create: bool = True) -> str:
        """
        获取模型相关文件的存储路径

        Args:
            model_type: 模型类型 ('trained', 'checkpoints', 'predictions')
            filename: 文件名
            create: 是否自动创建目录

        Returns:
            str: 完整的文件路径
        """
        base_dir = self._models_base / model_type

        if create and not base_dir.exists():
            base_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"创建目录: {base_dir}")

        return str(base_dir / filename)

    # ==================== 临时文件路径 ====================

    def get_temp_path(self, filename: str, create: bool = True) -> str:
        """
        获取临时文件的存储路径

        Args:
            filename: 文件名
            create: 是否自动创建目录

        Returns:
            str: 完整的文件路径
        """
        if create and not self._temp_base.exists():
            self._temp_base.mkdir(parents=True, exist_ok=True)
            logger.info(f"创建目录: {self._temp_base}")

        return str(self._temp_base / filename)

    # ==================== RNN 数据路径 ====================

    def get_rnn_data_path(self, months: str, data_type: str = 'json') -> Path:
        """
        获取 RNN 数据目录路径

        Args:
            months: 月份标识
            data_type: 数据类型 ('json', 'csv', 'model')

        Returns:
            Path: 目录路径
        """
        return self._project_root / 'App' / 'codes' / 'code_data' / 'RnnData' / months / data_type

    def get_rnn_json_path(self, months: str, stock_code: str) -> str:
        """获取 RNN JSON 文件路径"""
        return str(self.get_rnn_data_path(months, 'json') / f"{stock_code}.json")

    # ==================== 目录结构管理 ====================

    def ensure_data_directories(self) -> None:
        """确保所有必要的数据目录都存在"""
        directories = [
            self._data_base,
            self._data_base / 'quarters',
            self._data_base / '15m',
            self._data_base / '15m_standardized',
            self._data_base / 'daily',
            self._models_base,
            self._temp_base,
            self._processed_base,
            self._project_root / 'App' / 'codes' / 'code_data',
        ]

        for directory in directories:
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                logger.info(f"创建目录: {directory}")

    def create_quarter_structure(self, year: int = None, quarter: int = None) -> bool:
        """
        创建指定季度的目录结构

        Args:
            year: 年份
            quarter: 季度 (1-4)

        Returns:
            bool: 是否创建成功
        """
        year = year or datetime.now().year
        if quarter is None:
            quarter = (datetime.now().month - 1) // 3 + 1

        quarter_str = f"Q{quarter}"

        try:
            # 创建季度目录及子目录
            quarter_path = self._data_base / 'quarters' / str(year) / quarter_str

            for data_type in [self.DATA_1M, self.DATA_15M, self.DATA_DAILY, self.DATA_FUNDS]:
                data_path = quarter_path / data_type
                if not data_path.exists():
                    data_path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"创建目录: {data_path}")

            return True

        except Exception as e:
            logger.error(f"创建季度目录结构失败: {e}")
            return False

    def create_quarters_range(self, start_year: int, start_quarter: int,
                               end_year: int = None, end_quarter: int = None) -> bool:
        """
        创建多个季度的目录结构

        Args:
            start_year: 起始年份
            start_quarter: 起始季度
            end_year: 结束年份（默认当前年）
            end_quarter: 结束季度（默认当前季度）

        Returns:
            bool: 是否全部创建成功
        """
        if end_year is None:
            end_year = datetime.now().year
        if end_quarter is None:
            end_quarter = (datetime.now().month - 1) // 3 + 1

        success = True
        current_year = start_year
        current_quarter = start_quarter

        while (current_year < end_year or
               (current_year == end_year and current_quarter <= end_quarter)):
            if not self.create_quarter_structure(current_year, current_quarter):
                success = False

            current_quarter += 1
            if current_quarter > 4:
                current_quarter = 1
                current_year += 1

        return success

    def verify_structure(self, year: int = None, quarter: int = None) -> Tuple[bool, List[str]]:
        """
        验证目录结构是否完整

        Args:
            year: 要验证的年份
            quarter: 要验证的季度

        Returns:
            Tuple[bool, List[str]]: (是否完整, 缺失的目录列表)
        """
        missing_dirs = []

        if year and quarter:
            quarters_to_check = [(year, quarter)]
        else:
            quarters_path = self._data_base / 'quarters'
            if not quarters_path.exists():
                return False, [str(quarters_path)]

            quarters_to_check = []
            for year_dir in quarters_path.iterdir():
                if year_dir.is_dir():
                    for q_dir in year_dir.iterdir():
                        if q_dir.is_dir() and q_dir.name.startswith('Q'):
                            quarters_to_check.append((int(year_dir.name), int(q_dir.name[1])))

        for y, q in quarters_to_check:
            quarter_path = self._data_base / 'quarters' / str(y) / f'Q{q}'
            if not quarter_path.exists():
                missing_dirs.append(str(quarter_path))
                continue

            for data_type in [self.DATA_1M]:
                data_path = quarter_path / data_type
                if not data_path.exists():
                    missing_dirs.append(str(data_path))

        return len(missing_dirs) == 0, missing_dirs


# 全局实例
_path_manager: Optional[PathManager] = None


def get_path_manager() -> PathManager:
    """获取全局路径管理器实例"""
    global _path_manager
    if _path_manager is None:
        _path_manager = PathManager()
    return _path_manager


# ==================== 便捷函数（向后兼容） ====================

def ensure_data_directories():
    """确保所有必要的数据目录都存在"""
    get_path_manager().ensure_data_directories()


def get_stock_data_path(stock_code: str, data_type: str = '1m',
                         year: str = None, quarter: str = None,
                         create: bool = True) -> str:
    """获取股票数据的存储路径"""
    return get_path_manager().get_stock_data_path(stock_code, data_type, year, quarter, create)


def get_processed_data_path(data_type: str, filename: str, create: bool = True) -> str:
    """获取处理后数据的存储路径"""
    return get_path_manager().get_processed_data_path(data_type, filename, create)


def get_model_path(model_type: str, filename: str, create: bool = True) -> str:
    """获取模型相关文件的存储路径"""
    return get_path_manager().get_model_path(model_type, filename, create)


def get_temp_path(filename: str, create: bool = True) -> str:
    """获取临时文件的存储路径"""
    return get_path_manager().get_temp_path(filename, create)
