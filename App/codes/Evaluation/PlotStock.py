import pandas as pd
from App.codes.MySql.LoadMysql import LoadNortFunds as lf
from App.codes.MySql.LoadMysql import LoadRnnModel, LoadFundsAwkward
from App.codes.MySql.DataBaseStockData1m import StockData1m
from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.MySql.DataBaseStockPool import TableStockPool
import numpy as np
import matplotlib.pyplot as plt
from App.codes.utils.Normal import MathematicalFormula as mth
from App.codes.utils.Normal import ReadSaveFile
from App.codes.MySql.sql_utils import Stocks
import matplotlib.ticker as ticker
import matplotlib.dates as mdates
from App.codes.Signals.BollingerSignal import Bollinger
from App.codes.utils.Normal import ResampleData

plt.rcParams['font.sans-serif'] = 'SimHei'
plt.rcParams['axes.unicode_minus'] = False


def funds_trends(code: str):
    data = pd.DataFrame()
    score = 0.0

    try:
        cols = ['SECURITY_CODE', 'TRADE_DATE', 'ADD_MARKET_CAP']
        data = lf.load_funds2stock()

        data = data[cols]

        days = pd.Timestamp().today() + pd.Timedelta(days=-90)
        data = data[(data['SECURITY_CODE'] == code) &
                    (data['TRADE_DATE'] > days)].reset_index(drop=True)

        if data.shape[0] > 12:
            data.loc[:, 'ADD_MARKET_CAP'] = mth.data2normalization(data['ADD_MARKET_CAP'])
            data.loc[:, 'close'] = data['ADD_MARKET_CAP'].rolling(12, min_periods=1).mean()
            data = Bollinger(data)
            data.loc[:, 'score'] = data['close'] - data['BollMid']
            data = data.tail(60)

            score = round(data.iloc[-1]['score'], 3)  # 更新评估得分

    except IndexError:
        pass

    return data, score


def board_trends(code: str):
    data = pd.DataFrame()
    score = 0

    try:
        cols = ['date', 'close']
        days = pd.Timestamp().today() + pd.Timedelta(days=-90)
        _year = str(days.year)

        data = StockData1m.load_1m(code_=code, _year=_year)
        data = data[data['date'] > days].reset_index(drop=True)
        data = ResampleData.resample_1m_data(data=data, freq='120m')
        data = data[cols]

        data.loc[:, 'close'] = mth.data2normalization(data['close'])
        data = Bollinger(data=data)
        data.loc[:, 'BollTrend'] = data['BollMid'] - data['BollMid'].shift(1)

        # boll trends
        data.loc[data['BollTrend'] > 0, 'BollScore'] = 0.9
        data.loc[data['BollTrend'] <= 0, 'BollScore'] = 0.1

        # 得分记录
        data = data.tail(60).reset_index(drop=True)
        data.loc[data['BollScore'] == 0.9, 'pltbs'] = data['BollUp'].max()
        data.loc[data['BollScore'] == 0.1, 'pltbs'] = data['BollDn'].min()

        ntb = data.tail(10)
        num_h = ntb[ntb['BollScore'] == 0.9]['BollScore'].count()
        num_l = ntb[ntb['BollScore'] == 0.1]['BollScore'].count()

        score = (num_h - num_l) / 10

    except IndexError:
        pass

    return data, score


def data_stock_pool(name: str):
    sql = f'''where name = '{name}';'''
    params = (name,)
    data = TableStockPool.set_table_to_pool(sql, params)

    return data


class CommonData:

    def __init__(self):
        self.nameS, self.codeS, self.nameB, self.codeB = None, None, None, None
        self.months = None
        self.recordData, self.DataPool, self.DataJson = None, None, None
        self.Data15M, self.DataHist = None, None

    def data_record(self, ):
        self.recordData = LoadRnnModel.load_run_record()  # 'rnn_model','RunRecord'
        self.recordData = self.recordData[self.recordData['code'] == self.codeS]

    def data_pool(self):
        self.DataPool = TableStockPool.load_StockPool()  # 'StockPool', 'StockPool'
        self.DataPool = self.DataPool[self.DataPool['name'] == self.nameS]

        self.codeB = self.DataPool.iloc[0]['IndustryCode']
        self.nameB = self.DataPool.iloc[0]['Industry']

    def data_json(self):
        self.DataJson = ReadSaveFile.read_json(months=self.months, code=self.codeS)

    def data_15m(self):
        self.Data15M = StockData15m.load_15m(self.codeS)
        self.Data15M['Cycle1mVolMax5'] /= 100

    def data_hist(self):

        ups = ['上涨', 'up', 1]
        trend = self.recordData.iloc[0]['Trends']

        if trend in ups:
            condition = (self.Data15M['Signal'] == 1) & \
                        (~self.Data15M['SignalChoice'].isnull()) & \
                        (self.Data15M['CycleAmplitudeMax'] > 0.05)

        else:
            condition = (self.Data15M['Signal'] == -1) & \
                        (~self.Data15M['SignalChoice'].isnull()) & \
                        (self.Data15M['CycleAmplitudeMax'] < -0.05)

        self.DataHist = self.Data15M[condition].reset_index(drop=True)

        # remove extreme code_data
        cols = ['Cycle1mVolMax5', 'CycleLengthMax', 'CycleAmplitudeMax']

        for col in cols:
            self.DataHist = mth.filter_3sigma(self.DataHist, col)


class PlotBoardData:

    def __init__(self):
        self.DataPool = pd.DataFrame()
        self.nameS, self.codeB, self.nameB = None, None, None
        self.f_date = '%d/%m'

    def plot_board(self, ax):
        data, score = board_trends(self.codeB)
        board_date = data.iloc[-1]['date'].strftime(self.f_date)

        label1 = f'{self.nameB}'
        label2 = f'Date:{board_date}'
        label3 = '12均线'

        ax.scatter(data.iloc[-1]['date'], data.iloc[-1]['BollMid'], label=label2, color='orange')
        ax.plot(data['date'], data['close'], label=label1)
        ax.plot(data['date'], data['BollMid'], label=label3, color='orange')

        ax.xaxis.set_major_formatter(mdates.DateFormatter(self.f_date))

        ax.legend(loc='upper left')

    def plot_north_funds(self, ax):
        data, score = funds_trends(self.codeB)

        if data.shape[0]:
            label1 = data.iloc[-1]['TRADE_DATE'].strftime(self.f_date)

            label1 = f'Date:{label1}'
            label2 = '北向资金'
            label3 = '12均线'

            ax.scatter(data.iloc[-1]['TRADE_DATE'], data.iloc[-1]['BollMid'], label=label1, color='orange')
            ax.plot(data['TRADE_DATE'], data['close'], label=label2)
            ax.plot(data['TRADE_DATE'], data['BollMid'], color='orange', label=label3)
            ax.xaxis.set_major_formatter(mdates.DateFormatter(self.f_date))
            ax.legend(loc='upper left')

    def plot_funds_awkward(self, ax, tick_spacing=10):
        data = LoadFundsAwkward.load_fundsAwkward()  # 'funds_awkward_stock','normalization'

        data = data[data['stock_name'] == self.nameS].sort_values(by=['Date'])

        label1 = data.iloc[-1]['Date'].strftime(self.f_date)
        label1 = f'Date:{label1}'
        label2 = '基金重仓'

        ax.scatter(data.iloc[-1]['Date'], data.iloc[-1]['count'], label=label1, color='r')
        ax.plot(data['Date'], data['count'], label=label2)

        ax.xaxis.set_major_locator(ticker.MultipleLocator(tick_spacing))
        ax.xaxis.set_major_formatter(mdates.DateFormatter(self.f_date))
        ax.legend(loc='upper left')


class PlotStockData(CommonData):

    def __init__(self):
        CommonData.__init__(self)
        self.his_alpha = 0.8

    def model_data(self, model):

        trend = self.recordData.iloc[0]['Trends']

        if trend == '下跌':
            trend = 'down'

        else:
            trend = 'up'

        data = self.DataJson['models'][trend][model]
        max_ = data['max']
        min_ = data['min']
        mean_ = data['mean']
        std_ = data['std']

        return max_, min_, mean_, std_

    def plot_close(self, ax, tick_spacing=15):

        trend = self.recordData.iloc[0]['Trends']

        days = pd.Timestamp().today() + pd.Timedelta(days=-30)

        d = self.Data15M[self.Data15M['date'] > days].reset_index(drop=True)

        d['date'] = d['date'].dt.strftime('%m-%d %H:%M')
        ax.plot(d['date'], d['close'], linewidth=2.5, label='close')

        ax.xaxis.set_tick_params(rotation=25)
        ax.xaxis.set_major_locator(ticker.MultipleLocator(tick_spacing))
        ax.legend()

        label = f'股票: {self.nameS}, {self.codeS}; 趋势：{trend}'
        ax.set_title(label)

    # 成交量
    def plot_volume(self, ax):
        max_, min_, mean_, std_ = self.model_data('Vol')

        x = np.arange(min_, max_, 0.5)
        y = mth.normal2Y(x, mean_, std_)
        ax.plot(x, y, color='red', label='成交量', alpha=0.9)

        xPbv = self.recordData.iloc[0]['PredictBarVolume']
        if xPbv:
            yPbv = mth.normal2Y(xPbv, mean_, std_)
            ax.scatter(xPbv, yPbv, label='预测Vol', color='b')

        xRbv = self.recordData.iloc[0]['RealBarVolume']
        if xRbv:
            yRbv = mth.normal2Y(xRbv, mean_, std_)
            ax.scatter(xRbv, yRbv, label='真实Vol')

        ax.hist(self.DataHist['Cycle1mVolMax5'], bins=20, rwidth=0.5, density=True, color='green', alpha=self.his_alpha)
        ax.legend()

    def plot_length(self, ax):
        max_, min_, mean_, std_ = self.model_data(model='Length')

        x = np.arange(min_, max_, 0.02)
        y = mth.normal2Y(x, mean_, std_)
        ax.plot(x, y, color='red', label='长度曲线', alpha=0.9)

        xRcl = self.recordData.iloc[0]['RealCycleLength']
        yRcl = mth.normal2Y(xRcl, mean_, std_)
        ax.scatter(xRcl, yRcl, label='真实周期')

        xPcl = self.recordData.iloc[0]['PredictCycleLength']
        yPcl = mth.normal2Y(xPcl, mean_, std_)
        ax.scatter(xPcl, yPcl, label='预测周期')

        ax.hist(self.DataHist['CycleLengthMax'], bins=20, rwidth=0.5, density=True, color='green', alpha=self.his_alpha)
        ax.legend()

    def plot_amplitude(self, ax):
        max_, min_, mean_, std_ = self.model_data('Amplitude')
        x = np.arange(min_, max_, 0.01)
        y = mth.normal2Y(x, mean_, std_)
        ax.plot(x, y, color='red', label='周期曲线')

        xRcc = self.recordData.iloc[0]['RealCycleChange']
        yRcc = mth.normal2Y(xRcc, mean_, std_)
        ax.scatter(xRcc, yRcc, label='真实振幅')

        xPcc = self.recordData.iloc[0]['PredictCycleChange']
        yPcc = mth.normal2Y(xPcc, mean_, std_)
        ax.scatter(xPcc, yPcc, label='预测Cycle振幅')

        xPbc = self.recordData.iloc[0]['PredictBarChange']
        if xPbc:
            yPbc = mth.normal2Y(xPbc, mean_, std_)
            ax.scatter(xPbc, yPbc, label='预测Bar振幅')

        ax.hist(self.DataHist['CycleAmplitudeMax'], bins=20, rwidth=0.5, density=True, color='green',
                alpha=self.his_alpha)
        ax.legend()


class PlotsStock(PlotBoardData, PlotStockData):

    def __init__(self, Stock, parser_months='2022-02'):
        PlotBoardData.__init__(self)
        PlotStockData.__init__(self)

        self.nameS, self.codeS, self.stock_id = Stocks(Stock)
        self.months = parser_months

    def import_data(self):  # 导入数据
        self.data_15m()
        self.data_pool()  # 股票池数据
        self.data_record()  # RNN模型数据
        self.data_json()
        self.data_hist()  # 高斯分布直方图数据

    def plotting(self):
        self.import_data()

        plt.figure(figsize=(15, 15), dpi=80)

        ax11 = plt.subplot(311)
        self.plot_close(ax11)

        # 板块趋势
        ax21 = plt.subplot(334)
        self.plot_board(ax21)

        # 北向资金
        ax22 = plt.subplot(335)
        self.plot_north_funds(ax22)

        # 基金重仓
        ax23 = plt.subplot(336)
        self.plot_funds_awkward(ax23, tick_spacing=15)

        # 成交量
        ax31 = plt.subplot(337)
        self.plot_volume(ax31)

        # 周期长度
        ax32 = plt.subplot(338)
        self.plot_length(ax32)

        # 振幅
        ax33 = plt.subplot(339)
        self.plot_amplitude(ax33)

        # plt.x ticks(rotation=45)
        plt.subplots_adjust(wspace=0.2, hspace=0.4)  # 调整子图间距
        plt.show()
        # plt.savefig('001.jpg')


if __name__ == '__main__':
    stock = '002049'
    pp = PlotsStock(stock)
    pp.plotting()
