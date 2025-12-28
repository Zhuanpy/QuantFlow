# -*- coding: utf-8 -*-
from App.codes.parsers.BollingerParser import *


def Bollinger(data, ma_mid=20):
    data[BollMid] = data['close'].rolling(ma_mid, min_periods=1).mean()
    data[BollStd] = data['close'].rolling(ma_mid, min_periods=1).std()
    data[BollUp] = data[BollMid] + 2 * data[BollStd]
    data[BollDn] = data[BollMid] - 2 * data[BollStd]
    data[StopLoss] = round(data[BollDn] - 2 * data[BollStd], 2)
    return data


if __name__ == '__main__':
    print(Bollinger)
