# -*- coding: utf-8 -*-
import time
import json
import re
from selenium import webdriver
from bs4 import BeautifulSoup as soup
import pandas as pd
from download_utils import page_source
from download_utils import UrlCode
from App.codes.RnnDataFile.parser import my_headers, my_url
import logging
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, time as dt_time

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@dataclass
class DataValidationConfig:
    """数据验证配置"""
    REQUIRED_COLUMNS = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
    MIN_ROWS = 1
    TRADING_START_TIME = dt_time(9, 30)
    TRADING_END_TIME = dt_time(15, 0)
    

def show_download(freq: str, code: str) -> None:
    """
    使用 logging 显示下载成功的信息。

    参数:
        freq (str): 数据频率，如 '1m'。
        code (str): 股票代码。
    """
    logging.info(f'Success Download {freq} Data: {code}')


def return_FundsData(source: str, date_new: pd.Timestamp, table_index=1, relevant_columns=None) -> pd.DataFrame:
    """
    从网页源代码中提取并处理资金数据。

    参数: source (str): 包含HTML表格数据的网页源代码。 date_new (pd.Timestamp): 交易日期，会转换为datetime格式并添加到数据框中。 table_index (int,
    optional): 指定要读取的表格索引，默认值为1。 relevant_columns (list of str, optional): 返回的数据框中所需的列名，默认值为['trade_date',
    'stock_code', 'stock_name', 'industry']。

    返回:
        pd.DataFrame: 包含处理后的资金数据的数据框。
                       包括指定的列（默认为['trade_date', 'stock_code', 'stock_name', 'industry']）。
    """

    if relevant_columns is None:
        relevant_columns = ['trade_date', 'stock_code', 'stock_name', 'industry']

    # 读取指定索引的数据表
    df = pd.read_html(source)[table_index]

    # 定义所有列名
    columns = ['序号', 'stock_code', 'stock_name', '相关', '今日收盘价', '今日涨跌幅', '今日持股股数',
               '今日持股市值', '今日持股占流通股比', '今日持股占总股本比', '今日增持股数',
               '今日增持市值', '今日增持市值增幅', '今日增持占流通股比', '今日增持占总股本比', 'industry']

    # 为DataFrame赋予列名
    df.columns = columns

    # 添加交易日期列
    df['trade_date'] = pd.to_datetime(date_new)

    # 只选择需要的列并返回
    result_df = df[relevant_columns]

    # 确保stock_code为字符串类型
    result_df['stock_code'] = result_df['stock_code'].astype(str)

    return result_df


def convert_currency_unit(unit: str):
    """
    货币单位转换函数 (Unit conversion)

    参数:
        x (str): 货币单位，支持 '亿', '万', '百万', '千万' 等。

    返回:
        int: 对应货币单位的数值，如果输入单位不匹配，返回1。
    """

    # 使用字典映射单位与数值
    unit_mapping = {
        '亿': 100000000,
        '万': 10000,
        '百万': 1000000,
        '千万': 10000000}

    # 返回对应的数值，如果单位不匹配，返回1

    return unit_mapping.get(unit, 1)


def funds_data_clean(data):
    """
        清理并转换资金数据。

        参数:
            code_data (pd.DataFrame): 输入的数据框，包含原始资金数据。

        返回:
            pd.DataFrame: 清理并转换后的资金数据。
        """
    # 将数据转为DataFrame并选择所需的列
    data = pd.DataFrame(data.values).iloc[:, [1, 5, 6, 7, 9, 12]]

    # 重命名列
    data.columns = ['板块', 'NkPT市值', 'NkPT占板块比', 'NkPT占北向资金比', 'NRPT市值', 'NRPT占北向资金比']

    # 定义一个函数来处理单位转换和数值计算
    def convert_value(value):
        unit = value[-1]  # 提取单位
        number = float(value[:-1])  # 提取数字部分并转换为浮点数
        return number * convert_currency_unit(unit)

    # 应用转换函数到NkPT市值和NRPT市值列
    data['NkPT市值'] = data['NkPT市值'].apply(convert_value)
    data['NRPT市值'] = data['NRPT市值'].apply(convert_value)

    return data


def parse_json(source: str, match: bool) -> list:
    """
    解析 JSON 数据。

    参数:
        source (str): 原始数据字符串。
        match (bool): 是否进行正则匹配。

    返回:
        list: 解析后的数据列表。如果解析失败，返回空列表。
    """
    try:
        if not source:
            logging.error("收到空的源数据")
            return []

        if match:
            # 使用更精确的正则表达式来提取JSON数据
            pattern = re.compile(r'jQuery\d+_\d+\((.*)\)')
            matches = re.findall(pattern, source)
            if not matches:
                logging.error(f"正则匹配失败，源数据前200字符: {source[:200]}...")
                return []
            json_str = matches[0]
            logging.debug(f"正则匹配成功，提取的JSON字符串: {json_str[:200]}...")
        else:
            json_str = source
            
        # 尝试解析 JSON
        try:
            data = json.loads(json_str)
            logging.debug(f"JSON解析成功，数据结构: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
            print(f"[DEBUG] JSON解析成功，顶层字段: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
        except json.JSONDecodeError as e:
            logging.error(f"JSON解析错误: {e}, 源数据片段: {json_str[:200]}...")
            return []
            
        # 检查数据结构
        if not isinstance(data, dict):
            logging.error(f"数据格式错误，预期dict，实际: {type(data)}")
            return []
            
        if 'data' not in data:
            logging.error("数据中缺少'data'字段")
            print(f"[DEBUG] 数据中缺少'data'字段，可用字段: {list(data.keys())}")
            return []
        
        # 打印data字段的详细信息用于调试
        print(f"[DEBUG] data字段类型: {type(data['data'])}, 内容: {str(data['data'])[:500] if isinstance(data['data'], dict) else str(data['data'])[:200]}")
        if isinstance(data['data'], dict):
            print(f"[DEBUG] data字段包含的键: {list(data['data'].keys())}")
        
        # 检查 data['data'] 是否为 None
        if data['data'] is None:
            rc = data.get('rc', 0)
            rt = data.get('rt', 0)
            logging.error(f"data字段为None，API返回 rc={rc}, rt={rt}")
            print(f"[DEBUG] data字段为None，API响应: {json.dumps(data, ensure_ascii=False)[:500]}")
            
            # 检查API返回码
            # rc: 返回码，0表示成功，非0表示错误
            # rt: 响应类型，通常0表示成功
            if rc != 0 or rt != 0:
                error_msg = f"API返回错误: rc={rc}, rt={rt}"
                if rc == 100:
                    error_msg += " (可能是数据不存在或板块代码无效)"
                logging.error(error_msg)
                print(f"[DEBUG] {error_msg}")
            return []
            
        # 尝试不同的数据字段名称
        # 注意：板块数据可能使用不同的字段名，如 'trends' 或 'klines'
        data_fields = ['klines', 'trends', 'data']
        result_data = None
        
        for field in data_fields:
            if isinstance(data['data'], dict) and field in data['data']:
                field_value = data['data'][field]
                # 检查字段值是否有效（非空且是列表类型）
                if isinstance(field_value, list) and len(field_value) > 0:
                    result_data = field_value
                    logging.info(f"找到有效数据字段: {field}，包含 {len(result_data)} 条记录")
                    print(f"[DEBUG] 找到有效数据字段: {field}，包含 {len(result_data)} 条记录")
                    break
                elif isinstance(field_value, list) and len(field_value) == 0:
                    logging.warning(f"找到数据字段 {field}，但为空列表")
                    print(f"[DEBUG] 找到数据字段 {field}，但为空列表")
                else:
                    logging.warning(f"找到数据字段 {field}，但类型或格式不正确: {type(field_value)}")
                    print(f"[DEBUG] 找到数据字段 {field}，但类型或格式不正确: {type(field_value)}")
        
        # 如果没找到标准字段，检查是否是板块数据（可能包含其他字段）
        if result_data is None:
            available_fields = list(data['data'].keys())
            logging.warning(f"数据中缺少标准数据字段，可用字段: {available_fields}")
            
            # 检查是否是板块数据（板块数据可能没有trends字段，需要特殊处理）
            # 板块数据的data字段可能包含：code, market, type, status, name等元数据
            # 但实际的K线数据可能在trends或klines字段中，或者需要从其他地方获取
            if 'code' in data['data'] and 'market' in data['data']:
                # 这可能是板块数据，但trends字段可能为空或不存在
                logging.warning(f"检测到板块数据（code={data['data'].get('code')}, market={data['data'].get('market')}），但未找到trends/klines数据")
                # 尝试检查是否有其他可能包含数据的字段
                for key in ['trends', 'klines', 'data', 'list']:
                    if key in data['data'] and isinstance(data['data'][key], list) and len(data['data'][key]) > 0:
                        result_data = data['data'][key]
                        logging.info(f"在板块数据中找到数据字段: {key}")
                        break
            
            if result_data is None:
                logging.error(f"无法从数据中提取trends/klines数据，可用字段: {available_fields}")
                return []
            
        logging.info(f"成功提取数据，包含 {len(result_data)} 条记录")
        return result_data

    except Exception as e:
        logging.error(f"解析JSON过程发生异常: {e}", exc_info=True)
        return []


def process_data(df: pd.DataFrame, multiple: bool) -> pd.DataFrame:
    """
    处理并清理数据。

    参数:
        df (pd.DataFrame): 原始数据框。
        multiple (bool): 是否将09:30数据合并为09:31数据。

    返回:
        pd.DataFrame: 清理后的数据框。
    """
    if df.empty:
        return df

    # 转换数据类型
    df['date'] = pd.to_datetime(df['date'])
    df[['open', 'close', 'high', 'low', 'volume', 'money']] = df[
        ['open', 'close', 'high', 'low', 'volume', 'money']].astype(float)
    df['volume'] = (df['volume'] * 100).astype('int64')
    df[['volume', 'money']] = df[['volume', 'money']].astype('int64')

    if multiple:
        # 添加日期部分用于分组
        df['day'] = df['date'].dt.date
        df['time'] = df['date'].dt.time

        # 合并09:30数据到09:31
        df.loc[df['time'] == pd.Timestamp('09:30:00').time(), 'time'] = pd.Timestamp('09:31:00').time()

        # 按日期和时间进行分组聚合
        grouped = df.groupby(['day', 'time']).agg({
            'date': 'last',
            'open': 'first',
            'close': 'last',
            'high': 'max',
            'low': 'min',
            'volume': 'sum',
            'money': 'sum'
        }).reset_index(drop=False)

        grouped = grouped.drop(columns=['day', 'time'])

        return grouped

    else:
        # 处理单日数据的情况
        # 合并09:30 数据 到09:31
        if df.iloc[0]['date'].time() == pd.Timestamp('09:30:00').time():

            df.loc[1, ['volume', 'money']] += df.loc[0, ['volume', 'money']]

            df = df.iloc[1:].reset_index(drop=True)

        return df


def get_1m_data(source: str, match: bool = False, multiple: bool = False) -> pd.DataFrame:
    """
    获取1分钟数据并进行清理。

    参数:
        source (str): 原始数据源。
        match (bool): 是否进行正则匹配。
        multiple (bool): 是否将09:30数据合并为09:31数据。

    返回:
        pd.DataFrame: 清理后的数据框。如果处理失败，返回空的DataFrame。
    """
    try:
        # 添加源数据验证
        if not source or not isinstance(source, str):
            logging.error(f"无效的源数据格式: {type(source)}")
            return pd.DataFrame()
            
        logging.info(f"开始解析数据，数据长度: {len(source)}")
        logging.debug(f"数据样本: {source[:200]}...")  # 记录数据样本

        # 解析JSON数据
        print(f"[DEBUG] 开始解析JSON数据，match={match}")
        trends = parse_json(source, match)
        print(f"[DEBUG] JSON解析结果: trends数量={len(trends) if trends else 0}")
        
        if not trends:
            print(f"[DEBUG] 解析JSON数据失败，未获取到trends数据")
            logging.error("解析JSON数据失败，未获取到trends数据")
            return pd.DataFrame()

        logging.info(f"成功解析JSON数据，获取到 {len(trends)} 条记录")
        print(f"[DEBUG] 成功解析JSON数据，获取到 {len(trends)} 条记录")

        # 创建DataFrame
        try:
            # 检查第一条记录有多少列，以确定正确的列数
            if trends and len(trends) > 0:
                first_row_cols = len(trends[0].split(','))
                print(f"[DEBUG] 第一条记录列数: {first_row_cols}")
                logging.info(f"检测到数据列数: {first_row_cols}")
                
                # 根据列数定义列名
                if first_row_cols == 7:
                    columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
                elif first_row_cols == 8:
                    # 板块数据可能有8列，最后一列可能是均价或其他字段
                    columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money', 'avg_price']
                else:
                    logging.warning(f"意外的列数: {first_row_cols}，使用默认列名")
                    columns = [f'col_{i+1}' for i in range(first_row_cols)]
            else:
                columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
            
            df = pd.DataFrame([x.split(',') for x in trends], columns=columns)
            logging.info(f"成功创建DataFrame，形状: {df.shape}，列: {df.columns.tolist()}")
            print(f"[DEBUG] DataFrame列: {df.columns.tolist()}")
        except Exception as e:
            logging.error(f"创建DataFrame失败: {e}")
            print(f"[DEBUG] 创建DataFrame异常: {e}")
            # 尝试显示第一条记录以便调试
            if trends and len(trends) > 0:
                print(f"[DEBUG] 第一条记录: {trends[0]}")
                print(f"[DEBUG] 第一条记录分割后: {trends[0].split(',')}")
            return pd.DataFrame()

        # 处理数据
        try:
            data = process_data(df, multiple)
            if not data.empty:
                logging.info(f"数据处理成功，最终数据形状: {data.shape}")
                logging.debug(f"数据样本:\n{data.head()}")
            return data
        except Exception as e:
            logging.error(f"数据处理失败: {e}")
            return pd.DataFrame()

    except Exception as e:
        logging.error(f"get_1m_data处理过程发生异常: {e}", exc_info=True)
        return pd.DataFrame()

class HeaderRotator:
    """请求头轮换器 - 管理多个请求头的轮换"""
    
    def __init__(self, header_type: str = 'stock_1m_multiple_days'):
        from config import Config
        self.headers_pool = Config.get_eastmoney_headers_pool(header_type)
        self.current_index = 0
        self.failed_indices = set()  # 记录失败的请求头索引
        logging.info(f"初始化请求头池，共 {len(self.headers_pool)} 个请求头")
    
    def get_next_header(self):
        """获取下一个请求头"""
        if len(self.failed_indices) >= len(self.headers_pool):
            # 所有请求头都失败了，重置失败记录
            logging.warning("所有请求头都已失败，重置请求头池")
            self.failed_indices.clear()
            self.current_index = 0
        
        # 跳过已失败的请求头
        attempts = 0
        while self.current_index in self.failed_indices and attempts < len(self.headers_pool):
            self.current_index = (self.current_index + 1) % len(self.headers_pool)
            attempts += 1
        
        header = self.headers_pool[self.current_index].copy()
        user_agent = header.get('User-Agent', 'Unknown')
        logging.info(f"使用请求头 #{self.current_index + 1}/{len(self.headers_pool)}: {user_agent[:50]}...")
        return header
    
    def mark_current_failed(self):
        """标记当前请求头失败"""
        self.failed_indices.add(self.current_index)
        logging.warning(f"标记请求头 #{self.current_index + 1} 为失败状态")
    
    def rotate(self):
        """轮换到下一个请求头"""
        self.current_index = (self.current_index + 1) % len(self.headers_pool)
        logging.info(f"轮换到下一个请求头 #{self.current_index + 1}")
    
    def get_current_header(self):
        """获取当前请求头"""
        return self.headers_pool[self.current_index].copy()
    
    def reset(self):
        """重置轮换器状态"""
        self.current_index = 0
        self.failed_indices.clear()
        logging.info("重置请求头轮换器")


class DownloadData:
    """
    从东方财富下载数据
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    
    # 类级别的请求头轮换器（按类型存储）
    _header_rotators = {}
    
    @classmethod
    def get_header_rotator(cls, header_type: str = 'stock_1m_multiple_days'):
        """获取或创建请求头轮换器"""
        if header_type not in cls._header_rotators:
            cls._header_rotators[header_type] = HeaderRotator(header_type)
        return cls._header_rotators[header_type]

    @classmethod
    def validate_data(cls, df: pd.DataFrame) -> bool:
        """
        验证下载的数据是否有效
        
        参数:
            df (pd.DataFrame): 待验证的数据框
            
        返回:
            bool: 数据是否有效
        """
        if df.empty:
            logging.error("数据为空")
            return False
            
        # 检查必需列
        missing_cols = set(DataValidationConfig.REQUIRED_COLUMNS) - set(df.columns)
        if missing_cols:
            logging.error(f"缺少必需列: {missing_cols}")
            return False
            
        # 检查数据行数
        if len(df) < DataValidationConfig.MIN_ROWS:
            logging.error(f"数据行数不足: {len(df)}")
            return False
            
        # 检查时间范围
        if 'date' in df.columns:
            times = pd.to_datetime(df['date']).dt.time
            valid_times = times.between(
                DataValidationConfig.TRADING_START_TIME,
                DataValidationConfig.TRADING_END_TIME
            )
            if not valid_times.all():
                logging.error("存在交易时间范围外的数据")
                return False
                
        return True

    @classmethod
    def stock_1m_1day(cls, code: str) -> Optional[pd.DataFrame]:
        """
        从东方财富网下载 1 day , 1分钟股票数据。

        参数:
            code (str): 股票代码。

        返回:
            Optional[pd.DataFrame]: 下载的股票数据。如果下载失败，返回None。
        """
        import requests
        import json
        import re
        import time
        import pandas as pd
        from typing import Optional
        
        def get_stock_data(code: str) -> Optional[pd.DataFrame]:
            try:
                # 设置请求头
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Referer': 'http://quote.eastmoney.com/center/gridlist.html',
                }

                # 构建URL和参数
                timestamp = int(time.time() * 1000)
                params = {
                    'cb': f'jQuery112409537552066923317_{timestamp}',
                    'secid': f'0.{code}',
                    'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                    'fields1': 'f1,f2,f3,f4,f5,f6',
                    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
                    'klt': '1',
                    'fqt': '1',
                    'end': '20500101',
                    'lmt': '1000',
                    '_': timestamp
                }

                # 发送请求
                url = "http://83.push2.eastmoney.com/api/qt/stock/kline/get"
                response = requests.get(url, params=params, headers=headers, timeout=15)
                response.raise_for_status()

                # 提取JSON数据
                text = response.text
                logging.debug(f"原始响应数据: {text[:200]}")
                json_str = re.search(r'jQuery\d+_\d+\((.*?)\)', text)
                if not json_str:
                    logging.error("无法提取JSON数据")
                    return None

                # 解析JSON
                data = json.loads(json_str.group(1))
                if not data.get('data', {}).get('klines'):
                    logging.error("数据结构中没有klines数据")
                    return None

                # 处理数据
                klines = data['data']['klines']
                logging.info(f"获取到 {len(klines)} 条K线数据")
                
                # 将数据分割成列
                df_data = [item.split(',') for item in klines]
                if not df_data:
                    logging.error("分割数据后为空")
                    return None
                    
                # 检查数据列数
                columns_count = len(df_data[0])
                logging.info(f"数据列数: {columns_count}")
                
                # 创建DataFrame
                df = pd.DataFrame(df_data)
                
                # 根据实际列数设置列名
                all_columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money', 
                             'amplitude', 'change_percent', 'change_amount', 'turnover']
                df.columns = all_columns[:columns_count]
                
                logging.info(f"列名: {df.columns.tolist()}")
                
                # 转换数据类型
                try:
                    df['date'] = pd.to_datetime(df['date'])
                    numeric_columns = ['open', 'close', 'high', 'low']
                    for col in numeric_columns:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    if 'volume' in df.columns:
                        df['volume'] = pd.to_numeric(df['volume'], errors='coerce') * 100
                    if 'money' in df.columns:
                        df['money'] = pd.to_numeric(df['money'], errors='coerce')
                    
                    # 转换其他可能的数值列
                    optional_numeric = ['amplitude', 'change_percent', 'change_amount', 'turnover']
                    for col in optional_numeric:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                            
                    logging.info("数据类型转换成功")
                    
                except Exception as e:
                    logging.error(f"数据类型转换失败: {str(e)}")
                    return None
                
                # 确保至少包含必要的列
                required_columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
                if not all(col in df.columns for col in required_columns):
                    logging.error(f"缺少必要的列，当前列: {df.columns.tolist()}")
                    return None
                
                # 只返回必要的列
                return df[required_columns]

            except Exception as e:
                logging.error(f"获取数据失败: {str(e)}")
                return None

        # 主处理逻辑
        for attempt in range(cls.MAX_RETRIES):
            try:
                logging.info(f"开始下载股票 {code} 的1分钟数据 (尝试 {attempt + 1}/{cls.MAX_RETRIES})")
                
                df = get_stock_data(code)
                if df is not None and not df.empty:
                    logging.info(f"成功下载并解析 {code} 的1分钟数据，共 {len(df)} 条记录")
                    show_download('1m', code)
                    return df
                
                logging.error(f"获取数据失败或数据为空: {code}")
                if attempt < cls.MAX_RETRIES - 1:
                    time.sleep(cls.RETRY_DELAY)
                    
            except Exception as e:
                logging.error(f"处理过程发生异常 {code}: {str(e)}")
                if attempt < cls.MAX_RETRIES - 1:
                    time.sleep(cls.RETRY_DELAY)
                continue

        logging.error(f"在 {cls.MAX_RETRIES} 次尝试后仍然失败: {code}")
        return None

    @classmethod
    def _get_source_with_selenium(cls, url: str) -> str:
        """
        使用 Selenium 获取页面源代码（降级方案）
        
        参数:
            url (str): 请求的URL
            
        返回:
            str: 页面源代码
        """
        driver = None
        try:
            print("[DEBUG] 开始导入 Selenium 模块...")
            logging.info("🔧 开始初始化 Selenium...")
            
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            import time
            import random
            
            print("[DEBUG] Selenium 模块导入成功")
            logging.info("✓ Selenium 模块导入成功")
            
            # 从配置读取设置
            from config import Config
            use_headless = Config.DOWNLOAD_CONFIG.get('selenium_headless', True)
            
            # 配置 Chrome 选项
            chrome_options = Options()
            if use_headless:
                chrome_options.add_argument('--headless')  # 无头模式
                chrome_options.add_argument('--headless=new')  # 新版 Chrome 的无头模式
                logging.info("Selenium 使用无头模式")
                print("[DEBUG] Selenium 使用无头模式")
            else:
                logging.info("Selenium 使用有头模式（可见浏览器窗口）")
                print("[DEBUG] Selenium 使用有头模式")
            
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument('--window-size=1920,1080')  # 设置窗口大小
            chrome_options.add_argument('--ignore-certificate-errors')  # 忽略证书错误
            chrome_options.add_argument('--disable-extensions')  # 禁用扩展
            
            print("[DEBUG] Chrome 选项配置完成")
            
            # 随机选择一个 User-Agent
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15'
            ]
            chrome_options.add_argument(f'user-agent={random.choice(user_agents)}')
            
            # 添加更多反检测措施
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            print("[DEBUG] 正在创建 Chrome 驱动...")
            logging.info("🚀 正在启动 Chrome 浏览器...")
            
            # 创建驱动（优先使用 webdriver-manager 自动管理版本）
            try:
                # 方法1: 尝试使用 webdriver-manager 自动管理 ChromeDriver
                try:
                    from selenium.webdriver.chrome.service import Service
                    from webdriver_manager.chrome import ChromeDriverManager
                    
                    print("[DEBUG] 使用 webdriver-manager 自动管理 ChromeDriver 版本...")
                    logging.info("📦 使用 webdriver-manager 自动下载匹配的 ChromeDriver...")
                    
                    service = Service(ChromeDriverManager().install())
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    
                    print("[DEBUG] Chrome 驱动创建成功（使用 webdriver-manager）")
                    logging.info("✓ Chrome 浏览器启动成功（自动版本管理）")
                    
                except ImportError:
                    # 方法2: 如果没有安装 webdriver-manager，使用系统 ChromeDriver
                    print("[DEBUG] webdriver-manager 未安装，尝试使用系统 ChromeDriver...")
                    logging.warning("⚠️ webdriver-manager 未安装，尝试使用系统 ChromeDriver")
                    logging.warning("💡 建议安装: pip install webdriver-manager")
                    
                    driver = webdriver.Chrome(options=chrome_options)
                    print("[DEBUG] Chrome 驱动创建成功（使用系统 ChromeDriver）")
                    logging.info("✓ Chrome 浏览器启动成功（系统 ChromeDriver）")
                    
            except Exception as driver_error:
                print(f"[DEBUG] Chrome 驱动创建失败: {driver_error}")
                logging.error(f"❌ Chrome 驱动创建失败: {driver_error}")
                
                # 检查是否是版本不匹配问题
                error_str = str(driver_error)
                if "version" in error_str.lower() and "chrome" in error_str.lower():
                    logging.error("=" * 60)
                    logging.error("🔧 ChromeDriver 版本不匹配！")
                    logging.error("=" * 60)
                    logging.error("解决方案（选择其一）：")
                    logging.error("")
                    logging.error("方案 1 - 自动管理（推荐）:")
                    logging.error("  pip install webdriver-manager")
                    logging.error("  重启程序即可自动下载匹配的 ChromeDriver")
                    logging.error("")
                    logging.error("方案 2 - 手动下载:")
                    logging.error("  1. 查看 Chrome 版本: chrome://version")
                    logging.error("  2. 下载对应版本 ChromeDriver:")
                    logging.error("     https://googlechromelabs.github.io/chrome-for-testing/")
                    logging.error("  3. 替换旧的 chromedriver.exe")
                    logging.error("=" * 60)
                else:
                    logging.error("请检查：")
                    logging.error("  1. 是否已安装 Chrome 浏览器")
                    logging.error("  2. 是否已安装 ChromeDriver")
                    logging.error("  3. ChromeDriver 版本是否与 Chrome 版本匹配")
                    logging.error("  4. ChromeDriver 是否在系统 PATH 中")
                
                raise
            
            # 设置页面加载超时
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)
            print("[DEBUG] 设置超时时间完成")
            
            # 执行反检测脚本
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                '''
            })
            
            print(f"[DEBUG] Selenium 访问 URL: {url}")
            logging.info(f"🌐 Selenium 访问 URL: {url}")
            
            try:
                driver.get(url)
                print("[DEBUG] 页面加载完成")
                logging.info("✓ 页面加载完成")
            except Exception as load_error:
                print(f"[DEBUG] 页面加载失败: {load_error}")
                logging.error(f"❌ 页面加载失败: {load_error}")
                raise
            
            # 随机等待，模拟人类行为
            wait_time = random.uniform(3, 6)
            print(f"[DEBUG] 等待 {wait_time:.1f} 秒加载页面...")
            logging.info(f"⏳ 等待 {wait_time:.1f} 秒加载页面...")
            time.sleep(wait_time)
            
            # 获取页面源码
            print("[DEBUG] 获取页面源码...")
            page_source = driver.page_source
            print(f"[DEBUG] 获取到页面源码，长度: {len(page_source) if page_source else 0}")
            print(f"[DEBUG] 页面源码前200字符: {page_source[:200] if page_source else 'None'}")
            
            if not page_source or len(page_source) < 100:
                logging.error("❌ Selenium 获取的页面源码为空或过短")
                print("[DEBUG] 页面源码为空或过短")
                return ""
            
            # 检查是否是 HTML 错误页面（应该是 JSON 或 JSONP）
            if page_source.strip().startswith('<'):
                logging.error("❌ Selenium 返回的是 HTML 页面，而不是 JSON 数据")
                logging.error("说明：东方财富网检测到了自动化访问")
                print("[DEBUG] 返回的是 HTML 页面，不是 JSON 数据")
                print(f"[DEBUG] HTML 内容前500字符: {page_source[:500]}")
                
                # 尝试从 HTML 中提取错误信息
                if "访问异常" in page_source or "Access Denied" in page_source:
                    logging.error("❌ 访问被拒绝，可能是反爬虫机制")
                
                return ""
            
            # 检查是否包含预期的数据
            if 'jQuery' in page_source or '{' in page_source:
                logging.info(f"✓ Selenium 成功获取页面源码，长度: {len(page_source)}")
                print(f"[DEBUG] 页面内容看起来是 JSON/JSONP 数据")
                return page_source
            else:
                logging.error("❌ 页面内容格式不正确")
                print("[DEBUG] 页面内容格式不正确")
                return ""
        
        except ImportError as ie:
            print(f"[DEBUG] 导入错误: {ie}")
            logging.error(f"❌ Selenium 模块导入失败: {str(ie)}")
            logging.error("请安装 Selenium: pip install selenium")
            return ""
                
        except Exception as e:
            print(f"[DEBUG] Selenium 异常: {type(e).__name__}: {str(e)}")
            logging.error(f"❌ Selenium 获取数据失败: {str(e)}")
            import traceback
            error_trace = traceback.format_exc()
            print(f"[DEBUG] 异常堆栈:\n{error_trace}")
            logging.error(f"异常堆栈: {error_trace}")
            
            # 提供更具体的错误提示
            if "chromedriver" in str(e).lower() or "chrome" in str(e).lower():
                logging.error("=" * 60)
                logging.error("ChromeDriver 相关错误，请尝试以下解决方案：")
                logging.error("1. 安装 ChromeDriver:")
                logging.error("   - 下载地址: https://chromedriver.chromium.org/downloads")
                logging.error("   - 或使用: pip install webdriver-manager")
                logging.error("2. 确保 Chrome 浏览器已安装")
                logging.error("3. 确保 ChromeDriver 版本与 Chrome 版本匹配")
                logging.error("4. 将 ChromeDriver 添加到系统 PATH")
                logging.error("=" * 60)
            
            return ""
        finally:
            # 确保关闭浏览器
            if driver:
                try:
                    driver.quit()
                    logging.info("Selenium 浏览器已关闭")
                except:
                    pass
    
    @classmethod
    def _get_source_with_rotation(cls, url: str, header_type: str = 'stock_1m_multiple_days', use_selenium_fallback: bool = None) -> str:
        """
        使用请求头获取页面源代码，失败时立即降级到 Selenium
        
        参数:
            url (str): 请求的URL
            header_type (str): 请求头类型
            use_selenium_fallback (bool): 是否启用 Selenium 降级，None则从配置读取
            
        返回:
            str: 页面源代码
        """
        # 从配置读取是否启用 Selenium 降级
        if use_selenium_fallback is None:
            from config import Config
            use_selenium_fallback = Config.DOWNLOAD_CONFIG.get('use_selenium_fallback', True)
        
        rotator = cls.get_header_rotator(header_type)
        
        # 子阶段 2.1：尝试1个请求头
        logging.info(f"尝试使用 HTTP 请求（请求头轮换，类型: {header_type}）")
        print(f"[DEBUG] 使用请求头类型: {header_type}")
        
        # 获取当前请求头
        headers = rotator.get_next_header()
        
        try:
            content = cls._get_source(url, headers)
            if content and len(content) > 100:  # 确保获取到有效内容
                logging.info(f"✓ 使用请求头 #{rotator.current_index + 1} 成功获取数据")
                rotator.rotate()  # 成功后轮换到下一个，让下次使用不同的请求头
                return content
            else:
                logging.warning(f"✗ 请求头 #{rotator.current_index + 1} 返回空内容")
        except Exception as e:
            logging.error(f"✗ 请求头 #{rotator.current_index + 1} 请求失败: {e}")
            rotator.mark_current_failed()
        
        logging.warning("HTTP 请求失败")
        rotator.rotate()  # 失败后轮换到下一个
        
        # 子阶段 2.2：立即降级到 Selenium
        if use_selenium_fallback:
            print("")
            print("[DEBUG] ------------------------------------------------------------")
            print("[DEBUG] 降级使用 Selenium 浏览器模拟")
            print("[DEBUG] ------------------------------------------------------------")
            logging.info("")
            logging.warning("降级使用 Selenium 获取数据")
            
            try:
                print("[DEBUG] 调用 _get_source_with_selenium...")
                content = cls._get_source_with_selenium(url)
                print(f"[DEBUG] _get_source_with_selenium 返回内容长度: {len(content) if content else 0}")
                
                if content and len(content) > 100:
                    logging.info("✅ Selenium 成功获取数据")
                    print("[DEBUG] Selenium 成功获取数据")
                    return content
                else:
                    logging.error("❌ Selenium 获取失败或数据为空")
                    print("[DEBUG] Selenium 返回空数据")
            except Exception as e:
                logging.error(f"❌ Selenium 执行异常: {e}")
                print(f"[DEBUG] Selenium 执行异常: {e}")
                import traceback
                print(f"[DEBUG] 异常详情:\n{traceback.format_exc()}")
        else:
            print("[DEBUG] Selenium 降级已禁用（use_selenium_fallback=False）")
            logging.warning("⚠️ Selenium 降级已禁用")
        
        logging.error("❌ 东方财富和Selenium都失败")
        print("[DEBUG] 东方财富和Selenium都失败，返回空")
        return ""
    
    @staticmethod
    def _get_source(url: str, headers: dict) -> str:
        """
        获取页面源代码。

        参数:
            url (str): 请求的URL。
            headers (dict): 请求头信息。

        返回:
            str: 页面源代码。
        """
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            import urllib3
            
            # 禁用SSL警告
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            # 创建session并设置重试策略
            session = requests.Session()
            
            # 优化的重试策略 - 更多重试，更长延迟
            retry_strategy = Retry(
                total=4,  # 重试4次（给API更多机会）
                backoff_factor=2,  # 延迟因子2秒（2, 4, 8, 16秒递增）
                status_forcelist=[500, 502, 503, 504, 429, 408],  # 添加408超时状态码
                allowed_methods=["GET", "HEAD"],
                # 关键：增加对连接错误的重试
                raise_on_status=False,
                # 处理连接中断
                respect_retry_after_header=True
            )
            
            # 优化连接池配置
            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=10,  # 连接池数量
                pool_maxsize=20,      # 最大连接数
                pool_block=False      # 连接池满时不阻塞
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            
            # 发送请求前的随机延迟（更长延迟，避免被限流）
            import time
            import random
            delay = random.uniform(3, 5)  # 随机延迟3-5秒，模拟人类行为
            logging.info(f"请求延迟 {delay:.2f} 秒（避免限流）")
            print(f"[DEBUG] 请求延迟 {delay:.2f} 秒")
            time.sleep(delay)
            
            # 更新请求头，使其更像真实浏览器
            headers_copy = headers.copy()
            headers_copy.update({
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Cache-Control': 'no-cache',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Pragma': 'no-cache',
            })
            
            # 发送请求，增加超时时间
            print(f"[DEBUG] 正在发送 GET 请求...")
            logging.info(f"📡 正在请求: {url[:80]}...")
            
            response = session.get(
                url,
                headers=headers_copy,
                timeout=(10, 20),  # 连接超时10秒，读取超时20秒（给更多时间）
                verify=False,
                allow_redirects=True,
                # 关键：启用持久连接
                stream=False
            )
            
            print(f"[DEBUG] 请求完成，状态码: {response.status_code}")
            
            # 检查响应状态
            response.raise_for_status()
            
            # 记录响应信息
            logging.info(f"✓ 成功获取响应，状态码: {response.status_code}")
            print(f"[DEBUG] 响应头: {dict(response.headers)}")
            logging.debug(f"响应头: {dict(response.headers)}")
            
            # 确保正确处理压缩数据
            content_encoding = response.headers.get('Content-Encoding', '')
            logging.debug(f"响应编码: {content_encoding}")
            
            if content_encoding == 'br':
                # Brotli压缩，尝试手动解压
                try:
                    import brotli
                    content = brotli.decompress(response.content).decode('utf-8')
                    logging.info(f"Brotli解压缩成功，内容长度: {len(content)}")
                except Exception as e:
                    logging.warning(f"Brotli解压缩失败，使用response.text: {e}")
                    content = response.text
            else:
                # 其他压缩格式，使用response.text
                content = response.text
            
            # 关闭session
            session.close()
            
            logging.info(f"最终内容长度: {len(content)}")
            logging.debug(f"内容前200字符: {content[:200]}")
            
            return content

        except requests.exceptions.ConnectionError as e:
            print(f"[DEBUG] 连接错误: {str(e)}")
            logging.error(f"❌ 连接错误 {url[:80]}...: {str(e)}")
            logging.warning("💡 建议：1) 检查网络连接 2) 减少并发请求数 3) 增加请求延迟")
            return ""
        except requests.exceptions.Timeout as e:
            print(f"[DEBUG] 请求超时: {str(e)}")
            logging.error(f"⏱️ 请求超时 {url[:80]}...: {str(e)}")
            logging.warning("💡 建议：增加超时时间或稍后重试")
            return ""
        except requests.exceptions.TooManyRedirects as e:
            print(f"[DEBUG] 重定向次数过多: {str(e)}")
            logging.error(f"🔄 重定向次数过多: {url[:80]}...")
            return ""
        except requests.exceptions.RequestException as e:
            print(f"[DEBUG] 请求异常: {str(e)}")
            logging.error(f"❌ 请求异常 {url[:80]}...: {str(e)}")
            return ""
        except Exception as e:
            print(f"[DEBUG] 未知异常: {type(e).__name__}: {str(e)}")
            logging.error(f"❌ 获取源数据时发生异常: {str(e)}")
            import traceback
            error_trace = traceback.format_exc()
            print(f"[DEBUG] 异常堆栈:\n{error_trace}")
            logging.error(f"异常堆栈: {error_trace}")
            return ""

    @staticmethod
    def _handle_empty_source(code: str):
        """
        处理空数据源的情况并记录警告信息。

        参数:
            code (str): 股票代码。
        """
        info_text = f"Failed to retrieve code_data for {code}. Source is empty."
        logging.warning(info_text)
        return pd.DataFrame()

    @classmethod
    def stock_1m_days(cls, code: str, days: int = 5) -> pd.DataFrame:
        """
        下载 N days 1分钟股票数据（智能三阶段策略）
        
        优先级顺序：
        1. pytdx（稳定快速，优先使用）
        2. 东方财富 API（请求头轮换）
        3. Selenium（浏览器模拟）

        参数:
            code (str): 股票代码。
            days (int): 需要下载的天数，默认为5天。

        返回:
            pd.DataFrame: 下载的股票数据。
        """
        print(f"[DEBUG] DlEastMoney.stock_1m_days 被调用: code={code}, days={days}")
        logging.info("*" * 80)
        logging.info(f"开始下载股票 {code} 的 {days} 天数据")
        logging.info("*" * 80)
        
        try:
            # ============================================================
            # 第一阶段：优先使用 pytdx（稳定可靠）
            # ============================================================
            print(f"[DEBUG] ============================================================")
            print(f"[DEBUG] 第一阶段：使用 pytdx 获取数据（优先方案）")
            print(f"[DEBUG] ============================================================")
            logging.info("=" * 60)
            logging.info("🚀 第一阶段：使用 pytdx 获取数据（优先方案）")
            logging.info("=" * 60)
            
            try:
                from App.codes.downloads.DlPytdx import download_stock_1m_pytdx
                
                df_pytdx, end_date = download_stock_1m_pytdx(code, days)
                
                if not df_pytdx.empty:
                    logging.info(f"✅ pytdx 成功获取 {len(df_pytdx)} 条数据")
                    print(f"[DEBUG] ✓ pytdx 成功获取 {len(df_pytdx)} 条数据")
                    
                    show_download('1m', code)
                    logging.info("*" * 80)
                    logging.info(f"✅ 成功下载股票 {code} 的 {len(df_pytdx)} 条记录")
                    logging.info("*" * 80)
                    return df_pytdx
                else:
                    logging.warning("⚠️ pytdx 返回空数据，尝试备用方案")
                    print(f"[DEBUG] ✗ pytdx 返回空数据")
                    
            except ImportError:
                logging.warning("⚠️ pytdx 未安装，跳过第一阶段")
                print(f"[DEBUG] pytdx 未安装，跳过")
            except Exception as e:
                logging.warning(f"⚠️ pytdx 获取失败: {e}")
                print(f"[DEBUG] pytdx 失败: {e}")
            
            # ============================================================
            # 第二阶段：使用东方财富 API + Selenium
            # ============================================================
            print(f"")
            print(f"[DEBUG] ============================================================")
            print(f"[DEBUG] 第二阶段：使用东方财富 API（备用方案）")
            print(f"[DEBUG] ============================================================")
            logging.info("")
            logging.info("=" * 60)
            logging.info("🔄 第二阶段：使用东方财富 API（备用方案）")
            logging.info("=" * 60)
            
            url = my_url('stock_1m_multiple_days').format(days, UrlCode(code))
            print(f"[DEBUG] 生成的URL: {url}")
            
            # 使用请求头轮换机制获取数据（失败时会自动降级到 Selenium）
            source = cls._get_source_with_rotation(url, 'stock_1m_multiple_days')
            print(f"[DEBUG] 获取到的源数据长度: {len(source) if source else 0}")
            
            if source:
                print(f"[DEBUG] 源数据前200字符: {source[:200]}")
            else:
                print(f"[DEBUG] 源数据为空")

            if not source:
                logging.error(f"❌ 所有数据源都失败，无法获取股票 {code} 的数据")
                print(f"[DEBUG] 所有数据源都失败")
                return cls._handle_empty_source(code)

            # 解析东方财富数据
            print(f"[DEBUG] 开始解析东方财富数据...")
            logging.info(f"开始解析下载的数据...")
            # 注意：_get_source_with_rotation 返回的是纯JSON，不需要正则匹配
            dl = get_1m_data(source, match=False, multiple=True)
            print(f"[DEBUG] 数据解析结果: 形状={dl.shape if not dl.empty else 'Empty'}")

            if dl.empty:
                print(f"[DEBUG] 股票 {code} 数据解析后为空")
                logging.warning(f"⚠️ 股票 {code} 数据解析后为空")
                return dl

            show_download('1m', code)
            logging.info("*" * 80)
            logging.info(f"✅ 成功下载股票 {code} 的 {len(dl)} 条记录")
            logging.info("*" * 80)
            print(f"[DEBUG] 成功下载股票 {code} 的 {len(dl)} 条记录")
            return dl
            
        except Exception as e:
            print(f"[DEBUG] 下载股票 {code} 数据时发生异常: {str(e)}")
            logging.error(f"❌ 下载股票 {code} 数据时发生异常: {str(e)}")
            import traceback
            logging.error(f"异常堆栈: {traceback.format_exc()}")
            return pd.DataFrame()

    @classmethod
    def board_1m_data(cls, code: str):
        headers = my_headers('board_1m_data')
        url = my_url('board_1m_data').format(code)
        source = page_source(url=url, headers=headers)
        dl = get_1m_data(source, match=True, multiple=False)

        show_download('1m', code)  # 打印下载
        return dl

    @classmethod
    def board_1m_multiple(cls, code: str, days=5):
        """
        下载板块多天1分钟数据
        
        参数:
            code (str): 板块代码（如 BK0437）
            days (int): 需要下载的天数，默认为5天
            
        返回:
            pd.DataFrame: 下载的板块数据
        """
        logging.info(f"开始下载板块 {code} 的 {days} 天数据...")
        print(f"[DEBUG] 开始下载板块 {code} 的 {days} 天数据...")
        
        url = my_url('board_1m_multiple_days').format(days, code)
        print(f"[DEBUG] 生成的URL: {url}")
        logging.info(f"尝试访问URL: {url}")
        
        # 使用与 stock_1m_days 相同的数据获取方法，确保正确处理压缩响应
        source = cls._get_source_with_rotation(url, 'board_1m_multiple_days')
        print(f"[DEBUG] 获取到的源数据长度: {len(source) if source else 0}")
        
        if source:
            print(f"[DEBUG] 源数据前200字符: {source[:200]}")
        else:
            print(f"[DEBUG] 源数据为空")
            logging.warning(f"Failed to retrieve data for {code}. Source is empty.")
            return pd.DataFrame()

        # 解析东方财富数据
        print(f"[DEBUG] 开始解析东方财富数据...")
        logging.info(f"开始解析下载的数据...")
        dl = get_1m_data(source, match=False, multiple=True)
        print(f"[DEBUG] 数据解析结果: 形状={dl.shape if not dl.empty else 'Empty'}")

        if dl.empty:
            print(f"[DEBUG] 板块 {code} 数据解析后为空")
            logging.warning(f"⚠️ 板块 {code} 数据解析后为空")
            return dl

        show_download('1m', code)
        logging.info("*" * 80)
        logging.info(f"✅ 成功下载板块 {code} 的 {len(dl)} 条记录")
        logging.info("*" * 80)
        print(f"[DEBUG] 成功下载板块 {code} 的 {len(dl)} 条记录")
        return dl

    @classmethod
    def funds_to_stock(cls):

        """
        从东方财富网下载北向资金流入个股数据：
        下载北向资金每日流向数据，通过北向资金流入，选择自己的股票池;
        北向资金已经下载数据；
        """

        # 北向资金个股流入个股网址
        page1 = 'http://data.eastmoney.com/hsgtcg/list.html'
        page2 = '/html/body/div[1]/div[8]/div[2]/div[2]/div[2]/div[3]/div[3]/div[1]/a[2]'
        page3 = '/html/body/div[1]/div[8]/div[2]/div[2]/div[2]/div[3]/div[3]/div[1]/a[4]'

        path_date = '/html/body/div[1]/div[8]/div[2]/div[2]/div[1]/div[1]/div/span'

        driver = webdriver.Chrome()
        driver.get(page1)

        new_date = driver.find_element('xpath', path_date).text[1:-1]
        new_date = pd.to_datetime(new_date)

        # 获取第1页50条数据
        source01 = driver.page_source
        dl01 = return_FundsData(source01, new_date)

        # 获取第2页50条数据
        driver.find_element('xpath', page2).click()
        time.sleep(6)
        source02 = driver.page_source
        dl02 = return_FundsData(source02, new_date)

        # 获取第3页50条数据
        driver.find_element('xpath', page3).click()
        time.sleep(6)
        source03 = driver.page_source
        dl03 = return_FundsData(source03, new_date)
        driver.close()

        # 合并数据：
        data = pd.concat([dl01, dl02, dl03], ignore_index=True).reset_index(drop=True)

        print(f'东方财富下载{new_date}日北向资金流入个股据成功;')

        return data

    @classmethod
    def funds_to_stock2(cls):
        """
        ideal:
        try to use the web link download code_data;
        # http://datacenter-web.eastmoney.com/api/data/v1/get?callback=jQuery112309232266440254648_1657933725762&sortColumns=
        ADD_MARKET_CAP&sortTypes=-1&pageSize=50&pageNumber=1&reportName=RPT_MUTUAL_STOCK_NORTHSTA&columns=
        ALL&source=WEB&client=WEB&filter=(TRADE_DATE%3D%272022-07-15%27)(INTERVAL_TYPE%3D%221%22)

        target:
        last function:
        1. page size = 200;
        2. down full code_data
        """

        page_size = 50
        # date_ = pd.to
        w1 = 'http://datacenter-web.eastmoney.com/api/data/v1/get?callback=jQuery112309232266440254648_1657933725762' \
             '&sortColumns= '
        w2 = f'ADD_MARKET_CAP&sortTypes=-1&pageSize={page_size}&pageNumber=1&reportName=RPT_MUTUAL_STOCK_NORTHSTA' \
             f'&columns= '
        w3 = 'ALL&source=WEB&client=WEB&filter=(TRADE_DATE%3D%272022-07-16%27)(INTERVAL_TYPE%3D%221%22)'
        web = f'{w1}{w2}{w3}'
        print(web)
        pass

    @classmethod
    def funds_month_history(cls):  # 北向资金近1个月流入

        headers = my_headers('funds_month_history')

        url = my_url('funds_month_history')

        source = page_source(url=url, headers=headers)

        p1 = re.compile(r'[(](.*?)[)]', re.S)  # 最小匹配
        dl = re.findall(p1, source)[0]
        dl = pd.DataFrame(data=json.loads(dl)['result']['code_data'])
        dl = dl[['TRADE_DATE', 'NET_INFLOW_SH', 'NET_INFLOW_SZ', 'NET_INFLOW_BOTH']]
        dl.loc[:, 'TRADE_DATE'] = pd.to_datetime(dl['TRADE_DATE']).dt.date
        dl = dl.rename(columns={'TRADE_DATE': 'trade_date'})
        print('东方财富下载近一个月北向资金数据成功;')
        return dl

    @classmethod
    def funds_daily_data(cls):
        web_01 = 'https://data.eastmoney.com/hsgt/'
        driver = webdriver.Chrome()
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(60)
        driver.get(web_01)

        # 更新时间 07-30
        update_xpath = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[1]/div[3]/span'

        # 沪股通 净流入: SH money xpath， 深股通 净流入: SZ money xpath ， 北向 净流入: North_sum_xpath
        hmx = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[6]/ul[1]/li[1]/span[2]/span/span'
        zmx = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[6]/ul[1]/li[2]/span[2]/span/span'
        nsx = '/html/body/div[1]/div[8]/div[2]/div[2]/div[3]/div[6]/ul[1]/li[3]/span[2]/span/span'

        update = driver.find_element('xpath', update_xpath).text
        money_sh = float(driver.find_element('xpath', hmx).text[:-2]) * 100
        money_sz = float(driver.find_element('xpath', zmx).text[:-2]) * 100
        sum_north = float(driver.find_element('xpath', nsx).text[:-2]) * 100

        driver.close()

        dic_ = {'trade_date': [pd.to_datetime(update)],
                'NET_INFLOW_SH': [money_sh],
                'NET_INFLOW_SZ': [money_sz],
                'NET_INFLOW_BOTH': [sum_north]}

        df = pd.DataFrame(data=dic_)

        print(f'东方财富下载{update}日北向资金成功;')
        return df

    @classmethod
    def funds_to_sectors(cls, date_: str):

        """
        north funds to sectors code_data
        """

        headers = my_headers('funds_to_sectors')

        url = my_url('funds_to_sectors').format(date_)
        # print(url)
        # exit()
        source_ = page_source(url=url, headers=headers)

        try:
            p1 = re.compile(r'[(](.*?)[)]', re.S)

            page_data = re.findall(p1, source_)
            json_data = json.loads(page_data[0])
            json_data = json_data['result']['code_data']
            # print(json_data)
            value_list = []
            for i in range(len(json_data)):
                values = list(json_data[i].values())
                value_list.append(values)

            key_list = list(json_data[0])

            df = pd.DataFrame(data=value_list, columns=key_list)

            columns = ['SECURITY_CODE', 'BOARD_CODE', 'BOARD_NAME', 'TRADE_DATE', 'COMPOSITION_QUANTITY',
                       'ADD_MARKET_CAP', 'BOARD_VALUE', 'HK_VALUE', 'HK_BOARD_RATIO', 'MAXADD_SECURITY_CODE',
                       'MAXADD_SECURITY_NAME', 'MINADD_SECURITY_CODE', 'MINADD_SECURITY_NAME',
                       'MAXADD_RATIO_SECURITY_NAME', 'MAXADD_RATIO_SECURITY_CODE',
                       'MINADD_RATIO_SECURITY_NAME', 'MINADD_RATIO_SECURITY_CODE']

            df = df[columns]

            df['TRADE_DATE'] = pd.to_datetime(df['TRADE_DATE']).dt.date

        except TypeError:
            print(f'东方财富下载 Funds to Sectors 数据异常;')
            df = pd.DataFrame(data=None)

        return df

    @classmethod
    def industry_list(cls):  # 下载板块组成
        web = 'http://quote.eastmoney.com/center/boardlist.html#industry_board'
        driver = webdriver.Chrome()
        driver.get(web)

        source = driver.page_source
        bs_data = soup(source, 'html.parser')
        board_data = bs_data.find('li', class_='sub-items menu-industry_board-wrapper')
        board_data = board_data.find_all('li')
        data = pd.DataFrame(data=None)

        for i in range(len(board_data)):
            board_name = board_data[i].find(class_='text').text
            board_code = str(board_data[i].find('a')['href']).strip()[-6:]
            data.loc[i, 'board_name'] = board_name
            data.loc[i, 'board_code'] = board_code

        driver.close()
        data['stock_name'] = None
        data['stock_code'] = None
        return data

    @classmethod
    def industry_ind_stock(cls, name, code, num=300):  # 下载板块成份股
        url = my_url('industry_ind_stock').format(num, code)
        headers = my_headers('industry_ind_stock')
        source = page_source(url=url, headers=headers)

        dl = None
        if source:
            p1 = re.compile(r'[(](.*?)[)]', re.S)
            page_data = re.findall(p1, source)
            json_data = json.loads(page_data[0])['code_data']['diff']

            values_list = []
            for i in range(len(json_data)):
                values = list(json_data[i].values())
                values_list.append(values)

            key_list = list(json_data[0])
            dl = pd.DataFrame(data=values_list, columns=key_list)

            rename_ = {'f3': '涨跌幅', 'f4': '涨跌额', 'f5': '成交量', 'f6': '成交额',
                       'f7': '振幅', 'f8': '换手率', 'f9': '市盈率动', 'f10': '量比',
                       'f12': 'stock_code', 'f14': 'stock_name', 'f15': 'close',
                       'f16': 'low', 'f17': 'open', 'f18': 'preclose', 'f20': '总市值',
                       'f21': '流通市值', 'f23': '市净率', 'f115': '市盈率'}

            dl = dl.rename(columns=rename_)

            dl = dl.drop(columns=['f1', 'f2', 'f11', 'f13', 'f22', 'f24', 'f25', 'f45',
                                  'f62', 'f128', 'f140', 'f141', 'f136', 'f152'])

            dl['board_name'] = name
            dl['board_code'] = code
            dl['date'] = pd.Timestamp('today').date()

            dl = dl[['board_name', 'board_code', 'stock_code', 'stock_name', 'date']]

        return dl

    @classmethod
    def funds_awkward_api(cls, code):
        """
        尝试使用API接口获取基金持仓数据
        """
        try:
            # 尝试东方财富基金持仓API
            api_url = f"http://fund.eastmoney.com/data/fbsfundranking.html"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': f'http://fund.eastmoney.com/{code}.html',
                'X-Requested-With': 'XMLHttpRequest'
            }
            
            logging.info(f"尝试API方式获取基金 {code} 数据")
            
            # 尝试不同的API端点
            api_endpoints = [
                f"http://fund.eastmoney.com/api/FundPosition/{code}",
                f"http://fund.eastmoney.com/api/FundHoldings/{code}",
                f"http://fund.eastmoney.com/data/fbsfundranking.html?ft={code}",
            ]
            
            for api_url in api_endpoints:
                try:
                    source = page_source(url=api_url, headers=headers)
                    if source and len(source) > 100:  # 确保有足够的内容
                        logging.info(f"API {api_url} 返回数据长度: {len(source)}")
                        
                        # 尝试解析JSON数据
                        try:
                            import json
                            data = json.loads(source)
                            logging.info(f"成功解析JSON数据: {type(data)}")
                            return cls._parse_api_data(data, code)
                        except json.JSONDecodeError:
                            logging.warning(f"API返回的不是有效JSON: {api_url}")
                            continue
                        
                except Exception as e:
                    logging.warning(f"API {api_url} 请求失败: {e}")
                    continue
            
            logging.warning(f"所有API尝试都失败了")
            return pd.DataFrame()
            
        except Exception as e:
            logging.error(f"API方式获取基金 {code} 数据时发生错误: {e}")
            return pd.DataFrame()
    
    @classmethod
    def _parse_api_data(cls, data, code):
        """解析API返回的数据"""
        try:
            li_name = []
            li_code = []
            
            # 尝试不同的数据结构
            if isinstance(data, dict):
                # 查找可能包含股票数据的字段
                possible_fields = ['data', 'result', 'list', 'stocks', 'positions', 'holdings']
                
                for field in possible_fields:
                    if field in data:
                        items = data[field]
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, dict):
                                    # 尝试提取股票名称和代码
                                    stock_name = item.get('name') or item.get('stock_name') or item.get('title')
                                    stock_code = item.get('code') or item.get('stock_code') or item.get('id')
                                    
                                    if stock_name and stock_code:
                                        li_name.append(str(stock_name))
                                        li_code.append(str(stock_code))
                                        logging.info(f"从API解析到股票: {stock_name} ({stock_code})")
            
            if li_name and li_code:
                dic = {'stock_name': li_name, 'stock_code': li_code}
                return pd.DataFrame(dic)
            else:
                logging.warning(f"API数据中未找到有效的股票信息")
                return pd.DataFrame()
                
        except Exception as e:
            logging.error(f"解析API数据时发生错误: {e}")
            return pd.DataFrame()

    @classmethod
    def funds_awkward(cls, code):
        """
        获取基金持仓数据，尝试多种方法
        """
        # 首先尝试API方法
        data = cls.funds_awkward_api(code)
        if not data.empty:
            logging.info(f"API方法成功获取基金 {code} 数据")
            return data
        
        # 如果API失败，尝试网页解析方法
        logging.info(f"API方法失败，尝试网页解析方法")
        return cls.funds_awkward_web(code)

    @classmethod
    def funds_awkward_web(cls, code):
        """
        从网页解析基金持仓数据
        """
        try:
            url = my_url('funds_awkward').format(code)
            headers = my_headers('funds_awkward')
            
            # 检查URL是否为空
            if not url:
                logging.error(f"基金 {code} 的URL配置为空")
                return pd.DataFrame()
            
            logging.info(f"正在下载基金 {code} 的数据，URL: {url}")
            
            source = page_source(url=url, headers=headers)
            
            # 检查页面源码是否为空
            if not source:
                logging.error(f"基金 {code} 页面源码为空")
                return pd.DataFrame()
            
            # 解析HTML
            soup_obj = soup(source, 'html.parser')
            if not soup_obj:
                logging.error(f"基金 {code} HTML解析失败")
                return pd.DataFrame()
            
            # 查找所有股票链接
            stock_links = soup_obj.find_all("a", href=lambda href: href and '/stock/' in href)
            
            if not stock_links:
                logging.warning(f"基金 {code} 未找到股票链接")
                return pd.DataFrame()
            
            li_name = []
            li_code = []
            
            for link in stock_links:
                try:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)
                    
                    # 从链接中提取股票代码
                    if '/stock/' in href:
                        # 提取股票代码
                        code_match = href.split('/stock/')[-1].split('.')[0]
                        if len(code_match) == 6 and code_match.isdigit():
                            stock_code = code_match
                            stock_name = text
                            
                            # 验证股票名称（应该是中文）
                            if stock_name and len(stock_name) > 0:
                                # 清理文本
                                stock_name = stock_name.replace('\n', '').replace('\r', '').replace('\t', '').strip()
                                
                                li_name.append(stock_name)
                                li_code.append(stock_code)
                                logging.info(f"基金 {code} 解析到股票: {stock_name} ({stock_code})")
                    
                except Exception as e:
                    logging.warning(f"解析基金 {code} 股票链接时出错: {e}")
                    continue
            
            if not li_name or not li_code:
                logging.warning(f"基金 {code} 未解析到有效的股票数据")
                return pd.DataFrame()

            dic = {'stock_name': li_name, 'stock_code': li_code}
            data = pd.DataFrame(dic)
            
            logging.info(f"基金 {code} 成功下载 {len(data)} 条股票数据")
            return data
            
        except Exception as e:
            logging.error(f"下载基金 {code} 数据时发生错误: {e}")
            return pd.DataFrame()


if __name__ == '__main__':
    download = DownloadData.stock_1m_1day('002475')
    print(download)
