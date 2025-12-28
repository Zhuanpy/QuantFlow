# -*- coding: utf-8 -*-
from DlJuQuan import DownloadData as Download_juquan
from DlEastMoney import DownloadData as Download_east
import pandas as pd
import time

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


def download_1m(stock, code, days):

    try:
        df = Download_east.stock_1m_days(code, days=days)

    except Exception as ex:
        # todo 引入日志
        print(f'东方财富下载{stock}1m数据异常：{ex};')
        df = pd.DataFrame()

    return df