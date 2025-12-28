# -*- coding: utf-8 -*-
import pandas as pd
from App.codes.RnnModel.CheckModel import multiprocessing_count_pool
from App.codes.RnnModel.Rnn_utils import reset_record_time, date_range
from .EvaluateBoard import multiprocessing_count_board

from App.codes.Evaluation.CountPool import PoolCount

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)


class EvaluateAllTrend:

    def __init__(self, months='2022-02', start_=None, end_=None):

        self.months = months

        if not end_:
            self.date_ = pd.Timestamp.now().date()

        else:
            self.date_ = pd.to_datetime(end_).date()

        if not start_:
            self._date = self.date_

        else:
            self._date = pd.to_datetime(start_).date()

    def loop_by_date(self, reset=False):

        dates = date_range(self._date, self.date_)

        if reset:
            reset_record_time(dates[0])

        for day_ in dates:
            multiprocessing_count_pool(day_)
            multiprocessing_count_board(day_)

            my_count = PoolCount()
            my_count.count_trend()
            print(f'日期{day_}完成;')


if __name__ == '__main__':

    _date = '2022-05-06'
    date_ = '2022-05-06'
    rm = EvaluateAllTrend(end_=date_, start_=_date)
    rm.loop_by_date()
