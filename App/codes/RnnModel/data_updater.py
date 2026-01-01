# -*- coding: utf-8 -*-
"""
数据更新模块

提供数据库数据更新功能
"""

import pandas as pd

from App.codes.RnnModel.rnn_base import RnnBase
from App.codes.MySql.LoadMysql import LoadRnnModel
from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.MySql.DataBaseStockPool import TableStockPool


class UpdateData(RnnBase):
    """
    数据更新类

    提供更新股票池、运行记录和 15 分钟数据的功能
    """

    def __init__(self):
        super().__init__()

        self.current = pd.Timestamp('today').date()
        self.signalTimes = None
        self._signalTimes = None
        self.signalStartTime = None

        # 当前股票信息
        self.change_max = None
        self.trade_timing = None
        self.position_action = None
        self.trend_score = None

        self.RunDate = None
        self.trade_boll = False
        self._limitTradeTiming = None

    def update_StockPool(self):
        """更新股票池数据"""
        sql = ''' close = %s, ExpPrice = %s, RnnModel= %s, Trends= %s,
        ReTrend= %s, TrendProbability= %s, RecordDate= %s where id= %s; '''

        parser = (
            self.close, self.ExpPrice, self.trend_score, self.trendValue,
            self.reTrend, self.ScoreP, self.check_date, self.stock_id
        )
        TableStockPool.set_table_to_pool(sql, parser)

    def update_RecordRun(self):
        """更新运行记录"""
        sql1 = ''' Trends = %s, SignalStartTime = %s, PredictCycleLength = %s, RealCycleLength = %s,
        PredictCycleChange = %s, PredictCyclePrice = %s, RealCycleChange = %s, PredictBarChange = %s,
        RealBarChange = %s, PredictBarVolume = %s, RealBarVolume = %s, ScoreTrends = %s,
        TradePoint = %s, TimeRunBar = %s, RenewDate = %s where id = %s; '''

        parsers = (
            self.signalValue, self.signalStartTime, self.predict_length, self.real_length,
            self.predict_CycleChange, self.predict_CyclePrice, self.real_CycleChange, self.predict_bar_change,
            self.real_bar_change, self.predict_BarVolume, self.real_BarVolume, self.trend_score,
            self.tradAction, self.trade_timing, self.current, self.stock_id
        )

        LoadRnnModel.set_table_run_record(sql1, parsers)

    def update_sql_15m_data(self):
        """更新 15 分钟数据"""
        sql = '''PredictCycleChange = %s,
        PredictCyclePrice = %s,
        PredictCycleLength = %s,
        PredictBarChange = %s,
        PredictBarPrice = %s,
        PredictBarVolume = %s,
        ScoreRnnModel = %s,
        TradePoint = %s where date = %s;'''

        parsers = (
            self.predict_CycleChange, self.predict_CyclePrice, self.predict_length,
            self.predict_bar_change, self.predict_bar_price, self.predict_BarVolume,
            self.trend_score, self.tradAction, self.trade_timing
        )

        StockData15m.set_data_15m_data(self.stock_code, sql, parsers)
