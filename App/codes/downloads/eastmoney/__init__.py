# -*- coding: utf-8 -*-
"""
东方财富数据下载子包

将 DlEastMoney.py 拆分为以下模块：
- http_client: HTTP 请求封装和 Selenium 降级
- data_parser: 数据解析工具
- stock_downloader: 股票数据下载
- fund_downloader: 基金数据下载
- board_downloader: 板块数据下载
"""

from App.codes.downloads.eastmoney.http_client import (
    HeaderRotator,
    EastMoneyHttpClient,
)
from App.codes.downloads.eastmoney.data_parser import (
    DataValidationConfig,
    parse_json,
    process_data,
    get_1m_data,
)
from App.codes.downloads.eastmoney.stock_downloader import StockDownloader
from App.codes.downloads.eastmoney.fund_downloader import FundDownloader
from App.codes.downloads.eastmoney.board_downloader import BoardDownloader

__all__ = [
    # HTTP 客户端
    'HeaderRotator',
    'EastMoneyHttpClient',
    # 数据解析
    'DataValidationConfig',
    'parse_json',
    'process_data',
    'get_1m_data',
    # 下载器
    'StockDownloader',
    'FundDownloader',
    'BoardDownloader',
]
