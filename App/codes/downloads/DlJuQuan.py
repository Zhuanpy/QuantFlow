# -*- coding: utf-8 -*-
from jqdatasdk import *

auth('18780645482', 'Xiaohuhu*123')

import pandas as pd


def JQ_code(code: str) -> str:
    market_suffix = {
        '6': '.XSHG',
        '0': '.XSHE',
        '3': '.XSHE'
    }

    for prefix, suffix in market_suffix.items():
        if code.startswith(prefix):
            return f'{code}{suffix}'

    raise ValueError(f'股票:{code} JQ无市场分类')


def fuquan_value(fq):

    fq_mapping = {
        '前复权': 'pre',
        '后复权': 'post',
        '不复权': None
    }
    return fq_mapping.get(fq, fq)  # 默认返回原值，若fq不在字典中


class DownloadData:

    @classmethod
    def download_history_data(cls, code: str, start_date: str,
                              end_date: str, frequency: str, fq_value: str) -> pd.DataFrame:
        """
        下载历史数据并标准化数据格式。

        参数:
            code (str): 股票代码。
            start_date (str): 开始日期。
            end_date (str): 结束日期。
            frequency (str): 数据频率。
            fq_value (str): 复权类型。

        返回:
            pd.DataFrame: 标准化后的历史数据。
        """

        code = JQ_code(code)
        fq = fuquan_value(fq_value)
        download = get_price(code, start_date=start_date, end_date=end_date, frequency=frequency, fq=fq)

        if download.empty:
            print(f'股票:{code}在{start_date}到{end_date}期间无数据。')
            return pd.DataFrame()

        download.reset_index(inplace=True)
        download.rename(columns={'index': 'date'}, inplace=True)
        download['date'] = pd.to_datetime(download['date'])

        float_columns = ['open', 'close', 'high', 'low', 'volume', 'money']
        download[float_columns[:-2]] = download[float_columns[:-2]].astype(float)
        download[float_columns[-2:]] = download[float_columns[-2:]].astype('int64')

        standardized_columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
        download = download[standardized_columns]

        return download

    @classmethod
    def get_index_weight(cls, code: str, date: str = '2021-08-01'):
        """
        获取指定指数在特定日期的权重数据，并进行处理。

        参数:
        code (str): 指数代码
        date (str): 日期 (默认值为 '2021-08-01')

        返回:
        pd.DataFrame: 包含股票代码、股票名称、权重和检查日期的DataFrame
        """
        try:
            wh = get_index_weights(index_id=code, date=date)
            wh = wh.reset_index().sort_values(by='index')
            wh = wh.rename(columns={'index': 'stock_code', 'date': 'check_date', 'display_name': 'stock_name'})
            wh = wh[['stock_code', 'stock_name', 'weight', 'check_date']]
            return wh

        except Exception as e:
            print(f"获取指数权重数据时出错: {e}")
            return None

    @classmethod
    def get_sw_level1_weight(cls, code: str, name: str) -> pd.DataFrame:
        """
        获取某个一级行业的所有股票，并添加行业代码和行业名称。

        参数:
        code (str): 行业代码
        name (str): 行业名称

        返回:
        pd.DataFrame: 包含股票代码、行业代码和行业名称的DataFrame
        """
        l1 = get_industry_stocks(code)  # 假设get_industry_stocks返回一个股票代码列表
        l1 = pd.DataFrame(data=l1, columns=['stock_code'])
        l1 = l1.assign(industry_code=code, industry_name=name)
        return l1

    @classmethod
    def get_sw_level2_weight(cls, code: str, name: str) -> pd.DataFrame:
        """
        获取某个二级行业的所有股票，并添加行业代码和行业名称。

        参数:
        code (str): 行业代码
        name (str): 行业名称

        返回:
        pd.DataFrame: 包含股票代码、行业代码和行业名称的DataFrame
        """
        try:
            l2 = get_industry_stocks(code)  # 假设get_industry_stocks返回一个股票代码列表
            l2_df = pd.DataFrame(data=l2, columns=['stock_code'])
            l2_df = l2_df.assign(industry_code=code, industry_name=name)
            return l2_df

        except Exception as e:
            print(f"获取行业股票数据时出错: {e}")
            return pd.DataFrame()  # 返回一个空的DataFrame以防止后续操作出错

    @classmethod
    def get_all_securities(cls) -> pd.DataFrame:
        """
        获取所有股票的证券信息。

        返回:
        pd.DataFrame: 包含所有股票证券信息的DataFrame
        """
        try:
            data = get_all_securities(types=['stock'], date=None)
            return data

        except Exception as e:
            print(f"获取所有证券信息时出错: {e}")
            return pd.DataFrame()  # 返回一个空的DataFrame以防止后续操作出错


if __name__ == '__main__':
    stock = "000001"
    start_date = "2024-01-01"
    end_date = "2024-07-01"
    frequency = "1m"
    fq_value = '不复权'
    data = DownloadData.download_history_data(stock, start_date, end_date,frequency=frequency, fq_value=fq_value)
    print(data)
