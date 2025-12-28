# -*- coding: utf-8 -*-
import json
from download_utils import page_source
import pandas as pd
from App.codes.RnnDataFile.password import XueqiuParam

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


def market_code(stock_code):

    if stock_code[0] == '6':
        mk = 'SH'

    elif stock_code[0] == '0' or stock_code[0] == '3':
        mk = 'SZ'

    else:
        mk = None
        print(f'雪球无市场类股票：{stock_code};')

    return mk


class DownloadData:

    cookies = XueqiuParam.cookies()
    headers = XueqiuParam.headers()

    @classmethod
    def data_1m(cls, stock_code: str, days=1):
        """
        :param stock_code: 股票代码
        :param days: 获取数据的天数，默认1天
        """

        web_title = 'https://stock.xueqiu.com/v5/stock/chart/minute.json?'
        stock_market = market_code(stock_code)
        web_symbol = f'symbol={stock_market}{stock_code}'  # SZ002475
        web_days = '&period={}d'.format(days)
        web_site = f'{web_title}{web_symbol}{web_days}'

        pagesource = page_source(web_site, cls.headers, cls.cookies)
        json_data = json.loads(pagesource.text)
        data = pd.DataFrame(json_data['code_data']['items'])

        data['timestamp'] = pd.to_datetime(data['timestamp'].values, unit='ms', utc=True).tz_convert(
            'Asia/Shanghai').strftime('%Y-%m-%d %H:%M:%S')

        data['timestamp'] = pd.to_datetime(data['timestamp'])

        data = data[['timestamp', 'avg_price', 'current', 'high', 'low', 'volume', 'amount']]

        names = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
        data.columns = names

        flt = ['open', 'close', 'high', 'low', 'volume']
        data[flt] = data[flt].astype(float)

        data['volume'] = data['volume'] / 100

        data[['volume', 'money']] = data[['volume', 'money']].astype('int64')
        data['volume'] = data['volume'] * 100
        # 删除 09：30 时间数据
        data.loc[1, 'volume'] = data.loc[0, 'volume'] + data.loc[1, 'volume']
        data.loc[1, 'money'] = data.loc[0, 'money'] + data.loc[1, 'money']

        data = data.iloc[1:].reset_index(drop=True)

        print(f'雪球下载1m数据成功: {stock_code};')
        return data

    @classmethod
    def data_daily(cls, stock_code: str, count=100):
        """
        :param stock_code: stock code
        :param count: download days
        """

        title = 'https://stock.xueqiu.com/v5/stock/chart/kline.json?'
        market = market_code(stock_code)
        web_name = f'symbol={market}{stock_code}'

        begin = round((pd.Timestamp('today') + pd.Timedelta(days=1)).timestamp() * 1000)
        web_begin = f'&begin={begin}&period=day&type=before&count=-{count}'

        web_end = '&indicator=kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance'
        web_site = f'{title}{web_name}{web_begin}{web_end}'

        source = page_source(web_site, cls.headers, cls.cookies)

        json_data = json.loads(source)
        data = pd.DataFrame(json_data['code_data']['item'], columns=json_data['code_data']['column'])

        data['timestamp'] = pd.to_datetime(data['timestamp'].values,
                                           unit='ms', utc=True).tz_convert('Asia/Shanghai').date

        data = data.rename(columns={'timestamp': 'date', 'amount': 'money'})

        data = data[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]

        flt = ['open', 'close', 'high', 'low']
        data[flt] = data[flt].astype(float)
        data[['volume', 'money']] = data[['volume', 'money']].astype(int)

        print(f'雪球下载daily数据成功: {stock_code};')

        return data

    @classmethod
    def data_120m(cls, stock_name, stock_code, count):
        market = market_code(stock_code)
        title = f'https://stock.xueqiu.com/v5/stock/chart/kline.json?symbol={market}{stock_code}&'

        begin = round((pd.Timestamp('today') + pd.Timedelta(days=1)).timestamp() * 1000)

        begin_date = f'begin={begin}&period=120m&type=before&count=-{count}&'

        web_end = 'indicator=kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance'

        web_site = f'{title}{begin_date}{web_end}'

        source = page_source(web_site, cls.headers, cls.cookies)
        json_data = json.loads(source.text)
        data = pd.DataFrame(json_data['code_data']['item'], columns=json_data['code_data']['column'])
        data['timestamp'] = pd.to_datetime(data['timestamp'], unit='ms') + pd.Timedelta(hours=8)

        data = data.rename(columns={'timestamp': 'date', 'amount': 'money'})
        data = data[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]
        flt = ['open', 'close', 'high', 'low', 'volume', 'money']
        data[flt] = data[flt].astype(float)

        print(f'雪球下载120m数据成功: {stock_name};')
        return data


if __name__ == '__main__':
    code = "000001"
    data = DownloadData.data_1m(code)
    print(data)
