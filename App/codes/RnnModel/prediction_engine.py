# -*- coding: utf-8 -*-
"""
预测引擎模块

组合各个模块提供完整的预测功能
"""

import pandas as pd

from App.codes.RnnModel.data_loader import ModelData
from App.codes.RnnModel.model_predictor import DlModel
from App.codes.RnnModel.data_updater import UpdateData
from App.codes.MySql.sql_utils import Stocks
from App.codes.utils.Normal import MathematicalFormula as MyFormula
from App.codes.utils.Normal import Useful, count_times, ReadSaveFile
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.codes.parsers.RnnParser import (
    SignalTimes, Signal, SignalChoice, EndPrice, StartPrice,
    CycleLengthPerBar, CycleAmplitudeMax, CycleAmplitudePerBar,
    EndPriceIndex, DailyVolEmaParser
)


class PredictionCommon(ModelData, DlModel, UpdateData):
    """
    预测通用类

    组合数据加载、模型预测和数据更新功能
    """

    def __init__(self, stock: str, month_parsers: str, check_date,
                 alpha: float = 1, monitor: bool = True,
                 stop_loss: float = None, position=None):
        """
        初始化预测引擎

        Args:
            stock: 股票代码或名称
            month_parsers: 月份解析器
            check_date: 检查日期
            alpha: 模型系数
            monitor: 是否为监控模式
            stop_loss: 止损价格
            position: 持仓信息
        """
        ModelData.__init__(self)
        DlModel.__init__(self)
        UpdateData.__init__(self)

        self.stock_name, self.stock_code, self.stock_id = Stocks(stock)

        self.position = position
        self.month_parsers = month_parsers
        self.monitor = monitor
        self.stopLoss = stop_loss

        path = StockDataPath.json_data_path(self.month_parsers, self.stock_code)
        self.jsons = ReadSaveFile.read_json_by_path(path)

        # 生成预测数据
        if monitor:
            check_date = pd.Timestamp('today').strftime('%Y-%m-%d')

        self.check_date = pd.to_datetime(check_date)
        self.alpha = alpha

        self.checking_data = None
        self.checking = None
        self.predict_data = None
        self._endPriceTime = None
        self._limitPrice = None
        self.area = None

    def report_run(self):
        """输出运行报告"""
        headers = (
            f'{self.lines}\n'
            f'检测日期：{self.check_date.date()}；\n'
            f'股票:{self.stock_name}，代码:{self.stock_code}；\n'
            f'当前趋势:{self.signalValue}；\n'
            f'{self.line}\n'
        )

        cycles = (
            f'时间点：{self.trade_timing}；\n'
            f'预测周期长度：{self.predict_length} 根，现真实长度：{self.real_length} 根；\n'
            f'预测周期振幅：{self.predict_CycleChange} 点，现真实振幅：{self.real_CycleChange} 点；\n'
            f'预测周期{self.area}价：CNY {self.predict_CyclePrice}，现真实{self.area}价：CNY {self.real_CyclePrice}；\n'
        )

        bars = (
            f'预测Bar成交量：{self.predict_BarVolume} 手，真实成交量：{self.real_BarVolume} 手；\n'
            f'预测Bar振幅：{self.predict_bar_change} 点，真实振幅：{self.real_bar_change} 点；\n'
            f'预测Bar价格：CNY {self.predict_bar_price}，真实振幅：CNY {self.close}；\n'
        )

        score = f'\n趋势得分：{self.trend_score}\n'

        _cycles = (
            f'{self.line}\n前周期信息：\n'
            f'前周期峰值时间：{self._limitTradeTiming}；\n'
            f'前周期峰值价格：{self._limitPrice}；\n'
        )

        if self.predict_length >= self.real_length:
            shower = f'{headers}{cycles}{score}{_cycles}\n{self.lines}'
        else:
            shower = f'{headers}{cycles}{bars}{score}{_cycles}\n{self.lines}'

        print(shower)

    def report_trade(self):
        """输出交易点信息"""
        shapes = self.records[
            (self.records['RenewDate'] != self.current) &
            (self.records['TradePoint'] != 0)
        ].shape[0]

        if not shapes:
            title = f'股票:{self.stock_name},代码：{self.stock_code}; 触发{self.position_action}信号;'

            content = (
                f'交易时间点：{self.trade_timing};\n'
                f'预测此{self.signalValue}趋势周期bar: {self.predict_length}根,'
                f'现趋势真实bar: {self.real_length}根;\n;'
                f'预测此{self.signalValue}全周期振幅值：{self.predict_CycleChange}点,'
                f'现全周期真实振幅: {self.real_CycleChange}点;\n; '
                f'预测当前时间点振幅值：{self.predict_bar_change}点, '
                f'当前时间点真实振幅: {self.real_bar_change}点;\n'
                f'预测当前时间点成交量: {self.predict_BarVolume}手,'
                f'当前时间点真实成交量: {self.real_BarVolume}手;\n'
            )

            mails = f'{title}\n\n{content}'

            if self.monitor:
                Useful.sent_emails(message_title=title, mail_content=mails)

    def report_position(self):
        """输出持仓报告"""
        title, message = '', ''

        if self.close < self.stopLoss:
            title = f'持仓股：{self.stock_name}止损卖出；'
            message = (
                f'股票：{self.stock_name}, close: {self.close}, '
                f'stop loss: {self.stopLoss};\nTrend score: {self.trend_score}; '
            )
            self.sellAction = True

        if self.trend_score > 4:
            title = f'持仓股：{self.stock_name}止盈卖出；'
            self.sellAction = True
            message = (
                f'股票：{self.stock_name}, close: {self.close}, '
                f'stop loss: {self.stopLoss};\nTrend score: {self.trend_score};'
            )

        if self.sellAction:
            Useful.sent_emails(title, message)

    def predict_cycle_values(self):
        """趋势转变点预判"""
        R_signalStartTime = self.records.iloc[0]['SignalStartTime']

        # 判断是否运行转折点模型
        if self.signalStartTime == R_signalStartTime:
            self.predict_length = self.records.iloc[0]['PredictCycleLength']
            self.predict_CycleChange = self.records.iloc[0]['PredictCycleChange']
            self.predict_CyclePrice = self.records.iloc[0]['PredictCyclePrice']
        else:
            self.predict_data = self.data_15m[
                self.data_15m[SignalTimes] == self._signalTimes
            ]
            self._limitPrice = self.predict_data.iloc[0][EndPrice]

            self.predict_CycleChange = self.cycle_change()
            self.predict_length = self.cycle_length()

            counts = self.get_json_data(self.updown, 'Amplitude')
            mean_ = counts[2]
            std_ = counts[3]

            if self.signal == 1:
                p30 = MyFormula.normal_get_x(p=0.3, mean=mean_, std=counts[3])
                p95 = MyFormula.normal_get_x(p=0.95, mean=mean_, std=counts[3])

                if self.predict_CycleChange < p30:
                    self.predict_CycleChange = p30

                if self.predict_CycleChange > p95:
                    self.predict_CycleChange = MyFormula.normal_get_x(p=0.8, mean=mean_, std=std_)

            if self.signal == -1:
                p65 = MyFormula.normal_get_x(p=0.65, mean=mean_, std=std_)
                if self.predict_CycleChange > mean_:
                    self.predict_CycleChange = p65

            self.predict_CyclePrice = round(
                self._limitPrice * (1 + self.predict_CycleChange), 2
            )

            if self.predict_length < 16:
                self.predict_length = self.get_json_data(self.updown, 'Length')[2]

            self.predict_length = int(self.predict_length)
            self.predict_CycleChange = round(self.predict_CycleChange, 2)
            self.predict_CyclePrice = round(self.predict_CyclePrice, 2)

        mean_ = self.get_json_data(self.updown, 'Amplitude')[2]

        if self.signal == 1:
            self.ExpPrice = (1 + (mean_ + self.predict_CycleChange) / 2) * self._limitPrice

        if self.signal == -1:
            self.ExpPrice = (1 + (mean_ + self.predict_CycleChange) / 2) * self._limitPrice

        self.ExpPrice = round(self.ExpPrice, 2)

    def predict_bar_values(self):
        """预测 Bar 值"""
        self.predict_BarVolume = 0
        self.predict_bar_change = 0

        meanLength = self.get_json_data(self.updown, 'Length')[2]

        if self.real_length > min(meanLength, self.predict_length):
            self.predict_data = self.data_15m[
                (self.data_15m['date'] < self.trade_timing) &
                (self.data_15m[SignalTimes] == self.signalTimes)
            ].tail(30)

            vols = self.checking_data.iloc[-1][DailyVolEmaParser]
            self.predict_BarVolume = self.bar_volume(vols)
            self.predict_bar_change = self.bar_change()

            countChange = self.get_json_data(self.updown, 'Amplitude')
            countVol = self.get_json_data(self.updown, 'Vol')

            if self.signal == 1:
                p30Change = MyFormula.normal_get_x(p=0.3, mean=countChange[2], std=countChange[3])
                p80Change = MyFormula.normal_get_x(p=0.8, mean=countChange[2], std=countChange[3])
                p95Change = MyFormula.normal_get_x(p=0.95, mean=countChange[2], std=countChange[3])

                if self.predict_bar_change < p30Change:
                    self.predict_bar_change = p30Change

                if self.predict_bar_change > p95Change:
                    self.predict_bar_change = p80Change

                p30Vol = MyFormula.normal_get_x(p=0.3, mean=countVol[2], std=countVol[3])
                if self.predict_BarVolume < p30Vol:
                    self.predict_BarVolume = p30Vol

            if self.signal == -1:
                meanChange = countChange[2]
                p65Change = MyFormula.normal_get_x(p=0.65, mean=countChange[2], std=countChange[3])

                if self.predict_bar_change > meanChange:
                    self.predict_bar_change = p65Change

                meanVol = countVol[2]
                p65Vol = MyFormula.normal_get_x(p=0.65, mean=countVol[2], std=countVol[3])
                if self.predict_BarVolume < meanVol:
                    self.predict_BarVolume = p65Vol

        _price = self.checking_data.iloc[-1][StartPrice]

        self.predict_BarVolume = int(self.predict_BarVolume)
        self.predict_bar_change = round(self.predict_bar_change, 2)
        self.predict_bar_price = round(_price * (1 + self.predict_bar_change), 2)

    def trade_point_score(self):
        """趋势评分"""
        scoreCycleChange = 0
        scoreLength = 0
        scoreBarChange = 0
        scoreBarVol = 0

        pCycleChange = 0
        pLength = 0
        pBarChange = 0
        pBarVol = 0

        if self.signal == 1:
            if self.real_CycleChange >= self.predict_CycleChange:
                counts = self.get_json_data(self.updown, 'Amplitude')
                pCycleChange = 1 - MyFormula.normal_get_p(
                    x=self.real_CycleChange, mean=counts[2], std=counts[3]
                )
                scoreCycleChange = 1 + pCycleChange

            if self.real_CycleChange < self.predict_CycleChange:
                counts = self.get_json_data(self.updown, 'Amplitude')
                pCycleChange = 1 - MyFormula.normal_get_p(
                    x=self.real_CycleChange, mean=counts[2], std=counts[3]
                )
                scoreCycleChange = self.predict_CycleChange - self.real_CycleChange

            if self.real_length >= self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 1 - MyFormula.normal_get_p(
                    x=self.real_length, mean=counts[2], std=counts[3]
                )
                scoreLength = 1 + pLength

            if self.real_length < self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 1 - MyFormula.normal_get_p(
                    x=self.real_length, mean=counts[2], std=counts[3]
                )
                scoreLength = pLength

            if self.predict_bar_change != 0:
                if self.real_bar_change > self.predict_bar_change:
                    counts = self.get_json_data(self.updown, 'Amplitude')
                    pBarChange = 1 - MyFormula.normal_get_p(
                        x=self.real_CycleChange, mean=counts[2], std=counts[3]
                    )
                    scoreBarChange = 1 + pBarChange

            if self.predict_BarVolume != 0:
                if self.real_BarVolume > self.predict_BarVolume:
                    counts = self.get_json_data(self.updown, 'Vol')
                    pBarVol = 0.5 - MyFormula.normal_get_p(
                        x=self.real_BarVolume, mean=counts[2], std=counts[3]
                    )
                    scoreBarVol = 1 + pBarVol

        if self.signal == -1:
            if self.real_CycleChange <= self.predict_CycleChange:
                counts = self.get_json_data(self.updown, 'Amplitude')
                pCycleChange = 1 - MyFormula.normal_get_p(
                    x=self.real_CycleChange, mean=counts[2], std=counts[3]
                )
                scoreCycleChange = -1 - pCycleChange

            if self.real_CycleChange > self.predict_CycleChange:
                counts = self.get_json_data(self.updown, 'Amplitude')
                pCycleChange = 1 - MyFormula.normal_get_p(
                    x=self.real_CycleChange, mean=counts[2], std=counts[3]
                )
                scoreCycleChange = self.real_CycleChange - self.predict_CycleChange

            if self.real_length >= self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 1 - MyFormula.normal_get_p(
                    x=self.real_length, mean=counts[2], std=counts[3]
                )
                scoreLength = -1 - pLength

            if self.real_length < self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 0.5 - MyFormula.normal_get_p(
                    x=self.real_length, mean=counts[2], std=counts[3]
                )
                scoreLength = -pLength

            if self.predict_bar_change != 0:
                if self.real_bar_change < self.predict_bar_change:
                    counts = self.get_json_data(self.updown, 'Amplitude')
                    pBarChange = 1 - MyFormula.normal_get_p(
                        x=self.real_CycleChange, mean=counts[2], std=counts[3]
                    )
                    scoreBarChange = -1 - pBarChange

            if self.predict_BarVolume != 0:
                if self.real_BarVolume > self.predict_BarVolume:
                    counts = self.get_json_data(self.updown, 'Vol')
                    pBarVol = 1 - MyFormula.normal_get_p(
                        x=self.real_BarVolume, mean=counts[2], std=counts[3]
                    )
                    scoreBarVol = -1 - pBarVol

        self.trend_score = round(
            scoreCycleChange + scoreLength + scoreBarChange + scoreBarVol, 2
        )
        self.ScoreP = round(pCycleChange + pLength + pBarChange + pBarVol, 2)

        if self.trend_score > 5.5 or self.trend_score < -5.5:
            self.trade_boll = True

            if self.signal == 1:
                self.position_action = '卖出'
                self.tradAction = -1

            if self.signal == -1:
                self.position_action = '买入'
                self.tradAction = 1
                self.trade_boll = True

    def get_json_data(self, trend: str, name: str) -> tuple:
        """
        从 JSON 配置获取数据

        Args:
            trend: 趋势类型 ('up' 或 'down')
            name: 数据名称

        Returns:
            tuple: (max, min, mean, std)
        """
        data = self.jsons['models'][trend]
        max_ = data[name]['max']
        min_ = data[name]['min']
        mean_ = data[name]['mean']
        std_ = data[name]['std']
        return (max_, min_, mean_, std_)

    def get_bar_real(self):
        """获取 Bar 真实值"""
        con1 = (self.data_1m['date'] > self.check_date)
        con2 = (self.data_1m['date'] <= self.trade_timing)

        self.real_BarVolume = int(
            self.data_1m[con1 & con2].sort_values(by=['volume']).tail(5)['volume'].mean() / 100
        )
        self.real_bar_change = self.checking_data.iloc[-1][CycleAmplitudePerBar]
        self.real_bar_change = round(
            self.normal2value(self.real_bar_change, CycleAmplitudePerBar), 3
        )

    def get_cycle_real(self):
        """获取周期真实值"""
        self.real_length = self.checking.iloc[-1][CycleLengthPerBar]
        self.real_length = int(self.normal2value(self.real_length, CycleLengthPerBar))

        self.real_CycleChange = self.checking.iloc[-1][CycleAmplitudeMax]
        self.real_CycleChange = round(
            self.normal2value(self.real_CycleChange, CycleAmplitudeMax), 3
        )
        self.real_CyclePrice = self.checking.iloc[-1][EndPrice]

    def get_bar_data(self):
        """获取当前 Bar 数据"""
        self.trade_timing = self.checking.iloc[-1]['date']
        self.signalStartTime = self.checking.iloc[-1]['StartPriceIndex']
        self._endPriceTime = self.checking.iloc[-1]['StartPriceIndex']
        self.signalTimes = self.checking.iloc[-1][SignalTimes]
        self.signal = self.checking.iloc[-1][Signal]
        self.close = self.checking.iloc[-1]['close']

        reTrends = self.checking.tail(5).reset_index(drop=True)

        if self.signal == 1:
            self.area = '顶部'
            self.signalValue = '上涨'
            self.updown = 'up'

            self.reTrend = 0
            reTrends = reTrends[reTrends['ReTrends'] < 0]
            if reTrends.shape[0] == 5:
                self.reTrend = 1

        else:
            self.area = '底部'
            self.signalValue = '下跌'
            self.updown = 'down'

            self.reTrend = 0
            reTrends = reTrends[reTrends['ReTrends'] > 0]
            if reTrends.shape[0] == 5:
                self.reTrend = 1

        self._signalTimes = self.data_15m[
            self.data_15m['date'] < self.trade_timing
        ].dropna(subset=[SignalChoice]).iloc[-2][SignalTimes]

        self._limitPrice = self.data_15m[
            self.data_15m[SignalTimes] == self._signalTimes
        ].iloc[-1][EndPrice]

        self._limitTradeTiming = self.data_15m[
            self.data_15m[SignalTimes] == self._signalTimes
        ].iloc[-1][EndPriceIndex]

        self.get_cycle_real()
        self.get_bar_real()

    @count_times
    def single_stock(self) -> bool:
        """
        单股循环预测

        Returns:
            bool: 是否成功执行
        """
        # 生成数据
        self.checking_data = self.calculate_check_data()

        if self.checking_data.empty:
            return False

        # 循环处理每个 SignalTimes
        for s_ in self.checking_data.drop_duplicates(subset=[SignalTimes])[SignalTimes]:
            self.checking = self.checking_data[self.checking_data[SignalTimes] == s_]

            self.get_bar_data()

            # 周期点预测
            self.predict_cycle_values()
            self.predict_bar_values()

            self.trade_point_score()

            # 更新数据
            self.update_RecordRun()
            self.update_sql_15m_data()
            self.update_StockPool()

            if self.trade_boll:
                self.report_trade()

            if self.position:
                self.report_position()

            self.report_run()

        return True
