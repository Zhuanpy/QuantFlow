# -*- coding: utf-8 -*-
"""
数据处理模块

提供数据重采样、时间序列转换等功能
"""

import pandas as pd
from typing import Literal


class ResampleData:
    """数据重采样工具类"""

    # 支持的时间频率
    SUPPORTED_FREQUENCIES = ['15m', '30m', '60m', '120m', 'day', 'daily', 'd', 'D']

    @classmethod
    def resample_fun(cls, data: pd.DataFrame, parameter: str) -> pd.DataFrame:
        """
        通用数据重采样函数

        Args:
            data: 包含 OHLCV 数据的 DataFrame，必须有 'date' 列
            parameter: pandas 重采样参数 (如 '15T', '30T' 等)

        Returns:
            pd.DataFrame: 重采样后的数据
        """
        # 转换索引为 datetime
        data = data.copy()
        data['index_date'] = pd.to_datetime(data['date'])
        data = data.set_index('index_date')

        # 按指定频率重采样
        resampled = data.resample(parameter, closed='right', label='right').agg({
            'open': 'first',
            'close': 'last',
            'high': 'max',
            'low': 'min',
            'volume': 'sum',
            'money': 'sum'
        }).reset_index()

        # 重命名列并返回
        resampled = resampled.rename(columns={'index_date': 'date'}).dropna(how='any')
        return resampled[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]

    @classmethod
    def _split_and_resample_60m(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        将股票的1分钟数据转换为60分钟数据

        考虑A股交易时间段：
        - 上午：09:31-11:30，采样时间点为10:31和11:30
        - 下午：13:00-15:00，按整点采样

        Args:
            data: 1分钟 OHLCV 数据

        Returns:
            pd.DataFrame: 60分钟数据
        """
        data = data.copy()
        data["date"] = pd.to_datetime(data["date"])
        data["index_date"] = data["date"]
        data = data.set_index("index_date")

        # 分别提取上午和下午数据
        morning_data = data.between_time("09:31", "11:30")
        afternoon_data = data.between_time("13:00", "15:00")

        # 上午按 90分钟 采样
        morning_resampled = morning_data.resample("90T", closed="right", label="right").agg({
            "date": "last",
            "open": "first",
            "close": "last",
            "high": "max",
            "low": "min",
            "volume": "sum",
            "money": "sum"
        }).dropna()

        # 下午按 60分钟 采样
        afternoon_resampled = afternoon_data.resample("60T", closed="right", label="right").agg({
            "date": "last",
            "open": "first",
            "close": "last",
            "high": "max",
            "low": "min",
            "volume": "sum",
            "money": "sum"
        }).dropna()

        # 合并上午和下午数据
        resampled = pd.concat([morning_resampled, afternoon_resampled]).sort_values("date").reset_index(drop=True)
        return resampled

    @classmethod
    def _split_and_resample_120m(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        将1分钟数据转换为120分钟（2小时）数据

        Args:
            data: 1分钟 OHLCV 数据

        Returns:
            pd.DataFrame: 120分钟数据
        """
        data = data.copy()
        data["date"] = pd.to_datetime(data["date"])
        data["index_date"] = data["date"]
        data = data.set_index("index_date")

        resampled = data.resample("360T", closed="right", label="right").agg({
            "date": "last",
            "open": "first",
            "close": "last",
            "high": "max",
            "low": "min",
            "volume": "sum",
            "money": "sum"
        }).dropna().reset_index(drop=True)

        return resampled

    @classmethod
    def _resample_to_daily(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        将1分钟数据聚合为日K数据

        Args:
            data: 1分钟 OHLCV 数据

        Returns:
            pd.DataFrame: 日K数据
        """
        data = data.copy()
        data["date"] = pd.to_datetime(data["date"])
        day_k = data.groupby(data["date"].dt.date).agg(
            open=("open", "first"),
            close=("close", "last"),
            high=("high", "max"),
            low=("low", "min"),
            volume=("volume", "sum"),
            money=("money", "sum")
        ).reset_index()

        day_k = day_k.rename(columns={"index": "date"})
        return day_k

    @classmethod
    def resample_1m_data(cls, data: pd.DataFrame, freq: str) -> pd.DataFrame:
        """
        根据指定频率重采样1分钟数据

        Args:
            data: 1分钟 OHLCV 数据
            freq: 目标频率，支持 '15m', '30m', '60m', '120m', 'day'/'daily'

        Returns:
            pd.DataFrame: 重采样后的数据

        Raises:
            ValueError: 不支持的时间频率
        """
        # 时间映射字典
        time_mappings = {
            '15m': '15T',
            '30m': '30T',
            '120m': '360T',
            'day': '1440T',
            'daily': '1440T',
            'd': '1440T',
            'D': '1440T'
        }

        if freq == '60m':
            return cls._split_and_resample_60m(data)
        elif freq == '120m':
            return cls._split_and_resample_120m(data)
        elif freq in {'day', 'daily', 'd', 'D'}:
            return cls._resample_to_daily(data)
        elif freq in time_mappings:
            return cls.resample_fun(data, parameter=time_mappings[freq])
        else:
            raise ValueError(f"不支持的时间频率: {freq}。支持的频率: {cls.SUPPORTED_FREQUENCIES}")


def resample_to_15m(data: pd.DataFrame) -> pd.DataFrame:
    """将1分钟数据重采样为15分钟数据"""
    return ResampleData.resample_1m_data(data, '15m')


def resample_to_30m(data: pd.DataFrame) -> pd.DataFrame:
    """将1分钟数据重采样为30分钟数据"""
    return ResampleData.resample_1m_data(data, '30m')


def resample_to_60m(data: pd.DataFrame) -> pd.DataFrame:
    """将1分钟数据重采样为60分钟数据"""
    return ResampleData.resample_1m_data(data, '60m')


def resample_to_daily(data: pd.DataFrame) -> pd.DataFrame:
    """将1分钟数据重采样为日K数据"""
    return ResampleData.resample_1m_data(data, 'day')
