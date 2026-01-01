# -*- coding: utf-8 -*-
"""
数据解析模块

提供东方财富数据的解析和处理功能，包括：
- JSON 数据解析
- DataFrame 数据处理
- 数据验证
"""

import json
import re
import logging
from dataclasses import dataclass
from datetime import time as dt_time
from typing import List

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DataValidationConfig:
    """数据验证配置"""
    REQUIRED_COLUMNS = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
    MIN_ROWS = 1
    TRADING_START_TIME = dt_time(9, 30)
    TRADING_END_TIME = dt_time(15, 0)


def parse_json(source: str, match: bool) -> List[str]:
    """
    解析 JSON 数据

    Args:
        source: 原始数据字符串
        match: 是否进行正则匹配（用于 JSONP 格式）

    Returns:
        List[str]: 解析后的数据列表
    """
    try:
        if not source:
            logger.error("收到空的源数据")
            return []

        if match:
            pattern = re.compile(r'jQuery\d+_\d+\((.*)\)')
            matches = re.findall(pattern, source)
            if not matches:
                logger.error(f"正则匹配失败，源数据前200字符: {source[:200]}...")
                return []
            json_str = matches[0]
        else:
            json_str = source

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误: {e}")
            return []

        if not isinstance(data, dict):
            logger.error(f"数据格式错误，预期dict，实际: {type(data)}")
            return []

        if 'data' not in data:
            logger.error("数据中缺少'data'字段")
            return []

        if data['data'] is None:
            rc = data.get('rc', 0)
            rt = data.get('rt', 0)
            logger.error(f"data字段为None，API返回 rc={rc}, rt={rt}")
            return []

        data_fields = ['klines', 'trends', 'data']
        result_data = None

        for field in data_fields:
            if isinstance(data['data'], dict) and field in data['data']:
                field_value = data['data'][field]
                if isinstance(field_value, list) and len(field_value) > 0:
                    result_data = field_value
                    logger.info(f"找到有效数据字段: {field}，包含 {len(result_data)} 条记录")
                    break

        if result_data is None:
            available_fields = list(data['data'].keys()) if isinstance(data['data'], dict) else []
            logger.warning(f"数据中缺少标准数据字段，可用字段: {available_fields}")

            if isinstance(data['data'], dict) and 'code' in data['data'] and 'market' in data['data']:
                for key in ['trends', 'klines', 'data', 'list']:
                    if key in data['data'] and isinstance(data['data'][key], list) and len(data['data'][key]) > 0:
                        result_data = data['data'][key]
                        logger.info(f"在板块数据中找到数据字段: {key}")
                        break

            if result_data is None:
                logger.error(f"无法从数据中提取数据，可用字段: {available_fields}")
                return []

        logger.info(f"成功提取数据，包含 {len(result_data)} 条记录")
        return result_data

    except Exception as e:
        logger.error(f"解析JSON过程发生异常: {e}", exc_info=True)
        return []


def process_data(df: pd.DataFrame, multiple: bool) -> pd.DataFrame:
    """
    处理并清理数据

    Args:
        df: 原始数据框
        multiple: 是否将09:30数据合并为09:31数据

    Returns:
        pd.DataFrame: 清理后的数据框
    """
    if df.empty:
        return df

    df['date'] = pd.to_datetime(df['date'])
    df[['open', 'close', 'high', 'low', 'volume', 'money']] = df[
        ['open', 'close', 'high', 'low', 'volume', 'money']].astype(float)
    df['volume'] = (df['volume'] * 100).astype('int64')
    df[['volume', 'money']] = df[['volume', 'money']].astype('int64')

    if multiple:
        df['day'] = df['date'].dt.date
        df['time'] = df['date'].dt.time

        df.loc[df['time'] == pd.Timestamp('09:30:00').time(), 'time'] = pd.Timestamp('09:31:00').time()

        grouped = df.groupby(['day', 'time']).agg({
            'date': 'last',
            'open': 'first',
            'close': 'last',
            'high': 'max',
            'low': 'min',
            'volume': 'sum',
            'money': 'sum'
        }).reset_index(drop=False)

        grouped = grouped.drop(columns=['day', 'time'])
        return grouped

    else:
        if df.iloc[0]['date'].time() == pd.Timestamp('09:30:00').time():
            df.loc[1, ['volume', 'money']] += df.loc[0, ['volume', 'money']]
            df = df.iloc[1:].reset_index(drop=True)

        return df


def get_1m_data(source: str, match: bool = False, multiple: bool = False) -> pd.DataFrame:
    """
    获取1分钟数据并进行清理

    Args:
        source: 原始数据源
        match: 是否进行正则匹配
        multiple: 是否将09:30数据合并为09:31数据

    Returns:
        pd.DataFrame: 清理后的数据框
    """
    try:
        if not source or not isinstance(source, str):
            logger.error(f"无效的源数据格式: {type(source)}")
            return pd.DataFrame()

        logger.info(f"开始解析数据，数据长度: {len(source)}")

        trends = parse_json(source, match)

        if not trends:
            logger.error("解析JSON数据失败，未获取到数据")
            return pd.DataFrame()

        logger.info(f"成功解析JSON数据，获取到 {len(trends)} 条记录")

        try:
            if trends and len(trends) > 0:
                first_row_cols = len(trends[0].split(','))
                logger.info(f"检测到数据列数: {first_row_cols}")

                if first_row_cols == 7:
                    columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
                elif first_row_cols == 8:
                    columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money', 'avg_price']
                else:
                    logger.warning(f"意外的列数: {first_row_cols}，使用默认列名")
                    columns = [f'col_{i + 1}' for i in range(first_row_cols)]
            else:
                columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']

            df = pd.DataFrame([x.split(',') for x in trends], columns=columns)
            logger.info(f"成功创建DataFrame，形状: {df.shape}")
        except Exception as e:
            logger.error(f"创建DataFrame失败: {e}")
            return pd.DataFrame()

        try:
            data = process_data(df, multiple)
            if not data.empty:
                logger.info(f"数据处理成功，最终数据形状: {data.shape}")
            return data
        except Exception as e:
            logger.error(f"数据处理失败: {e}")
            return pd.DataFrame()

    except Exception as e:
        logger.error(f"get_1m_data处理过程发生异常: {e}", exc_info=True)
        return pd.DataFrame()


def validate_data(df: pd.DataFrame) -> bool:
    """
    验证下载的数据是否有效

    Args:
        df: 待验证的数据框

    Returns:
        bool: 数据是否有效
    """
    if df.empty:
        logger.error("数据为空")
        return False

    missing_cols = set(DataValidationConfig.REQUIRED_COLUMNS) - set(df.columns)
    if missing_cols:
        logger.error(f"缺少必需列: {missing_cols}")
        return False

    if len(df) < DataValidationConfig.MIN_ROWS:
        logger.error(f"数据行数不足: {len(df)}")
        return False

    if 'date' in df.columns:
        times = pd.to_datetime(df['date']).dt.time
        valid_times = times.between(
            DataValidationConfig.TRADING_START_TIME,
            DataValidationConfig.TRADING_END_TIME
        )
        if not valid_times.all():
            logger.error("存在交易时间范围外的数据")
            return False

    return True


def show_download(freq: str, code: str) -> None:
    """显示下载成功的信息"""
    logger.info(f'Success Download {freq} Data: {code}')


def convert_currency_unit(unit: str) -> int:
    """
    货币单位转换

    Args:
        unit: 货币单位，支持 '亿', '万', '百万', '千万'

    Returns:
        int: 对应货币单位的数值
    """
    unit_mapping = {
        '亿': 100000000,
        '万': 10000,
        '百万': 1000000,
        '千万': 10000000
    }
    return unit_mapping.get(unit, 1)


def return_funds_data(source: str, date_new: pd.Timestamp,
                      table_index: int = 1, relevant_columns: list = None) -> pd.DataFrame:
    """
    从网页源代码中提取并处理资金数据

    Args:
        source: 包含HTML表格数据的网页源代码
        date_new: 交易日期
        table_index: 指定要读取的表格索引
        relevant_columns: 返回的数据框中所需的列名

    Returns:
        pd.DataFrame: 包含处理后的资金数据
    """
    if relevant_columns is None:
        relevant_columns = ['trade_date', 'stock_code', 'stock_name', 'industry']

    df = pd.read_html(source)[table_index]

    columns = ['序号', 'stock_code', 'stock_name', '相关', '今日收盘价', '今日涨跌幅', '今日持股股数',
               '今日持股市值', '今日持股占流通股比', '今日持股占总股本比', '今日增持股数',
               '今日增持市值', '今日增持市值增幅', '今日增持占流通股比', '今日增持占总股本比', 'industry']

    df.columns = columns
    df['trade_date'] = pd.to_datetime(date_new)
    result_df = df[relevant_columns]
    result_df['stock_code'] = result_df['stock_code'].astype(str)

    return result_df


def funds_data_clean(data: pd.DataFrame) -> pd.DataFrame:
    """
    清理并转换资金数据

    Args:
        data: 输入的数据框，包含原始资金数据

    Returns:
        pd.DataFrame: 清理并转换后的资金数据
    """
    data = pd.DataFrame(data.values).iloc[:, [1, 5, 6, 7, 9, 12]]

    data.columns = ['板块', 'NkPT市值', 'NkPT占板块比', 'NkPT占北向资金比', 'NRPT市值', 'NRPT占北向资金比']

    def convert_value(value):
        unit = value[-1]
        number = float(value[:-1])
        return number * convert_currency_unit(unit)

    data['NkPT市值'] = data['NkPT市值'].apply(convert_value)
    data['NRPT市值'] = data['NRPT市值'].apply(convert_value)

    return data
