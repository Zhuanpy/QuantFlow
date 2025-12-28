# from code.MySql.LoadMysql import LoadRnnModel


"""
处理训练数据： 
一： 处理 1分钟数据
1. 考虑第一次训练， 日后继续训练; 这里得考虑数据第一次采用，日后怎么避免重复和漏用； 
2. 保存1分钟开始 结束参数， 保存 15分钟信号名；
3. 保存1分钟开始 结束参数， 保存 15分钟信号名；

二： 处理 15分钟数据

3. 保存15分钟数据参数，特别是极大 极小，中位数等 ；

三： 处理模型数据
4. 模型数据保存 及 记录； 从什么日期及信号名开始保存 ； 从什么日期 及 信号名开始结束保存

一个思路，读取数据，处理数据，保存数据；

"""
import pandas as pd
import numpy as np
from ..MySql.sql_utils import Stocks
from ..MySql.DataBaseStockData1m import StockData1m
from ..MySql.DataBaseStockDataDaily import StockDataDaily
from ..RnnDataFile.JsonData import MyJsonData
from ..parsers.RnnParser import *
from App.codes.utils.Normal import ReadSaveFile, ResampleData
from ..Signals.StatisticsMacd import SignalMethod
from datetime import datetime, timedelta


def get_previous_month(current_month):
    # 将输入的月份字符串解析为datetime对象
    current_date = datetime.strptime(current_month, "%Y-%m")

    # 计算前一个月的日期
    previous_month = current_date - timedelta(days=current_date.day)

    # 将结果格式化为字符串并返回
    pre_month = previous_month.strftime("%Y-%m")
    return pre_month


class TrainingDataCalculate:
    """
    训练数据处理
    """

    def __init__(self, stock: str, month: str, ):  # _month
        self.stock_start_year = '2023-01-01'
        self.stock_name, self.stock_code, self.stock_id = Stocks(stock)

        self.month = month
        self.pre_month = get_previous_month(self.month)  # 前一个月数据

        self.load_year = '2018-01'
        self.data_1m = None
        self.data_15m = None
        self.times_data = None

        self.record_json_data = None
        self.RecordStartDate = None
        self.RecordEndDate = None

        self.freq = '15m'
        # self.start_date_1m = None
        self.data1m_start_date = None
        self.data1m_end_date = None

        self.daily_volume_max = None

        self.x_columns = XColumn()
        self.y_column = YColumn()
        self.model_name = ModelName

    def stand_save_parser(self, data: pd.DataFrame, column, drop_duplicates, drop_column):

        """
        标准化并保存指定列的数据。

        参数:
            code_data (pd.DataFrame): 输入的DataFrame。
            column (str): 要标准化的列名。
            drop_duplicates (bool): 是否删除重复项的标志。
            drop_column (str): 如果`drop_duplicates`为True，指定要删除的列名。

        返回:
            pd.DataFrame: 经过标准化并保存数据的DataFrame。
        """

        # 判断是否需要删除重复项
        if drop_duplicates:

            # 根据drop_column的值处理不同情况
            if drop_column == SignalChoice:
                df = data.dropna(subset=[SignalChoice])

            else:
                df = data.drop_duplicates(subset=[column])

            # 计算中位数和绝对中位差（MAD）
            med = df[column].median()
            mad = abs(df[column] - med).median()

        else:
            # 当不需要删除重复项时，仅计算中位数和MAD
            med = data[column].median()
            mad = abs(data[column] - med).median()

        # 计算上下限值，用于去极值
        high = round(med + (3 * 1.4826 * mad), 2)
        low = round(med - (3 * 1.4826 * mad), 2)

        # 尝试读取历史参数的JSON数据
        try:
            parser_data, pre_month = MyJsonData.find_previous_month_json_parser(self.pre_month, self.stock_code)

            # 提取历史最大最小值
            pre_high = parser_data['TrainingData']['dataDaily'][column]['num_max']
            pre_low = parser_data['TrainingData']['dataDaily'][column]['num_min']

        except ValueError:
            # 如果读取失败，则使用当前计算的high和low值
            pre_high = high
            pre_low = low

        # 将新数据与历史数据进行对比，取最大和最小值
        high = max([high, pre_high])
        low = min([low, pre_low])

        # 去极值处理，确保数据在合理范围内
        data.loc[(data[column] > high), column] = high
        data.loc[(data[column] < low), column] = low

        # 数据归一化处理
        data[column] = (data[column] - low) / (high - low)

        # 读取当前月的参数JSON数据
        current_parser_data = ReadSaveFile.read_json(self.month, self.stock_code)
        # 更新当前列的最大最小值参数
        current_parser_data['TrainingData']['dataDaily'][column] = {'num_max': high, 'num_min': low}

        # 保存更新后的JSON数据, 更新参数
        ReadSaveFile.save_json(current_parser_data, self.month, self.stock_code)

        return data

    def stand_read_parser(self, data: pd.DataFrame, column: str) -> pd.DataFrame:
        """
        读取和应用标准化参数，对指定列的数据进行标准化处理。

        参数:
            code_data (pd.DataFrame): 输入的DataFrame。
            column (str): 要标准化的列名。

        返回:
            pd.DataFrame: 经过标准化处理的DataFrame。
        """

        # 读取当前月的参数JSON数据
        parser_data = ReadSaveFile.read_json(self.month, self.stock_code)

        # 获取指定列的训练数据参数
        column_data = parser_data['TrainingData']["dataDaily"][column]
        num_max = column_data['num_max']
        num_min = column_data['num_min']

        # 使用clip函数将数据限制在最大值和最小值之间
        data[column] = data[column].clip(num_min, num_max)

        # 对数据进行归一化处理
        data[column] = (data[column] - num_min) / (num_max - num_min)

        return data

    def column_stand(self, data_15m: pd.DataFrame) -> pd.DataFrame:
        """
        标准化处理指定数据列，并根据需要保存和读取标准化参数。

        参数:
            data_15m (pd.DataFrame): 输入的15分钟频率的数据DataFrame。

        返回:
            pd.DataFrame: 经过处理和标准化的DataFrame。
        """

        # 检查是否已经计算了daily_volume_max
        if not self.daily_volume_max:

            # 加载1分钟频率的数据，并进行日期过滤
            _date = '2018-01-01'
            self.data_1m = StockData1m.load_1m(self.stock_code, _date)
            self.data_1m = self.data_1m[self.data_1m['date'] > pd.to_datetime(_date)]

            # 将1分钟数据重采样为每日数据
            data_daily = ResampleData.resample_1m_data(data=self.data_1m, freq='daily')
            data_daily['date'] = pd.to_datetime(data_daily['date']) + pd.Timedelta(minutes=585)
            data_daily[DailyVolEma] = data_daily['volume'].rolling(90, min_periods=1).mean()

            # 计算daily_volume_max
            self.daily_volume_max = round(data_daily[DailyVolEma].max(), 2)

        # 读取当前月的参数JSON数据并更新daily_volume_max
        parser_data = ReadSaveFile.read_json(self.month, self.stock_code)
        parser_data[DailyVolEma] = self.daily_volume_max
        ReadSaveFile.save_json(parser_data, self.month, self.stock_code)

        # 定义需要保存的列及其相关参数
        save_list = [('volume', False, None),
                     (Daily1mVolMax1, True, Daily1mVolMax1),
                     (Daily1mVolMax5, True, Daily1mVolMax5),
                     (Daily1mVolMax15, True, Daily1mVolMax15),
                     (Bar1mVolMax1, False, None),
                     (Cycle1mVolMax1, True, SignalChoice),
                     (Cycle1mVolMax5, True, SignalChoice),
                     (Bar1mVolMax5, False, None),
                     (CycleLengthMax, True, SignalChoice),
                     (nextCycleLengthMax, True, SignalChoice),
                     (CycleLengthPerBar, False, None),
                     (CycleAmplitudeMax, True, SignalChoice),
                     (nextCycleAmplitudeMax, True, SignalChoice),
                     (CycleAmplitudePerBar, False, None),
                     ('EndDaily1mVolMax5', True, SignalChoice)]

        # 对每列数据进行标准化处理并保存参数
        for i in save_list:
            column = i[0]
            drop_duplicates = i[1]
            drop_column = i[2]
            # todo 验证保存参数的正确性
            data_15m = self.stand_save_parser(data_15m, column, drop_duplicates, drop_column)

        # 定义需要读取的列及其历史参数
        read_dict = {preCycle1mVolMax1: Cycle1mVolMax1,
                     preCycle1mVolMax5: Cycle1mVolMax5,
                     preCycleLengthMax: CycleLengthMax,
                     preCycleAmplitudeMax: CycleAmplitudeMax}

        # 对每列数据进行标准化处理并读取历史参数
        for key, value in read_dict.items():
            # todo 验证保存参数的正确性
            data_15m = self.stand_read_parser(data_15m, value)

        # 删除指定信号的缺失值
        data_15m = data_15m.dropna(subset=[Signal])

        # 获取最新信号的时间，并去除具有相同信号时间的数据
        last_signal_times = data_15m.iloc[-1][SignalTimes]
        data_15m = data_15m[data_15m[SignalTimes] != last_signal_times]

        # 选择需要的模型数据列
        all_columns = ['date', Signal, SignalTimes, SignalChoice,
                       StartPriceIndex, EndPriceIndex, CycleAmplitudePerBar, CycleAmplitudeMax,
                       Cycle1mVolMax1, Cycle1mVolMax5,
                       CycleLengthMax, CycleLengthPerBar,
                       Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15,
                       preCycle1mVolMax1, preCycleLengthMax, Bar1mVolMax1,
                       nextCycleLengthMax, preCycle1mVolMax5,
                       'volume', Bar1mVolMax5, preCycleAmplitudeMax,
                       'EndDaily1mVolMax5', nextCycleAmplitudeMax]

        # 选择指定的列
        data_15m = data_15m[all_columns]

        return data_15m

    def find_bar_max_1m(self, x, num):

        try:
            start_time = pd.to_datetime(x) + pd.Timedelta(minutes=-15)

            end_time = pd.to_datetime(x)

            max_vol = self.data_1m[(self.data_1m['date'] > start_time) & (self.data_1m['date'] < end_time)]
            max_vol = max_vol.sort_values(by=['volume'])['volume'].tail(num).mean()

            if pd.notna(max_vol):  # 如果 max_vol 不是 NaN
                max_vol = int(max_vol)  # volume 1m is 'nan'

            else:
                max_vol = None

        except Exception as ex:
            # 处理其他异常，并打印错误信息
            max_vol = None
            print(f'{self.stock_name} 函数： find_bar_max_1m 错误;\n{ex}')

        return max_vol

    def data_daily(self, start_date):
        start_date = (pd.to_datetime(start_date) - pd.DateOffset(years=1)).date()  # 导入的年份减去1年，度一个360天的数据

        data_daily = StockDataDaily.load_daily_data(self.stock_code)
        columns = ['date', 'volume']
        data_daily = data_daily[columns]
        data_daily['date '] = pd.to_datetime(data_daily['date'])

        data_daily = data_daily[data_daily['date'] >= start_date]

        data_daily[DailyVolEma] = data_daily['volume'].rolling(90, min_periods=1).mean()

        daily_volume_max = round(data_daily['volume'].max(), 2)
        daily_volume_min = round(data_daily['volume'].min(), 2)

        daily_volume_ema_max = round(data_daily[DailyVolEma].max(), 2)
        daily_volume_ema_min = round(data_daily[DailyVolEma].min(), 2)

        try:
            """ 读取历史的参数json数据 """
            pre_json, pre_month = MyJsonData.find_previous_month_json_parser(self.month, self.stock_code)

            # { "volume": {"num_max": 1, "num_min": 0.0}, }
            pre_daily_volume_max = pre_json["TrainingData"]["dataDaily"]["volume"]["num_max"]
            pre_daily_volume_min = pre_json["TrainingData"]["dataDaily"]["volume"]["num_min"]

            # {"DailyVolEma": 1,}
            pre_daily_volume_ema_max = pre_json["TrainingData"]["dataDaily"][DailyVolEma]["num_max"]
            pre_daily_volume_ema_min = pre_json["TrainingData"]["dataDaily"][DailyVolEma]["num_min"]

        except ValueError:
            pre_daily_volume_max = daily_volume_max
            pre_daily_volume_min = daily_volume_min

            pre_daily_volume_ema_max = daily_volume_ema_max
            pre_daily_volume_ema_min = daily_volume_ema_min

        # 使用 max 函数一行完成最大值的更新
        daily_volume_max = max(daily_volume_max, pre_daily_volume_max)
        daily_volume_min = max(daily_volume_min, pre_daily_volume_min)
        daily_vol_ema_max = max(daily_volume_ema_max, pre_daily_volume_ema_max)
        daily_vol_ema_min = max(daily_volume_ema_min, pre_daily_volume_ema_min)

        # 简化日线数据处理
        data_daily[DailyVolEmaParser] = daily_volume_max / data_daily[DailyVolEma]
        data_daily['date'] = pd.to_datetime(data_daily['date']) + pd.Timedelta(minutes=585)
        data_daily = data_daily[['date', DailyVolEmaParser]]  # .set_index('date', drop=True)

        # 保存数据
        daily_volume_dic = {"num_max": str(daily_volume_max), "num_min": str(daily_volume_min)}
        daily_vol_ema_dic = {"num_max": str(daily_vol_ema_max), "num_min": str(daily_vol_ema_min)}
        new_parser = {"TrainingData": {'dataDaily': {'volume': daily_volume_dic, DailyVolEma: daily_vol_ema_dic}}}

        current_json = MyJsonData.loadJsonData(self.month, self.stock_code)
        current_json = MyJsonData.modify_nested_dict(current_json, new_parser)
        MyJsonData.save_json(current_json, self.month, self.stock_code)

        return data_daily

    def data_15m_first_calculate(self, data_1m, data_daily):

        """ 读取历史的参数json数据 """
        try:
            pre_json, pre_month = MyJsonData.find_previous_month_json_parser(self.month, self.stock_code)
            pre_EndSignal_SignalName = pre_json["TrainingData"]["EndSignal"]["SignalName"]

        except ValueError:
            pre_EndSignal_SignalName = None

        data_15m = ResampleData.resample_1m_data(data=data_1m, freq=self.freq)

        data_15m = SignalMethod.signal_by_MACD_3ema(data_15m, data_1m).set_index('date', drop=True)

        # 删除最后一次 signal Times , 可能此周期并未走完整
        last_signal_times = data_15m.iloc[-1][SignalTimes]
        data_15m = data_15m[data_15m[SignalTimes] != last_signal_times]

        # 清理已经历史已经 计算保存的数据， 通过json参数记录获取
        if pre_EndSignal_SignalName:
            data_15m = data_15m[data_15m['SignalTimes'] > pre_EndSignal_SignalName]

        data_15m = data_15m.dropna(subset=[SignalTimes])

        # 获取新数据的 开始信号 和 结束信号信息
        current_StartSignal_SignalName = data_15m.iloc[0]['SignalTimes']
        current_StartSignal_StartTime = data_15m.iloc[0]['SignalStartTime']

        current_EndSignal_SignalName = data_15m.iloc[-1]['SignalTimes']
        current_EndSignal_StartTime = data_15m.iloc[-1]['SignalStartTime']

        # 整理 data_daily 数据， 大于 current_StartSignal_StartTime & 小于 current_EndSignal_StartTime
        data_daily = data_daily[(data_daily['date'] > current_StartSignal_StartTime) &
                                (data_daily['date'] < current_EndSignal_StartTime)]

        # 合并 data_daily 数据：
        data_daily = data_daily.set_index('date', drop=True)
        data_15m = data_15m.join([data_daily]).reset_index()
        data_15m[DailyVolEmaParser] = data_15m[DailyVolEmaParser].ffill()  #

        # 保存新的 json 数据
        current_StartSignal_dic = {"SignalName": current_StartSignal_SignalName,
                                   "StartTime": str(current_StartSignal_StartTime)}

        current_EndSignal_dic = {"SignalName": current_EndSignal_SignalName,
                                 "StartTime": str(current_EndSignal_StartTime)}

        new_parser = {"TrainingData": {"StartSignal": current_StartSignal_dic,
                                       "EndSignal": current_EndSignal_dic}}

        current_json = MyJsonData.loadJsonData(self.month, self.stock_code)
        current_json = MyJsonData.modify_nested_dict(current_json, new_parser)
        MyJsonData.save_json(current_json, self.month, self.stock_code)

        return data_15m

    def data_15m_second_calculate(self, data_15m):

        """ 找到 bar 1m 最大值； """

        for _, row in data_15m.dropna(subset=[SignalChoice, EndPriceIndex]).iterrows():
            signal_times = row[SignalTimes]
            end_price_time = row[EndPriceIndex]

            selects = data_15m[(data_15m[SignalTimes] == signal_times) &
                               (data_15m[EndPriceIndex] <= end_price_time)].tail(35)

            # 优化索引获取
            start_index, end_index = selects.index[0], selects.index[-1]

            data_15m.loc[start_index:end_index, Bar1mVolMax1] = data_15m.loc[start_index:end_index]['date'].apply(
                self.find_bar_max_1m, args=(1,))

            data_15m.loc[start_index:end_index, Bar1mVolMax5] = data_15m.loc[start_index:end_index]['date'].apply(
                self.find_bar_max_1m, args=(5,))

        # 替换无穷大和无穷小的值为 NaN
        data_15m = data_15m.replace([np.inf, -np.inf], np.nan)

        return data_15m

    def data_15m_third_calculate(self, data_15m):

        """ 第三次处理， 提取前周期信息 """
        data_15m[Signal] = data_15m[Signal].astype(float)

        # 成交量相关参数的处理
        vol_parser = ['volume', Cycle1mVolMax1, Cycle1mVolMax5,
                      Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15,
                      Bar1mVolMax1, Bar1mVolMax5, 'EndDaily1mVolMax5']  # 成交量乘以参数，相似化

        for i in vol_parser:
            data_15m[i] = round(data_15m[i] * data_15m[DailyVolEmaParser])

        next_dic = {nextCycleAmplitudeMax: CycleAmplitudeMax,
                    nextCycleLengthMax: CycleLengthMax}

        condition = (~data_15m[SignalChoice].isnull())
        for keys, values in next_dic.items():
            data_15m.loc[condition, keys] = data_15m.loc[condition, values].shift(-1)

        # 提取前周期相关数据：
        condition = (~data_15m[SignalChoice].isnull())

        pre_dic = {preCycle1mVolMax1: Cycle1mVolMax1,
                   preCycle1mVolMax5: Cycle1mVolMax5,
                   preCycleAmplitudeMax: CycleAmplitudeMax,
                   preCycleLengthMax: CycleLengthMax}

        for key, values in pre_dic.items():
            data_15m.loc[condition, key] = data_15m.loc[condition, values].shift(1)

        fills = list(pre_dic.keys()) + list(next_dic.keys())

        data_15m[fills] = data_15m[fills].ffill()  # fillna(method='ffill')

        return data_15m

    def data_1m_calculate(self, ):

        """ 根据历史训练数据，导入1M 数据"""
        try:
            json_data, parser_month = MyJsonData.find_previous_month_json_parser(self.month, self.stock_code)
            start_date_1m = json_data['TrainingData']['EndSignal']['StartTime']  # 提取上次训练数据，截止日期
            start_date_1m = pd.to_datetime(start_date_1m) + pd.Timedelta(days=-120)

            start_date_1m = start_date_1m.normalize()

            if start_date_1m < pd.to_datetime(self.stock_start_year).normalize():
                start_date_1m = pd.to_datetime(self.stock_start_year).normalize()

        except ValueError:
            start_date_1m = pd.to_datetime(self.stock_start_year).normalize()

        # 导入1m数据
        self.load_year = str(start_date_1m.year)

        data_1m = StockData1m.load_1m(self.stock_code, self.load_year)

        # 筛选出需要的日期
        end_data_1m = pd.to_datetime(self.month) + pd.Timedelta(days=-30)

        data_1m = data_1m.sort_values(by=['date'])
        data_1m = data_1m[(data_1m['date'] > start_date_1m) & (data_1m['date'] < end_data_1m)]

        # 去除重复值和缺失值
        data_1m = data_1m.dropna(subset=['date']).drop_duplicates(subset=['date']).reset_index(drop=True)

        """ 提取 & 保存 data1m数据 起始日期 """
        # "data1m": {"startDate": "2023-01-11", "EndDate": "2023-01-11"},
        self.data1m_start_date = data_1m.iloc[0]['date'].strftime('%Y-%m-%d %H:%M:%S')
        self.data1m_end_date = data_1m.iloc[-1]['date'].strftime('%Y-%m-%d %H:%M:%S')

        # 保存 code_data 1m 数据, Update JSON code_data
        json_parser = MyJsonData.loadJsonData(self.month, self.stock_code)
        data1m_dic = {"startDate": self.data1m_start_date,
                      "EndDate": self.data1m_end_date}

        json_parser["TrainingData"]["data1m"] = data1m_dic
        MyJsonData.save_json(json_parser, self.month, self.stock_code)

        return data_1m

    # def save_15m_data(self):
    #
    #     # if self.RecordStartDate:
    #     #
    #     #     # 筛选数据并加载旧数据
    #     #     self.data_15m = self.data_15m[self.data_15m['date'] > self.RecordEndDate]
    #     #
    #     #     from sqlalchemy.exc import IntegrityError
    #     #     try:
    #     #         StockData15m.append_15m(self.stock_code, self.data_15m)
    #     #
    #     #     except IntegrityError:
    #     #         old = StockData15m.load_15m(self.stock_code)
    #     #         last_date = old.iloc[-1]['date']
    #     #         new = self.data_15m[self.data_15m['date'] > last_date]
    #     #         old = pd.concat([old, new], ignore_index=True)
    #     #         StockData15m.replace_15m(self.stock_code, old)
    #     #
    #     # else:
    #     #     StockData15m.replace_15m(self.stock_code, self.data_15m)
    #
    #     # 保存15m数据截止日期相关信息
    #
    #     record_end_signal = self.data_15m.iloc[-1]['Signal']
    #     record_end_date = self.data_15m.iloc[-1]['date'].strftime('%Y-%m-%d %H:%M:%S')
    #
    #     record_end_signal_times = self.data_15m.iloc[-1]['SignalTimes']
    #     record_end_signal_start_time = self.data_15m.iloc[-1]['SignalStartTime'].strftime('%Y-%m-%d %H:%M:%S')
    #     record_next_start = self.data_15m.drop_duplicates(subset=[SignalTimes]).tail(6).iloc[0]['date'].strftime(
    #         '%Y-%m-%d %H:%M:%S')
    #
    #     # 读取旧参数并更新
    #     records = ReadSaveFile.read_json(self.month, self.stock_code)
    #
    #     records.update({
    #         'RecordEndDate': record_end_date,
    #         'RecordEndSignal': record_end_signal,
    #         'RecordEndSignalTimes': record_end_signal_times,
    #         'RecordEndSignalStartTime': record_end_signal_start_time,
    #         'RecordNextStartDate': record_next_start})
    #
    #     ReadSaveFile.save_json(records, self.month, self.stock_code)  # 更新参数

    def data_calculate(self):

        self.data_1m = self.data_1m_calculate()  # 导入 1m 数据 及 整理 1m 数据， 跟新当月参数json数据；

        daily_parser = self.data_daily(self.load_year)

        self.data_15m = self.data_15m_first_calculate(self.data_1m, daily_parser)

        self.data_15m = self.data_15m_second_calculate(self.data_15m)

        # 统计保存计算的15m数据
        # todo 需要再次验证此方法的正确性
        # self.save_15m_data()

        self.data_15m = self.data_15m_third_calculate(self.data_15m)

        self.data_15m = self.column_stand(self.data_15m)  # 标准化数据

        return self.data_15m


# class TrainingDataProcess(TrainingDataCalculate):
#
#     def calculation_single(self):
#
#         try:
#             path = find_file_in_paths(self.month, 'json', f'{self.stock_code}.json')  # 返回 json 路径
#             record = ReadSaveFile.read_json_by_path(path)
#             self.RecordEndDate = record[self.stock_code]['RecordEndDate']
#             self.RecordStartDate = record[self.stock_code]['NextStartDate']
#
#         except ValueError:
#             pass
#
#         self.data_15m = self.data_calculate()
#
#         for i in range(4):
#             x = self.x_columns[i]
#             y = self.y_column[i]
#             model_name = self.model_name[i]
#             self.data_common(model_name, x, y)
#
#     def calculation_read_from_sql(self):
#
#         self.data_15m = StockData15m.load_15m(self.stock_code)
#
#         self.data_15m = self.third_calculate()
#         self.data_15m = self.column_stand()  # 标准化数据
#
#         for i in range(4):
#             x = self.x_columns[i]
#             y = self.y_column[i]
#             model_name = self.model_name[i]
#             self.data_common(model_name, x, y)


# class RMTrainingData:
#
#     def __init__(self, month: str):  # _month
#         self.month = month
#
#     def single_stock(self, stock: str):
#         calculation = TrainingDataCalculate(stock, self.month)
#         calculation.calculation_single()
#
#     def update_train_records(self, records):
#         """更新训练表格记录"""
#         ids = tuple(records.id)
#
#         sql = f'''ParserMonth = %s, ModelData = 'pending' where id in %s;'''
#
#         params = (self.month, ids)
#         LoadRnnModel.set_table_train_record(sql, params)
#
#     def all_stock(self):
#
#         load = LoadRnnModel.load_train_record()
#
#         records = load[load['ParserMonth'] == self.month]
#
#         # 更新训练记录中的状态
#         if records.empty:
#             records = load.copy()
#             records['ParserMonth'] = self.month
#             records['ModelData'] = 'pending'
#             records['ModelCheck'] = 'pending'
#             records['ModelError'] = 'pending'
#
#             self.update_train_records(records)
#
#         # 查看等待数据
#         records = records[~records['ModelData'].isin(['success'])].reset_index(drop=True)
#
#         if records.empty:
#             print(f'{self.month}月训练数据创建完成')
#             return False  # 结束运行，因为 records 为空
#
#         for i, row in records.iterrows():
#             stock_ = row['name']
#             id_ = row['id']
#
#             print(f'\n计算进度：'
#                   f'\n剩余股票: {(records.shape[0] - i)} 个; 总股票数: {records.shape[0]}个;'
#                   f'\n当前股票：{stock_};')
#
#             try:
#                 run = TrainingDataCalculate(stock_, self.month)
#                 run.calculation_read_from_sql()
#
#                 sql = f'''ModelData = 'success', ModelDataTiming = %s where id = %s; '''
#
#                 params = (pd.Timestamp('now').date(), id_)
#                 LoadRnnModel.set_table_train_record(sql, params)
#
#             except Exception as ex:
#                 print(f'Model Data Create Error: {ex}')
#                 sql = f'''ModelData = 'error', ModelDataTiming = NULL where id = %s; '''
#                 params = (LoadRnnModel.db_rnn, LoadRnnModel.tb_train_record, id_)
#                 LoadRnnModel.set_table_train_record(sql, params)
#
#         return True


if __name__ == '__main__':
    month_ = '2023-10'
    stock_name = '000001'
    running = TrainingDataCalculate(stock_name, month_)
    D = running.data_calculate()
    print(D)
    # code_data = running.data_calculate()
