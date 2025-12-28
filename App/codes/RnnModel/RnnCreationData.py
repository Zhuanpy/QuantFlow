# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from typing import Optional, Union, List, Dict, Tuple
import logging
from datetime import datetime
from sqlalchemy.exc import IntegrityError

from ..MySql.DataBaseStockData1m import StockData1m
from ..MySql.DataBaseStockData15m import StockData15m
from ..MySql.sql_utils import Stocks
from ..parsers.RnnParser import *
from App.codes.utils.Normal import ReadSaveFile, ResampleData
from ..Signals.StatisticsMacd import SignalMethod
from ..RnnDataFile.stock_path import StockDataPath

from ..RnnDataFile.stock_path import file_root
from .Rnn_utils import find_file_in_paths

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('rnn_data.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# 创建logger实例
logger = logging.getLogger(__name__)

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


class ModelData:
    """
    1. 处理模型数据
    2. 模型数据准备
    """

    def __init__(self):

        self.root = file_root()
        self.month = None
        self.stock_code = None
        self.data_15m = None

        self.x_columns = XColumn()
        self.y_column = YColumn()
        self.model_name = ModelName

    def load_pre_month_existing_train_data(self, model_name: str) -> tuple:

        """
        导入以前的数据，尝试读取前数据文件夹。

        :param model_name: 模型名字
        :return: 前数据文件夹内容，格式为 (data_x, data_y, pre_month)
        """

        file_x = f'{model_name}_{self.stock_code}_x.npy'
        file_y = f'{model_name}_{self.stock_code}_y.npy'

        try:
            # 前数据读取
            file_path_x, pre_month = find_file_in_paths(self.month, 'train_data', file_x)
            file_path_y, pre_month = find_file_in_paths(self.month, 'train_data', file_y)
            data_x = np.load(file_path_x, allow_pickle=True)
            data_y = np.load(file_path_y, allow_pickle=True)
            return data_x, data_y, pre_month

        except FileNotFoundError:
            return np.zeros([0]), np.empty([0]), None

    def _save_data(self, model_name: str, data_x: np.ndarray, data_y: np.ndarray) -> None:
        """
        保存数据至指定路径。

        :param model_name: 模型名字
        :param data_x: 训练数据集 X
        :param data_y: 训练数据集 Y
        """
        file_x = f'{model_name}_{self.stock_code}_x.npy'
        file_y = f'{model_name}_{self.stock_code}_y.npy'

        file_path_x = StockDataPath.train_data_path(self.month, file_x)
        file_path_y = StockDataPath.train_data_path(self.month, file_y)

        np.save(file_path_x, data_x)
        np.save(file_path_y, data_y)

    def data_common(self, model_name: str, column_x: list, column_y: list, height: int = 30, width: int = 30):  # width=w2, height=h1
        """
        处理通用数据。

        :param model_name: 模型名字
        :param column_x: X 数据列名称集
        :param column_y: Y 数据列名称集
        :param height: 数据矩阵的高度（默认为30）
        :param width: 数据矩阵的宽度（默认为30）
        """

        data_x, data_y, pre_month = self.load_pre_month_existing_train_data(model_name)  # 加载以前数据

        # 整理数据
        data_ = self.data_15m.dropna(subset=[SignalChoice])

        for st in data_[SignalTimes]:
            x = self.data_15m[self.data_15m[SignalTimes] == st][column_x].dropna(how='any').tail(height)
            y = self.data_15m[self.data_15m[SignalTimes] == st][column_y].dropna(how='any').tail(1)

            if not x.shape[0] or not y.shape[0]:
                continue

            x = pd.concat([x[[Signal]], x], axis=1)
            x = x.to_numpy()

            # 填充数据为 30*30 矩阵，不足部分补0
            h = height - x.shape[0]
            w = width - x.shape[1]

            ht = h // 2  # height top
            hl = h - ht  # height bottom

            wl = w // 2  # width left
            wr = w - wl  # width right

            x = np.pad(x, ((ht, hl), (wr, wl)), 'constant', constant_values=(0, 0))
            x.shape = (1, height, width, 1)
            y = y.to_numpy()

            # 合并数据
            if data_x.shape[0]:
                data_x = np.append(data_x, x, axis=0)
                data_y = np.append(data_y, y, axis=0)

            else:
                data_x = x
                data_y = y

        # 新数据储存
        self._save_data(model_name, data_x, data_y)

        print(f'{model_name}, shape: {data_x.shape};')

    def data_cycle_length(self) -> None:
        x = self.x_columns[0]
        y = self.y_column[0]
        self.data_common(self.model_name[0], x, y)

    def data_cycle_change(self) -> None:
        x = self.x_columns[1]
        y = self.y_column[1]
        self.data_common(self.model_name[1], x, y)

    def data_bar_change(self) -> None:
        x = self.x_columns[2]
        y = self.y_column[2]
        self.data_common(self.model_name[2], x, y)

    def data_bar_volume(self) -> None:
        x = self.x_columns[3]
        y = self.y_column[3]
        self.data_common(self.model_name[3], x, y)


class TrainingDataCalculate(ModelData):
    """
    RNN模型训练数据处理类
    
    该类负责处理和准备RNN模型训练所需的数据，包括：
    1. 数据加载和预处理
    2. 数据标准化
    3. 特征计算和转换
    4. 数据存储和管理
    
    主要功能：
    - 1分钟和15分钟数据的处理和转换
    - 数据标准化和参数管理
    - 交易信号的生成和处理
    - 特征工程和数据准备
    """

    def __init__(self, stock: str, month: str, start_date: str):
        """
        初始化训练数据处理器
        
        Args:
            stock (str): 股票代码或名称
            month (str): 处理的月份
            start_date (str): 起始日期
        """
        super().__init__()
        # 初始化股票信息
        self.stock_name, self.stock_code, self.stock_id = Stocks(stock)
        
        # 基础参数设置
        self.month = month
        self.freq = '15m'
        self.start_date = start_date
        
        # 数据存储
        self.data_1m = None  # 1分钟数据
        self.data_15m = None  # 15分钟数据
        self.times_data = None  # 时间序列数据
        self.daily_volume_max = None  # 日成交量最大值
        
        # 记录日期
        self.start_date_1m = None  # 1分钟数据起始日期
        self.RecordStartDate = None  # 记录起始日期
        self.RecordEndDate = None  # 记录结束日期

    def rnn_parser_data(self):
        """
        初始化或读取股票的参数数据
        确保每个股票都有对应的参数记录
        """
        data = ReadSaveFile.read_json(self.month, self.stock_code)
        if self.stock_code not in data:
            data[self.stock_code] = {}
            ReadSaveFile.save_json(data, self.month, self.stock_code)

    def stand_save_parser(self, data: pd.DataFrame, column: str, drop_duplicates: bool, drop_column: str) -> pd.DataFrame:
        """
        标准化并保存指定列的数据
        
        使用中位数绝对偏差(MAD)方法进行异常值处理和标准化
        
        Args:
            data: 输入数据框
            column: 需要标准化的列
            drop_duplicates: 是否删除重复值
            drop_column: 用于去重的列名
            
        Returns:
            标准化后的数据框
        """
        # 数据预处理
        if drop_duplicates:
            df = (data.dropna(subset=[SignalChoice]) if drop_column == SignalChoice 
                  else data.drop_duplicates(subset=[column]))
            med = df[column].median()
            mad = abs(df[column] - med).median()
        else:
            med = data[column].median()
            mad = abs(data[column] - med).median()
        
        # 计算上下限
        high = round(med + (3 * 1.4826 * mad), 2)
        low = round(med - (3 * 1.4826 * mad), 2)
        
        # 读取历史参数并比较
        try:
            file_name = f"{self.stock_code}.json"
            file_path, pre_month = find_file_in_paths(self.month, 'json', file_name)
            parser_data = ReadSaveFile.read_json_by_path(file_path)
            pre_high = parser_data[self.stock_code][column]['num_max']
            pre_low = parser_data[self.stock_code][column]['num_min']
            
            # 更新上下限
            high = max(high, pre_high)
            low = min(low, pre_low)
        except ValueError:
            pass
        
        # 数据截断和归一化
        data.loc[data[column] > high, column] = high
        data.loc[data[column] < low, column] = low
        data[column] = (data[column] - low) / (high - low)
        
        # 保存参数
        parser_data = ReadSaveFile.read_json(self.month, self.stock_code)
        parser_data[column] = {'num_max': high, 'num_min': low}
        ReadSaveFile.save_json(parser_data, self.month, self.stock_code)
        
        return data

    def stand_read_parser(self, data: pd.DataFrame, column: str, match: str) -> pd.DataFrame:
        """
        使用已保存的标准化参数处理数据
        
        Args:
            data: 输入数据框
            column: 需要标准化的列
            match: 参数匹配的列名
            
        Returns:
            标准化后的数据框
        """
        # 读取标准化参数
        parser_data = ReadSaveFile.read_json(self.month, self.stock_code)
        num_max = parser_data[self.stock_code][match]['num_max']
        num_min = parser_data[self.stock_code][match]['num_min']
        
        # 数据截断和归一化
        data.loc[data[column] > num_max, column] = num_max
        data.loc[data[column] < num_min, column] = num_min
        data[column] = (data[column] - num_min) / (num_max - num_min)
        
        return data

    def column_stand(self) -> pd.DataFrame:
        """
        对所有需要标准化的列进行处理
        
        包括：
        - 成交量相关指标
        - 周期相关指标
        - 振幅相关指标
        
        Returns:
            标准化后的完整数据框
        """
        # 处理日成交量最大值
        if not self.daily_volume_max:
            self._calculate_daily_volume_max()
        
        # 保存日成交量最大值参数
        parser_data = ReadSaveFile.read_json(self.month, self.stock_code)
        parser_data[DailyVolEma] = self.daily_volume_max
        ReadSaveFile.save_json(parser_data, self.month, self.stock_code)
        
        # 定义需要标准化的列及其参数
        save_list = [
            ('volume', False, None),
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
            ('EndDaily1mVolMax5', True, SignalChoice)
        ]
        
        # 执行标准化
        for column, drop_duplicates, drop_column in save_list:
            self.data_15m = self.stand_save_parser(
                self.data_15m, column, drop_duplicates, drop_column)
        
        # 处理前置周期数据
        read_dict = {
            preCycle1mVolMax1: Cycle1mVolMax1,
            preCycle1mVolMax5: Cycle1mVolMax5,
            preCycleLengthMax: CycleLengthMax,
            preCycleAmplitudeMax: CycleAmplitudeMax
        }
        
        for key, value in read_dict.items():
            self.data_15m = self.stand_read_parser(self.data_15m, key, value)
        
        # 清理数据
        self.data_15m = self.data_15m.dropna(subset=[Signal])
        last_signal_times = self.data_15m.iloc[-1][SignalTimes]
        self.data_15m = self.data_15m[
            self.data_15m[SignalTimes] != last_signal_times]
        
        # 选择最终列
        all_columns = [
            'date', Signal, SignalTimes, SignalChoice,
            StartPriceIndex, EndPriceIndex, CycleAmplitudePerBar,
            CycleAmplitudeMax, Cycle1mVolMax1, Cycle1mVolMax5,
            CycleLengthMax, CycleLengthPerBar, Daily1mVolMax1,
            Daily1mVolMax5, Daily1mVolMax15, preCycle1mVolMax1,
            preCycleLengthMax, Bar1mVolMax1, nextCycleLengthMax,
            preCycle1mVolMax5, 'volume', Bar1mVolMax5,
            preCycleAmplitudeMax, 'EndDaily1mVolMax5',
            nextCycleAmplitudeMax
        ]
        
        self.data_15m = self.data_15m[all_columns]
        return self.data_15m

    def _calculate_daily_volume_max(self):
        """
        计算日成交量最大值
        用于数据标准化的基准
        """
        _date = '2018-01-01'
        self.data_1m = StockData1m.load_1m(self.stock_code, _date)
        self.data_1m = self.data_1m[
            self.data_1m['date'] > pd.to_datetime(_date)]
        
        data_daily = ResampleData.resample_1m_data(
            data=self.data_1m, freq='daily')
        data_daily.loc[:, 'date'] = (
            pd.to_datetime(data_daily['date']) + 
            pd.Timedelta(minutes=585)
        )
        data_daily.loc[:, DailyVolEma] = (
            data_daily['volume']
            .rolling(90, min_periods=1)
            .mean()
        )
        
        self.daily_volume_max = round(data_daily[DailyVolEma].max(), 2)

    def first_calculate(self) -> pd.DataFrame:
        """
        第一阶段数据处理
        
        包括：
        - 数据重采样
        - 信号生成
        - 日线数据处理
        
        Returns:
            处理后的数据框
        """
        # 重采样到15分钟
        self.data_15m = ResampleData.resample_1m_data(
            data=self.data_1m, freq=self.freq)
        
        # 生成MACD信号
        self.data_15m = SignalMethod.signal_by_MACD_3ema(
            self.data_15m, self.data_1m).set_index('date', drop=True)
        
        # 处理日线数据
        data_daily = self._process_daily_data()
        
        # 合并数据
        self.data_15m = self.data_15m.join([data_daily]).reset_index()
        self.data_15m[DailyVolEmaParser] = self.data_15m[
            DailyVolEmaParser].fillna(method='ffill')
        
        # 排除最后一个信号周期
        last_signal_times = self.data_15m.iloc[-1][SignalTimes]
        self.data_15m = self.data_15m[
            self.data_15m[SignalTimes] != last_signal_times]
        
        return self.data_15m

    def _process_daily_data(self) -> pd.DataFrame:
        """
        处理日线数据
        
        Returns:
            处理后的日线数据
        """
        data_daily = ResampleData.resample_1m_data(
            data=self.data_1m, freq='daily')
        data_daily['date'] = (
            pd.to_datetime(data_daily['date']) + 
            pd.Timedelta(minutes=585)
        )
        data_daily[DailyVolEma] = (
            data_daily['volume']
            .rolling(90, min_periods=1)
            .mean()
        )
        
        daily_volume_max = round(data_daily[DailyVolEma].max(), 2)
        
        try:
            file_name = f"{self.stock_code}.json"
            file_path = find_file_in_paths(self.month, 'json', file_name)
            parser_data = ReadSaveFile.read_json_by_path(file_path)
            pre_daily_volume_max = parser_data[self.stock_code][DailyVolEma]
        except:
            pre_daily_volume_max = daily_volume_max
        
        self.daily_volume_max = max(daily_volume_max, pre_daily_volume_max)
        
        data_daily[DailyVolEmaParser] = (
            self.daily_volume_max / data_daily[DailyVolEma]
        )
        return data_daily[['date', DailyVolEmaParser]].set_index('date', drop=True)

    def second_calculate(self) -> pd.DataFrame:
        """
        第二阶段数据处理
        
        计算每个15分钟K线内的最大成交量
        
        Returns:
            处理后的数据框
        """
        for index in self.data_15m.dropna(
            subset=[SignalChoice, EndPriceIndex]).index:
            signal_times = self.data_15m.loc[index, SignalTimes]
            end_price_time = self.data_15m.loc[index, EndPriceIndex]
            
            selects = self.data_15m[
                (self.data_15m[SignalTimes] == signal_times) &
                (self.data_15m[EndPriceIndex] <= end_price_time)
            ].tail(35)
            
            st_index, ed_index = selects.index[0], selects.index[-1]
            
            # 计算Bar1mVolMax1和Bar1mVolMax5
            self.data_15m.loc[st_index:ed_index, Bar1mVolMax1] = (
                self.data_15m.loc[st_index:ed_index]['date']
                .apply(self.find_bar_max_1m, args=(1,))
            )
            
            self.data_15m.loc[st_index:ed_index, Bar1mVolMax5] = (
                self.data_15m.loc[st_index:ed_index]['date']
                .apply(self.find_bar_max_1m, args=(5,))
            )
        
        # 清理异常值
        self.data_15m = self.data_15m.replace([np.inf, -np.inf], np.nan)
        
        # 保存数据
        self.save_15m_data()
        
        return self.data_15m

    def third_calculate(self) -> pd.DataFrame:
        """
        第三阶段数据处理
        
        处理成交量相关参数和周期数据
        
        Returns:
            处理后的数据框
        """
        self.data_15m[Signal] = self.data_15m[Signal].astype(float)
        
        # 处理成交量相关参数
        vol_parser = [
            'volume', Cycle1mVolMax1, Cycle1mVolMax5,
            Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15,
            Bar1mVolMax1, Bar1mVolMax5, 'EndDaily1mVolMax5'
        ]
        
        for col in vol_parser:
            self.data_15m[col] = round(
                self.data_15m[col] * self.data_15m[DailyVolEmaParser])
        
        # 处理下一周期数据
        next_dic = {
            nextCycleAmplitudeMax: CycleAmplitudeMax,
            nextCycleLengthMax: CycleLengthMax
        }
        
        condition = (~self.data_15m[SignalChoice].isnull())
        for key, value in next_dic.items():
            self.data_15m.loc[condition, key] = (
                self.data_15m.loc[condition, value].shift(-1)
            )
        
        # 处理前周期数据
        pre_dic = {
            preCycle1mVolMax1: Cycle1mVolMax1,
            preCycle1mVolMax5: Cycle1mVolMax5,
            preCycleAmplitudeMax: CycleAmplitudeMax,
            preCycleLengthMax: CycleLengthMax
        }
        
        for key, value in pre_dic.items():
            self.data_15m.loc[condition, key] = (
                self.data_15m.loc[condition, value].shift(1)
            )
        
        # 填充缺失值
        fills = list(pre_dic.keys()) + list(next_dic.keys())
        self.data_15m[fills] = self.data_15m[fills].fillna(method='ffill')
        
        return self.data_15m

    def find_bar_max_1m(self, x: pd.Timestamp, num: int) -> Optional[int]:
        """
        查找指定时间段内的最大成交量
        
        Args:
            x (pd.Timestamp): 时间点
            num (int): 取前n个最大值的平均
            
        Returns:
            Optional[int]: 最大成交量值，如果计算失败则返回None
        """
        try:
            start_time = pd.to_datetime(x) + pd.Timedelta(minutes=-15)
            end_time = pd.to_datetime(x)
            
            max_vol = (
                self.data_1m[
                    (self.data_1m['date'] > start_time) & 
                    (self.data_1m['date'] < end_time)
                ]
                .sort_values(by=['volume'])['volume']
                .tail(num)
                .mean()
            )
            
            return int(max_vol) if pd.notna(max_vol) else None
            
        except Exception as ex:
            logger.error(
                f'{self.stock_name} - find_bar_max_1m 错误: {str(ex)}\n'
                f'时间点: {x}, num: {num}'
            )
            return None

    def save_15m_data(self):
        """
        保存15分钟数据和相关记录
        """
        if self.RecordStartDate:
            self._append_or_update_data()
        else:
            StockData15m.replace_15m(self.stock_code, self.data_15m)
        
        self._save_record_info()

    def _append_or_update_data(self):
        """
        追加或更新15分钟数据
        """
        self.data_15m = self.data_15m[
            self.data_15m['date'] > self.RecordEndDate
        ]
        
        try:
            StockData15m.append_15m(self.stock_code, self.data_15m)
        except IntegrityError:
            old = StockData15m.load_15m(self.stock_code)
            last_date = old.iloc[-1]['date']
            new = self.data_15m[self.data_15m['date'] > last_date]
            old = pd.concat([old, new], ignore_index=True)
            StockData15m.replace_15m(self.stock_code, old)

    def _save_record_info(self):
        """
        保存记录信息
        """
        record_info = {
            'RecordEndDate': self.data_15m.iloc[-1]['date'].strftime(
                '%Y-%m-%d %H:%M:%S'),
            'RecordEndSignal': self.data_15m.iloc[-1]['Signal'],
            'RecordEndSignalTimes': self.data_15m.iloc[-1]['SignalTimes'],
            'RecordEndSignalStartTime': self.data_15m.iloc[-1][
                'SignalStartTime'].strftime('%Y-%m-%d %H:%M:%S'),
            'RecordNextStartDate': self.data_15m.drop_duplicates(
                subset=[SignalTimes]).tail(6).iloc[0]['date'].strftime(
                '%Y-%m-%d %H:%M:%S')
        }
        
        records = ReadSaveFile.read_json(self.month, self.stock_code)
        records.update(record_info)
        ReadSaveFile.save_json(records, self.month, self.stock_code)

    def data_15m_calculate(self) -> pd.DataFrame:
        """
        执行完整的15分钟数据处理流程
        
        Returns:
            处理完成的15分钟数据
        """
        self.data_1m_calculate()
        self.data_15m = self.first_calculate()
        self.data_15m = self.second_calculate()
        self.data_15m = self.third_calculate()
        self.data_15m = self.column_stand()
        return self.data_15m

    def calculation_single(self):
        """
        执行单个股票的计算流程
        """
        try:
            path = find_file_in_paths(
                self.month, 'json', f'{self.stock_code}.json')
            record = ReadSaveFile.read_json_by_path(path)
            self.RecordEndDate = record[self.stock_code]['RecordEndDate']
            self.RecordStartDate = record[self.stock_code]['NextStartDate']

        except ValueError:
            pass
        
        self.data_15m = self.data_15m_calculate()
        
        # 处理不同模型的数据
        for i in range(4):
            x = self.x_columns[i]
            y = self.y_column[i]
            model_name = self.model_name[i]
            self.data_common(model_name, x, y)

    def calculation_read_from_sql(self):
        """
        从SQL数据库读取并处理数据
        """
        self.data_15m = StockData15m.load_15m(self.stock_code)
        self.data_15m = self.third_calculate()
        self.data_15m = self.column_stand()
        
        # 处理不同模型的数据
        for i in range(4):
            x = self.x_columns[i]
            y = self.y_column[i]
            model_name = self.model_name[i]
            self.data_common(model_name, x, y)


if __name__ == '__main__':
    month_ = '2023-01'
    start_d = '2018-01-01'
    # running = TrainingDataCalculate(month_, start_d)
    # running.all_stock()
