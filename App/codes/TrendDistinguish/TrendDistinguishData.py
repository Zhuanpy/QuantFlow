import pandas as pd
import numpy as np
from App.codes.utils.Normal import ResampleData
from App.codes.MySql.sql_utils import Stocks
from App.codes.Signals.StatisticsMacd import SignalMethod
from App.codes.MySql.DataBaseStockPool import TableStockPool
from App.codes.MySql.DataBaseStockData1m import StockData1m
from Distinguish_utils import array_data
from App.codes.RnnDataFile.stock_path import AnalysisDataPath

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)


class TrendDistinguishData:
    """
    判断趋势 数据处理
    """

    def __init__(self, stock):
        self.data_15m, self.data_1m = None, None
        self.stock_name, self.stock_code, self.stock_id = Stocks(stock)

    def load_1m(self, _date):
        """
        load 1m code_data
        """
        data = StockData1m.load_1m(self.stock_code, _date)
        data = data[data['date'] >= pd.to_datetime(_date)]
        return data

    def calculates(self, _date):
        self.data_1m = self.load_1m(_date)
        self.data_15m = ResampleData.resample_1m_data(data=self.data_1m, freq='15m')

        self.data_15m = SignalMethod.trend_3ema_MACDBoll(self.data_15m)
        self.data_15m = self.data_15m.dropna(subset=['SignalTimes'])

        return self.data_15m


class CountTrendData(TrendDistinguishData):
    """
    count trend code_data
    """

    def __init__(self, stock, _date='2019-01-01'):
        TrendDistinguishData.__init__(self, stock)
        self.data = self.calculates(_date)

    def save_array_data(self, array_data, file_path):

        array_data.shape = (1, 150, 200, 4)

        try:
            existing_data = np.load(file_path, allow_pickle=True)
            combined_data = np.append(existing_data, array_data, axis=0)
            np.save(file_path, combined_data)

        except FileNotFoundError:

            pass

    def count_trend(self):

        # 从self.data中截取一部分数据，去除缺失的'SignalChoice'
        df = self.data[200:].dropna(subset=['SignalChoice'])

        # 计算价格变化的百分比，存储到'maxChange'列
        df['maxChange'] = (df['EndPrice'] - df['StartPrice']) / df['StartPrice']

        df1 = df[df['maxChange'] > 0.1]
        df2 = df[df['maxChange'] < -0.1]

        df12 = pd.concat([df1, df2]).sort_index()

        for y in df12.index:

            last2SignalTimes = list(df.loc[:y]['SignalTimes'].tail(3))

            if len(last2SignalTimes) <= 2:
                continue

            # 根据SignalTimes筛选数据
            data_SignalTimes = self.data[self.data['SignalTimes'].isin(last2SignalTimes)]

            signal_times = df.loc[y, 'SignalTimes']
            signal_ = df.loc[y, 'Signal']

            start_time = df.loc[y, 'SignalStartTime']
            end_time = df.loc[y, 'EndPriceIndex']

            print(f'{self.stock_code}: {signal_times}')

            # 计算起始和结束的索引
            _index = self.data[self.data['date'] <= start_time].index[-1] + 5  # start index
            index_ = self.data[self.data['date'] <= end_time].index[-1]  # end index

            # 获取signal_times对应的数据
            signal_data = self.data[self.data['SignalTimes'] == signal_times]
            shapes = signal_data.shape[0] // 6
            shapes = min(shapes, 5)  # one signal max 5 pictures.

            # 根据Signal的值选择不同的文件夹和文件名
            folder_prefix = '_up' if signal_ == 1 else '_down'
            folder_suffix = 'up_' if signal_ == 1 else '_down'

            filename_jpg = f'{self.stock_code}_{signal_times}.jpg'
            filename_npy = f'{self.stock_code}.npy'

            for s in range(shapes):

                _figName = AnalysisDataPath.macd_train_path(folder_prefix, filename_jpg)
                _file = AnalysisDataPath.macd_train_path(folder_prefix, filename_npy)

                figName_ = AnalysisDataPath.macd_train_path(folder_suffix, filename_jpg)
                file_ = AnalysisDataPath.macd_train_path(folder_suffix, filename_npy)

                _num = _index + s
                _data = data_SignalTimes.loc[:_num].tail(100)  # _up = # _up : 上涨前期
                _array = array_data(data=_data, name_=_figName)
                self.save_array_data(_array, _file)

                num_ = index_ - s
                data_ = data_SignalTimes.loc[:num_].tail(100)
                array_ = array_data(data=data_, name_=figName_)
                self.save_array_data(array_, file_)


if __name__ == '__main__':

    pool = TableStockPool.load_StockPool()
    pool = pool.sort_values(by=['CycleAmplitude'])
    pool = pool.tail(40).head(20)
    print(pool['name'])
    # exit()
    for stock in pool['name']:
        count = CountTrendData(stock)
        count.count_trend()
        print(f'{stock} successfully;')
