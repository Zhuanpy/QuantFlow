# -*- coding: utf-8 -*-
import json
from download_utils import page_source
import time
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)

headers = {'accept': '*/*',
           'accept-encoding': 'gzip, deflate, br',
           'accept-language': 'en-US,en;q=0.9',
           'referer': 'https://s.yimg.com/rq/darla/4-8-0/html/r-sf.html',
           'sec-ch-ua': '" Not;A Brand";v="99", "Google Chrome";v="91", "Chromium";v="91"',
           'sec-ch-ua-mobile': '?0',
           'sec-fetch-dest': 'script',
           'sec-fetch-mode': 'no-cors',
           'sec-fetch-site': 'cross-site',
           'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}


def yahoo_code(stock_code):
    if stock_code[0] == '0' or stock_code[0] == '3':
        stock_code = f'{stock_code}.SZ'

    if stock_code[0] == '6':
        stock_code = f'{stock_code}.SS'

    else:
        print(f'股票:{stock_code}无yahoo市场分类')

    return stock_code


class DownloadData:

    @classmethod
    def download_stock_1m_data(cls, stock_name, stock_code):
        cd = yahoo_code(stock_code)
        web_title = f'https://query1.finance.yahoo.com/v8/finance/chart/{cd}?symbol={cd}&'

        str_start = time.strptime(pd.Timestamp('now').date().strftime('%Y-%m-%d %H:%M:%S'), '%Y-%m-%d %H:%M:%S')
        str_end = time.strptime((pd.Timestamp().today() + pd.Timedelta(days=1)).date().strftime('%Y-%m-%d %H:%M:%S'),
                                '%Y-%m-%d %H:%M:%S')

        start_time = int(time.mktime(str_start))
        end_time = int(time.mktime(str_end))

        web_time_line = f'period1={start_time}&period2={end_time}&'
        web_end = 'useYfid=true&interval=1m&includePrePost=true&events=' \
                  'div%7Csplit%7Cearn&lang=zh-Hant-HK&region=HK&crumb=' \
                  'ChYXqUDJwy8&corsDomain=hk.finance.yahoo.com'

        web_site = f'{web_title}{web_time_line}{web_end}'

        source = page_source(url=web_site, headers=headers)
        download_data = json.loads(source)
        quote = download_data['chart']['result'][0]['indicators']['quote'][0]
        quote = pd.DataFrame(data=quote)
        timestamp = pd.DataFrame(download_data['chart']['result'][0]['timestamp'], columns=['timestamp'])
        timestamp.loc[:, 'date'] = pd.to_datetime(timestamp['timestamp'], unit='s', origin='1970-01-01 08:00:00')

        data = pd.concat([quote, timestamp], axis=1).dropna(subset=['close'])
        data = data[data['volume'] > 0]
        data.loc[:, 'money'] = None
        columns_list = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
        data = data[columns_list].reset_index(drop=True)

        # 一天的数据交易时间出现13:00：00 和 14:57:00 不正常数据；
        data.loc[:, 'minute'] = pd.to_datetime(data['trade_date']).dt.time
        data.loc[:, 'date'] = pd.to_datetime(data['trade_date']).dt.date

        # 清洗交易时间 13:00：00 数据
        if len(data[data['minute'] == pd.to_datetime('13:00:00').time()]) > 0:
            index_num = data[data['minute'] == pd.to_datetime('13:00:00').time()].index
            data.loc[index_num, 'trade_date'] = data.loc[index_num, 'trade_date'] + pd.Timedelta(minutes=-90)

        # 清洗最后一行数据时间不是15:00:00结尾数据
        if data.iloc[-1]['minute'] > pd.to_datetime('14:55:00').time():
            data.loc[data.tail(1).index, 'trade_date'] = pd.to_datetime(data.iloc[-1]['date']) + pd.Timedelta(
                minutes=900)

        st_con = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
        data = data[st_con]

        print(f'雅虎财经下载1m数据成功: {stock_name};')
        return data


if __name__ == '__main__':
    st = DownloadData()
