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
            # find_file_in_paths 找不到时返回 (False, False)，须先判定再 np.load
            file_path_x, pre_month_x = find_file_in_paths(self.month, 'train_data', file_x)
            file_path_y, pre_month_y = find_file_in_paths(self.month, 'train_data', file_y)
            if not (file_path_x and file_path_y):
                return np.zeros([0]), np.empty([0]), None
            data_x = np.load(file_path_x, allow_pickle=True)
            data_y = np.load(file_path_y, allow_pickle=True)
            return data_x, data_y, pre_month_x

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

        # train_data_path 只拼路径不建目录；新月份（如 2026-01）首次训练时
        # data/RnnData/<month>/train_data/ 还不存在，np.save 会报
        # "No such file or directory"。保存前确保目录存在。
        import os
        os.makedirs(os.path.dirname(file_path_x), exist_ok=True)

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

    # 标准化时只看最近 N 个月的数据来确定 max/min。
    # 月底重训会自动反映最新市场状态，避免远古异常值永久锚定 scale。
    # 改大 → 范围更稳定；改小 → 对市场风格变化更敏感。
    STANDARDIZATION_WINDOW_MONTHS = 24

    def stand_save_parser(self, data: pd.DataFrame, column: str, drop_duplicates: bool, drop_column: str) -> pd.DataFrame:
        """
        标准化并保存指定列的数据。

        使用中位数绝对偏差(MAD)方法进行异常值处理，归一化到 [0, 1]。
        统计量基于"近 N 个月"窗口内的数据计算（N = STANDARDIZATION_WINDOW_MONTHS），
        但归一化应用到 data 的全部行——这样模型同时见到历史和当前样本，但 scale
        由近期数据决定。

        Args:
            data: 输入数据框（该股票的完整 15m 历史）
            column: 需要标准化的列
            drop_duplicates: 计算 MAD 时是否去重
            drop_column: 去重依据列名

        Returns:
            标准化后的 DataFrame（与输入 data 行数相同）
        """
        # 1. 截取近 N 个月数据用于统计 max/min
        if 'date' in data.columns:
            cutoff = pd.Timestamp.now() - pd.DateOffset(months=self.STANDARDIZATION_WINDOW_MONTHS)
            recent = data[pd.to_datetime(data['date']) >= cutoff]
            # 数据不足时回退到全量，避免样本太少导致 MAD 不稳定
            if len(recent) < 100:
                recent = data
        else:
            recent = data

        # 2. 在 recent 窗口上算 MAD
        if drop_duplicates:
            df_stats = (recent.dropna(subset=[SignalChoice]) if drop_column == SignalChoice
                        else recent.drop_duplicates(subset=[column]))
            med = df_stats[column].median()
            mad = abs(df_stats[column] - med).median()
        else:
            med = recent[column].median()
            mad = abs(recent[column] - med).median()

        # 3. 计算上下限（±3 倍标准差，用 MAD 的 normal-consistent 近似 1.4826*mad ≈ std）
        high = round(med + (3 * 1.4826 * mad), 2)
        low = round(med - (3 * 1.4826 * mad), 2)

        # 4. 退化保护：mad=0 时 high==low，归一化会除零；给 high 加一个最小步进
        if high == low:
            high = low + 1e-6

        # 5. 截断 + 归一化（应用到全部 data，不只 recent）
        # 新版 pandas 不允许往 int 列写 float，先显式转 float64
        if data[column].dtype != float:
            data[column] = data[column].astype('float64')
        data.loc[data[column] > high, column] = high
        data.loc[data[column] < low, column] = low
        data[column] = (data[column] - low) / (high - low)

        # 6. 保存本月参数（含元数据，方便日后审计/调试）
        parser_data = ReadSaveFile.read_json(self.month, self.stock_code)
        parser_data[column] = {
            'num_max': high,
            'num_min': low,
            'window_months': self.STANDARDIZATION_WINDOW_MONTHS,
            'sample_count': int(len(recent)),
            'computed_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
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
        # 读取标准化参数（扁平结构 {col: {num_max, num_min}}，与 stand_save_parser 一致）
        parser_data = ReadSaveFile.read_json(self.month, self.stock_code) or {}
        if match not in parser_data or 'num_max' not in parser_data[match]:
            # 参数未保存（可能是 pre* 列在主列之前被处理，或第一次跑）
            # 跳过标准化以免炸；调用方应保证调用顺序正确
            return data

        num_max = parser_data[match]['num_max']
        num_min = parser_data[match]['num_min']

        # 数据截断和归一化（防止往 int 列写 float）
        if data[column].dtype != float:
            data[column] = data[column].astype('float64')
        data.loc[data[column] > num_max, column] = num_max
        data.loc[data[column] < num_min, column] = num_min
        denom = num_max - num_min if num_max != num_min else 1e-6
        data[column] = (data[column] - num_min) / denom

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
        # 用 self.data_1m（已由 data_1m_calculate 加载完整 start_date → 今）
        # 不再重新调 StockData1m.load_1m（旧代码传日期字符串当 year，会 ValueError）
        if self.data_1m is None or self.data_1m.empty:
            today = pd.Timestamp.now().strftime('%Y-%m-%d')
            self.data_1m = StockData1m.load_1m_by_date_range(
                self.stock_code, self.start_date, today)

        if self.data_1m.empty:
            self.daily_volume_max = 0.0
            return

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

    def data_1m_calculate(self) -> None:
        """
        加载并预处理 1 分钟数据（跨年范围）

        从 self.start_date 到今天，跨年拼接 1m 数据到 self.data_1m。
        历史代码这个方法在 RnnCreationData.TrainingDataCalculate 里缺失，
        导致 calculation_single 链路根本跑不通——这里补全。
        """
        today = pd.Timestamp.now().strftime('%Y-%m-%d')
        logger.info(f"加载 1 分钟数据: {self.stock_code}, {self.start_date} → {today}")

        self.data_1m = StockData1m.load_1m_by_date_range(
            self.stock_code, self.start_date, today)

        if self.data_1m is None or self.data_1m.empty:
            raise ValueError(
                f"无法加载 1 分钟数据: {self.stock_code} (范围 {self.start_date} → {today})。"
                f"请检查 data/quarters/ 里是否有该股票的 parquet 文件。"
            )

        # 标准化 date 列 + 去重排序
        self.data_1m['date'] = pd.to_datetime(self.data_1m['date'])
        self.data_1m = (self.data_1m
                        .drop_duplicates(subset=['date'])
                        .sort_values('date')
                        .reset_index(drop=True))

        # 缺失值处理
        price_cols = ['open', 'high', 'low', 'close']
        for c in price_cols:
            if c in self.data_1m.columns:
                self.data_1m[c] = self.data_1m[c].ffill()
        if 'volume' in self.data_1m.columns:
            self.data_1m['volume'] = self.data_1m['volume'].fillna(0)

        self.start_date_1m = self.data_1m['date'].min().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"1m 数据加载完成: {self.stock_code}, {len(self.data_1m)} 条")

        # 顺手算日成交量最大值（标准化时会用到）
        self._calculate_daily_volume_max()

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
            DailyVolEmaParser].ffill()
        
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
        # Signal 列上游（MacdSignalV2）以 pd.NA 初始化，首个 flip 之前的行 ffill 也填不上，
        # 仍是 pd.NA（object dtype）。.astype(float) 会逐元素 float(pd.NA) 报
        # "float() argument ... not 'NAType'"。用 to_numeric coerce 成 NaN，
        # 这些 head 行随后在 column_stand 的 dropna(subset=[Signal]) 被清除，语义不变。
        self.data_15m[Signal] = pd.to_numeric(self.data_15m[Signal], errors='coerce')

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
        self.data_15m[fills] = self.data_15m[fills].ffill()
        
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
        保存记录信息（用于下次增量训练定位起点）。
        老 schema 用的列名 SignalStartTime / SignalTimes 已重命名为
        SignalStartIndex / SignalId（见 MacdParser），这里跟着改。
        缺列时跳过对应字段而不抛异常，避免训练流程中断。
        """
        last = self.data_15m.iloc[-1]
        record_info = {}

        def _safe(col, fmt=None):
            if col not in self.data_15m.columns:
                return None
            v = last.get(col)
            if v is None or pd.isna(v):
                return None
            return v.strftime(fmt) if (fmt and hasattr(v, 'strftime')) else v

        record_info['RecordEndDate'] = _safe('date', '%Y-%m-%d %H:%M:%S')
        record_info['RecordEndSignal'] = _safe('Signal')
        record_info['RecordEndSignalTimes'] = _safe(SignalTimes)
        # SignalStartIndex 是个 datetime（来自 SignalStartTime 重命名）
        record_info['RecordEndSignalStartTime'] = _safe(SignalStartIndex, '%Y-%m-%d %H:%M:%S')

        try:
            sub = self.data_15m.drop_duplicates(subset=[SignalTimes]).tail(6)
            if len(sub) > 0:
                record_info['RecordNextStartDate'] = sub.iloc[0]['date'].strftime(
                    '%Y-%m-%d %H:%M:%S')
        except Exception:
            pass

        records = ReadSaveFile.read_json(self.month, self.stock_code) or {}
        records.update({k: v for k, v in record_info.items() if v is not None})
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
        # 读上一月份的 RecordEndDate（用于增量训练定位）
        # find_file_in_paths 返回 (path, month) 二元组，老代码这里漏了解包
        try:
            result = find_file_in_paths(
                self.month, 'json', f'{self.stock_code}.json')
            path = result[0] if isinstance(result, tuple) else result
            if path:
                record = ReadSaveFile.read_json_by_path(path)
                if record and self.stock_code in record:
                    self.RecordEndDate = record[self.stock_code].get('RecordEndDate')
                    self.RecordStartDate = record[self.stock_code].get('NextStartDate')
        except (ValueError, TypeError, KeyError):
            # 第一次训练 / 上月没有记录 / 字段缺失，跳过
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
