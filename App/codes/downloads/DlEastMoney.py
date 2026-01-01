# -*- coding: utf-8 -*-
"""
东方财富数据下载模块

此模块已重构，核心功能已迁移到 eastmoney 子包：
- eastmoney/http_client.py: HTTP 请求封装
- eastmoney/data_parser.py: 数据解析工具
- eastmoney/stock_downloader.py: 股票数据下载
- eastmoney/fund_downloader.py: 基金数据下载
- eastmoney/board_downloader.py: 板块数据下载

本文件保留所有导出以保持向后兼容性。

新代码建议直接使用:
    from App.codes.downloads.eastmoney import (
        StockDownloader,
        FundDownloader,
        BoardDownloader,
        EastMoneyHttpClient,
    )
"""

import logging
import pandas as pd

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==================== 向后兼容导入 ====================

# 从 eastmoney 子包导入所有功能
from App.codes.downloads.eastmoney.http_client import (
    HeaderRotator,
    EastMoneyHttpClient,
)
from App.codes.downloads.eastmoney.data_parser import (
    DataValidationConfig,
    parse_json,
    process_data,
    get_1m_data,
    validate_data,
    show_download,
    convert_currency_unit,
    return_funds_data as return_FundsData,
    funds_data_clean,
)
from App.codes.downloads.eastmoney.stock_downloader import StockDownloader
from App.codes.downloads.eastmoney.fund_downloader import FundDownloader
from App.codes.downloads.eastmoney.board_downloader import BoardDownloader


# ==================== 向后兼容的 DownloadData 类 ====================

class DownloadData:
    """
    从东方财富下载数据的统一入口类

    此类是向后兼容的封装，实际功能委托给专用下载器类：
    - StockDownloader: 股票数据
    - FundDownloader: 基金数据
    - BoardDownloader: 板块数据
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 2

    # 类级别的请求头轮换器
    _header_rotators = {}

    @classmethod
    def get_header_rotator(cls, header_type: str = 'stock_1m_multiple_days'):
        """获取或创建请求头轮换器"""
        return EastMoneyHttpClient.get_header_rotator(header_type)

    @classmethod
    def validate_data(cls, df: pd.DataFrame) -> bool:
        """验证下载的数据是否有效"""
        return StockDownloader.validate_data(df)

    # ==================== 股票数据方法 ====================

    @classmethod
    def stock_1m_1day(cls, code: str):
        """下载 1 天的 1 分钟股票数据"""
        return StockDownloader.stock_1m_1day(code)

    @classmethod
    def stock_1m_days(cls, code: str, days: int = 5):
        """下载 N 天的 1 分钟股票数据"""
        return StockDownloader.stock_1m_days(code, days)

    # ==================== 板块数据方法 ====================

    @classmethod
    def board_1m_data(cls, code: str):
        """下载板块单日1分钟数据"""
        return BoardDownloader.board_1m_data(code)

    @classmethod
    def board_1m_multiple(cls, code: str, days: int = 5):
        """下载板块多天1分钟数据"""
        return BoardDownloader.board_1m_multiple(code, days)

    @classmethod
    def industry_list(cls):
        """下载板块列表"""
        return BoardDownloader.industry_list()

    @classmethod
    def industry_ind_stock(cls, name: str, code: str, num: int = 300):
        """下载板块成份股"""
        return BoardDownloader.industry_ind_stock(name, code, num)

    # ==================== 基金数据方法 ====================

    @classmethod
    def funds_to_stock(cls):
        """下载北向资金流入个股数据"""
        return FundDownloader.funds_to_stock()

    @classmethod
    def funds_to_stock2(cls):
        """北向资金流入个股（API版本，待实现）"""
        # 保留原有逻辑
        page_size = 50
        w1 = 'http://datacenter-web.eastmoney.com/api/data/v1/get?callback=jQuery112309232266440254648_1657933725762&sortColumns= '
        w2 = f'ADD_MARKET_CAP&sortTypes=-1&pageSize={page_size}&pageNumber=1&reportName=RPT_MUTUAL_STOCK_NORTHSTA&columns= '
        w3 = 'ALL&source=WEB&client=WEB&filter=(TRADE_DATE%3D%272022-07-16%27)(INTERVAL_TYPE%3D%221%22)'
        web = f'{w1}{w2}{w3}'
        print(web)
        pass

    @classmethod
    def funds_month_history(cls):
        """下载北向资金近1个月流入数据"""
        return FundDownloader.funds_month_history()

    @classmethod
    def funds_daily_data(cls):
        """下载当日北向资金数据"""
        return FundDownloader.funds_daily_data()

    @classmethod
    def funds_to_sectors(cls, date_: str):
        """下载北向资金流入板块数据"""
        return FundDownloader.funds_to_sectors(date_)

    @classmethod
    def funds_awkward(cls, code: str):
        """获取基金持仓数据"""
        return FundDownloader.funds_awkward(code)

    @classmethod
    def funds_awkward_api(cls, code: str):
        """使用API获取基金持仓数据"""
        return FundDownloader.funds_awkward_api(code)

    @classmethod
    def funds_awkward_web(cls, code: str):
        """从网页解析基金持仓数据"""
        return FundDownloader.funds_awkward_web(code)

    # ==================== 内部方法 ====================

    @classmethod
    def _get_source_with_selenium(cls, url: str) -> str:
        """使用 Selenium 获取页面源代码"""
        return EastMoneyHttpClient.get_source_with_selenium(url)

    @classmethod
    def _get_source_with_rotation(cls, url: str, header_type: str = 'stock_1m_multiple_days',
                                   use_selenium_fallback: bool = None) -> str:
        """使用请求头轮换获取页面源代码"""
        return EastMoneyHttpClient.get_source_with_rotation(url, header_type, use_selenium_fallback)

    @staticmethod
    def _get_source(url: str, headers: dict) -> str:
        """获取页面源代码"""
        return EastMoneyHttpClient.get_source(url, headers)

    @staticmethod
    def _handle_empty_source(code: str):
        """处理空数据源"""
        return StockDownloader._handle_empty_source(code)


# ==================== 模块导出 ====================

__all__ = [
    # 主类
    'DownloadData',
    # 专用下载器
    'StockDownloader',
    'FundDownloader',
    'BoardDownloader',
    # HTTP 客户端
    'HeaderRotator',
    'EastMoneyHttpClient',
    # 数据解析
    'DataValidationConfig',
    'parse_json',
    'process_data',
    'get_1m_data',
    'validate_data',
    'show_download',
    'convert_currency_unit',
    'return_FundsData',
    'funds_data_clean',
]


if __name__ == '__main__':
    download = DownloadData.stock_1m_1day('002475')
    print(download)
