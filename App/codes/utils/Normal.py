# -*- coding: utf-8 -*-
"""
通用工具函数和类
包含数据重采样、数学公式、股票代码处理等功能
"""
import os
import smtplib
import time
from email.message import Message
from typing import Optional, Dict, Any
import pandas as pd
from scipy import stats
import numpy as np
import json

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


def count_times(func):
    """装饰器：统计函数运行时间"""
    def wrapper(*args, **kwargs):
        start = time.process_time()
        results = func(*args, **kwargs)
        end = time.process_time()
        print(f'运行时间：{int(end - start)}秒\n')
        return results
    return wrapper


class MathematicalFormula:
    """数学公式工具类"""
    
    @classmethod
    def normal_get_p(cls, x, mean=0, std=1):
        """计算正态分布的累积分布函数值"""
        z = (x - mean) / std
        p = stats.norm.cdf(z)
        return p
    
    @classmethod
    def normal_get_x(cls, p, mean=0, std=1):
        """根据累积分布函数值计算对应的x值"""
        z = stats.norm.ppf(p)
        x = z * std + mean
        return x
    
    @classmethod
    def filter_median(cls, data: pd.DataFrame, column: str) -> pd.DataFrame:
        """使用中位数绝对偏差（MAD）过滤异常值"""
        med = data[column].median()
        mad = abs(data[column] - med).median()
        
        high = med + (3 * 1.4826 * mad)
        low = med - (3 * 1.4826 * mad)
        
        data.loc[(data[column] > high), column] = high
        data.loc[(data[column] < low), column] = low
        
        return data
    
    @classmethod
    def filter_3sigma(cls, data: pd.DataFrame, column: str, n=3) -> pd.DataFrame:
        """使用3倍标准差过滤异常值"""
        mean_ = data[column].mean()
        std_ = data[column].std()
        
        max_ = mean_ + n * std_
        min_ = mean_ - n * std_
        
        data.loc[(data[column] > max_), column] = max_
        data.loc[(data[column] < min_), column] = min_
        return data
    
    @classmethod
    def data2normalization(cls, column: pd.Series) -> pd.Series:
        """数据归一化到[0, 1]区间"""
        num_max = column.max()
        num_min = column.min()
        if num_max == num_min:
            return pd.Series([0.5] * len(column), index=column.index)
        column = (column - num_min) / (num_max - num_min)
        return column
    
    @classmethod
    def normal2value(cls, data, parser_month: str, stock_code: str, match_column: str):
        """将归一化值转换回原始值"""
        parser_data = ReadSaveFile.read_json(parser_month, stock_code)
        if parser_data is None:
            raise ValueError(f"无法读取解析数据: {parser_month}, {stock_code}")
        parser_data = parser_data[stock_code][match_column]
        
        high = parser_data['num_max']
        low = parser_data['num_min']
        
        num_normal = data * (high - low) + low
        return num_normal
    
    @classmethod
    def normal2Y(cls, x, mu, sigma):
        """计算正态分布的概率密度函数值"""
        pdf = np.exp(-((x - mu) ** 2) / (2 * sigma ** 2)) / (sigma * np.sqrt(2 * np.pi))
        return pdf


class StockCode:
    """股票代码处理工具类"""
    
    @classmethod
    def stand_code(cls, code: str) -> str:
        """标准化股票代码为6位数字"""
        code = str(code)
        len_ = len(code)
        if len_ < 6:
            _code = '000000'
            code = f'{_code[:(6 - len_)]}{code}'
        else:
            code = code[:6]
        return code
    
    @classmethod
    def code2market(cls, code: str) -> str:
        """根据股票代码判断市场"""
        code = str(code)
        if code[0] == '6':
            return 'SH'
        elif code[0] == '0' or code[0] == '3':
            return 'SZ'
        else:
            print(f'股票: {code}未区分市场类；')
            return 'None'
    
    @classmethod
    def code_with_market(cls, code: str) -> str:
        """为股票代码添加市场后缀"""
        code = str(code)
        if code[0] == '6':
            return f'{code}.SH'
        elif code[0] == '0' or code[0] == '3':
            return f'{code}.SZ'
        else:
            print(f'股票: {code}无市场分类;')
            return code
    
    @classmethod
    def code2classification(cls, code: str) -> Optional[str]:
        """根据股票代码判断分类"""
        code = str(code)
        classification = None
        
        if code[:3] in ['600', '601', '602', '603', '605', '000']:
            classification = '主板'
        elif code[:3] == '002':
            classification = '中小板'
        elif code[:3] == '003':
            classification = '深股峙'
        elif code[:3] in ['688', '689']:
            classification = '科创板'
        elif code[:3] == '300':
            classification = '创业板'
        elif code[:3] in ['900', '200']:
            classification = 'B股'
        elif code[:3] == '880':
            classification = '指数'
        elif code[:2] in ['12', '13', '11']:
            classification = '转债'
        elif code[:2] == '20':
            classification = '债券'
        elif code[:2] in ['15', '16', '50', '51', '56', '58']:
            classification = '基金'
        
        return classification


class ReadSaveFile:
    """文件读写工具类"""
    
    @classmethod
    def read_json(cls, months: str, code: str) -> Optional[Dict]:
        """读取JSON文件"""
        try:
            from config import Config
            # 构建JSON文件路径: App/codes/code_data/RnnData/{months}/json/{code}.json
            project_root = Config.get_project_root()
            path = os.path.join(
                project_root, 
                'App', 'codes', 'code_data', 'RnnData', 
                months, 'json', f'{code}.json'
            )
            with open(path, 'r', encoding='utf-8') as lf:
                return json.load(lf)
        except (ValueError, FileNotFoundError, ImportError) as e:
            print(f"读取JSON文件失败: {e}")
            return None
    
    @classmethod
    def read_json_by_path(cls, path: str) -> Optional[Dict]:
        """根据路径读取JSON文件"""
        try:
            with open(path, 'r', encoding='utf-8') as lf:
                return json.load(lf)
        except (FileNotFoundError, ValueError) as e:
            print(f"读取JSON文件失败: {path}, {e}")
            return None
    
    @classmethod
    def save_json(cls, dic: dict, months: str, code: str) -> bool:
        """保存JSON文件"""
        try:
            from config import Config
            # 构建JSON文件路径: App/codes/code_data/RnnData/{months}/json/{code}.json
            project_root = Config.get_project_root()
            path = os.path.join(
                project_root, 
                'App', 'codes', 'code_data', 'RnnData', 
                months, 'json', f'{code}.json'
            )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(dic, f, ensure_ascii=False, indent=2)
            return True
        except (ImportError, IOError) as e:
            print(f"保存JSON文件失败: {e}")
            return False
    
    @classmethod
    def read_all_file(cls, path: str, ends: str) -> list:
        """读取目录下所有指定后缀的文件"""
        fl = []
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.endswith(ends):
                    fl.append(f)
        return fl
    
    @classmethod
    def find_all_file(cls, path: str) -> list:
        """查找目录下所有文件"""
        fl = []
        for p, dir_list, files in os.walk(path):
            fl.append(files)
        return fl


class ResampleData:
    """数据重采样工具类"""
    
    @classmethod
    def resample_fun(cls, data: pd.DataFrame, parameter: str) -> pd.DataFrame:
        """通用数据重采样函数"""
        # 转换索引为 datetime
        data['index_date'] = pd.to_datetime(data['date'])
        data = data.set_index('index_date')
        
        # 按指定频率重采样
        resampled = data.resample(parameter, closed='right', label='right').agg({
            'open': 'first',
            'close': 'last',
            'high': 'max',
            'low': 'min',
            'volume': 'sum',
            'money': 'sum'
        }).reset_index()
        
        # 重命名列并返回
        resampled = resampled.rename(columns={'index_date': 'date'}).dropna(how='any')
        return resampled[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]
    
    @classmethod
    def _split_and_resample_60m(cls, data: pd.DataFrame) -> pd.DataFrame:
        """
        将股票的1分钟数据转换为60分钟数据，考虑上午和下午的交易时间段
        上午：从09:31到11:30，采样时间点为10:31和11:30
        下午：从13:00到15:00，按整点采样
        """
        # 确保日期为 datetime 类型，并设置为索引
        data["date"] = pd.to_datetime(data["date"])
        data["index_date"] = data["date"]
        data = data.set_index("index_date")
        
        # 分别提取上午和下午数据
        morning_data = data.between_time("09:31", "11:30")
        afternoon_data = data.between_time("13:00", "15:00")
        
        # 上午按 "90T" 采样
        morning_resampled = morning_data.resample("90T", closed="right", label="right").agg({
            "date": "last",
            "open": "first",
            "close": "last",
            "high": "max",
            "low": "min",
            "volume": "sum",
            "money": "sum"
        }).dropna()
        
        # 下午按 "60T" 采样
        afternoon_resampled = afternoon_data.resample("60T", closed="right", label="right").agg({
            "date": "last",
            "open": "first",
            "close": "last",
            "high": "max",
            "low": "min",
            "volume": "sum",
            "money": "sum"
        }).dropna()
        
        # 合并上午和下午数据
        resampled = pd.concat([morning_resampled, afternoon_resampled]).sort_values("date").reset_index(drop=True)
        return resampled
    
    @classmethod
    def _split_and_resample_120m(cls, data: pd.DataFrame) -> pd.DataFrame:
        """将1分钟数据转换为120分钟数据"""
        data["date"] = pd.to_datetime(data["date"])
        data["index_date"] = data["date"]
        data = data.set_index("index_date")
        
        resampled = data.resample("360T", closed="right", label="right").agg({
            "date": "last",
            "open": "first",
            "close": "last",
            "high": "max",
            "low": "min",
            "volume": "sum",
            "money": "sum"
        }).dropna().reset_index(drop=True)
        
        return resampled
    
    @classmethod
    def _resample_to_daily(cls, data: pd.DataFrame) -> pd.DataFrame:
        """将1分钟数据聚合为日K数据"""
        data["date"] = pd.to_datetime(data["date"])
        day_k = data.groupby(data["date"].dt.date).agg(
            open=("open", "first"),
            close=("close", "last"),
            high=("high", "max"),
            low=("low", "min"),
            volume=("volume", "sum"),
            money=("money", "sum")
        ).reset_index()
        
        day_k = day_k.rename(columns={"index": "date"})
        return day_k
    
    @classmethod
    def resample_1m_data(cls, data: pd.DataFrame, freq: str) -> pd.DataFrame:
        """根据指定频率重采样1分钟数据"""
        # 时间映射字典
        time_mappings = {
            '15m': '15T',
            '30m': '30T',
            '120m': '360T',
            'day': '1440T',
            'daily': '1440T',
            'd': '1440T',
            'D': '1440T'
        }
        
        if freq == '60m':
            return cls._split_and_resample_60m(data)
        elif freq == '120m':
            return cls._split_and_resample_120m(data)
        elif freq in {'day', 'daily', 'd', 'D'}:
            return cls._resample_to_daily(data)
        elif freq in time_mappings:
            return cls.resample_fun(data, parameter=time_mappings[freq])
        else:
            raise ValueError(f"不支持的时间频率: {freq}")


class Useful:
    """实用工具类"""
    
    @classmethod
    def sent_emails(cls, message_title: str, mail_content: str) -> bool:
        """发送邮件（使用Gmail SMTP）"""
        try:
            smtpserver = 'smtp.gmail.com'
            username = 'legendtravel004@gmail.com'
            password = 'duooevejgywtaoka'
            from_addr = 'legendtravel004@gmail.com'
            to_addr = ['zhangzhuan516@gmail.com']
            cc_addr = ['651748264@qq.com']
            
            message = Message()
            message['Subject'] = message_title
            message['From'] = from_addr
            message['To'] = ','.join(to_addr)
            message['Cc'] = ','.join(cc_addr)
            message.set_payload(mail_content)
            msg = message.as_string().encode('utf-8')
            
            sm = smtplib.SMTP(smtpserver, port=587, timeout=20)
            sm.set_debuglevel(1)
            sm.ehlo()
            sm.starttls()
            sm.ehlo()
            sm.login(username, password)
            sm.sendmail(from_addr, (to_addr + cc_addr), msg)
            time.sleep(2)
            sm.quit()
            return True
        except Exception as e:
            print(f"发送邮件失败: {e}")
            return False
    
    @classmethod
    def dashed_line(cls, num: int) -> tuple:
        """生成等号和减号线"""
        return '=' * num, '-' * num
    
    @classmethod
    def stock_columns(cls) -> Dict[str, Dict]:
        """
        获取股票列名配置
        已迁移到 config.py，此方法保留以保持向后兼容性
        """
        try:
            from config import Config
            # 从配置中读取，并转换键为整数以保持兼容性
            columns = Config.STOCK_COLUMNS.copy()
            # 转换字符串键为整数键
            result = {}
            for key, value in columns.items():
                result[key] = {int(k): v for k, v in value.items()}
            return result
        except ImportError:
            # 如果无法导入Config，返回默认配置
            return {
                'Basic': {1: 'date', 2: 'open', 3: 'close', 4: 'high', 5: 'low', 6: 'volume', 7: 'money'},
                'Macd': {1: 'EmaShort', 2: 'EmaMid', 3: 'EmaLong', 4: 'Dif', 5: 'DifSm', 6: 'DifMl', 7: 'Dea', 8: 'MACD'},
                'Boll': {1: 'BollMid', 2: 'BollStd', 3: 'BollUp', 4: 'BollDn', 5: 'StopLoss'},
                'Signal': {1: 'Signal', 2: 'SignalTimes', 3: 'SignalChoice', 4: 'SignalStartIndex'},
                'cycle': {
                    1: 'EndPrice', 2: 'EndPriceIndex', 3: 'StartPrice', 4: 'StartPriceIndex',
                    5: 'Cycle1mVolMax1', 6: 'Cycle1mVolMax5', 7: 'Bar1mVolMax1', 8: 'Bar1mVolMax5',
                    9: 'CycleLengthMax', 10: 'CycleLengthPerBar', 11: 'CycleAmplitudePerBar', 12: 'CycleAmplitudeMax'
                },
                'Signal30m': {1: '30mSignal', 2: '30mSignalChoice', 3: '30mSignalTimes'},
                'Signal120m': {1: '120mSignal', 2: '120mSignalChoice', 3: '120mSignalTimes'},
                'SignalDaily': {1: 'Daily1mVolMax1', 2: 'Daily1mVolMax5', 3: 'Daily1mVolMax15', 4: 'DailyVolEmaParser'}
            }


def rename_and_merge_csv_files(folder_path: str) -> None:
    """
    遍历指定目录及其子目录，将所有 .csv.csv 文件重命名为 .csv
    如果目标文件已存在，则将两个文件内容合并（基于去重）
    
    参数:
        folder_path (str): 根目录路径
    """
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.csv.csv'):
                old_path = os.path.join(root, file)
                new_path = os.path.join(root, file.replace('.csv.csv', '.csv'))
                
                if os.path.exists(new_path):
                    # 合并两个文件的内容
                    print(f"合并文件: {old_path} -> {new_path}")
                    old_data = pd.read_csv(old_path)
                    existing_data = pd.read_csv(new_path)
                    combined_data = pd.concat([existing_data, old_data]).drop_duplicates()
                    combined_data.to_csv(new_path, index=False)
                    os.remove(old_path)
                else:
                    # 重命名文件
                    os.rename(old_path, new_path)
                    print(f"重命名: {old_path} -> {new_path}")


if __name__ == '__main__':
    # 测试代码
    pass
