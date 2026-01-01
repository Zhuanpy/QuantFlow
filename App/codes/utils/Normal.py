# -*- coding: utf-8 -*-
"""
通用工具函数和类

此模块已重构拆分为以下子模块：
- math_utils.py: 数学公式、统计分析、归一化
- data_processing.py: 数据重采样、时间序列转换
- file_io.py: 文件读写操作
- stock_utils.py: 股票代码处理

本文件保留所有导出以保持向后兼容性。
新代码建议直接从对应子模块导入。
"""

import time
import smtplib
from email.message import Message
from typing import Dict

# ==================== 向后兼容导入 ====================

# 从 math_utils 导入
from App.codes.utils.math_utils import MathematicalFormula

# 从 data_processing 导入
from App.codes.utils.data_processing import ResampleData

# 从 file_io 导入
from App.codes.utils.file_io import ReadSaveFile, rename_and_merge_csv_files

# 从 stock_utils 导入
from App.codes.utils.stock_utils import StockCode

# ==================== 保留的实用工具 ====================


def count_times(func):
    """装饰器：统计函数运行时间"""
    def wrapper(*args, **kwargs):
        start = time.process_time()
        results = func(*args, **kwargs)
        end = time.process_time()
        print(f'运行时间：{int(end - start)}秒\n')
        return results
    return wrapper


class Useful:
    """实用工具类"""

    @classmethod
    def sent_emails(cls, message_title: str, mail_content: str) -> bool:
        """
        发送邮件（使用Gmail SMTP）

        注意：此方法包含硬编码的邮箱配置，建议迁移到环境变量

        Args:
            message_title: 邮件标题
            mail_content: 邮件内容

        Returns:
            bool: 发送是否成功
        """
        try:
            import os
            smtpserver = 'smtp.gmail.com'
            username = os.getenv('EMAIL_USERNAME', 'legendtravel004@gmail.com')
            password = os.getenv('EMAIL_PASSWORD', '')
            from_addr = os.getenv('EMAIL_FROM', 'legendtravel004@gmail.com')
            to_addr = os.getenv('EMAIL_TO', 'zhangzhuan516@gmail.com').split(',')
            cc_addr = os.getenv('EMAIL_CC', '651748264@qq.com').split(',')

            if not password:
                print("警告: 未设置 EMAIL_PASSWORD 环境变量")
                return False

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
        """
        生成等号和减号线

        Args:
            num: 线的长度

        Returns:
            tuple: (等号线, 减号线)
        """
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


# ==================== 模块导出 ====================

__all__ = [
    # 装饰器
    'count_times',

    # 类
    'MathematicalFormula',
    'StockCode',
    'ReadSaveFile',
    'ResampleData',
    'Useful',

    # 函数
    'rename_and_merge_csv_files',
]


if __name__ == '__main__':
    # 测试代码
    pass
