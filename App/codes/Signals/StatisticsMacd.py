# -*- coding: utf-8 -*-
import pandas as pd
from .MacdSignal import calculate_MACD

from .BollingerSignal import Bollinger
from App.codes.parsers.MacdParser import *


class MacdSignalPipeline:

    @classmethod
    def remark_MACD(cls, data):
        """
        用于根据MACD指标标注数据的上涨和下跌信号。

        参数:
        - code_data (pd.DataFrame): 包含MACD指标的原始数据表。
        - macd_ (str): MACD指标所在列的名称。
        - Signal (str): 用于标注信号的列名。
        - downInt (int): 下跌信号的标识值。
        - upInt (int): 上涨信号的标识值。

        返回:
        - pd.DataFrame: 包含标注后的数据表。

        逻辑:
        - 当当前行的MACD值大于0，而下一行的MACD值小于0时，标注为'下跌'信号。
        - 当当前行的MACD值小于0，而下一行的MACD值大于0时，标注为'上涨'信号。
        """
        # 标注'下跌'信号
        data.loc[(data[macd_] > 0) & (data[macd_].shift(-1) < 0), Signal] = downInt

        # 标注'上涨'信号
        data.loc[(data[macd_] < 0) & (data[macd_].shift(-1) > 0), Signal] = upInt

        return data

    # 找出 MACD 次数
    @classmethod
    def mark_macd_signal_segment_id(cls, data):
        """
        为每个MACD信号区间标注唯一ID（SignalId，基于首次发生时间），用于分段/分组统计。

        参数:
        - code_data (pd.DataFrame): 包含MACD信号数据的原始数据表。
        - Signal (str): 用于标记信号的列名。
        - downInt (int): 下跌信号的标识值。
        - upInt (int): 上涨信号的标识值。
        - SignalChoice (str): 标记信号选择的列名。
        - SignalTimes (str): 信号区间ID列名（配置中映射到 SignalId）。

        返回:
        - pd.DataFrame: 包含处理后的数据表。
        """
        # 标记上涨和下跌信号选择
        data.loc[data[Signal] == downInt, SignalChoice] = down
        data.loc[data[Signal] == upInt, SignalChoice] = up

        # 记录信号区间ID（以首次出现时间作为区分键）
        data.loc[data[Signal].notnull(), SignalId] = data.loc[data[Signal].notnull(), 'date'].dt.strftime("%Y%m%d%H%M")

        data[SignalId] = data[SignalId].ffill()

        return data

    @classmethod
    def filter_invalid_macd_signals(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        处理 MACD 信号的有效性，过滤不符合条件的信号，并返回处理后的数据。

        参数:
        - cls: 类对象（未使用），通常用于类方法的第一个参数
        - code_data: pd.DataFrame, 包含原始数据的 DataFrame，其中至少应包含 Signal, SignalId, DifMl, DifSm 等列

        返回:
        - pd.DataFrame, 处理后的数据，其中无效的信号已被标记为 None

        功能:
        1. 去除 Signal 列中为空的数据行。
        2. 根据上涨和下跌信号，计算 DifMl 和 DifSm 列中的数据是否符合条件。
        3. 删除满足特定条件的信号（如不满足条件的信号和连续相同的信号）。
        4. 重置 SignalTimes 和 SignalChoice 列，以便进一步统计。
        """

        # 去除 Signal 列中为空的数据行，得到一个新的 DataFrame df
        df = data.dropna(subset=[Signal])

        # 如果 df 为空，则返回原始数据（或进行其他处理）
        if df.empty:
            return data

        for index in df.index:
            signal_times = data.loc[index, SignalId]
            signal = data.loc[index, Signal]
            condition = data[SignalId] == signal_times

            diffs = data[condition & (data[DifMl] > 0) & (data[DifSm] > 0)].shape[0] if signal == upInt else \
                    data[condition & (data[DifMl] < 0) & (data[DifSm] < 0)].shape[0]

            if diffs < 7:
                data.loc[index, Signal] = None


        # 删除连续相同的信号
        drop = data.dropna(subset=[Signal])

        if not drop.empty:

            for i, index in zip(range(drop.shape[0]), drop.index):

                try:
                    if drop.iloc[i][Signal] == drop.iloc[i - 1][Signal]:
                        data.loc[index, Signal] = None

                except ValueError:
                    pass

        # 重置统计列
        data[SignalId] = None  # 重置区间ID，准备再次标注
        data[SignalChoice] = None  # 第2次统计出 涨跌次数

        return data

    @classmethod
    def compute_macd_signals_pipeline(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        统计出 MACD 信号，并处理无效信号。

        参数:
        - cls: 类对象，用于调用类内的其他方法
        - code_data: pd.DataFrame, 包含原始数据的 DataFrame

        返回:
        - pd.DataFrame, 处理后的数据，其中 MACD 信号和相关统计已完成
        """

        # 计算 MACD 值
        data = calculate_MACD(data)

        # 标记 MACD 信号
        data = cls.remark_MACD(data)

        # 标注信号区间ID
        data = cls.mark_macd_signal_segment_id(data)

        # 删除无效振幅波段
        data = cls.filter_invalid_macd_signals(data)

        # 再次标注信号区间ID，确保一致性
        data = cls.mark_macd_signal_segment_id(data)

        # 标记信号的起始位置
        con1 = data[SignalChoice].isin([up, down])
        data.loc[con1, SignalStartIndex] = data[con1]['date']

        # 前向填充 Signal 和 SignalStartIndex 列
        fills = [Signal, SignalStartIndex]
        data[fills] = data[fills].ffill()

        # 返回处理后的数据
        return data


class StatisticsMACD:

    @classmethod
    def compute_macd_signal_segment_endpoints(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        计算每个信号选择的开始价格和结束价格及其索引。

        参数:
        - code_data: pd.DataFrame, 包含股票价格数据的 DataFrame，需要有 'date'、'SignalChoice'、'SignalId' 等列

        返回:
        - pd.DataFrame, 添加了开始价格、结束价格及其索引的数据
        """

        # 设置 'date' 列为索引
        data = data.set_index('date', drop=True)

        # 删除 'SignalChoice' 列中含有缺失值的行
        drops = data.dropna(subset=[SignalChoice])

        if drops.empty:
            # 如果没有有效数据，直接返回
            data = data.reset_index()
            return data

        # 计算结束价格和结束价格索引
        for i, index in zip(range(drops.shape[0]), drops.index):

            if i > 0:
                choice_ = data.loc[index, SignalChoice]
                times_ = data.loc[index, SignalId]  # SignalId（区间ID）

                if choice_ == down:
                    end_price = data[data[SignalId] == times_]['low'].min()
                    end_price_id = data[data[SignalId] == times_]['low'].idxmin()

                else:
                    end_price = data[data[SignalId] == times_]['high'].max()
                    end_price_id = data[data[SignalId] == times_]['high'].idxmax()

                # 设置结束价格和结束价格索引
                data.loc[index, EndPrice] = end_price  # '结束价'
                data.loc[index, EndPriceIndex] = end_price_id  # '结束价_index'

        # find start price & start price id;
        con1 = (~data[SignalChoice].isnull())
        data.loc[con1, StartPrice] = data[con1][EndPrice].shift(1)
        data.loc[con1, StartPriceIndex] = data[con1][EndPriceIndex].shift(1)

        # fill nan
        fills = [EndPrice, EndPriceIndex, StartPrice, StartPriceIndex]
        data[fills] = data[fills].fillna(method='ffill')

        # reset 'date' to column
        data = data.reset_index()

        return data

    @classmethod
    def s_StartEndIndex(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
         计算信号的开始和结束索引，以及相关的价格信息。

         参数:
         - code_data (pd.DataFrame): 包含信号和价格数据的数据集，必须包含 'date', 'SignalChoice', 'SignalId',
           'low', 'high', 'Daily1mVolMax5' 等列。

         返回:
         - pd.DataFrame: 添加了起始和结束价格及其索引的data数据集。
         """

        # 将'date'列设为索引，并删除原来的'date'列
        data = data.set_index('date', drop=True)

        # 删除'SignalChoice'列为空的行
        drops = data.dropna(subset=[SignalChoice])

        for i, index in zip(range(len(drops)), drops.index):

            if i > 0:  # 从第二个信号开始处理
                signal_ = data.loc[index, SignalChoice]
                times_ = data.loc[index, SignalId]  # SignalId（区间ID）

                if signal_ == down:
                    end_price = data[data[SignalId] == times_]['low'].min()
                    end_price_id = data[data[SignalId] == times_]['low'].idxmin()

                else:
                    end_price = data[data[SignalId] == times_]['high'].max()
                    end_price_id = data[data[SignalId] == times_]['high'].idxmax()

                data.loc[index, EndPrice] = end_price  # '结束价'
                data.loc[index, EndPriceIndex] = end_price_id  # '结束价_index'
                data.loc[index, 'EndDaily1mVolMax5'] = data.loc[end_price_id, 'Daily1mVolMax5']

        # 计算每个信号的起始价格和起始价格索引
        condition = (~data[SignalChoice].isnull())
        data.loc[condition, StartPrice] = data[condition][EndPrice].shift(1)
        data.loc[condition, StartPriceIndex] = data[condition][EndPriceIndex].shift(1)

        # 将结果进行前向填充，填补缺失值
        fills = [EndPrice, EndPriceIndex, StartPrice, StartPriceIndex, 'EndDaily1mVolMax5']
        data[fills] = data[fills].ffill()

        # 将索引重置回原来的状态
        data = data.reset_index()
        return data

        # 思路：下跌趋势中，找出信号区间，上涨区间的最大值，下跌区间最小值，用于算出振幅;
        # 此处和价格监视模块的思路不一样，价格监视模块，

    @classmethod
    def s_CycleAmplitude(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        计算每个信号周期的振幅，包括每个bar的相对振幅和整个周期的最大振幅。

        参数:
        - code_data (pd.DataFrame): 包含信号、价格数据的数据集，必须包含'Signal', 'high', 'low', 'StartPrice', 'EndPrice'等列。

        返回:
        - pd.DataFrame: 添加了'CycleAmplitudePerBar'和'CycleAmplitudeMax'列的data数据集。
        """

        # 定义条件，用于筛选上升和下降信号
        con01 = data[Signal] == upInt
        con02 = data[Signal] == downInt

        # 对于上升信号，计算相对振幅（high - StartPrice）/ StartPrice
        data.loc[con01, CycleAmplitudePerBar] = round((data['high'] - data[StartPrice]) / data[StartPrice], 3)

        # 对于下降信号，计算相对振幅（low - StartPrice）/ StartPrice
        data.loc[con02, CycleAmplitudePerBar] = round((data['low'] - data[StartPrice]) / data[StartPrice], 3)

        # 计算每个周期的最大振幅（EndPrice - StartPrice）/ StartPrice
        data.loc[:, CycleAmplitudeMax] = round((data[EndPrice] - data[StartPrice]) / data[StartPrice], 3)

        return data

    @classmethod
    def s_Cycle1mVolumeMax(cls, data: pd.DataFrame, data1m: pd.DataFrame) -> pd.DataFrame:

        """
         计算每个信号周期内的1分钟最大成交量，以及基于该成交量的5分钟平均成交量。

         参数:
         - code_data (pd.DataFrame): 包含信号数据的数据集，必须包含 'SignalChoice', 'SignalTimes', 'EndPriceIndex', 'date' 等列。
         - data1m (pd.DataFrame): 包含1分钟成交量数据的数据集，必须包含 'date' 和 'volume' 列。

         返回:
         - pd.DataFrame: 添加了 'Cycle1mVolMax1' 和 'Cycle1mVolMax5' 列的 code_data 数据集。
         """

        # 筛选出 SignalChoice 不为空的行
        conditions = (~data[SignalChoice].isnull())

        for index in data[conditions].index:
            st = data.loc[index, SignalId]  # 获取当前信号区间ID
            ed_time = data.loc[index, EndPriceIndex]  # 获取当前信号结束的时间

            # 获取在该信号时间段内的数据
            selects = data[(data[SignalId] == st) & (data['date'] < ed_time)]

            if len(selects) > 5:  # 如果信号区间内的数据超过5条
                # 获取倒数第5条数据的时间，作为开始时间
                st_time = data[(data[SignalId] == st) &
                               (data['date'] < ed_time)].iloc[-5]['date']
            else:
                # 否则，使用结束时间当天的日期作为开始时间
                st_time = pd.to_datetime(ed_time.date())

            # 计算从开始时间到结束时间内，1分钟成交量的最大值
            max1 = data1m[(data1m['date'] > st_time) &
                          (data1m['date'] <= ed_time)].sort_values(by=['volume']).tail(1)['volume'].mean()

            # 计算从开始时间到结束时间内，1分钟成交量的最大5个值的平均值
            max5 = data1m[(data1m['date'] > st_time) &
                          (data1m['date'] <= ed_time)].sort_values(by=['volume']).tail(5)['volume'].mean()

            # 将计算出的最大1分钟和最大5分钟成交量分别赋值给对应的列
            data.loc[(data[SignalId] == st), Cycle1mVolMax1] = max1
            data.loc[(data[SignalId] == st), Cycle1mVolMax5] = max5

        return data

    @classmethod
    def s_CycleLength(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        计算信号周期的长度以及每个周期中每个bar的位置。

        参数:
        - code_data (pd.DataFrame): 包含信号数据的数据集，必须包含'SignalChoice'和'SignalId'列。

        返回:
        - pd.DataFrame: 添加了'CycleLengthMax'和'CycleLengthPerBar'列的data数据集。
        """

        for index in data[~data[SignalChoice].isnull()].index:
            st = data.loc[index, SignalId]  # 获取当前信号区间ID
            conditions = data[SignalId] == st  # 找到相同信号区间ID的所有行

            st_index = data[conditions].index[0]  # 该信号第一次出现的索引
            ed_index = data[conditions].index[-1]  # 该信号最后一次出现的索引

            # 计算周期的总长度，并填充到'CycleLengthMax'列中
            data.loc[conditions, CycleLengthMax] = ed_index - st_index

            # 计算每个bar在周期中的位置，并填充到'CycleLengthPerBar'列中
            data.loc[conditions, CycleLengthPerBar] = data[conditions].index - st_index

        return data

    @classmethod
    def s_Daily1mMax(cls, data: pd.DataFrame, data1m: pd.DataFrame) -> pd.DataFrame:  # 找出每天最大的 1根，5根，15根 1分钟成交量
        """
        计算每一天的最大1分钟成交量，以及基于该成交量的5分钟和15分钟平均成交量。

        参数:
        - code_data (pd.DataFrame): 包含日期信息的原始数据集，必须包含 'date' 列。
        - data1m (pd.DataFrame): 包含1分钟成交量数据的数据集，必须包含 'date' 和 'volume' 列。

        返回:
        - pd.DataFrame: 添加了最大1分钟成交量及其5分钟和15分钟平均成交量的data数据集。
        """

        # 用于存储每天最大1分钟，5分钟和15分钟成交量的列名
        fills = [Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15]

        # 提取日期中的时间部分，作为新的列 'minute_date'
        data.loc[:, 'minute_date'] = data['date'].dt.time

        def find_Daily1mMax(x: pd.Timestamp, num: int, data1m: pd.DataFrame) -> int:
            """
            在给定日期的下一天内，找到最大的num个成交量并计算平均值。

            参数:
            - x (pd.Timestamp): 当天的日期。
            - num (int): 要取的最大成交量数量。
            - data1m (pd.DataFrame): 包含1分钟成交量数据的数据集。

            返回:
            - int: 计算出的最大成交量的平均值（取整）。
            """
            st_date = pd.to_datetime(x.date())
            ed_date = st_date + pd.Timedelta(days=1)

            # 筛选出当天的1分钟成交量数据
            select = data1m[(data1m['date'] > st_date) & (data1m['date'] < ed_date)]

            # 计算出最大num个成交量的平均值，并取整
            max_volume = select.sort_values(by=['volume'])['volume'].tail(num).mean()
            return int(max_volume)

        # 找到当天09:45:00的记录，并对其应用find_Daily1mMax函数，分别计算最大1, 5, 15分钟的成交量
        con = data['minute_date'] == pd.to_datetime('09:45:00').time()
        data.loc[con, Daily1mVolMax1] = data.loc[con, 'date'].apply(find_Daily1mMax, args=(1, data1m,))
        data.loc[con, Daily1mVolMax5] = data.loc[con, 'date'].apply(find_Daily1mMax, args=(5, data1m,))
        data.loc[con, Daily1mVolMax15] = data.loc[con, 'date'].apply(find_Daily1mMax, args=(15, data1m,))

        # 对计算出来的最大成交量进行前向填充，填充空值
        data[fills] = data[fills].ffill()

        return data

    @classmethod
    def find_Bar1mMax(cls, x: pd.Timestamp, num: int, data1m: pd.DataFrame) -> int:
        """
        找出给定时间范围内1分钟成交量的最大值或最大值的平均值。

        参数:
        - x (pd.Timestamp): 目标时间点。
        - num (int): 用于计算平均值的成交量条数。
        - data1m (pd.DataFrame): 包含1分钟成交量数据的数据集，必须包含 'date' 和 'volume' 列。

        返回:
        - int: 指定时间范围内1分钟成交量的最大值或最大值的平均值。如果没有数据，返回0。
        """

        # 计算起始时间（目标时间前15分钟）和结束时间（目标时间）
        st = pd.to_datetime(x) + pd.Timedelta(minutes=-15)
        ed = pd.to_datetime(x)

        # 筛选在起始时间和结束时间之间的数据
        filtered_data = data1m[(data1m['date'] > st) & (data1m['date'] <= ed)]

        # 如果筛选的数据量少于 num 条，则直接返回 0
        if filtered_data.empty:
            return 0

        # 获取最大 num 个成交量并计算其平均值
        max_volume = filtered_data['volume'].nlargest(num).mean()

        return max_volume

    @classmethod
    def s_BarMax1mVolume(cls, data: pd.DataFrame, data1m: pd.DataFrame) -> pd.DataFrame:
        """
        为每个信号周期计算1分钟最大成交量以及最大5个成交量的平均值。

        参数:
        - code_data (pd.DataFrame): 包含信号数据的数据集，必须包含 'date' 列。
        - data1m (pd.DataFrame): 包含1分钟成交量数据的数据集，必须包含 'date' 和 'volume' 列。

        返回:
        - pd.DataFrame: 添加了 'Cycle1mVolMax1' 和 'Cycle1mVolMax5' 列的 code_data 数据集。
        """
        # 使用 find_Bar1mMax 方法为每个时间点计算 1 分钟最大成交量
        data.loc[:, Cycle1mVolMax1] = data['date'].apply(cls.find_Bar1mMax, args=(1, data1m,))

        # 使用 find_Bar1mMax 方法为每个时间点计算最大 5 个成交量的平均值
        data.loc[:, Cycle1mVolMax5] = data['date'].apply(cls.find_Bar1mMax, args=(5, data1m,))
        return data


class SignalMethod:


    @classmethod
    def signal_by_MACD_3ema(cls, data, data1m):

        data = MacdSignalPipeline.compute_macd_signals_pipeline(data)

        data = StatisticsMACD.s_Daily1mMax(data, data1m)

        data = StatisticsMACD.s_StartEndIndex(data)

        data = StatisticsMACD.s_CycleAmplitude(data)
        data = StatisticsMACD.s_Cycle1mVolumeMax(data, data1m)

        data = StatisticsMACD.s_CycleLength(data)

        data = data.drop(columns=['minute_date'])
        data = Bollinger(data=data)  # 找出boll通道价格
        return data

    @classmethod
    def trend_3ema_MACDBoll(cls, data):
        data = MacdSignalPipeline.compute_macd_signals_pipeline(data)
        data = StatisticsMACD.compute_macd_signal_segment_endpoints(data)
        data = Bollinger(data=data)  # 找出boll通道价格
        return data

    @classmethod
    def ema3_MACDBoll(cls, data):
        data = MacdSignalPipeline.compute_macd_signals_pipeline(data)
        data = Bollinger(data=data)  # 找出boll通道价格
        return data

    @classmethod
    def trend_MACD(cls, data):
        data = MacdSignalPipeline.compute_macd_signals_pipeline(data)
        data = data[[Signal, SignalChoice, SignalId, 'date']]
        return data


if __name__ == '__main__':
    pass
    # data1m = alc.pd_read(database='stock_1m_data', table='sz002475')
    # print(count)
