# -*- coding: utf-8 -*-
from App.codes.parsers.MacdParser import *
import pandas as pd


def calculate_MACD(data: pd.DataFrame, s=12, m=20, l=30, em=9) -> pd.DataFrame:
    """
    MACD 计算 :
    1. 需要计算的几个数据  ema short: s, ema long: l, ema mid: m;
    2. Dif值: , DifSm值： ，   DifMl值： ，    Dea值: ,   macd值: ,
    """

    # 使用 ewm 计算 EMA
    data.loc[:, EmaShort] = data['close'].rolling(s, min_periods=1).mean()
    data.loc[:, EmaMid] = data['close'].rolling(m, min_periods=1).mean()
    data.loc[:, EmaLong] = data['close'].rolling(l, min_periods=1).mean()

    # 计算 DIFF 和相关的差值
    data.loc[:, Dif] = data[EmaShort] - data[EmaLong]
    data.loc[:, DifSm] = data[EmaShort] - data[EmaMid]
    data.loc[:, DifMl] = data[EmaMid] - data[EmaLong]

    # 计算 DEA（DIFF的EMA）和 MACD 柱线
    data.loc[:, Dea] = data[Dif].rolling(em, min_periods=1).mean()
    data.loc[:, macd_] = (data[Dif] - data[Dea]) * 2

    return data
