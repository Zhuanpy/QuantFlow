# -*- coding: utf-8 -*-
"""
RNN 模型基础模块

提供共享的基类和变量定义
"""

import pandas as pd
from App.codes.utils.Normal import Useful


class RnnBase:
    """
    RNN 模型基类

    定义所有 RNN 相关类共享的变量和基础方法
    """

    def __init__(self, freq: str = '15m'):
        self.lines, self.line = Useful.dashed_line(50)

        # 股票基础变量
        self.stock_name = None
        self.stock_code = None
        self.stock_id = None
        self.month_parsers = None

        self.jsons = None
        self.freq = freq
        self.monitor = False

        # 数据变量
        self.data_1m = None
        self.data_15m = None
        self.records = None
        self.checking_data = None
        self.record_last_15m_time = None
        self.check_date = None

        # 趋势模型预测变量
        self.trendLabel = None
        self.trendValue = None

        # Rnn 模型预测变量
        self.predict_length = None
        self.predict_CycleChange = None
        self.predict_CyclePrice = None
        self.predict_BarVolume = None
        self.real_length = None
        self.real_CycleChange = None
        self.real_CyclePrice = None
        self.real_BarVolume = None
        self.predict_bar_change = None
        self.real_bar_change = None
        self.predict_bar_price = None

        # 股票交易变量
        self.position = None
        self.close = None
        self.stopLoss = None
        self.ExpPrice = None
        self.score_trends = None
        self.ScoreP = None
        self.sellAction = False
        self.buyAction = False
        self.reTrend = 0
        self.signal = None
        self.updown = None
        self.signalValue = None
        self.tradAction = 0


# 别名，保持向后兼容
Parsers = RnnBase
