# -*- coding: utf-8 -*-
# Download_juquan 依赖 jqdatasdk，未安装时不应阻塞整个模块加载
try:
    from DlJuQuan import DownloadData as Download_juquan
except Exception:
    Download_juquan = None
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