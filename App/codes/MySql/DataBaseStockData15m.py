# -*- coding: utf-8 -*-
"""
股票15分钟数据访问层（本地 parquet 唯一来源）

历史背景：原版"MySQL 优先，失败再读本地"。但 15m 模型绑定的 SQLAlchemy bind
`datadaily` 从未配置进 SQLALCHEMY_BINDS，所有 SQL 调用都会失败再 fallback。
现在统一只用本地 `<root>/data/15m/<code>.parquet`（与 viewer_15m / 个股趋势同源）。

公开 API 全部保留：load_15m / save_15m / append_15m / replace_15m / delete_15m /
set_data_15m_data / get_data_15m_data_end_date —— 调用方无需改。
"""
import pandas as pd
import logging
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)


def _local_15m_path(stock_code: str, ext: str = 'parquet') -> Path:
    base = Path(Config.get_project_root()) / 'data' / '15m'
    base.mkdir(parents=True, exist_ok=True)
    return base / f'{stock_code}.{ext}'


def _read_local_15m(stock_code: str) -> pd.DataFrame:
    """parquet 优先，csv 兜底，都没有返回空"""
    p = _local_15m_path(stock_code, 'parquet')
    c = _local_15m_path(stock_code, 'csv')
    if p.exists():
        df = pd.read_parquet(p)
        df['date'] = pd.to_datetime(df['date'])
        return df
    if c.exists():
        return pd.read_csv(c, parse_dates=['date'])
    return pd.DataFrame()


def _write_local_15m(stock_code: str, data: pd.DataFrame) -> bool:
    """覆盖式写入 parquet（同时清掉旧 csv，避免读到陈旧数据）"""
    try:
        if data is None or data.empty:
            return False
        df = data.copy()
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        df.to_parquet(_local_15m_path(stock_code, 'parquet'), index=False)
        c = _local_15m_path(stock_code, 'csv')
        if c.exists():
            try:
                c.unlink()
            except Exception:
                pass
        return True
    except Exception as e:
        logger.error(f"写本地 15m 失败 {stock_code}: {e}")
        return False


class StockData15m:
    """股票15分钟数据访问类（本地 parquet）"""

    @staticmethod
    def load_15m(stock_code: str) -> pd.DataFrame:
        """加载股票15分钟数据"""
        try:
            df = _read_local_15m(stock_code)
            if df.empty:
                logger.warning(f"15m 本地文件不存在: {stock_code}")
                return df
            logger.info(f"加载 {stock_code} 15m: {len(df)} 条")
            return df
        except Exception as e:
            logger.error(f"加载 {stock_code} 15m 失败: {e}")
            return pd.DataFrame()

    @staticmethod
    def save_15m(stock_code: str, data: pd.DataFrame) -> bool:
        """保存（覆盖式）股票15分钟数据"""
        ok = _write_local_15m(stock_code, data)
        if ok:
            logger.info(f"保存 {stock_code} 15m 成功: {len(data)} 条")
        return ok

    @staticmethod
    def replace_15m(stock_code: str, data: pd.DataFrame) -> bool:
        """替换：直接覆盖写"""
        return StockData15m.save_15m(stock_code, data)

    @staticmethod
    def append_15m(stock_code: str, data: pd.DataFrame) -> bool:
        """追加：合并已有数据后按 date 去重 + 排序 + 覆盖写"""
        if data is None or data.empty:
            return False
        try:
            old = _read_local_15m(stock_code)
            new = data.copy()
            new['date'] = pd.to_datetime(new['date'])
            merged = (pd.concat([old, new], ignore_index=True)
                      if not old.empty else new)
            merged = (merged
                      .drop_duplicates(subset=['date'], keep='last')
                      .sort_values('date')
                      .reset_index(drop=True))
            return _write_local_15m(stock_code, merged)
        except Exception as e:
            logger.error(f"追加 {stock_code} 15m 失败: {e}")
            return False

    @staticmethod
    def get_data_15m_data_end_date(stock_code: str):
        """该股票 15m 数据的最新日期。无数据时返回 1900-01-01。"""
        try:
            df = _read_local_15m(stock_code)
            if df is None or df.empty:
                return pd.Timestamp('1900-01-01')
            return pd.to_datetime(df['date']).max()
        except Exception as e:
            logger.warning(f"取 {stock_code} 15m 最新日期失败: {e}")
            return pd.Timestamp('1900-01-01')

    @staticmethod
    def delete_15m(stock_code: str) -> bool:
        """删除本地 15m 文件"""
        try:
            removed = False
            for ext in ('parquet', 'csv'):
                p = _local_15m_path(stock_code, ext)
                if p.exists():
                    p.unlink()
                    removed = True
            if removed:
                logger.info(f"删除 {stock_code} 15m 成功")
            return True
        except Exception as e:
            logger.error(f"删除 {stock_code} 15m 失败: {e}")
            return False

    @staticmethod
    def set_data_15m_data(stock_code: str, sql: str = None, params=None) -> bool:
        """旧 API：原本是对 MySQL 表做 UPDATE。本地化后不再支持按 SQL 部分更新。

        调用方都在 try/except 里，返回 False 即可让上层走 fallback。
        如需精确字段更新（如更新某一行的 Signal/SignalTimes），
        请用 load_15m → 修改 DataFrame → replace_15m 的方式。
        """
        logger.debug(
            f"set_data_15m_data({stock_code}) 已废弃（本地 parquet 不支持按 SQL UPDATE）"
        )
        return False


if __name__ == '__main__':
    data = StockData15m.load_15m('000001')
    print(f"加载数据: {len(data)} 条记录")
    print(data.head() if not data.empty else "数据为空")
