from App.codes.MySql.DataBaseStockPool import TableStockBoard, TableStockPool, TableStockPoolCount
import pandas as pd
import numpy as np
import logging


def count_board_by_date(date_):
    """统计板块表格"""
    ''' count board '''

    board = TableStockBoard.load_board()

    b_down = board[board['Trends'] == 0].shape[0]
    b_down_ = board[board['Trends'] == 1].shape[0]
    b_up = board[board['Trends'] == 2].shape[0]
    b_up_ = board[board['Trends'] == 3].shape[0]

    sql = f''' _BoardUp = '%s', BoardUp_='%s', _BoardDown= '%s', BoardDown_= '%s', where date = '%s';'''

    params = (b_up, b_up_, b_down, b_down_, date_)
    TableStockPool.set_table_to_pool(sql, params=params)


class PoolCount:
    """
    统计股票池的趋势并将结果存储到数据库中。

    参数:
    - date_ (str or None): 用于指定要统计趋势的日期。如果为 None，则使用股票池的第一个记录日期。

    返回:
    - pd.DataFrame: 包含统计结果的 DataFrame，列包括日期、上涨股票数、反转上涨股票数、下跌股票数、反转下跌股票数、
                   板块上涨股票数、板块反转上涨股票数、板块下跌股票数、板块反转下跌股票数、
                   趋势为上涨1、上涨2、上涨3、下跌1、下跌2、下跌3的股票数。

    注意:
    - 函数会将统计结果存储到数据库中，如果数据库中已存在相同日期的记录，则进行更新。
    """

    def __init__(self, date_=None):
        self.date_ = date_
        self.pool = None
        self.ups, self.re_ups, self._up, self.up_, self.downs, self.re_downs, self._down, self.down_ = [None] * 8
        self.up1, self.up2, self.up3, self.down1, self.down2, self.down3 = [None] * 6
        self.b_down, self.b_down_, self.b_up, self.b_up_ = [None] * 4

    def load_pool_data(self):

        data = TableStockPool.load_StockPool()

        if not self.date_:
            self.date_ = data.iloc[0]['RecordDate']

        data = data[data['RecordDate'] == pd.to_datetime(self.date_)]
        data = data[['RecordDate', 'Trends', 'ReTrend', 'RnnModel']].reset_index(drop=True)

        return data

    def calculate_trend_statistics(self, pool):

        pool['UpDown'] = np.where(pool['Trends'].isin([2, 3]), 1, -1)

        self.ups = pool[pool['UpDown'] == 1].shape[0]
        self.re_ups = pool[(pool['UpDown'] == 1) & (pool['ReTrend'] == 1)].shape[0]
        self.downs = pool[pool['UpDown'] == -1].shape[0]
        self.re_downs = pool[(pool['UpDown'] == -1) & (pool['ReTrend'] == 1)].shape[0]

        self._down = pool[pool['Trends'] == 0].shape[0]
        self.down_ = pool[pool['Trends'] == 1].shape[0]
        self._up = pool[pool['Trends'] == 2].shape[0]
        self.up_ = pool[pool['Trends'] == 3].shape[0]

        return pool

    def calculate_rnn_statistics(self, pool):
        """ 统计Rnn得分 """
        self.up1 = pool[(pool['RnnModel'] > 0) & (pool['RnnModel'] < 2.5)].shape[0]
        self.up2 = pool[(pool['RnnModel'] >= 2.5) & (pool['RnnModel'] < 5)].shape[0]
        self.up3 = pool[(pool['RnnModel'] >= 5)].shape[0]

        self.down1 = pool[(pool['RnnModel'] > -2.5) & (pool['RnnModel'] < 0)].shape[0]
        self.down2 = pool[(pool['RnnModel'] > -5) & (pool['RnnModel'] <= -2.5)].shape[0]
        self.down3 = pool[(pool['RnnModel'] <= -5)].shape[0]

        return pool

    def calculate_board_statistics(self):
        board = TableStockBoard.load_board()
        self.b_down = board[board['Trends'] == 0].shape[0]
        self.b_down_ = board[board['Trends'] == 1].shape[0]
        self.b_up = board[board['Trends'] == 2].shape[0]
        self.b_up_ = board[board['Trends'] == 3].shape[0]
        return board  # b_down, b_down_, b_up, b_up_

    def count_trend(self):
        """ 加载数据 """
        self.pool = self.load_pool_data()

        ''' 统计趋势 '''
        self.pool = self.calculate_trend_statistics(self.pool)

        ''' 统计Rnn得分'''
        self.calculate_rnn_statistics(self.pool)

        ''' count board '''
        self.calculate_board_statistics()

        ''' values DataFrame '''

        dic = {'date': [self.date_], 'Up': [self.ups], 'ReUp': [self.re_ups], 'Down': [self.downs],
               'ReDown': [self.re_downs],
               '_BoardUp': [self.b_up], 'BoardUp_': [self.b_up_], '_BoardDown': [self.b_down],
               'BoardDown_': [self.b_down_],
               '_up': [self._up], 'up_': [self.up_], '_down': [self._down], 'down_': [self.down_],
               'Up1': [self.up1], 'Up2': [self.up2], 'Up3': [self.up3], 'Down1': [self.down1], 'Down2': [self.down2],
               'Down3': [self.down3]}

        data = pd.DataFrame(dic)

        import sqlalchemy.exc
        try:
            TableStockPoolCount.append_poolCount(data)

        except sqlalchemy.exc.IntegrityError:

            sql = f''' Up= %s, ReUp= %s, Down= %s, _up= %s, up_= %s, 
            _down= %s, down_= %s, ReDown= %s, 
            _BoardUp= %s, BoardUp_= %s, _BoardDown= %s, BoardDown_= %s,
             Up1= %s, Up2= %s, Up3= %s, 
             Down1= %s, Down2= %s, Down3= %s WHERE date = %s;'''

            params = (self.ups, self.re_ups, self.downs, self._up, self.up_,
                      self._down, self.down_, self.re_downs,
                      self.b_up, self.b_up_, self.b_down, self.b_down_,
                      self.up1, self.up2, self.up3,
                      self.down1, self.down2, self.down3, self.date_)

            TableStockPoolCount.set_table_poolCount(sql, params=params)

        info_text = 'Count Pool Trends Success;'
        logging.info(info_text)
        return data


if __name__ == '__main__':
    _date_ = '2022-11-18'
    pc = PoolCount()
    pc.count_trend()
