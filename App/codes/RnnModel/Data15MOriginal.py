# -*- coding: utf-8 -*-
"""
股票数据处理模块

主要功能：
1. 将1分钟数据转换为15分钟数据
2. 计算交易信号和技术指标
3. 处理和标准化成交量数据
4. 生成模型训练数据
"""

from typing import Optional, Dict, List, Tuple, Any
import numpy as np
import pandas as pd
import logging
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from dataclasses import dataclass
from functools import lru_cache
import os

from ..MySql.DataBaseStockData1m import StockData1m  # 1分钟数据库操作
from ..MySql.DataBaseStockData15m import StockData15m  # 15分钟数据库操作
from ..MySql.sql_utils import Stocks  # 股票基本信息
from ..parsers.RnnParser import *  # 解析器常量和配置
from App.codes.utils.Normal import ReadSaveFile, ResampleData  # 文件读写和数据重采样
from ..Signals.StatisticsMacd import SignalMethod  # MACD信号计算
from ..RnnDataFile.stock_path import StockDataPath  # 文件路径管理

from ..RnnDataFile.stock_path import file_root
from .Rnn_utils import find_file_in_paths

# 配置日志系统
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data_15m_original.log', encoding='utf-8'),  # 文件日志
        logging.StreamHandler()  # 控制台日志
    ]
)

logger = logging.getLogger(__name__)

# 设置pandas显示选项
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)

@classmethod
def load_1m_by_local_file(code_: str, year: str, file_path: str) -> pd.DataFrame:
    """
    从本地文件加载1分钟数据
    
    Args:
        code_: 股票代码
        year: 年份
        file_path: 文件路径
        
    Returns:
        pd.DataFrame: 包含1分钟数据的DataFrame
        
    示例:
        >>> data = load_1m_by_local_file("000001", "2023", "E:/data/stock/1m")
    """
    try:
        logger.info(f"开始从本地文件加载1分钟数据: {code_}, 年份: {year}")
        
        # 构建完整的文件路径
        full_path = os.path.join(file_path, year, f"{code_}.csv")
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"找不到文件: {full_path}")

            
        # 读取CSV文件
        df = pd.read_csv(full_path, 
                        parse_dates=['date'],  # 将date列解析为日期类型
                        dtype={
                            'code': str,
                            'open': float,
                            'high': float,
                            'low': float,
                            'close': float,
                            'volume': float,
                            'amount': float
                        })
        
        # 检查必要的列是否存在
        required_columns = ['date', 'code', 'open', 'high', 'low', 'close', 'volume']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"缺少必要的列: {missing_columns}")
            
        # 数据清理和预处理
        df = df.sort_values('date')  # 按时间排序
        df = df.drop_duplicates(subset=['date'])  # 删除重复数据
        
        # 处理缺失值
        df['volume'] = df['volume'].fillna(0)  # 成交量缺失填充为0
        price_cols = ['open', 'high', 'low', 'close']
        df[price_cols] = df[price_cols].fillna(method='ffill')  # 价格使用前值填充
        
        # 检查数据连续性
        time_diff = df['date'].diff()
        irregular_intervals = time_diff[time_diff != pd.Timedelta(minutes=1)]
        if not irregular_intervals.empty:
            logger.warning(f"发现不规则时间间隔，股票: {code_}, 年份: {year}\n"
                         f"不规则间隔数量: {len(irregular_intervals)}")
        
        # 检查数据质量
        logger.info(f"数据加载完成: {code_}, 年份: {year}")
        logger.info(f"数据范围: {df['date'].min()} 至 {df['date'].max()}")
        logger.info(f"数据条数: {len(df)}")
        
        return df
        
    except Exception as e:
        logger.error(f"加载本地1分钟数据失败: {code_}, 年份: {year}, 错误: {str(e)}")
        raise

@dataclass
class DataConfig:
    """
    数据处理配置类
    
    包含：
    1. 需要处理的成交量相关列
    2. 下一周期数据映射关系
    3. 前一周期数据映射关系
    """
    # 需要处理的成交量相关列名
    VOLUME_COLUMNS = [
        'volume',            # 原始成交量
        Cycle1mVolMax1,      # 周期内1分钟最大成交量
        Cycle1mVolMax5,      # 周期内5分钟最大成交量
        Daily1mVolMax1,      # 日内1分钟最大成交量
        Daily1mVolMax5,      # 日内5分钟最大成交量
        Daily1mVolMax15,     # 日内15分钟最大成交量
        Bar1mVolMax1,        # K线内1分钟最大成交量
        Bar1mVolMax5,        # K线内5分钟最大成交量
        'EndDaily1mVolMax5'  # 结束时日内5分钟最大成交量
    ]
    
    # 下一周期数据映射关系
    NEXT_CYCLE_MAPPING = {
        nextCycleAmplitudeMax: CycleAmplitudeMax,  # 下一周期振幅
        nextCycleLengthMax: CycleLengthMax         # 下一周期长度
    }
    
    # 前一周期数据映射关系
    PRE_CYCLE_MAPPING = {
        preCycle1mVolMax1: Cycle1mVolMax1,         # 前一周期1分钟最大成交量
        preCycle1mVolMax5: Cycle1mVolMax5,         # 前一周期5分钟最大成交量
        preCycleAmplitudeMax: CycleAmplitudeMax,   # 前一周期振幅
        preCycleLengthMax: CycleLengthMax          # 前一周期长度
    }


class ModelData:
    """
    基础模型数据处理类
    
    主要功能：
    1. 管理模型训练数据
    2. 处理和保存训练数据矩阵
    """

    def __init__(self):
        """初始化基础属性"""
        self.month: Optional[str] = None            # 处理的月份
        self.stock_code: Optional[str] = None       # 股票代码
        self.data_15m: Optional[pd.DataFrame] = None  # 15分钟数据
        self.x_columns = XColumn()                  # 特征列配置
        self.y_column = YColumn()                   # 标签列配置
        self.model_name = ModelName                 # 模型名称配置

    def _save_data(self, model_name: str, data_x: np.ndarray, data_y: np.ndarray) -> None:
        """
        保存训练数据到文件
        
        Args:
            model_name: 模型名称，用于生成文件名
            data_x: 特征矩阵，shape为(样本数, height, width, 1)
            data_y: 标签矩阵，shape为(样本数, 标签数)
        """
        try:
            # 构建文件名
            file_x = f'{model_name}_{self.stock_code}_x.npy'
            file_y = f'{model_name}_{self.stock_code}_y.npy'

            # 获取保存路径
            file_path_x = StockDataPath.train_data_path(self.month, file_x)
            file_path_y = StockDataPath.train_data_path(self.month, file_y)

            # 保存数据
            np.save(file_path_x, data_x)
            np.save(file_path_y, data_y)
            logger.info(f"保存训练数据成功: {model_name}, {self.stock_code}")
            
        except Exception as e:
            logger.error(f"保存训练数据失败: {model_name}, {self.stock_code}, 错误: {str(e)}")
            raise

    def data_common(self, model_name: str, column_x: list, column_y: list, height: int = 30, width: int = 30) -> None:
        """
        处理通用训练数据
        
        将15分钟数据处理成固定大小的矩阵，用于模型训练
        
        Args:
            model_name: 模型名称
            column_x: 特征列名列表
            column_y: 标签列名列表
            height: 矩阵高度，默认30
            width: 矩阵宽度，默认30
        """
        try:
            logger.info(f"开始处理数据: {model_name}, {self.stock_code}")
            data_x = np.zeros([0])  # 初始化特征矩阵
            data_y = np.empty([0])  # 初始化标签矩阵

            # 获取有效的信号数据
            data_ = self.data_15m.dropna(subset=[SignalChoice])
            
            # 按信号周期处理数据
            for st in data_[SignalTimes]:
                # 提取当前周期的特征和标签
                x = self.data_15m[self.data_15m[SignalTimes] == st][column_x].dropna(how='any').tail(height)
                y = self.data_15m[self.data_15m[SignalTimes] == st][column_y].dropna(how='any').tail(1)

                if not x.shape[0] or not y.shape[0]:
                    continue

                # 处理特征数据
                x = pd.concat([x[[Signal]], x], axis=1).to_numpy()
                
                # 填充数据为指定维度矩阵
                h = height - x.shape[0]  # 需要填充的高度
                w = width - x.shape[1]   # 需要填充的宽度
                
                # 计算填充尺寸
                ht, hl = h // 2, h - h // 2  # 上下填充
                wl, wr = w // 2, w - w // 2  # 左右填充
                
                # 使用0填充矩阵
                x = np.pad(x, ((ht, hl), (wr, wl)), 'constant', constant_values=(0, 0))
                x = x.reshape(1, height, width, 1)  # 调整维度
                y = y.to_numpy()  # 转换标签为numpy数组

                # 合并数据
                data_x = x if data_x.shape[0] == 0 else np.append(data_x, x, axis=0)
                data_y = y if data_y.shape[0] == 0 else np.append(data_y, y, axis=0)

            # 保存处理后的数据
            self._save_data(model_name, data_x, data_y)
            logger.info(f"数据处理完成: {model_name}, shape: {data_x.shape}")
            
        except Exception as e:
            logger.error(f"数据处理失败: {model_name}, {self.stock_code}, 错误: {str(e)}")
            raise


class Data15MOriginalCalculate(ModelData):
    """
    15分钟原始数据处理类
    
    主要功能：
    1. 加载和预处理1分钟数据
    2. 计算15分钟技术指标
    3. 生成交易信号
    4. 处理成交量数据
    5. 保存处理结果
    """

    def __init__(self, stock: str, month: str, start_date: str):
        """
        初始化数据处理器
        
        Args:
            stock: 股票代码
            month: 处理月份
            start_date: 起始日期
        """
        super().__init__()
        # 获取股票信息
        self.stock_name, self.stock_code, self.stock_id = Stocks(stock)
        
        # 基础设置
        self.month = month          # 处理月份
        self.freq = '15m'           # 数据频率
        self.start_date = start_date  # 起始日期
        
        # 数据存储
        self.data_1m: Optional[pd.DataFrame] = None     # 1分钟数据
        self.data_15m: Optional[pd.DataFrame] = None    # 15分钟数据
        self.daily_volume_max: Optional[float] = None   # 日成交量最大值
        
        # 记录日期
        self.start_date_1m: Optional[str] = None     # 1分钟数据起始日期
        self.RecordStartDate: Optional[str] = None   # 记录起始日期
        self.RecordEndDate: Optional[str] = None     # 记录结束日期
        
        # 配置
        self.config = DataConfig()  # 数据处理配置

    def data_1m_calculate(self) -> None:
        """
        加载和预处理1分钟数据
        
        主要步骤：
        1. 从数据库加载1分钟数据
        2. 检查数据完整性
        3. 处理缺失值和异常值
        4. 计算基础指标
        """
        try:
            logger.info(f"开始加载1分钟数据: {self.stock_code}")
            
            # 从数据库加载数据
            self.data_1m = load_1m_by_local_file(self.stock_code, self.start_date, )
            
            if self.data_1m is None or len(self.data_1m) == 0:
                raise ValueError(f"无法加载1分钟数据: {self.stock_code}")
            
            # 确保日期列是datetime类型
            self.data_1m['date'] = pd.to_datetime(self.data_1m['date'])
            
            # 按时间排序
            self.data_1m = self.data_1m.sort_values('date')
            
            # 检查并处理缺失值
            missing_values = self.data_1m.isnull().sum()

            if missing_values.any():
                logger.warning(f"发现缺失值:\n{missing_values[missing_values > 0]}")
                # 对于价格，使用前值填充
                price_cols = ['open', 'high', 'low', 'close']
                self.data_1m[price_cols] = self.data_1m[price_cols].fillna(method='ffill')
                # 对于成交量，使用0填充
                self.data_1m['volume'] = self.data_1m['volume'].fillna(0)
            
            # 检查数据连续性
            time_diff = self.data_1m['date'].diff()
            irregular_intervals = time_diff[time_diff != pd.Timedelta(minutes=1)]

            if not irregular_intervals.empty:
                logger.warning(f"发现不规则时间间隔:\n{irregular_intervals}")
            
            # 记录起始日期
            self.start_date_1m = self.data_1m['date'].min().strftime('%Y-%m-%d %H:%M:%S')
            
            # 计算每日成交量最大值
            self._calculate_daily_volume_max()
            
            logger.info(f"1分钟数据加载完成: {self.stock_code}, 数据量: {len(self.data_1m)}")
            
        except Exception as e:
            logger.error(f"加载1分钟数据失败: {self.stock_code}, 错误: {str(e)}")
            raise

    @lru_cache(maxsize=128)
    def find_bar_max_1m(self, x: pd.Timestamp, num: int) -> Optional[int]:
        """
        计算指定时间段内的最大成交量
        
        使用@lru_cache装饰器缓存结果，提高性能
        
        Args:
            x: 时间点
            num: 取前n个最大值的平均
            
        Returns:
            最大成交量值或None（计算失败时）
        """
        try:
            # 计算时间窗口
            start_time = pd.to_datetime(x) + pd.Timedelta(minutes=-15)
            end_time = pd.to_datetime(x)
            
            # 筛选时间段内的数据
            mask = (self.data_1m['date'] > start_time) & (self.data_1m['date'] < end_time)
            
            # 计算最大成交量
            max_vol = (
                self.data_1m[mask]
                .nlargest(num, 'volume')['volume']  # 取前n个最大值
                .mean()  # 计算平均值
            )
            
            return int(max_vol) if pd.notna(max_vol) else None
            
        except Exception as ex:
            logger.error(
                f'{self.stock_name} - find_bar_max_1m 错误: {str(ex)}\n'
                f'时间点: {x}, num: {num}'
            )
            return None

    def _calculate_daily_volume_max(self) -> None:
        """
        计算日成交量最大值
        
        用于数据标准化的基准
        """
        # 加载数据
        _date = '2018-01-01'  # 固定的起始日期
        self.data_1m = StockData1m.load_1m(self.stock_code, _date)
        mask = self.data_1m['date'] > pd.to_datetime(_date)
        self.data_1m = self.data_1m[mask]
        
        # 计算日线数据
        data_daily = ResampleData.resample_1m_data(
            data=self.data_1m, freq='daily')
        
        # 调整时间和计算均值
        data_daily.loc[:, 'date'] = pd.to_datetime(data_daily['date']) + pd.Timedelta(minutes=585)
        data_daily.loc[:, DailyVolEma] = data_daily['volume'].rolling(90, min_periods=1).mean()
        
        # 计算最大值
        self.daily_volume_max = round(data_daily[DailyVolEma].max(), 2)

    def _process_daily_data(self) -> pd.DataFrame:
        """
        处理日线数据
        
        Returns:
            处理后的日线数据，包含日期和成交量解析器
        """
        # 计算日线数据
        data_daily = ResampleData.resample_1m_data(data=self.data_1m, freq='daily')
        data_daily['date'] = pd.to_datetime(data_daily['date']) + pd.Timedelta(minutes=585)
        data_daily[DailyVolEma] = data_daily['volume'].rolling(90, min_periods=1).mean()
        
        # 计算当前最大值
        daily_volume_max = round(data_daily[DailyVolEma].max(), 2)
        
        try:
            # 读取历史数据
            file_name = f"{self.stock_code}.json"
            file_path = find_file_in_paths(self.month, 'json', file_name)
            parser_data = ReadSaveFile.read_json_by_path(file_path)
            pre_daily_volume_max = parser_data[self.stock_code][DailyVolEma]
            
            # 更新最大值
            self.daily_volume_max = max(daily_volume_max, pre_daily_volume_max)

        except Exception as e:
            logger.warning(f"获取历史日成交量数据失败: {str(e)}")
            self.daily_volume_max = daily_volume_max
        
        # 计算成交量解析器值
        data_daily[DailyVolEmaParser] = self.daily_volume_max / data_daily[DailyVolEma]
        return data_daily[['date', DailyVolEmaParser]].set_index('date', drop=True)

    def first_calculate(self) -> pd.DataFrame:
        """
        第一阶段数据处理
        
        主要步骤：
        1. 数据重采样为15分钟
        2. 生成MACD信号
        3. 处理日线数据
        4. 合并数据并处理缺失值
        
        Returns:
            处理后的15分钟数据框
        """
        logger.info(f"开始第一阶段数据处理: {self.stock_code}")
        
        # 重采样和信号生成
        self.data_15m = ResampleData.resample_1m_data(data=self.data_1m, freq=self.freq)
        self.data_15m = SignalMethod.signal_by_MACD_3ema(self.data_15m, self.data_1m).set_index('date', drop=True)
        
        # 处理日线数据
        data_daily = self._process_daily_data()
        self.data_15m = self.data_15m.join([data_daily]).reset_index()
        self.data_15m[DailyVolEmaParser] = self.data_15m[DailyVolEmaParser].fillna(method='ffill')
        
        # 排除最后一个信号周期（可能未完成）
        last_signal_times = self.data_15m.iloc[-1][SignalTimes]
        self.data_15m = self.data_15m[self.data_15m[SignalTimes] != last_signal_times]
        
        logger.info(f"完成第一阶段数据处理: {self.stock_code}")
        return self.data_15m

    def second_calculate(self) -> pd.DataFrame:
        """
        第二阶段数据处理
        
        主要步骤：
        1. 计算每个15分钟K线内的最大成交量
        2. 处理异常值
        3. 保存处理结果
        
        Returns:
            处理后的数据框
        """
        logger.info(f"开始第二阶段数据处理: {self.stock_code}")
        
        # 获取需要处理的数据索引
        valid_indices = self.data_15m.dropna(subset=[SignalChoice, EndPriceIndex]).index
        
        # 处理每个有效周期
        for index in valid_indices:
            signal_times = self.data_15m.loc[index, SignalTimes]
            end_price_time = self.data_15m.loc[index, EndPriceIndex]
            
            # 获取当前周期数据
            mask = ((self.data_15m[SignalTimes] == signal_times) & 
                   (self.data_15m[EndPriceIndex] <= end_price_time))
            selects = self.data_15m[mask].tail(35)
            
            if selects.empty:
                continue
                
            st_index, ed_index = selects.index[0], selects.index[-1]
            
            # 计算最大成交量
            dates = self.data_15m.loc[st_index:ed_index, 'date']
            self.data_15m.loc[st_index:ed_index, Bar1mVolMax1] = dates.apply(
                self.find_bar_max_1m, args=(1,))
            self.data_15m.loc[st_index:ed_index, Bar1mVolMax5] = dates.apply(
                self.find_bar_max_1m, args=(5,))
        
        # 清理异常值
        self.data_15m = self.data_15m.replace([np.inf, -np.inf], np.nan)
        
        # 保存数据
        self._save_15m_data()
        
        logger.info(f"完成第二阶段数据处理: {self.stock_code}")
        return self.data_15m

    def third_calculate(self) -> pd.DataFrame:
        """
        第三阶段数据处理
        
        主要步骤：
        1. 处理成交量相关参数
        2. 处理周期数据（前后周期关系）
        3. 填充缺失值
        
        Returns:
            处理后的数据框
        """
        logger.info(f"开始第三阶段数据处理: {self.stock_code}")
        
        # 转换信号类型
        self.data_15m[Signal] = self.data_15m[Signal].astype(float)
        
        # 处理成交量相关参数
        for col in self.config.VOLUME_COLUMNS:
            self.data_15m[col] = round(
                self.data_15m[col] * self.data_15m[DailyVolEmaParser])
        
        # 获取有效数据条件
        condition = ~self.data_15m[SignalChoice].isnull()
        
        # 处理下一周期数据
        for key, value in self.config.NEXT_CYCLE_MAPPING.items():
            self.data_15m.loc[condition, key] = (
                self.data_15m.loc[condition, value].shift(-1))
        
        # 处理前周期数据
        for key, value in self.config.PRE_CYCLE_MAPPING.items():
            self.data_15m.loc[condition, key] = (
                self.data_15m.loc[condition, value].shift(1))
        
        # 填充缺失值
        fills = list(self.config.PRE_CYCLE_MAPPING.keys()) + list(self.config.NEXT_CYCLE_MAPPING.keys())
        self.data_15m[fills] = self.data_15m[fills].fillna(method='ffill')
        
        logger.info(f"完成第三阶段数据处理: {self.stock_code}")
        return self.data_15m

    def _append_or_update_data(self) -> None:
        """
        追加或更新15分钟数据
        
        处理逻辑：
        1. 如果数据可以直接追加，则追加
        2. 如果发生冲突，则更新已存在的数据
        """
        try:
            # 筛选新数据
            self.data_15m = self.data_15m[self.data_15m['date'] > self.RecordEndDate]
            
            try:
                # 尝试追加数据
                logger.info(f"尝试追加数据: {self.stock_code}")
                StockData15m.append_15m(self.stock_code, self.data_15m)
            except IntegrityError:
                # 处理数据冲突
                logger.warning(f"数据冲突，尝试更新: {self.stock_code}")
                old = StockData15m.load_15m(self.stock_code)
                last_date = old.iloc[-1]['date']
                new = self.data_15m[self.data_15m['date'] > last_date]
                old = pd.concat([old, new], ignore_index=True)
                StockData15m.replace_15m(self.stock_code, old)
                
            logger.info(f"数据更新成功: {self.stock_code}")
            
        except Exception as e:
            logger.error(f"数据更新失败: {self.stock_code}, 错误: {str(e)}")
            raise

    def _save_record_info(self) -> None:
        """
        保存记录信息
        
        保存内容：
        1. 记录结束日期
        2. 结束信号信息
        3. 信号时间信息
        4. 下一个开始日期
        """
        try:
            # 准备记录信息
            record_info = {
                'RecordEndDate': self.data_15m.iloc[-1]['date'].strftime('%Y-%m-%d %H:%M:%S'),
                'RecordEndSignal': self.data_15m.iloc[-1]['Signal'],
                'RecordEndSignalTimes': self.data_15m.iloc[-1]['SignalTimes'],
                'RecordEndSignalStartTime': self.data_15m.iloc[-1]['SignalStartTime'].strftime('%Y-%m-%d %H:%M:%S'),
                'RecordNextStartDate': self.data_15m.drop_duplicates(
                    subset=[SignalTimes]).tail(6).iloc[0]['date'].strftime('%Y-%m-%d %H:%M:%S')
            }
            
            # 更新并保存记录
            records = ReadSaveFile.read_json(self.month, self.stock_code)
            records.update(record_info)
            ReadSaveFile.save_json(records, self.month, self.stock_code)
            
            logger.info(f"记录信息保存成功: {self.stock_code}")
            
        except Exception as e:
            logger.error(f"记录信息保存失败: {self.stock_code}, 错误: {str(e)}")
            raise

    def _save_15m_data(self) -> None:
        """
        保存15分钟数据
        
        包括：
        1. 保存数据到数据库
        2. 更新记录信息
        """
        try:
            # 根据情况选择保存方式
            if self.RecordStartDate:
                self._append_or_update_data()
            else:
                StockData15m.replace_15m(self.stock_code, self.data_15m)
            
            # 保存记录信息
            self._save_record_info()
            
        except Exception as e:
            logger.error(f"保存15分钟数据失败: {self.stock_code}, 错误: {str(e)}")
            raise

    def data_15m_calculate(self) -> pd.DataFrame:
        """
        执行完整的15分钟数据处理流程
        
        处理步骤：
        1. 加载1分钟数据
        2. 第一阶段处理（重采样和信号生成）
        3. 第二阶段处理（成交量计算）
        4. 第三阶段处理（数据标准化）
        
        Returns:
            处理完成的15分钟数据
        """
        try:
            logger.info(f"开始处理股票数据: {self.stock_code}")
            
            # 执行处理流程
            self.data_1m_calculate()
            self.data_15m = self.first_calculate()
            self.data_15m = self.second_calculate()
            self.data_15m = self.third_calculate()
            
            logger.info(f"数据处理完成: {self.stock_code}")
            return self.data_15m
            
        except Exception as e:
            logger.error(f"数据处理失败: {self.stock_code}, 错误: {str(e)}")
            raise


if __name__ == '__main__':
    stock_code = '000001'
 
    data = load_1m_by_local_file(stock_code)
    print(data.head(5))