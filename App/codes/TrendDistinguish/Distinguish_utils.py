import numpy as np
import pandas as pd
from App.codes.MySql.DataBaseStockData1m import StockData1m
from App.codes.utils.Normal import ResampleData
from App.codes.Signals.BollingerSignal import Bollinger
from App.codes.Signals.MacdSignal import calculate_MACD


def array_data(data, name_, showTicks=False):

    import matplotlib

    matplotlib.use('agg')

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(nrows=2, ncols=1, figsize=(1.5, 2))

    ''' plot price '''
    ax1 = ax[0]
    ax1.plot(data.index, data['close'])
    ax1.plot(data.index, data['EmaMid'])

    ax1.plot(data.index, data['BollUp'])
    ax1.plot(data.index, data['BollDn'])

    ''' plot macd '''

    ax2 = ax[1]
    ax2.plot(data.index, data['Dif'])
    ax2.plot(data.index, data['DifMl'])

    r_ = data['MACD'] > 0
    g_ = data['MACD'] < 0
    ax2.bar(data[r_].index, data[r_]['MACD'], color='red')
    ax2.bar(data[g_].index, data[g_]['MACD'], color='green')

    ''' setting fig '''
    if not showTicks:
        ax1.axes.xaxis.set_ticks([])
        ax1.axes.yaxis.set_ticks([])

        ax2.axes.xaxis.set_ticks([])
        ax2.axes.yaxis.set_ticks([])

        # fig.show()

    fig.canvas.draw()
    array_ = np.array(fig.canvas.renderer.buffer_rgba())

    if name_:
        plt.savefig(name_)
        # print(name_)
    plt.axis('off')
    plt.close('all')

    return array_


def calculate_distinguish_data(Stock: str, freq: str, date_):

    """ calculate end date: date_ """

    if not date_:
        date_ = (pd.Timestamp('today') + pd.Timedelta(days=1))  # .date()

    else:
        date_ = (pd.to_datetime(date_) + pd.Timedelta(days=1))  # .date()

    """ calculate start date: _date """
    if freq == '15m':
        _date = pd.to_datetime(date_) + pd.Timedelta(days=-60)  # .date()

    elif freq == '120m':
        _date = pd.to_datetime(date_) + pd.Timedelta(days=-90)

    elif freq == 'day':
        _date = pd.to_datetime(date_) + pd.Timedelta(days=-120)

    elif freq == 'weekly':
        _date = pd.to_datetime(date_) + pd.Timedelta(days=-180)

    else:
        _date = pd.to_datetime(date_) + pd.Timedelta(days=-90)

    """ load 1m code_data & select 1m code_data """
    _year = str(date_.year)

    data = StockData1m.load_1m(Stock, _year)

    data = data[(data['date'] > _date) & (data['date'] < date_)].reset_index(drop=True)
    data = ResampleData.resample_1m_data(data=data, freq=freq)

    data = calculate_MACD(data)
    data = Bollinger(data).tail(100).reset_index(drop=True)

    return data


if __name__ == '__main__':
    pass
    # array_data()
