# -*- coding: utf-8 -*-
"""
数据加载模块

提供 RNN 模型所需的数据加载和预处理功能
"""

import pandas as pd
import numpy as np

from App.codes.RnnModel.rnn_base import RnnBase
from App.codes.MySql.LoadMysql import LoadRnnModel
from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.MySql.DataBaseStockData1m import StockData1m
from App.codes.utils.Normal import ResampleData, ReadSaveFile
from App.codes.Signals.StatisticsMacd import SignalMethod
from App.codes.downloads.DlDataCombine import download_1m
from App.codes.TrendDistinguish.TrendDistinguishRunModel import TrendDistinguishModel
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.codes.parsers.RnnParser import (
    DailyVolEma, DailyVolEmaParser, Bar1mVolMax1, Bar1mVolMax5,
    SignalTimes, Signal
)


class ModelData(RnnBase):
    """
    模型数据加载和处理类

    提供 1 分钟和 15 分钟数据的加载、转换和预处理功能
    """

    def __init__(self):
        super().__init__()
        self.db_rnn_model = 'rnn_model'
        self.tb_rnn_record = 'RunRecord'

    def read_1m_by_15m_record(self) -> pd.DataFrame:
        """
        根据 15 分钟记录读取 1 分钟数据

        Returns:
            pd.DataFrame: 1 分钟股票数据
        """
        # 加载 RNN 模型运行记录
        self.records = LoadRnnModel.load_run_record()

        # 筛选出特定股票代码的记录
        self.records = self.records[self.records['code'] == self.stock_code]
        self.record_last_15m_time = self.records.iloc[0]['Time15m']
        data_last_150_days = pd.Timestamp('today') + pd.Timedelta(days=-150)

        try:
            # 如果记录的 15 分钟级别数据时间在 150 天内，则向前推 10 天
            select_15m_time = (
                self.record_last_15m_time + pd.Timedelta(days=-10)
                if self.record_last_15m_time > data_last_150_days
                else data_last_150_days
            )
        except Exception as ex:
            select_15m_time = data_last_150_days
            print(f'Select 1m data Date Error: {ex}')

        # 从 StockData1m 加载 1 分钟级别的股票数据
        data_1m = StockData1m.load_1m(self.stock_code, str(data_last_150_days.year))

        # 筛选出所需的 1m 数据
        data_1m = data_1m[data_1m['date'] > select_15m_time].drop_duplicates(
            subset=['date']
        ).reset_index(drop=True)

        return data_1m

    def monitor_read_1m(self) -> pd.DataFrame:
        """
        监控模式下读取 1 分钟数据

        Returns:
            pd.DataFrame: 合并后的 1 分钟数据
        """
        data_1m = self.read_1m_by_15m_record()
        data_ = download_1m(self.stock_name, self.stock_code, days=1)

        monitor_1m_data = StockDataPath.monitor_1m_data_path(self.stock_code)
        if not data_.empty:
            data_.to_csv(monitor_1m_data, index=False)
        else:
            data_ = pd.read_csv(monitor_1m_data)

        data_1m = pd.concat([data_1m, data_], ignore_index=True)
        return data_1m

    def check_date_read_1m(self) -> pd.DataFrame:
        """
        按检查日期读取 1 分钟数据

        Returns:
            pd.DataFrame: 过滤后的 1 分钟数据
        """
        data_1m = self.read_1m_by_15m_record()

        data_select_date = self.check_date + pd.Timedelta(days=1)
        data_1m = data_1m[data_1m['date'] < data_select_date]
        data_1m = data_1m.drop_duplicates(subset=['date']).reset_index(drop=True)
        return data_1m

    def column2normal(self, column: str, match: str):
        """
        将列数据归一化

        Args:
            column: 列名
            match: JSON 配置中的匹配键
        """
        max_ = self.jsons[match]['num_max']
        min_ = self.jsons[match]['num_min']
        self.data_15m.loc[self.data_15m[column] > max_, column] = max_
        self.data_15m.loc[self.data_15m[column] < min_, column] = min_
        self.data_15m.loc[:, column] = (self.data_15m[column] - min_) / (max_ - min_)

    def Bar1mVolumeMax(self, x, num: int) -> int:
        """
        计算 Bar 内 1 分钟最大成交量

        Args:
            x: 时间戳
            num: 取前 N 个最大值的均值

        Returns:
            int: 最大成交量均值
        """
        st = x + pd.Timedelta(minutes=-15)
        ed = x
        vol = self.data_1m[
            (self.data_1m['date'] > st) &
            (self.data_1m['date'] < ed)
        ].sort_values(by=['volume'])['volume'].tail(num).mean()

        try:
            vol = int(vol)
        except ValueError:
            vol = 0

        return vol

    def update_15m(self):
        """更新 15 分钟数据到数据库"""
        end_15m_timing = StockData15m.get_data_15m_data_end_date(self.stock_code)
        new_15m_data = self.data_15m[self.data_15m['date'] > end_15m_timing]

        if new_15m_data.empty:
            return False

        # 15 分钟时间点列表
        list_timing_15m_minute = [
            '09:45:00', '10:00:00', '10:15:00', '10:30:00', '10:45:00',
            '11:00:00', '11:15:00', '11:30:00', '13:15:00', '13:30:00',
            '13:45:00', '14:00:00', '14:15:00', '14:30:00', '14:45:00', '15:00:00'
        ]

        new_end_15m_timing = new_15m_data.iloc[-1]['date']
        new_end_minute = new_end_15m_timing.time().strftime('%H:%M:%S')

        if new_end_minute not in list_timing_15m_minute:
            new_15m_data = new_15m_data[new_15m_data['date'] < new_end_15m_timing]

        StockData15m.append_15m(data=new_15m_data, stock_code=self.stock_code)

        signalTimes = self.data_15m[
            self.data_15m['date'] >= end_15m_timing
        ].iloc[0]['SignalTimes']
        _signalTimes = self.jsons['RecordEndSignalTimes']

        if signalTimes == _signalTimes:
            return False

        new_data = self.data_15m[
            (self.data_15m['SignalTimes'] == signalTimes) &
            (self.data_15m['date'] <= end_15m_timing)
        ]

        sql_update_signal = '''SignalTimes = %s, SignalStartTime = %s,
        Signal = %s, SignalChoice= %s where date = %s;'''

        for _, row in new_data.iterrows():
            data_id = row['date']
            signal_times = row['SignalTimes']
            signal_start_time = row['SignalStartTime']
            signal = row['Signal']
            signal_choice = row['SignalChoice']

            parser = (signal_times, signal_start_time, signal, signal_choice, data_id)
            StockData15m.set_data_15m_data(self.stock_code, sql_update_signal, parser)

        # 保存 15m 数据截止日期
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

        ReadSaveFile.save_json(dic=self.jsons, months=self.month_parsers, code=self.stock_code)

    def daily_data(self) -> pd.DataFrame:
        """
        生成日线数据

        Returns:
            pd.DataFrame: 日线数据
        """
        data = ResampleData.resample_1m_data(data=self.data_1m, freq='day')

        data['date'] = pd.to_datetime(data['date']) + pd.Timedelta(minutes=585)
        data[DailyVolEma] = data['volume'].rolling(90, min_periods=1).mean()

        max_ = self.jsons[DailyVolEma]
        data['DailyVolEmaParser'] = max_ / data[DailyVolEma]
        data = data[data['date'] > (self.record_last_15m_time + pd.Timedelta(days=-1))]
        data = data[['date', 'DailyVolEmaParser']].set_index('date', drop=True)
        return data

    def first_15m(self) -> pd.DataFrame:
        """
        计算 15 分钟数据（第一阶段）

        Returns:
            pd.DataFrame: 处理后的 15 分钟数据
        """
        path = StockDataPath.json_data_path(self.month_parsers, self.stock_code)
        self.jsons = ReadSaveFile.read_json_by_path(path)

        data_daily = self.daily_data()

        self.data_15m = self.data_1m[
            self.data_1m['date'] > pd.to_datetime(self.record_last_15m_time)
        ].reset_index(drop=True)

        self.data_15m = ResampleData.resample_1m_data(data=self.data_15m, freq='15m')
        self.data_15m = SignalMethod.signal_by_MACD_3ema(data=self.data_15m, data1m=self.data_1m)

        t15m = self.data_15m.drop_duplicates(subset=[SignalTimes]).tail(6).iloc[0]['date'].date()

        sql = ''' Time15m = %s where id = %s;'''
        parser = (t15m, self.stock_id)
        LoadRnnModel.set_table_run_record(sql, parser)

        self.data_15m = self.data_15m.set_index('date', drop=True)
        self.data_15m = self.data_15m.join([data_daily]).reset_index()

        fills = ['DailyVolEmaParser']
        self.data_15m[fills] = self.data_15m[fills].ffill()

        lastST = list(self.data_15m.drop_duplicates(subset=[SignalTimes]).tail(6)[SignalTimes])
        self.data_15m = self.data_15m[self.data_15m[SignalTimes].isin(lastST)]

        # 计算 Bar1mVolumeMax
        self.data_15m.loc[:, Bar1mVolMax1] = self.data_15m['date'].apply(
            self.Bar1mVolumeMax, args=(1,)
        )
        self.data_15m.loc[:, Bar1mVolMax5] = self.data_15m['date'].apply(
            self.Bar1mVolumeMax, args=(5,)
        )

        # 保存 15m 新数据
        self.update_15m()

        # 运行趋势辨别模块
        distinguish = TrendDistinguishModel()
        self.trendLabel, self.trendValue = distinguish.distinguish_freq(
            self.stock_code, self.data_15m
        )

        return self.data_15m

    def second_15m(self) -> pd.DataFrame:
        """
        15 分钟数据标准化（第二阶段）

        Returns:
            pd.DataFrame: 标准化后的 15 分钟数据
        """
        from App.codes.parsers.RnnParser import (
            Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15,
            Cycle1mVolMax1, Cycle1mVolMax5, CycleAmplitudeMax, CycleLengthMax,
            CycleLengthPerBar, CycleAmplitudePerBar,
            preCycle1mVolMax1, preCycle1mVolMax5, preCycleAmplitudeMax, preCycleLengthMax,
            nextCycleLengthMax, nextCycleAmplitudeMax, SignalChoice
        )

        li = [
            'volume', Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15, 'EndDaily1mVolMax5',
            Cycle1mVolMax1, Cycle1mVolMax5, Bar1mVolMax1, Bar1mVolMax5
        ]

        for c in li:
            self.data_15m[c] = round(self.data_15m[c] * self.data_15m['DailyVolEmaParser'])

        pre_dic = {
            preCycle1mVolMax1: Cycle1mVolMax1,
            preCycle1mVolMax5: Cycle1mVolMax5,
            preCycleAmplitudeMax: CycleAmplitudeMax,
            preCycleLengthMax: CycleLengthMax
        }

        condition = (~self.data_15m[SignalChoice].isnull())
        for key, value in pre_dic.items():
            self.data_15m.loc[condition, key] = self.data_15m.loc[condition, value].shift(1)

        next_dic = {
            nextCycleLengthMax: CycleLengthMax,
            nextCycleAmplitudeMax: CycleAmplitudeMax
        }
        for key, value in next_dic.items():
            self.data_15m.loc[condition, key] = self.data_15m.loc[condition, value].shift(-1)

        fills = list(pre_dic.keys()) + list(next_dic.keys())
        self.data_15m[fills] = self.data_15m[fills].ffill()
        self.data_15m[Signal] = self.data_15m[Signal].astype(float)

        dic = {
            'volume': 'volume',
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
            nextCycleAmplitudeMax: nextCycleAmplitudeMax
        }

        for key, value in dic.items():
            self.column2normal(key, value)

        self.data_15m['ReTrends'] = self.data_15m['close'] - self.data_15m['EmaMid']
        self.data_15m = self.data_15m.replace([np.inf, -np.inf], np.nan)

        return self.data_15m

    def calculate_check_data(self) -> pd.DataFrame:
        """
        计算检查数据

        Returns:
            pd.DataFrame: 检查日期之后的数据
        """
        # 获取 1m 数据
        self.data_1m = self.monitor_read_1m() if self.monitor else self.check_date_read_1m()

        self.first_15m()
        self.second_15m()

        data_ = self.data_15m[self.data_15m['date'] > self.check_date]

        return data_
