# -*- coding: utf-8 -*-
"""
数学工具函数模块

提供统计分析、数据归一化、异常值处理等数学函数
"""

import numpy as np
import pandas as pd
from scipy import stats
from typing import Union


class MathematicalFormula:
    """数学公式工具类"""

    @classmethod
    def normal_get_p(cls, x: float, mean: float = 0, std: float = 1) -> float:
        """
        计算正态分布的累积分布函数值 (CDF)

        Args:
            x: 输入值
            mean: 均值，默认0
            std: 标准差，默认1

        Returns:
            float: 累积概率值 P(X <= x)
        """
        z = (x - mean) / std
        p = stats.norm.cdf(z)
        return p

    @classmethod
    def normal_get_x(cls, p: float, mean: float = 0, std: float = 1) -> float:
        """
        根据累积分布函数值计算对应的x值 (逆CDF / PPF)

        Args:
            p: 累积概率值
            mean: 均值，默认0
            std: 标准差，默认1

        Returns:
            float: 对应的x值
        """
        z = stats.norm.ppf(p)
        x = z * std + mean
        return x

    @classmethod
    def normal_pdf(cls, x: float, mu: float, sigma: float) -> float:
        """
        计算正态分布的概率密度函数值 (PDF)

        Args:
            x: 输入值
            mu: 均值
            sigma: 标准差

        Returns:
            float: 概率密度值
        """
        pdf = np.exp(-((x - mu) ** 2) / (2 * sigma ** 2)) / (sigma * np.sqrt(2 * np.pi))
        return pdf

    # 别名，保持向后兼容
    normal2Y = normal_pdf

    @classmethod
    def filter_median(cls, data: pd.DataFrame, column: str) -> pd.DataFrame:
        """
        使用中位数绝对偏差（MAD）过滤异常值

        基于 3 * 1.4826 * MAD 规则，将超出范围的值裁剪到边界

        Args:
            data: 输入数据框
            column: 要处理的列名

        Returns:
            pd.DataFrame: 处理后的数据框
        """
        med = data[column].median()
        mad = abs(data[column] - med).median()

        high = med + (3 * 1.4826 * mad)
        low = med - (3 * 1.4826 * mad)

        data.loc[(data[column] > high), column] = high
        data.loc[(data[column] < low), column] = low

        return data

    @classmethod
    def filter_3sigma(cls, data: pd.DataFrame, column: str, n: int = 3) -> pd.DataFrame:
        """
        使用n倍标准差过滤异常值

        Args:
            data: 输入数据框
            column: 要处理的列名
            n: 标准差倍数，默认3

        Returns:
            pd.DataFrame: 处理后的数据框
        """
        mean_ = data[column].mean()
        std_ = data[column].std()

        max_ = mean_ + n * std_
        min_ = mean_ - n * std_

        data.loc[(data[column] > max_), column] = max_
        data.loc[(data[column] < min_), column] = min_
        return data

    @classmethod
    def data2normalization(cls, column: pd.Series) -> pd.Series:
        """
        数据归一化到[0, 1]区间 (Min-Max归一化)

        Args:
            column: 输入数据序列

        Returns:
            pd.Series: 归一化后的数据
        """
        num_max = column.max()
        num_min = column.min()
        if num_max == num_min:
            return pd.Series([0.5] * len(column), index=column.index)
        column = (column - num_min) / (num_max - num_min)
        return column

    @classmethod
    def normal2value(cls, data: Union[float, pd.Series], parser_month: str,
                     stock_code: str, match_column: str) -> Union[float, pd.Series]:
        """
        将归一化值转换回原始值

        Args:
            data: 归一化后的数据
            parser_month: 解析月份
            stock_code: 股票代码
            match_column: 匹配列名

        Returns:
            原始尺度的数据
        """
        from App.codes.utils.file_io import ReadSaveFile

        parser_data = ReadSaveFile.read_json(parser_month, stock_code)
        if parser_data is None:
            raise ValueError(f"无法读取解析数据: {parser_month}, {stock_code}")
        parser_data = parser_data[stock_code][match_column]

        high = parser_data['num_max']
        low = parser_data['num_min']

        num_normal = data * (high - low) + low
        return num_normal


# 便捷函数别名
normalize = MathematicalFormula.data2normalization
filter_outliers_mad = MathematicalFormula.filter_median
filter_outliers_sigma = MathematicalFormula.filter_3sigma
