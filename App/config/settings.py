# -*- coding: utf-8 -*-
"""
应用配置模块

所有配置集中管理，敏感信息通过 secrets.py 从环境变量读取
"""
import os
from pathlib import Path

from .secrets import Secrets

# 东方财富基础请求头（定义在类外部以便在类定义时使用）
_EASTMONEY_BASE_HEADERS_TEMPLATE = {
    'Accept': '*/*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Referer': 'http://quote.eastmoney.com/',
    'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'Sec-Fetch-Dest': 'script',
    'Sec-Fetch-Mode': 'no-cors',
    'Sec-Fetch-Site': 'same-site',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
}


class Config:
    """应用配置类"""

    # 获取项目根目录
    @staticmethod
    def get_project_root():
        """获取项目根目录路径"""
        # App/config/settings.py -> 向上3级到项目根目录
        current_file = Path(__file__).resolve()
        return str(current_file.parent.parent.parent)

    # Flask配置（从环境变量读取）
    SECRET_KEY = Secrets.get_secret_key()

    # 数据库配置（从环境变量读取）
    DB_HOST = Secrets.get_db_host()
    DB_PORT = Secrets.get_db_port()
    DB_USER = Secrets.get_db_user()
    DB_PASSWORD = Secrets.get_db_password()
    DB_NAME = Secrets.get_db_name()

    # 数据库配置字典（用于直接连接）
    @classmethod
    def get_db_config(cls):
        """获取数据库配置字典"""
        return {
            'host': cls.DB_HOST,
            'user': cls.DB_USER,
            'password': cls.DB_PASSWORD,
            'charset': 'utf8mb4'
        }

    # 静态属性，保持向后兼容
    # 不能用 property —— Config 一直是当类用（Config.DB_CONFIG[...]），
    # 走类访问会拿到 property descriptor 本身，触发 "'property' object is not subscriptable"
    DB_CONFIG = {
        'host': DB_HOST,
        'user': DB_USER,
        'password': DB_PASSWORD,
        'charset': 'utf8mb4',
    }

    # 使用 Secrets 模块构建数据库连接字符串
    SQLALCHEMY_DATABASE_URI = Secrets.get_database_uri()

    # 多数据库绑定配置
    SQLALCHEMY_BINDS = {
        'quanttradingsystem': SQLALCHEMY_DATABASE_URI,
    }

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = os.getenv('SQLALCHEMY_ECHO', 'False').lower() == 'true'

    # 其他配置
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

    # 股票列名配置
    STOCK_COLUMNS = {
        'Basic': {
            '1': 'date', '2': 'open', '3': 'close', '4': 'high',
            '5': 'low', '6': 'volume', '7': 'money'
        },
        'Macd': {
            '1': 'EmaShort', '2': 'EmaMid', '3': 'EmaLong', '4': 'Dif',
            '5': 'DifSm', '6': 'DifMl', '7': 'Dea', '8': 'MACD'
        },
        'Boll': {
            '1': 'BollMid', '2': 'BollStd', '3': 'BollUp',
            '4': 'BollDn', '5': 'StopLoss'
        },
        'Signal': {
            '1': 'Signal', '2': 'SignalTimes', '3': 'SignalChoice',
            '4': 'SignalStartIndex', '5': 'up', '6': 'down',
            '7': '1', '8': '-1'
        },
        'cycle': {
            '1': 'EndPrice', '2': 'EndPriceIndex', '3': 'StartPrice',
            '4': 'StartPriceIndex', '5': 'Cycle1mVolMax1', '6': 'Cycle1mVolMax5',
            '7': 'Bar1mVolMax1', '8': 'Bar1mVolMax5', '9': 'CycleLengthMax',
            '10': 'CycleLengthPerBar', '11': 'CycleAmplitudePerBar',
            '12': 'CycleAmplitudeMax'
        },
        'Recycle': {
            '1': 'preCycle1mVolMax1', '2': 'preCycle1mVolMax5',
            '3': 'preCycleAmplitudeMax', '4': 'preCycleLengthMax',
            '5': 'nextCycleLengthMax', '6': 'nextCycleAmplitudeMax'
        },
        'Signal30m': {
            '1': '30mSignal', '2': '30mSignalChoice', '3': '30mSignalTimes'
        },
        'Signal120m': {
            '1': '120mSignal', '2': '120mSignalChoice', '3': '120mSignalTimes'
        },
        'SignalDaily': {
            '1': 'Daily1mVolMax1', '2': 'Daily1mVolMax5',
            '3': 'Daily1mVolMax15', '4': 'DailyVolEmaParser',
            '5': 'DailyVolEma'
        }
    }

    # 东方财富（EastMoney）配置
    _EASTMONEY_COOKIE = Secrets.get_eastmoney_cookie()
    _EASTMONEY_FUNDS_COOKIE = Secrets.get_eastmoney_funds_cookie()
    _EASTMONEY_BASE_HEADERS = _EASTMONEY_BASE_HEADERS_TEMPLATE

    @classmethod
    def _build_eastmoney_headers(cls, host: str, cookie: str = None) -> dict:
        """构建东方财富请求头"""
        headers = cls._EASTMONEY_BASE_HEADERS.copy()
        headers['Host'] = host
        headers['Cookie'] = cookie or cls._EASTMONEY_COOKIE
        return headers

    EASTMONEY_HEADERS = {
        'stock_1m_data': {
            **_EASTMONEY_BASE_HEADERS_TEMPLATE,
            'Host': 'push2.eastmoney.com',
            'Cookie': Secrets.get_eastmoney_cookie()
        },
        'stock_1m_multiple_days': {
            **_EASTMONEY_BASE_HEADERS_TEMPLATE,
            'Host': 'push2his.eastmoney.com',
            'Cookie': Secrets.get_eastmoney_cookie()
        },
        'board_1m_data': {
            **_EASTMONEY_BASE_HEADERS_TEMPLATE,
            'Host': 'push2.eastmoney.com',
            'Cookie': Secrets.get_eastmoney_cookie()
        },
        'board_1m_multiple_days': {
            **_EASTMONEY_BASE_HEADERS_TEMPLATE,
            'Host': 'push2his.eastmoney.com',
            'Cookie': Secrets.get_eastmoney_cookie()
        },
        'board_daily_kline': {
            **_EASTMONEY_BASE_HEADERS_TEMPLATE,
            'Host': 'push2his.eastmoney.com',
            'Cookie': Secrets.get_eastmoney_cookie()
        },
        'board_1m_trends_delay': {
            **_EASTMONEY_BASE_HEADERS_TEMPLATE,
            'Host': 'push2delay.eastmoney.com',
            'Cookie': Secrets.get_eastmoney_cookie()
        },
        'stock_quote': {
            **_EASTMONEY_BASE_HEADERS_TEMPLATE,
            'Host': 'push2.eastmoney.com',
            'Cookie': Secrets.get_eastmoney_cookie()
        },
        'funds_awkward': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'max-age=0',
            'Connection': 'keep-alive',
            'Cookie': Secrets.get_eastmoney_funds_cookie() or Secrets.get_eastmoney_cookie(),
            'Host': 'fundf10.eastmoney.com',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.93 Safari/537.36'
        }
    }

    EASTMONEY_URLS = {
        # 单日分时数据（trends2 API）
        'stock_1m_data': 'http://push2.eastmoney.com/api/qt/stock/trends2/get?secid={}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58',
        'board_1m_data': 'http://push2.eastmoney.com/api/qt/stock/trends2/get?secid=90.{}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58',
        # 板块多日 1m —— push2delay 备用源（trends2，HTTPS，ndays 1–5）。
        # push2his:80 的 kline 接口常被整段 TCP RST，而浏览器能直连
        # push2delay 的 https，故板块优先走这条。无 cb → 原始 JSON（match=False），
        # fields2 8 段对应解析器 8 列分支（date/open/close/high/low/volume/money/avg）。
        'board_1m_trends_delay': 'https://push2delay.eastmoney.com/api/qt/stock/trends2/get?secid=90.{}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&iscca=0&ut=fa5fd1943c7b386f172d6893dbfba10b&ndays={}',
        # 多天K线数据（kline API）- klt=1表示1分钟K线
        'stock_1m_multiple_days': 'http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={}&klt=1&fqt=1&lmt={}&end=20500101&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        # 注意：板块指数没有"复权"概念，必须使用 fqt=0（不复权）。
        # East Money 后端对板块加 fqt=1 会按"成份股复权后加权"返回另一套数值，
        # 与官网展示及实际指数点位不一致（约真实值的 56%）。
        'board_1m_multiple_days': 'http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.{}&klt=1&fqt=0&lmt={}&end=20500101&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        # 板块日 K（klt=101 日，fqt=0 不复权）。pytdx 的 get_index_bars 也会返回
        # 复权后的"翻倍"指数，所以板块日 K 必须走这条 East Money 接口。
        'board_daily_kline': 'http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.{}&klt=101&fqt=0&lmt={}&end=20500101&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        # 个股实时行情 + 基本面（最新价/涨跌/总市值/流通市值/总股本/流通股本/PE/PB/换手率等）
        # 注意：必须用 HTTPS。HTTP 会被 East Money 30x 重定向到 HTTPS，
        # 项目 HTTP 客户端跟进重定向时会进死循环。
        # 实际调用见 App/services/stock_quote_service.py（用裸 requests）
        'stock_quote': 'https://push2.eastmoney.com/api/qt/stock/get?secid={}&ut=fa5fd1943c7b386f172d6893dbfba10b&fields={}',
        'funds_awkward': 'http://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={}&topline=10&year=&month=&rt=0.7468124095836639'
    }

    # 雪球（XueQiu）配置
    XUEQIU_COOKIES = Secrets.get_xueqiu_cookies()

    XUEQIU_HEADERS = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        'Connection': 'keep-alive',
        'Host': 'stock.xueqiu.com',
        'Origin': 'https://xueqiu.com',
        'Referer': 'https://xueqiu.com/S/SZ300750',
        'Sec-Ch-Ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    }

    @staticmethod
    def get_eastmoney_headers(header_type: str) -> dict:
        """获取东方财富请求头"""
        return Config.EASTMONEY_HEADERS.get(header_type, Config.EASTMONEY_HEADERS['stock_1m_data']).copy()

    @staticmethod
    def get_eastmoney_headers_pool(header_type: str) -> list:
        """获取东方财富请求头池（用于轮换）"""
        header = Config.get_eastmoney_headers(header_type)
        return [header]

    @staticmethod
    def get_eastmoney_urls(url_type: str) -> str:
        """获取东方财富URL模板"""
        return Config.EASTMONEY_URLS.get(url_type, '')

    @staticmethod
    def get_xueqiu_cookies() -> str:
        """获取雪球Cookies"""
        return Config.XUEQIU_COOKIES

    @staticmethod
    def get_xueqiu_headers() -> dict:
        """获取雪球请求头"""
        return Config.XUEQIU_HEADERS.copy()

    @staticmethod
    def get_sql_password() -> str:
        """获取SQL密码（已迁移到DB_PASSWORD配置）"""
        return Config.DB_PASSWORD

    # 下载配置
    # use_selenium_fallback=False：HTTP 失败就直接报失败，不启动 Chrome 兜底。
    # 之前一旦被东财限流，每个失败都开一个新 Chrome（headless），27+ 进程能把 Flask 卡死。
    # 板块 / 股票的 1m kline API 走 selenium 解出来也是同一个 JSON，没必要付出 Chrome 的代价。
    DOWNLOAD_CONFIG = {
        'selenium_headless': True,
        'use_selenium_fallback': False,
        'max_retries': 3,
        'retry_delay': 5,
        'request_timeout': 30,
    }
