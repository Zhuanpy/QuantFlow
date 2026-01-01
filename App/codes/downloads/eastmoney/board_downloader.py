# -*- coding: utf-8 -*-
"""
板块数据下载模块

提供从东方财富下载板块数据的功能
"""

import json
import re
import logging
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup as soup

from App.codes.downloads.eastmoney.http_client import EastMoneyHttpClient
from App.codes.downloads.eastmoney.data_parser import get_1m_data, show_download
from download_utils import page_source
from App.codes.RnnDataFile.parser import my_headers, my_url

logger = logging.getLogger(__name__)


class BoardDownloader:
    """板块数据下载器"""

    @classmethod
    def board_1m_data(cls, code: str) -> pd.DataFrame:
        """
        下载板块单日1分钟数据

        Args:
            code: 板块代码

        Returns:
            pd.DataFrame: 板块数据
        """
        headers = my_headers('board_1m_data')
        url = my_url('board_1m_data').format(code)
        source = page_source(url=url, headers=headers)
        dl = get_1m_data(source, match=True, multiple=False)

        show_download('1m', code)
        return dl

    @classmethod
    def board_1m_multiple(cls, code: str, days: int = 5) -> pd.DataFrame:
        """
        下载板块多天1分钟数据

        Args:
            code: 板块代码（如 BK0437）
            days: 需要下载的天数

        Returns:
            pd.DataFrame: 下载的板块数据
        """
        logger.info(f"开始下载板块 {code} 的 {days} 天数据...")

        url = my_url('board_1m_multiple_days').format(days, code)
        logger.info(f"尝试访问URL: {url}")

        source = EastMoneyHttpClient.get_source_with_rotation(url, 'board_1m_multiple_days')

        if not source:
            logger.warning(f"Failed to retrieve data for {code}. Source is empty.")
            return pd.DataFrame()

        logger.info(f"开始解析下载的数据...")
        dl = get_1m_data(source, match=False, multiple=True)

        if dl.empty:
            logger.warning(f"板块 {code} 数据解析后为空")
            return dl

        show_download('1m', code)
        logger.info("*" * 80)
        logger.info(f"成功下载板块 {code} 的 {len(dl)} 条记录")
        logger.info("*" * 80)
        return dl

    @classmethod
    def industry_list(cls) -> pd.DataFrame:
        """
        下载板块列表

        Returns:
            pd.DataFrame: 板块列表
        """
        from selenium import webdriver

        web = 'http://quote.eastmoney.com/center/boardlist.html#industry_board'
        driver = webdriver.Chrome()
        driver.get(web)

        source = driver.page_source
        bs_data = soup(source, 'html.parser')
        board_data = bs_data.find('li', class_='sub-items menu-industry_board-wrapper')
        board_data = board_data.find_all('li')
        data = pd.DataFrame(data=None)

        for i in range(len(board_data)):
            board_name = board_data[i].find(class_='text').text
            board_code = str(board_data[i].find('a')['href']).strip()[-6:]
            data.loc[i, 'board_name'] = board_name
            data.loc[i, 'board_code'] = board_code

        driver.close()
        data['stock_name'] = None
        data['stock_code'] = None
        return data

    @classmethod
    def industry_ind_stock(cls, name: str, code: str, num: int = 300) -> Optional[pd.DataFrame]:
        """
        下载板块成份股

        Args:
            name: 板块名称
            code: 板块代码
            num: 最大返回数量

        Returns:
            Optional[pd.DataFrame]: 板块成份股数据
        """
        url = my_url('industry_ind_stock').format(num, code)
        headers = my_headers('industry_ind_stock')
        source = page_source(url=url, headers=headers)

        dl = None
        if source:
            p1 = re.compile(r'[(](.*?)[)]', re.S)
            page_data = re.findall(p1, source)
            json_data = json.loads(page_data[0])['code_data']['diff']

            values_list = []
            for i in range(len(json_data)):
                values = list(json_data[i].values())
                values_list.append(values)

            key_list = list(json_data[0])
            dl = pd.DataFrame(data=values_list, columns=key_list)

            rename_ = {
                'f3': '涨跌幅', 'f4': '涨跌额', 'f5': '成交量', 'f6': '成交额',
                'f7': '振幅', 'f8': '换手率', 'f9': '市盈率动', 'f10': '量比',
                'f12': 'stock_code', 'f14': 'stock_name', 'f15': 'close',
                'f16': 'low', 'f17': 'open', 'f18': 'preclose', 'f20': '总市值',
                'f21': '流通市值', 'f23': '市净率', 'f115': '市盈率'
            }

            dl = dl.rename(columns=rename_)

            dl = dl.drop(columns=['f1', 'f2', 'f11', 'f13', 'f22', 'f24', 'f25', 'f45',
                                  'f62', 'f128', 'f140', 'f141', 'f136', 'f152'])

            dl['board_name'] = name
            dl['board_code'] = code
            dl['date'] = pd.Timestamp('today').date()

            dl = dl[['board_name', 'board_code', 'stock_code', 'stock_name', 'date']]

        return dl
