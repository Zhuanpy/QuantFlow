# -*- coding: utf-8 -*-
"""
交易执行模块

提供交易信号判断和执行功能
"""

from App.codes.RnnModel.rnn_base import RnnBase


class TradingAction(RnnBase):
    """
    交易动作类

    提供买入和卖出信号的判断逻辑
    """

    def __init__(self):
        super().__init__()

    def buy_action(self):
        """
        判断是否执行买入操作

        条件1: 趋势反转买入
        条件2: 跌入底部触发信号买入
        """
        # 条件1：趋势反转买入
        trend_reversal_condition = (self.signal == -1) and (self.reTrend == 1)

        # 条件2：跌入底部触发信号买入
        bottom_signal_condition = (self.signal == -1) and (self.tradAction == 1)

        if trend_reversal_condition or bottom_signal_condition:
            self.buyAction = True

    def sell_action(self):
        """
        判断是否执行卖出操作

        条件1: 趋势反转卖出
        条件2: 涨至顶部触发信号卖出
        """
        # 条件1：趋势反转卖出
        trend_reversal_condition = (self.signal == 1) and (self.reTrend == 1)

        # 条件2：涨至顶部触发信号卖出
        top_signal_condition = (self.signal == 1) and (self.tradAction == -1)

        if trend_reversal_condition or top_signal_condition:
            self.sellAction = True

    def check_stop_loss(self, current_price: float, stop_loss_price: float) -> bool:
        """
        检查是否触发止损

        Args:
            current_price: 当前价格
            stop_loss_price: 止损价格

        Returns:
            bool: 是否触发止损
        """
        if stop_loss_price is None:
            return False
        return current_price < stop_loss_price

    def check_take_profit(self, trend_score: float, threshold: float = 4.0) -> bool:
        """
        检查是否触发止盈

        Args:
            trend_score: 趋势得分
            threshold: 止盈阈值

        Returns:
            bool: 是否触发止盈
        """
        return trend_score > threshold
