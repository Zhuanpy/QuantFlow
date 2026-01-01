# -*- coding: utf-8 -*-
"""
股票数据下载模块

提供从东方财富下载股票数据的功能
"""

import json
import re
import time
import logging
from typing import Optional

import requests
import pandas as pd

from App.codes.downloads.eastmoney.http_client import EastMoneyHttpClient
from App.codes.downloads.eastmoney.data_parser import (
    get_1m_data,
    show_download,
    DataValidationConfig,
)
from download_utils import UrlCode
from App.codes.RnnDataFile.parser import my_url

logger = logging.getLogger(__name__)


class StockDownloader:
    """股票数据下载器"""

    MAX_RETRIES = 3
    RETRY_DELAY = 2

    @classmethod
    def validate_data(cls, df: pd.DataFrame) -> bool:
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

    @classmethod
    def stock_1m_1day(cls, code: str) -> Optional[pd.DataFrame]:
        """
        下载 1 天的 1 分钟股票数据

        Args:
            code: 股票代码

        Returns:
            Optional[pd.DataFrame]: 下载的股票数据
        """

        def get_stock_data(code: str) -> Optional[pd.DataFrame]:
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Referer': 'http://quote.eastmoney.com/center/gridlist.html',
                }

                timestamp = int(time.time() * 1000)
                params = {
                    'cb': f'jQuery112409537552066923317_{timestamp}',
                    'secid': f'0.{code}',
                    'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                    'fields1': 'f1,f2,f3,f4,f5,f6',
                    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
                    'klt': '1',
                    'fqt': '1',
                    'end': '20500101',
                    'lmt': '1000',
                    '_': timestamp
                }

                url = "http://83.push2.eastmoney.com/api/qt/stock/kline/get"
                response = requests.get(url, params=params, headers=headers, timeout=15)
                response.raise_for_status()

                text = response.text
                json_str = re.search(r'jQuery\d+_\d+\((.*?)\)', text)
                if not json_str:
                    logger.error("无法提取JSON数据")
                    return None

                data = json.loads(json_str.group(1))
                if not data.get('data', {}).get('klines'):
                    logger.error("数据结构中没有klines数据")
                    return None

                klines = data['data']['klines']
                logger.info(f"获取到 {len(klines)} 条K线数据")

                df_data = [item.split(',') for item in klines]
                if not df_data:
                    logger.error("分割数据后为空")
                    return None

                columns_count = len(df_data[0])
                logger.info(f"数据列数: {columns_count}")

                df = pd.DataFrame(df_data)

                all_columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money',
                               'amplitude', 'change_percent', 'change_amount', 'turnover']
                df.columns = all_columns[:columns_count]

                try:
                    df['date'] = pd.to_datetime(df['date'])
                    numeric_columns = ['open', 'close', 'high', 'low']
                    for col in numeric_columns:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')

                    if 'volume' in df.columns:
                        df['volume'] = pd.to_numeric(df['volume'], errors='coerce') * 100
                    if 'money' in df.columns:
                        df['money'] = pd.to_numeric(df['money'], errors='coerce')

                    optional_numeric = ['amplitude', 'change_percent', 'change_amount', 'turnover']
                    for col in optional_numeric:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')

                    logger.info("数据类型转换成功")

                except Exception as e:
                    logger.error(f"数据类型转换失败: {str(e)}")
                    return None

                required_columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
                if not all(col in df.columns for col in required_columns):
                    logger.error(f"缺少必要的列，当前列: {df.columns.tolist()}")
                    return None

                return df[required_columns]

            except Exception as e:
                logger.error(f"获取数据失败: {str(e)}")
                return None

        for attempt in range(cls.MAX_RETRIES):
            try:
                logger.info(f"开始下载股票 {code} 的1分钟数据 (尝试 {attempt + 1}/{cls.MAX_RETRIES})")

                df = get_stock_data(code)
                if df is not None and not df.empty:
                    logger.info(f"成功下载并解析 {code} 的1分钟数据，共 {len(df)} 条记录")
                    show_download('1m', code)
                    return df

                logger.error(f"获取数据失败或数据为空: {code}")
                if attempt < cls.MAX_RETRIES - 1:
                    time.sleep(cls.RETRY_DELAY)

            except Exception as e:
                logger.error(f"处理过程发生异常 {code}: {str(e)}")
                if attempt < cls.MAX_RETRIES - 1:
                    time.sleep(cls.RETRY_DELAY)
                continue

        logger.error(f"在 {cls.MAX_RETRIES} 次尝试后仍然失败: {code}")
        return None

    @classmethod
    def stock_1m_days(cls, code: str, days: int = 5) -> pd.DataFrame:
        """
        下载 N 天的 1 分钟股票数据（智能三阶段策略）

        优先级顺序：
        1. pytdx（稳定快速）
        2. 东方财富 API（请求头轮换）
        3. Selenium（浏览器模拟）

        Args:
            code: 股票代码
            days: 需要下载的天数

        Returns:
            pd.DataFrame: 下载的股票数据
        """
        logger.info("*" * 80)
        logger.info(f"开始下载股票 {code} 的 {days} 天数据")
        logger.info("*" * 80)

        try:
            # 第一阶段：优先使用 pytdx
            logger.info("=" * 60)
            logger.info("第一阶段：使用 pytdx 获取数据（优先方案）")
            logger.info("=" * 60)

            try:
                from App.codes.downloads.DlPytdx import download_stock_1m_pytdx

                df_pytdx, end_date = download_stock_1m_pytdx(code, days)

                if not df_pytdx.empty:
                    logger.info(f"pytdx 成功获取 {len(df_pytdx)} 条数据")
                    show_download('1m', code)
                    logger.info("*" * 80)
                    logger.info(f"成功下载股票 {code} 的 {len(df_pytdx)} 条记录")
                    logger.info("*" * 80)
                    return df_pytdx
                else:
                    logger.warning("pytdx 返回空数据，尝试备用方案")

            except ImportError:
                logger.warning("pytdx 未安装，跳过第一阶段")
            except Exception as e:
                logger.warning(f"pytdx 获取失败: {e}")

            # 第二阶段：使用东方财富 API
            logger.info("")
            logger.info("=" * 60)
            logger.info("第二阶段：使用东方财富 API（备用方案）")
            logger.info("=" * 60)

            url = my_url('stock_1m_multiple_days').format(days, UrlCode(code))

            source = EastMoneyHttpClient.get_source_with_rotation(url, 'stock_1m_multiple_days')

            if not source:
                logger.error(f"所有数据源都失败，无法获取股票 {code} 的数据")
                return cls._handle_empty_source(code)

            logger.info(f"开始解析下载的数据...")
            dl = get_1m_data(source, match=False, multiple=True)

            if dl.empty:
                logger.warning(f"股票 {code} 数据解析后为空")
                return dl

            show_download('1m', code)
            logger.info("*" * 80)
            logger.info(f"成功下载股票 {code} 的 {len(dl)} 条记录")
            logger.info("*" * 80)
            return dl

        except Exception as e:
            logger.error(f"下载股票 {code} 数据时发生异常: {str(e)}")
            import traceback
            logger.error(f"异常堆栈: {traceback.format_exc()}")
            return pd.DataFrame()

    @staticmethod
    def _handle_empty_source(code: str) -> pd.DataFrame:
        """处理空数据源"""
        logger.warning(f"Failed to retrieve data for {code}. Source is empty.")
        return pd.DataFrame()
