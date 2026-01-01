# -*- coding: utf-8 -*-
"""
基金数据下载模块

提供从东方财富下载基金和北向资金数据的功能
"""

import json
import re
import time
import logging
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup as soup

from download_utils import page_source
from App.codes.RnnDataFile.parser import my_headers, my_url

logger = logging.getLogger(__name__)


class FundDownloader:
    """基金数据下载器"""

    @classmethod
    def funds_to_stock(cls) -> pd.DataFrame:
        """
        从东方财富网下载北向资金流入个股数据

        Returns:
            pd.DataFrame: 北向资金流入个股数据
        """
        from selenium import webdriver
        from App.codes.downloads.eastmoney.data_parser import return_funds_data

        page1 = 'http://data.eastmoney.com/hsgtcg/list.html'
        page2 = '/html/body/div[1]/div[8]/div[2]/div[2]/div[2]/div[3]/div[3]/div[1]/a[2]'
        page3 = '/html/body/div[1]/div[8]/div[2]/div[2]/div[2]/div[3]/div[3]/div[1]/a[4]'
        path_date = '/html/body/div[1]/div[8]/div[2]/div[2]/div[1]/div[1]/div/span'

        driver = webdriver.Chrome()
        driver.get(page1)

        new_date = driver.find_element('xpath', path_date).text[1:-1]
        new_date = pd.to_datetime(new_date)

        # 获取第1页50条数据
        source01 = driver.page_source
        dl01 = return_funds_data(source01, new_date)

        # 获取第2页50条数据
        driver.find_element('xpath', page2).click()
        time.sleep(6)
        source02 = driver.page_source
        dl02 = return_funds_data(source02, new_date)

        # 获取第3页50条数据
        driver.find_element('xpath', page3).click()
        time.sleep(6)
        source03 = driver.page_source
        dl03 = return_funds_data(source03, new_date)
        driver.close()

        data = pd.concat([dl01, dl02, dl03], ignore_index=True).reset_index(drop=True)

        logger.info(f'东方财富下载{new_date}日北向资金流入个股据成功')
        return data

    @classmethod
    def funds_month_history(cls) -> pd.DataFrame:
        """
        下载北向资金近1个月流入数据

        Returns:
            pd.DataFrame: 近一个月北向资金数据
        """
        headers = my_headers('funds_month_history')
        url = my_url('funds_month_history')
        source = page_source(url=url, headers=headers)

        p1 = re.compile(r'[(](.*?)[)]', re.S)
        dl = re.findall(p1, source)[0]
        dl = pd.DataFrame(data=json.loads(dl)['result']['code_data'])
        dl = dl[['TRADE_DATE', 'NET_INFLOW_SH', 'NET_INFLOW_SZ', 'NET_INFLOW_BOTH']]
        dl.loc[:, 'TRADE_DATE'] = pd.to_datetime(dl['TRADE_DATE']).dt.date
        dl = dl.rename(columns={'TRADE_DATE': 'trade_date'})

        logger.info('东方财富下载近一个月北向资金数据成功')
        return dl

    @classmethod
    def funds_daily_data(cls) -> pd.DataFrame:
        """
        下载当日北向资金数据

        Returns:
            pd.DataFrame: 当日北向资金数据
        """
        from selenium import webdriver

        web_01 = 'https://data.eastmoney.com/hsgt/'
        driver = webdriver.Chrome()
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(60)
        driver.get(web_01)

        update_xpath = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[1]/div[3]/span'
        hmx = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[6]/ul[1]/li[1]/span[2]/span/span'
        zmx = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[6]/ul[1]/li[2]/span[2]/span/span'
        nsx = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[6]/ul[1]/li[3]/span[2]/span/span'

        update = driver.find_element('xpath', update_xpath).text
        money_sh = float(driver.find_element('xpath', hmx).text[:-2]) * 100
        money_sz = float(driver.find_element('xpath', zmx).text[:-2]) * 100
        sum_north = float(driver.find_element('xpath', nsx).text[:-2]) * 100

        driver.close()

        dic_ = {
            'trade_date': [pd.to_datetime(update)],
            'NET_INFLOW_SH': [money_sh],
            'NET_INFLOW_SZ': [money_sz],
            'NET_INFLOW_BOTH': [sum_north]
        }

        df = pd.DataFrame(data=dic_)

        logger.info(f'东方财富下载{update}日北向资金成功')
        return df

    @classmethod
    def funds_to_sectors(cls, date_: str) -> pd.DataFrame:
        """
        下载北向资金流入板块数据

        Args:
            date_: 日期字符串

        Returns:
            pd.DataFrame: 北向资金流入板块数据
        """
        headers = my_headers('funds_to_sectors')
        url = my_url('funds_to_sectors').format(date_)
        source_ = page_source(url=url, headers=headers)

        try:
            p1 = re.compile(r'[(](.*?)[)]', re.S)
            page_data = re.findall(p1, source_)
            json_data = json.loads(page_data[0])
            json_data = json_data['result']['code_data']

            value_list = []
            for i in range(len(json_data)):
                values = list(json_data[i].values())
                value_list.append(values)

            key_list = list(json_data[0])
            df = pd.DataFrame(data=value_list, columns=key_list)

            columns = [
                'SECURITY_CODE', 'BOARD_CODE', 'BOARD_NAME', 'TRADE_DATE', 'COMPOSITION_QUANTITY',
                'ADD_MARKET_CAP', 'BOARD_VALUE', 'HK_VALUE', 'HK_BOARD_RATIO', 'MAXADD_SECURITY_CODE',
                'MAXADD_SECURITY_NAME', 'MINADD_SECURITY_CODE', 'MINADD_SECURITY_NAME',
                'MAXADD_RATIO_SECURITY_NAME', 'MAXADD_RATIO_SECURITY_CODE',
                'MINADD_RATIO_SECURITY_NAME', 'MINADD_RATIO_SECURITY_CODE'
            ]

            df = df[columns]
            df['TRADE_DATE'] = pd.to_datetime(df['TRADE_DATE']).dt.date

        except TypeError:
            logger.error(f'东方财富下载 Funds to Sectors 数据异常')
            df = pd.DataFrame(data=None)

        return df

    @classmethod
    def funds_awkward(cls, code: str) -> pd.DataFrame:
        """
        获取基金持仓数据，尝试多种方法

        Args:
            code: 基金代码

        Returns:
            pd.DataFrame: 基金持仓数据
        """
        # 首先尝试API方法
        data = cls.funds_awkward_api(code)
        if not data.empty:
            logger.info(f"API方法成功获取基金 {code} 数据")
            return data

        # 如果API失败，尝试网页解析方法
        logger.info(f"API方法失败，尝试网页解析方法")
        return cls.funds_awkward_web(code)

    @classmethod
    def funds_awkward_api(cls, code: str) -> pd.DataFrame:
        """
        尝试使用API接口获取基金持仓数据

        Args:
            code: 基金代码

        Returns:
            pd.DataFrame: 基金持仓数据
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/106.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': f'http://fund.eastmoney.com/{code}.html',
                'X-Requested-With': 'XMLHttpRequest'
            }

            logger.info(f"尝试API方式获取基金 {code} 数据")

            api_endpoints = [
                f"http://fund.eastmoney.com/api/FundPosition/{code}",
                f"http://fund.eastmoney.com/api/FundHoldings/{code}",
                f"http://fund.eastmoney.com/data/fbsfundranking.html?ft={code}",
            ]

            for api_url in api_endpoints:
                try:
                    source = page_source(url=api_url, headers=headers)
                    if source and len(source) > 100:
                        logger.info(f"API {api_url} 返回数据长度: {len(source)}")

                        try:
                            data = json.loads(source)
                            logger.info(f"成功解析JSON数据: {type(data)}")
                            return cls._parse_api_data(data, code)
                        except json.JSONDecodeError:
                            logger.warning(f"API返回的不是有效JSON: {api_url}")
                            continue

                except Exception as e:
                    logger.warning(f"API {api_url} 请求失败: {e}")
                    continue

            logger.warning(f"所有API尝试都失败了")
            return pd.DataFrame()

        except Exception as e:
            logger.error(f"API方式获取基金 {code} 数据时发生错误: {e}")
            return pd.DataFrame()

    @classmethod
    def _parse_api_data(cls, data: dict, code: str) -> pd.DataFrame:
        """解析API返回的数据"""
        try:
            li_name = []
            li_code = []

            if isinstance(data, dict):
                possible_fields = ['data', 'result', 'list', 'stocks', 'positions', 'holdings']

                for field in possible_fields:
                    if field in data:
                        items = data[field]
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, dict):
                                    stock_name = item.get('name') or item.get('stock_name') or item.get('title')
                                    stock_code = item.get('code') or item.get('stock_code') or item.get('id')

                                    if stock_name and stock_code:
                                        li_name.append(str(stock_name))
                                        li_code.append(str(stock_code))
                                        logger.info(f"从API解析到股票: {stock_name} ({stock_code})")

            if li_name and li_code:
                dic = {'stock_name': li_name, 'stock_code': li_code}
                return pd.DataFrame(dic)
            else:
                logger.warning(f"API数据中未找到有效的股票信息")
                return pd.DataFrame()

        except Exception as e:
            logger.error(f"解析API数据时发生错误: {e}")
            return pd.DataFrame()

    @classmethod
    def funds_awkward_web(cls, code: str) -> pd.DataFrame:
        """
        从网页解析基金持仓数据

        Args:
            code: 基金代码

        Returns:
            pd.DataFrame: 基金持仓数据
        """
        try:
            url = my_url('funds_awkward').format(code)
            headers = my_headers('funds_awkward')

            if not url:
                logger.error(f"基金 {code} 的URL配置为空")
                return pd.DataFrame()

            logger.info(f"正在下载基金 {code} 的数据，URL: {url}")

            source = page_source(url=url, headers=headers)

            if not source:
                logger.error(f"基金 {code} 页面源码为空")
                return pd.DataFrame()

            soup_obj = soup(source, 'html.parser')
            if not soup_obj:
                logger.error(f"基金 {code} HTML解析失败")
                return pd.DataFrame()

            stock_links = soup_obj.find_all("a", href=lambda href: href and '/stock/' in href)

            if not stock_links:
                logger.warning(f"基金 {code} 未找到股票链接")
                return pd.DataFrame()

            li_name = []
            li_code = []

            for link in stock_links:
                try:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)

                    if '/stock/' in href:
                        code_match = href.split('/stock/')[-1].split('.')[0]
                        if len(code_match) == 6 and code_match.isdigit():
                            stock_code = code_match
                            stock_name = text

                            if stock_name and len(stock_name) > 0:
                                stock_name = stock_name.replace('\n', '').replace('\r', '').replace('\t', '').strip()

                                li_name.append(stock_name)
                                li_code.append(stock_code)
                                logger.info(f"基金 {code} 解析到股票: {stock_name} ({stock_code})")

                except Exception as e:
                    logger.warning(f"解析基金 {code} 股票链接时出错: {e}")
                    continue

            if not li_name or not li_code:
                logger.warning(f"基金 {code} 未解析到有效的股票数据")
                return pd.DataFrame()

            dic = {'stock_name': li_name, 'stock_code': li_code}
            data = pd.DataFrame(dic)

            logger.info(f"基金 {code} 成功下载 {len(data)} 条股票数据")
            return data

        except Exception as e:
            logger.error(f"下载基金 {code} 数据时发生错误: {e}")
            return pd.DataFrame()
