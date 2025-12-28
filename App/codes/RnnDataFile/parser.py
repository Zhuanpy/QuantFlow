# -*- coding: utf-8 -*-
"""
数据解析器模块
提供请求头和URL配置
"""
from config import Config


def my_url(pp: str) -> str:
    """
    获取东方财富URL模板
    
    Args:
        pp: URL类型，如 'stock_1m_data', 'stock_1m_multiple_days' 等
        
    Returns:
        str: URL模板字符串
    """
    return Config.get_eastmoney_urls(pp)


def my_headers(pp: str) -> dict:
    """
    获取东方财富请求头
    
    Args:
        pp: 请求头类型，如 'stock_1m_data', 'stock_1m_multiple_days' 等
        
    Returns:
        dict: 请求头字典
    """
    return Config.get_eastmoney_headers(pp)

