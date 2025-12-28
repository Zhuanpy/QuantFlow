import pandas as pd
from App.codes.MySql.DataBaseStockPool import TableStockPoolCount

import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = 'SimHei'
plt.rcParams['axes.unicode_minus'] = False


class Subplot:

    @classmethod
    def plot_up_down(cls, ax, data):
        ax.plot(data['date'], data['Up'], label='UP', color='red')
        ax.plot(data['date'], data['Down'], label='DOWN', color='green')

        ax.legend(loc='upper left', prop={'size': 6})
        ax.set_title('涨跌统计', fontsize=10)

    @classmethod
    def plot_up_down_reUp_reDown(cls, ax, data):
        ax.plot(data['date'], data['Up'], label='Up', color='red')
        ax.plot(data['date'], data['ReUp'], label='ReUp', color='#690c0c')
        ax.plot(data['date'], data['Down'], label='Down', color='green')
        ax.plot(data['date'], data['ReDown'], label='ReDown', color='#48a570')

        ax.legend(loc='upper left', prop={'size': 6})
        ax.set_title('涨跌与趋势反转统计', fontsize=10)

    @classmethod
    def plot_trends_location(cls, ax, data):
        ax.plot(data['date'], data['_up'], label=' _up', color='red')
        ax.plot(data['date'], data['up_'], label='up_', color='#690c0c')
        ax.plot(data['date'], data['_down'], label=' _down', color='green')
        ax.plot(data['date'], data['down_'], label='down_', color='#48a570')

        ax.legend(loc='upper left', prop={'size': 6})
        ax.set_title('趋势阶段统计', fontsize=10)

    @classmethod
    def plot_score_count(cls, ax, data):
        ax.plot(data['date'], data['Up1'], label='Up1', color='red')
        ax.plot(data['date'], data['Up2'], label='Up2', color='#690c0c')
        # ax.plot(code_data['date'], code_data['Up3'], label='Up3')
        ax.plot(data['date'], data['Down1'], label='Down1', color='green')
        # ax.plot(code_data['date'], code_data['Down2'], label='Down2')
        ax.plot(data['date'], data['Down3'], label='Down3', color='#48a570')

        ax.legend(loc='upper left', prop={'size': 6})
        ax.set_title('趋势得分统计', fontsize=10)

    @classmethod
    def plot_board_trends(cls, ax, data):
        ax.plot(data['date'], data['_BoardUp'], label=' _BoardUp', color='red')
        ax.plot(data['date'], data['BoardUp_'], label='BoardUp_', color='#690c0c')
        ax.plot(data['date'], data['_BoardDown'], label=' _BoardDown', color='green')
        ax.plot(data['date'], data['BoardDown_'], label='BoardDown_', color='#48a570')

        ax.legend(loc='upper left', prop={'size': 6})
        ax.set_title('板块趋势统计', fontsize=10)


def plot_pool_count():
    data = TableStockPoolCount.load_poolCount().tail(30)
    data.loc[:, 'date'] = pd.to_datetime(data['date']).dt.strftime('%m/%d')
    print(data.head())

    plt.figure(figsize=(5, 10))  #

    ax1 = plt.subplot(511)
    ax2 = plt.subplot(512)
    ax3 = plt.subplot(513)
    ax4 = plt.subplot(514)
    ax5 = plt.subplot(515)

    '''趋势统计 '''
    Subplot.plot_up_down(ax1, data)

    '''趋势和趋势反转统计'''
    Subplot.plot_up_down_reUp_reDown(ax2, data)

    '''趋势阶段统计'''
    Subplot.plot_trends_location(ax3, data)

    '''趋势得分统计'''
    Subplot.plot_score_count(ax4, data)

    '''板块趋势统计'''
    Subplot.plot_board_trends(ax5, data)

    plt.subplots_adjust(wspace=0, hspace=0.8)  # 调整子图间距
    plt.show()


if __name__ == '__main__':
    plot_pool_count()
