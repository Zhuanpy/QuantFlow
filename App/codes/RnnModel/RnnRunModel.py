# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from keras.models import load_model
from keras import backend as k
from ..downloads.DlDataCombine import download_1m
from ..MySql.LoadMysql import LoadRnnModel
from ..MySql.DataBaseStockData15m import StockData15m
from ..MySql.DataBaseStockData1m import StockData1m
from ..MySql.DataBaseStockPool import TableStockPool
from ..MySql.sql_utils import Stocks
from App.codes.utils.Normal import MathematicalFormula as MyFormula
from App.codes.utils.Normal import ResampleData, Useful, count_times, ReadSaveFile
from ..Signals.StatisticsMacd import SignalMethod
from ..parsers.RnnParser import *
import matplotlib.pyplot as plt
from ..TrendDistinguish.TrendDistinguishRunModel import TrendDistinguishModel
from ..RnnDataFile.stock_path import StockDataPath

plt.rcParams['font.sans-serif'] = ['FangSong']
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)


class Parsers:

    def __init__(self, freq='15m'):
        self.lines, self.line = Useful.dashed_line(50)

        # 股票基础变量
        self.stock_name, self.stock_code, self.stock_id, self.month_parsers = None, None, None, None

        self.jsons = None
        self.freq = freq
        self.monitor = False

        self.data_1m, self.data_15m = None, None
        self.records = None
        self.checking_data = None
        self.record_last_15m_time = None
        self.check_date = None

        #  趋势模型 模型预测变量
        self.trendLabel, self.trendValue = None, None

        # Rnn 模型预测变量
        self.predict_length, self.predict_CycleChange = None, None
        self.predict_CyclePrice, self.predict_BarVolume = None, None,
        self.real_length, self.real_CycleChange, self.real_CyclePrice, self.real_BarVolume = None, None, None, None
        self.predict_bar_change, self.real_bar_change, self.predict_bar_price = None, None, None

        # 股票交易变量
        self.position = None
        self.close, self.stopLoss, self.ExpPrice = None, None, None
        self.score_trends, self.ScoreP = None, None
        self.sellAction, self.buyAction = False, False
        self.reTrend = 0
        self.signal, self.updown, self.signalValue = None, None, None
        self.tradAction = 0


class ModelData(Parsers):

    def __init__(self):

        Parsers.__init__(self)

        self.db_rnn_model, self.tb_rnn_record = 'rnn_model', 'RunRecord'

    def read_1m_by_15m_record(self):

        # 加载RnnModel数据
        self.records = LoadRnnModel.load_run_record()

        # 筛选出特定股票代码的记录
        # todo : 这能是否能改成，输入股票ID 获取某个数值：-获取 record_last_15m_time
        # self.record_last_15m_time = LoadRnnModel.load_run_record(self.stock_code, 'Time15m')
        self.records = self.records[self.records['code'] == self.stock_code]
        self.record_last_15m_time = self.records.iloc[0]['Time15m']
        data_last_150_days = pd.Timestamp('today') + pd.Timedelta(days=-150)

        try:
            # 如果记录的15分钟级别数据时间 150天内，则向前推10天
            select_15m_time = self.record_last_15m_time + pd.Timedelta(
                days=-10) if self.record_last_15m_time > data_last_150_days else data_last_150_days

        except Exception as ex:
            select_15m_time = data_last_150_days
            print(f'Select 1m code_data Date Error: {ex}')

        # 从StockData1m 加载1分钟级别的股票数据
        data_1m = StockData1m.load_1m(self.stock_code, str(data_last_150_days.year))

        # 筛选出所需的 code_data 1m 数据, 大于筛选时间变量 select_15m_time
        data_1m = data_1m[data_1m['date'] > select_15m_time].drop_duplicates(subset=['date']).reset_index(drop=True)

        return data_1m

    def monitor_read_1m(self):
        data_1m = self.read_1m_by_15m_record()
        data_ = download_1m(self.stock_name, self.stock_code, days=1)

        monitor_1m_data = StockDataPath.monitor_1m_data_path(self.stock_code)  # 通过文件路径函数找到对应数据
        if not data_.empty:
            data_.to_csv(monitor_1m_data, index=False)

        else:
            data_ = pd.read_csv(monitor_1m_data)

        data_1m = pd.concat([data_1m, data_], ignore_index=True)
        return data_1m

    def check_date_read_1m(self):
        data_1m = self.read_1m_by_15m_record()

        # todo 这里导入数据的逻辑有问题
        # check_data 查看的日期 和 record 15m 会有冲突； 假设  record 15m 是 2023年12月15号， 但是 check date 是 12月1号 就起冲突；
        # 解决方法：record_15m 是否可以放入到 Json 数据中

        """
        参数说明
        例如：
        self.check_date =  2024-01-08
        data_select_date 返回数据：  2024-01-09 00:00:00
        """

        data_select_date = self.check_date + pd.Timedelta(days=1)
        data_1m = data_1m[data_1m['date'] < data_select_date]
        data_1m = data_1m.drop_duplicates(subset=['date']).reset_index(drop=True)
        return data_1m

    def column2normal(self, column: str, match: str):
        max_ = self.jsons[match]['num_max']
        min_ = self.jsons[match]['num_min']
        self.data_15m.loc[self.data_15m[column] > max_, column] = max_
        self.data_15m.loc[self.data_15m[column] < min_, column] = min_
        self.data_15m.loc[:, column] = (self.data_15m[column] - min_) / (max_ - min_)

    def Bar1mVolumeMax(self, x, num):
        st = x + pd.Timedelta(minutes=-15)
        ed = x
        vol = self.data_1m[(self.data_1m['date'] > st) &
                           (self.data_1m['date'] < ed)].sort_values(by=['volume'])['volume'].tail(num).mean()
        try:
            vol = int(vol)

        except ValueError:
            vol = 0

        return vol

    def update_15m(self):

        # todo end_15m_timing 是从全部的15分钟数据中筛选出来，这样是否会浪费时间？ 是否有必要这么做 ?
        end_15m_timing = StockData15m.get_data_15m_data_end_date(self.stock_code)
        new_15m_data = self.data_15m[self.data_15m['date'] > end_15m_timing]

        if new_15m_data.empty:
            return False

        """ 监测时，会出现非整数时间，此时需要把此时间删除
        例如：new code_data 中 date = 14:13:00 
        解决方案： 建立一个 15m minute list,
        如果最后的时间 分钟不在 list中，则不要
        """

        list_timing_15m_minute = ['09:45:00', '10:00:00',
                                  '10:15:00', '10:30:00', '10:45:00',
                                  '11:00:00', '11:15:00', '11:30:00',
                                  '13:15:00', '13:30:00', '13:45:00',
                                  '14:00:00', '14:15:00', '14:30:00',
                                  '14:45:00', '15:00:00']

        new_end_15m_timing = new_15m_data.iloc[-1]['date']
        new_end_minute = new_end_15m_timing.time().strftime('%H:%M:%S')

        if new_end_minute not in list_timing_15m_minute:
            new_15m_data = new_15m_data[new_15m_data['date'] < new_end_15m_timing]

        StockData15m.append_15m(data=new_15m_data, stock_code=self.stock_code)

        signalTimes = self.data_15m[self.data_15m['date'] >= end_15m_timing].iloc[0]['SignalTimes']
        _signalTimes = self.jsons['RecordEndSignalTimes']

        # SignalStartTime
        if signalTimes == _signalTimes:
            return False

        new_data = self.data_15m[(self.data_15m['SignalTimes'] == signalTimes) &
                                 (self.data_15m['date'] <= end_15m_timing)]

        sql_update_signal = f'''SignalTimes = %s, SignalStartTime = %s, 
        Signal = %s, SignalChoice= %s where date = %s;'''
        for _, row in new_data.iterrows():
            data_id = row['date']
            signal_times = row['SignalTimes']
            signal_start_time = row['SignalStartTime']
            signal = row['Signal']
            signal_choice = row['SignalChoice']

            parser = (signal_times, signal_start_time, signal, signal_choice, data_id)
            StockData15m.set_data_15m_data(self.stock_code, sql_update_signal, parser)

        # 保存 15m 数据 截止日期
        date_ = self.data_15m.iloc[-1]['date'].strftime('%Y-%m-%d %H:%M:%S')
        signal_start_time_ = self.data_15m.iloc[-1]['SignalStartTime'].strftime('%Y-%m-%d %H:%M:%S')
        signal_ = self.data_15m.iloc[-1]['Signal']
        signal_times_ = self.data_15m.iloc[-1]['SignalTimes']

        self.jsons.update({
            'RecordEndDate': date_,
            'RecordEndSignal': signal_,
            'RecordEndSignalTimes': signal_times_,
            'RecordEndSignalStartTime': signal_start_time_
        })

        ReadSaveFile.save_json(dic=self.jsons, months=self.month_parsers, code=self.stock_code)  # 更新参数

    def daily_data(self):

        data = ResampleData.resample_1m_data(data=self.data_1m, freq='day')

        data['date'] = pd.to_datetime(data['date']) + pd.Timedelta(minutes=585)
        data[DailyVolEma] = data['volume'].rolling(90, min_periods=1).mean()

        max_ = self.jsons[DailyVolEma]
        data[DailyVolEmaParser] = max_ / data[DailyVolEma]
        data = data[data['date'] > (self.record_last_15m_time + pd.Timedelta(days=-1))]
        data = data[['date', DailyVolEmaParser]].set_index('date', drop=True)
        return data

    def first_15m(self):
        """
        15m 数据计算
        """
        path = StockDataPath.json_data_path(self.month_parsers, self.stock_code)
        self.jsons = ReadSaveFile.read_json_by_path(path)

        data_daily = self.daily_data()

        self.data_15m = self.data_1m[self.data_1m['date'] > pd.to_datetime(self.record_last_15m_time)].reset_index(
            drop=True)

        self.data_15m = ResampleData.resample_1m_data(data=self.data_15m, freq='15m')

        self.data_15m = SignalMethod.signal_by_MACD_3ema(data=self.data_15m, data1m=self.data_1m)

        t15m = self.data_15m.drop_duplicates(subset=[SignalTimes]).tail(6).iloc[0]['date'].date()

        sql = f''' Time15m = %s where id = %s;'''
        parser = (t15m, self.stock_id)
        LoadRnnModel.set_table_run_record(sql, parser)

        self.data_15m = self.data_15m.set_index('date', drop=True)

        self.data_15m = self.data_15m.join([data_daily]).reset_index()

        fills = [DailyVolEmaParser]
        self.data_15m[fills] = self.data_15m[fills].ffill()

        lastST = list(self.data_15m.drop_duplicates(subset=[SignalTimes]).tail(6)[SignalTimes])
        self.data_15m = self.data_15m[self.data_15m[SignalTimes].isin(lastST)]

        # find Bar1mVolumeMax
        self.data_15m.loc[:, Bar1mVolMax1] = self.data_15m['date'].apply(self.Bar1mVolumeMax, args=(1,))
        self.data_15m.loc[:, Bar1mVolMax5] = self.data_15m['date'].apply(self.Bar1mVolumeMax, args=(5,))

        # 保存15m新数据
        self.update_15m()

        # 运行趋势辨别模块
        distinguish = TrendDistinguishModel()
        self.trendLabel, self.trendValue = distinguish.distinguish_freq(self.stock_code, self.data_15m)

        return self.data_15m

    def second_15m(self):

        """
        15m 数据标准化；
        """

        li = ['volume', Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15, 'EndDaily1mVolMax5',
              Cycle1mVolMax1, Cycle1mVolMax5, Bar1mVolMax1, Bar1mVolMax5]

        for c in li:
            self.data_15m[c] = round(self.data_15m[c] * self.data_15m[DailyVolEmaParser])

        pre_dic = {preCycle1mVolMax1: Cycle1mVolMax1,
                   preCycle1mVolMax5: Cycle1mVolMax5,
                   preCycleAmplitudeMax: CycleAmplitudeMax,
                   preCycleLengthMax: CycleLengthMax}

        condition = (~self.data_15m[SignalChoice].isnull())
        for key, value in pre_dic.items():
            self.data_15m.loc[condition, key] = self.data_15m.loc[condition, value].shift(1)

        next_dic = {nextCycleLengthMax: CycleLengthMax, nextCycleAmplitudeMax: CycleAmplitudeMax}
        condition = (~self.data_15m[SignalChoice].isnull())
        for key, value in next_dic.items():
            self.data_15m.loc[condition, key] = self.data_15m.loc[condition, value].shift(-1)

        fills = list(pre_dic.keys()) + list(next_dic.keys())
        self.data_15m[fills] = self.data_15m[fills].ffill()
        self.data_15m[Signal] = self.data_15m[Signal].astype(float)

        dic = {'volume': 'volume',
               CycleLengthMax: CycleLengthMax,
               CycleLengthPerBar: CycleLengthPerBar,
               Cycle1mVolMax1: Cycle1mVolMax1,
               Cycle1mVolMax5: Cycle1mVolMax5,
               Bar1mVolMax1: Daily1mVolMax1,
               Bar1mVolMax5: Daily1mVolMax5,
               Daily1mVolMax1: Daily1mVolMax1,
               Daily1mVolMax5: Daily1mVolMax5,
               Daily1mVolMax15: Daily1mVolMax15,
               'EndDaily1mVolMax5': 'EndDaily1mVolMax5',
               preCycle1mVolMax1: Cycle1mVolMax1,
               preCycle1mVolMax5: Cycle1mVolMax5,
               preCycleLengthMax: CycleLengthMax,

               CycleAmplitudePerBar: CycleAmplitudePerBar,
               CycleAmplitudeMax: CycleAmplitudeMax,

               nextCycleLengthMax: nextCycleLengthMax,
               nextCycleAmplitudeMax: nextCycleAmplitudeMax}

        for key, value in dic.items():
            self.column2normal(key, value)

        self.data_15m['ReTrends'] = self.data_15m['close'] - self.data_15m['EmaMid']

        self.data_15m = self.data_15m.replace([np.inf, -np.inf], np.nan)
        # print(self.data_15m)
        return self.data_15m

    def calculate_check_data(self):

        # 获取 1m 数据
        self.data_1m = self.monitor_read_1m() if self.monitor else self.check_date_read_1m()

        self.first_15m()

        self.second_15m()

        data_ = self.data_15m[self.data_15m['date'] > self.check_date]

        return data_


class DlModel(Parsers):

    def __init__(self, model_alpha=1):
        Parsers.__init__(self)
        self.predict_data = None
        self.model_alpha = model_alpha
        self.model_name = ModelName
        self.X = XColumn()

    def normal2value(self, data: float, match: str):
        high = self.jsons[match]['num_max']
        low = self.jsons[match]['num_min']
        num_normal = data * (high - low) + low
        return num_normal

    def predictive_value(self, model_name, x):
        k.clear_session()
        file_name = f'{model_name}_{self.stock_code}.h5'
        path = StockDataPath.model_path(self.month_parsers, file_name)
        model = load_model(path)
        val = model.predict(x)
        val = val[0][0]
        return val

    def x_data(self, columns):
        x = self.predict_data[columns].tail(30)
        x = pd.concat([x[[Signal]], x], axis=1)
        x = x.to_numpy()
        h = 30 - x.shape[0]
        w = 30 - x.shape[1]

        ht = h // 2
        hl = h - ht

        wl = w // 2
        wr = w - wl

        x = np.pad(x, ((ht, hl), (wr, wl)), 'constant', constant_values=(0, 0))
        x.shape = (1, 30, 30, 1)

        return x

    def cycle_length(self):
        x = self.x_data(self.X[0])
        y = self.predictive_value(self.model_name[0], x)
        y = round(self.normal2value(data=y, match=nextCycleLengthMax) * self.model_alpha)
        return y

    def cycle_change(self):
        x = self.x_data(self.X[1])
        y = self.predictive_value(self.model_name[1], x)
        y = round(self.normal2value(data=y, match=nextCycleAmplitudeMax) * self.model_alpha, 3)
        return y

    def bar_change(self):
        x = self.x_data(self.X[2])
        y = self.predictive_value(self.model_name[2], x)
        y = round(self.normal2value(data=y, match=CycleAmplitudeMax) * self.model_alpha, 3)
        return y

    def bar_volume(self, vol_parser):
        x = self.x_data(self.X[3])
        y = self.predictive_value(self.model_name[3], x)

        try:
            y = round(self.normal2value(y, 'EndDaily1mVolMax5') * self.model_alpha / vol_parser / 100)

        except Exception as ex:
            y = 0
            print(f'Prediction bar volume error: \n{ex}')

        return y


class UpdateData(Parsers):

    def __init__(self):
        Parsers.__init__(self)

        self.current = pd.Timestamp('today').date()
        self.signalTimes, self._signalTimes, self.signalStartTime = None, None, None

        # current stock inform
        self.change_max = None
        self.trade_timing = None
        self.position_action = None
        self.trend_score = None

        self.RunDate = None
        self.trade_boll = False
        self._limitTradeTiming = None

    def update_StockPool(self):
        sql = f''' close = %s, ExpPrice = %s, RnnModel= %s, Trends= %s, 
        ReTrend= %s,  TrendProbability= %s,  RecordDate= %s where id= %s; '''

        parser = (self.close, self.ExpPrice, self.trend_score, self.trendValue,
                  self.reTrend, self.ScoreP, self.check_date, self.stock_id)
        TableStockPool.set_table_to_pool(sql, parser)

    def update_RecordRun(self):
        sql1 = f''' Trends = %s, SignalStartTime = %s, PredictCycleLength = %s, RealCycleLength = %s,
        PredictCycleChange = %s, PredictCyclePrice = %s, RealCycleChange = %s, PredictBarChange = %s,
        RealBarChange = %s, PredictBarVolume = %s, RealBarVolume = %s, ScoreTrends = %s,
        TradePoint = %s, TimeRunBar = %s, RenewDate = %s where id = %s; '''

        parsers = (self.signalValue, self.signalStartTime, self.predict_length, self.real_length,
                   self.predict_CycleChange, self.predict_CyclePrice, self.real_CycleChange, self.predict_bar_change,
                   self.real_bar_change, self.predict_BarVolume, self.real_BarVolume, self.trend_score,
                   self.tradAction, self.trade_timing, self.current, self.stock_id)

        LoadRnnModel.set_table_run_record(sql1, parsers)

    def update_sql_15m_data(self):
        sql = f'''PredictCycleChange = %s, 
        PredictCyclePrice = %s, 
        PredictCycleLength = %s, 
        PredictBarChange = %s, 
        PredictBarPrice = %s, 
        PredictBarVolume = %s, 
        ScoreRnnModel = %s, 
        TradePoint = %s where date = %s;'''

        parsers = (self.predict_CycleChange, self.predict_CyclePrice, self.predict_length, self.predict_bar_change,
                   self.predict_bar_price, self.predict_BarVolume, self.trend_score, self.tradAction,
                   self.trade_timing)

        StockData15m.set_data_15m_data(self.stock_code, sql, parsers)


class TradingAction(Parsers):

    def __init__(self):
        Parsers.__init__(self)

    def buy_action(self):
        # 条件1：趋势反转买入
        trend_reversal_condition = (self.signal == -1) and (self.reTrend == 1)

        # 条件2：跌入底部触发信号买入
        bottom_signal_condition = (self.signal == -1) and (self.tradAction == 1)

        if trend_reversal_condition or bottom_signal_condition:
            self.buyAction = True


class PredictionCommon(ModelData, DlModel, UpdateData):

    def __init__(self, stock: str, month_parsers: str, check_date,
                 alpha=1, monitor=True, stop_loss=None, position=None):
        ModelData.__init__(self)
        DlModel.__init__(self)
        UpdateData.__init__(self)

        self.stock_name, self.stock_code, self.stock_id = Stocks(stock)

        self.position = position
        self.month_parsers, self.monitor = month_parsers, monitor
        self.stopLoss = stop_loss

        # todo 读取月份文件夹下数据失败时候怎么处理？
        path = StockDataPath.json_data_path(self.month_parsers, self.stock_code)
        self.jsons = ReadSaveFile.read_json_by_path(path)

        # 生成预测数据
        if monitor:
            check_date = pd.Timestamp('today').strftime('%Y-%m-%d')

        self.check_date = pd.to_datetime(check_date)
        self.alpha = alpha

        self.checking_data, self.checking, self.predict_data = None, None, None
        self._endPriceTime, self._limitPrice, self.area = None, None, None

    def report_run(self):

        headers = f'{self.lines}\n' \
                  f'检测日期：{self.check_date.date()}；\n' \
                  f'股票:{self.stock_name}，代码:{self.stock_code}；\n' \
                  f'当前趋势:{self.signalValue}；\n' \
                  f'{self.line}\n'

        cycles = f'时间点：{self.trade_timing}；\n' \
                 f'预测周期长度：{self.predict_length} 根，现真实长度：{self.real_length} 根；\n' \
                 f'预测周期振幅：{self.predict_CycleChange} 点， 现真实振幅：{self.real_CycleChange} 点；\n' \
                 f'预测周期{self.area}价：CNY {self.predict_CyclePrice}，现真实{self.area}价：CNY {self.real_CyclePrice}；\n'

        bars = f'预测Bar成交量：{self.predict_BarVolume} 手，真实成交量：{self.real_BarVolume} 手；\n' \
               f'预测Bar振幅：{self.predict_bar_change} 点，真实振幅：{self.real_bar_change} 点；\n' \
               f'预测Bar价格：CNY {self.predict_bar_price}，真实振幅：CNY {self.close}；\n'

        score = f'\n趋势得分：{self.trend_score}\n'

        _cycles = f'{self.line}\n前周期信息：\n前周期峰值时间：{self._limitTradeTiming}；\n前周期峰值价格：{self._limitPrice}；\n'

        if self.predict_length >= self.real_length:
            shower = f'{headers}{cycles}{score}{_cycles}\n{self.lines}'

        else:
            shower = f'{headers}{cycles}{bars}{score}{_cycles}\n{self.lines}'

        # print(f'当前价：{self.close}, 止损价：{self.stopLoss}；')
        print(shower)

    # 买卖点记录
    def report_trade(self):  # 交易点信息

        shapes = self.records[(self.records['RenewDate'] != self.current) &
                              (self.records['TradePoint'] != 0)].shape[0]
        if not shapes:

            title = f'股票:{self.stock_name},代码：{self.stock_code}; 触发{self.position_action}信号;'

            content = f'交易时间点：{self.trade_timing};\n' \
                      f'预测此{self.signalValue}趋势周期bar: {self.predict_length}根,' \
                      f'现趋势真实bar: {self.real_length}根;\n;' \
                      f'预测此{self.signalValue}全周期振幅值：{self.predict_CycleChange}点,' \
                      f'现全周期真实振幅: {self.real_CycleChange}点;\n; ' \
                      f'预测当前时间点振幅值：{self.predict_bar_change}点, ' \
                      f'当前时间点真实振幅: {self.real_bar_change}点;\n' \
                      f'预测当前时间点成交量: {self.predict_BarVolume}手,' \
                      f'当前时间点真实成交量: {self.real_BarVolume}手;\n'

            mails = f'{title}\n\n{content}'

            if self.monitor:
                Useful.sent_emails(message_title=title, mail_content=mails)

    def report_position(self):

        title, message = '', ''

        if self.close < self.stopLoss:
            title = f'持仓股：{self.stock_name}止损卖出；'

            message = f'''股票：{self.stock_name}, close: {self.close}, 
            stop loss: {self.stopLoss};\nTrend score: ''{self.trend_score}; '''
            self.sellAction = True

        if self.trend_score > 4:
            title = f'持仓股：{self.stock_name}止盈卖出；'
            self.sellAction = True

            message = f'''股票：{self.stock_name}, close: {self.close}, 
            stop loss: {self.stopLoss};\nTrend score: {self.trend_score};'''

        if self.sellAction:
            Useful.sent_emails(title, message)

    def predict_cycle_values(self):  # 趋势转变点预判

        R_signalStartTime = self.records.iloc[0]['SignalStartTime']

        # 判断是否运行转折点模型
        if self.signalStartTime == R_signalStartTime:
            self.predict_length = self.records.iloc[0]['PredictCycleLength']
            self.predict_CycleChange = self.records.iloc[0]['PredictCycleChange']
            self.predict_CyclePrice = self.records.iloc[0]['PredictCyclePrice']

        else:
            self.predict_data = self.data_15m[self.data_15m[SignalTimes] == self._signalTimes]
            self._limitPrice = self.predict_data.iloc[0][EndPrice]

            self.predict_CycleChange = self.cycle_change()  # 预测下个周期振幅 信号出现点周期振幅
            self.predict_length = self.cycle_length()  # 预测下个周期长度  信号出现点周期长度

            counts = self.get_json_data(self.updown, 'Amplitude')  # mean_amplitude = stand[2]
            mean_ = counts[2]
            std_ = counts[3]
            if self.signal == 1:
                # 上涨，选择 30% - 80% 胜率，
                p30 = MyFormula.normal_get_x(p=0.3, mean=mean_, std=counts[3])
                p95 = MyFormula.normal_get_x(p=0.95, mean=mean_, std=counts[3])

                #  预估值小于0， 不准确时候
                if self.predict_CycleChange < p30:
                    self.predict_CycleChange = p30

                if self.predict_CycleChange > p95:
                    self.predict_CycleChange = MyFormula.normal_get_x(p=0.8, mean=mean_, std=std_)

            if self.signal == -1:
                # 预测跌幅太小（小于均值）时：  选择65%的胜率
                p65 = MyFormula.normal_get_x(p=0.65, mean=mean_, std=std_)
                if self.predict_CycleChange > mean_:
                    self.predict_CycleChange = p65

            self.predict_CyclePrice = round(self._limitPrice * (1 + self.predict_CycleChange), 2)  # 预测下个周期峰值价位

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

        self.predict_BarVolume = 0
        self.predict_bar_change = 0

        meanLength = self.get_json_data(self.updown, 'Length')[2]

        if self.real_length > min(meanLength, self.predict_length):
            # 运行 bar change & volume 模型, 获取 bar_change & bar_volume 值
            self.predict_data = self.data_15m[(self.data_15m['date'] < self.trade_timing) &
                                              (self.data_15m[SignalTimes] == self.signalTimes)].tail(30)

            vols = self.checking_data.iloc[-1][DailyVolEmaParser]
            self.predict_BarVolume = self.bar_volume(vols)

            self.predict_bar_change = self.bar_change()

            countChange = self.get_json_data(self.updown, 'Amplitude')  # mean_amplitude = stand[2]
            countVol = self.get_json_data(self.updown, 'Vol')  # mean_amplitude = stand[2]

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

    # ScoreTrends 趋势评分
    def trade_point_score(self):

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

                pCycleChange = 1 - MyFormula.normal_get_p(x=self.real_CycleChange, mean=counts[2], std=counts[3])
                scoreCycleChange = 1 + pCycleChange

            if self.real_CycleChange < self.predict_CycleChange:
                counts = self.get_json_data(self.updown, 'Amplitude')
                pCycleChange = 1 - MyFormula.normal_get_p(x=self.real_CycleChange, mean=counts[2], std=counts[3])
                scoreCycleChange = self.predict_CycleChange - self.real_CycleChange

            if self.real_length >= self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 1 - MyFormula.normal_get_p(x=self.real_length, mean=counts[2], std=counts[3])
                scoreLength = 1 + pLength

            if self.real_length < self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 1 - MyFormula.normal_get_p(x=self.real_length, mean=counts[2], std=counts[3])
                scoreLength = 1 - MyFormula.normal_get_p(x=self.real_length, mean=counts[2], std=counts[3])

            if self.predict_bar_change != 0:
                if self.real_bar_change > self.predict_bar_change:
                    counts = self.get_json_data(self.updown, 'Amplitude')
                    pBarChange = 1 - MyFormula.normal_get_p(x=self.real_CycleChange, mean=counts[2], std=counts[3])
                    scoreBarChange = 1 + pBarChange

            if self.predict_BarVolume != 0:
                if self.real_BarVolume > self.predict_BarVolume:
                    counts = self.get_json_data(self.updown, 'Vol')
                    pBarVol = 0.5 - MyFormula.normal_get_p(x=self.real_BarVolume, mean=counts[2], std=counts[3])
                    scoreBarVol = 1 + pBarVol

        if self.signal == -1:

            if self.real_CycleChange <= self.predict_CycleChange:
                counts = self.get_json_data(self.updown, 'Amplitude')
                pCycleChange = 1 - MyFormula.normal_get_p(x=self.real_CycleChange, mean=counts[2], std=counts[3])
                scoreCycleChange = -1 - pCycleChange

            if self.real_CycleChange > self.predict_CycleChange:
                counts = self.get_json_data(self.updown, 'Amplitude')
                pCycleChange = 1 - MyFormula.normal_get_p(x=self.real_CycleChange, mean=counts[2], std=counts[3])
                scoreCycleChange = self.real_CycleChange - self.predict_CycleChange

            if self.real_length >= self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 1 - MyFormula.normal_get_p(x=self.real_length, mean=counts[2], std=counts[3])
                scoreLength = -1 - pLength

            if self.real_length < self.predict_length:
                counts = self.get_json_data(self.updown, 'Length')
                pLength = 0.5 - MyFormula.normal_get_p(x=self.real_length, mean=counts[2], std=counts[3])
                scoreLength = -pLength

            if self.predict_bar_change != 0:
                if self.real_bar_change < self.predict_bar_change:
                    counts = self.get_json_data(self.updown, 'Amplitude')
                    pBarChange = 1 - MyFormula.normal_get_p(x=self.real_CycleChange, mean=counts[2], std=counts[3])
                    scoreBarChange = -1 - pBarChange

            if self.predict_BarVolume != 0:
                if self.real_BarVolume > self.predict_BarVolume:
                    counts = self.get_json_data(self.updown, 'Vol')
                    pBarVol = 1 - MyFormula.normal_get_p(x=self.real_BarVolume, mean=counts[2], std=counts[3])
                    scoreBarVol = -1 - pBarVol

        self.trend_score = round(scoreCycleChange + scoreLength + scoreBarChange + scoreBarVol, 2)
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

    def get_json_data(self, trend: str, name: str):
        data = self.jsons['models'][trend]
        max_ = data[name]['max']
        min_ = data[name]['min']
        mean_ = data[name]['mean']
        std_ = data[name]['std']
        result = (max_, min_, mean_, std_)
        return result

    def get_bar_real(self):
        con1 = (self.data_1m['date'] > self.check_date)
        con2 = (self.data_1m['date'] <= self.trade_timing)

        self.real_BarVolume = int(self.data_1m[con1 & con2].sort_values(by=['volume']).tail(5)['volume'].mean() / 100)
        self.real_bar_change = self.checking_data.iloc[-1][CycleAmplitudePerBar]
        self.real_bar_change = round(self.normal2value(self.real_bar_change, CycleAmplitudePerBar), 3)

    def get_cycle_real(self):
        # Condition Real Data;
        self.real_length = self.checking.iloc[-1][CycleLengthPerBar]
        self.real_length = int(self.normal2value(self.real_length, CycleLengthPerBar))

        self.real_CycleChange = self.checking.iloc[-1][CycleAmplitudeMax]
        self.real_CycleChange = round(self.normal2value(self.real_CycleChange, CycleAmplitudeMax), 3)
        self.real_CyclePrice = self.checking.iloc[-1][EndPrice]

    def get_bar_data(self):
        self.trade_timing = self.checking.iloc[-1]['date']  # 交易日期
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

            # 判断是否反转
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

        self._signalTimes = self.data_15m[self.data_15m['date'] < self.trade_timing
                                          ].dropna(subset=[SignalChoice]).iloc[-2][SignalTimes]
        self._limitPrice = self.data_15m[self.data_15m[SignalTimes] == self._signalTimes].iloc[-1][EndPrice]

        self._limitTradeTiming = self.data_15m[self.data_15m[SignalTimes] == self._signalTimes
                                               ].iloc[-1][EndPriceIndex]  # 前周期峰值时间点

        self.get_cycle_real()  # cycle real value
        self.get_bar_real()  # bar real value

    @count_times
    def single_stock(self):  # 单股循环

        # 生成数据 data_1m, data_15m, checking_data, checking
        self.checking_data = self.calculate_check_data()

        if self.checking_data.empty:  # 判断check date code_data 是否为空
            return False

        for s_ in self.checking_data.drop_duplicates(subset=[SignalTimes])[SignalTimes]:  # 判断 SignalTimes 个数
            self.checking = self.checking_data[self.checking_data[SignalTimes] == s_]

            self.get_bar_data()  # 获取当前Bar 各个数据值

            # 周期点预测，需先判断是否运行模型
            self.predict_cycle_values()
            self.predict_bar_values()

            self.trade_point_score()  # 趋势得分

            # 更新数据
            self.update_RecordRun()
            self.update_sql_15m_data()  # 更新 sql_15m_data
            self.update_StockPool()  # 更新股票池

            if self.trade_boll:
                self.report_trade()

            if self.position:
                self.report_position()

            self.report_run()  # 显示运行结果


if __name__ == '__main__':
    # month_ = '2022-02'
    # _date = '2024-01-15'
    stock_ = '002466'
    # rm = PredictionCommon(stock=stock_, month_parsers=month_, monitor=False, check_date=_date)
    # rm.single_stock()

