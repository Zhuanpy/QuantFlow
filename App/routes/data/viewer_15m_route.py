# -*- coding: utf-8 -*-
"""
15m 数据文件访问工具函数

注：原「15分钟数据查看器」页面及其 API 已下线，仅保留以下文件读取辅助函数，
被趋势打分 / 实时数据 / 板块数据等模块复用（data/15m/<code>.parquet 同源）。
"""
from App.utils.path_manager import get_path_manager
import pandas as pd
import os
import logging

logger = logging.getLogger(__name__)


def _get_15m_dir() -> str:
    """获取15m数据目录"""
    pm = get_path_manager()
    return str(pm.data_base / '15m')


def _find_15m_file(data_dir: str, stock_code: str) -> str:
    """查找指定股票的15m数据文件，parquet优先"""
    for ext in ['.parquet', '.csv']:
        candidate = os.path.join(data_dir, f'{stock_code}{ext}')
        if os.path.exists(candidate):
            return candidate
    return None


def _read_15m_file(fpath: str) -> pd.DataFrame:
    """读取15m数据文件，支持csv和parquet"""
    if fpath.endswith('.csv'):
        return pd.read_csv(fpath, parse_dates=['date'])
    else:
        try:
            return pd.read_parquet(fpath)
        except ImportError:
            logger.warning(f"pyarrow未安装，无法读取parquet文件: {fpath}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"读取parquet失败: {fpath}, {e}")
            return pd.DataFrame()
