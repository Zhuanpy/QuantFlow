# -*- coding: utf-8 -*-
from .RnnCreationData import RMTrainingData
from .RnnCreationModel import RMBuiltModel
from .CheckModel import RMHistoryCheck


def full_running(month_, _start):

    train = RMTrainingData(months=month_, start_=_start)
    train.all_stock()

    model = RMBuiltModel(month_)
    model.train_remaining_models()

    check = RMHistoryCheck(month_parsers=month_)
    check.loop_by_date()


if __name__ == '__main__':
    months = '2022-02'
    start = '2018-01-01'
    full_running(months, start)
