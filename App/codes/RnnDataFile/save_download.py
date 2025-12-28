# -*- coding: utf-8 -*-
"""
数据保存模块
提供将下载的数据保存到CSV、数据库等功能
"""
import os
import pandas as pd
import logging
from datetime import datetime
from typing import Dict, List, Optional
from config import Config

logger = logging.getLogger(__name__)


def save_1m_to_csv(data: pd.DataFrame, stock_code: str) -> bool:
    """
    将1分钟数据保存到CSV文件
    
    Args:
        data: 1分钟数据DataFrame
        stock_code: 股票代码
        
    Returns:
        bool: 保存是否成功
    """
    try:
        from App.utils.file_utils import get_stock_data_path
        file_path = get_stock_data_path(stock_code, data_type='1m', create=True)
        data.to_csv(file_path, index=False, encoding='utf-8-sig')
        logger.info(f"成功保存1分钟数据到CSV: {stock_code}")
        return True
    except Exception as e:
        logger.error(f"保存1分钟数据到CSV失败: {stock_code}, 错误: {e}")
        return False


def save_1m_to_daily(data: pd.DataFrame, stock_code: str) -> bool:
    """
    将1分钟数据转换为日线数据并保存
    
    Args:
        data: 1分钟数据DataFrame
        stock_code: 股票代码
        
    Returns:
        bool: 保存是否成功
    """
    try:
        from App.codes.utils.Normal import ResampleData
        from App.utils.file_utils import get_stock_data_path
        
        # 转换为日线数据
        daily_data = ResampleData.resample_1m_data(data, freq='daily')
        
        # 保存到文件
        file_path = get_stock_data_path(stock_code, data_type='daily', create=True)
        daily_data.to_csv(file_path, index=False, encoding='utf-8-sig')
        
        logger.info(f"成功保存日线数据: {stock_code}")
        return True
    except Exception as e:
        logger.error(f"保存日线数据失败: {stock_code}, 错误: {e}")
        return False


def save_1m_to_mysql(stock_code: str, year: str, data: pd.DataFrame) -> bool:
    """
    将1分钟数据保存到MySQL数据库
    
    Args:
        stock_code: 股票代码
        year: 年份
        data: 1分钟数据DataFrame
        
    Returns:
        bool: 保存是否成功
    """
    try:
        from App.models.data.Stock1m import save_1m_stock_data_to_sql
        save_1m_stock_data_to_sql(stock_code, data)
        logger.info(f"成功保存1分钟数据到MySQL: {stock_code}")
        return True
    except Exception as e:
        logger.error(f"保存1分钟数据到MySQL失败: {stock_code}, 错误: {e}")
        return False


def complete_download_process(stock_code: str, days: int = 1, update_record: bool = True) -> Dict[str, any]:
    """
    完整的下载流程：下载1分钟数据，转换为15分钟和日线数据，并保存
    
    Args:
        stock_code: 股票代码
        days: 下载天数
        update_record: 是否更新记录
        
    Returns:
        dict: 包含success和message的字典
    """
    try:
        from App.codes.downloads.DlEastMoney import DownloadData
        from App.codes.utils.Normal import ResampleData
        from App.utils.file_utils import get_stock_data_path
        
        logger.info(f"开始完整下载流程: {stock_code}, 天数: {days}")
        
        # 下载1分钟数据
        data_1m = DownloadData.stock_1m_days(stock_code, days)
        
        if data_1m.empty:
            return {
                'success': False,
                'message': f'下载失败: 未获取到 {stock_code} 的数据'
            }
        
        # 保存1分钟数据
        save_1m_to_csv(data_1m, stock_code)
        
        # 转换为15分钟数据
        try:
            data_15m = ResampleData.resample_1m_data(data_1m, freq='15m')
            file_path_15m = get_stock_data_path(stock_code, data_type='15m', create=True)
            data_15m.to_csv(file_path_15m, index=False, encoding='utf-8-sig')
            logger.info(f"成功保存15分钟数据: {stock_code}")
        except Exception as e:
            logger.warning(f"保存15分钟数据失败: {stock_code}, 错误: {e}")
        
        # 转换为日线数据
        save_1m_to_daily(data_1m, stock_code)
        
        # 保存到数据库
        try:
            year = datetime.now().strftime('%Y')
            save_1m_to_mysql(stock_code, year, data_1m)
        except Exception as e:
            logger.warning(f"保存到数据库失败: {stock_code}, 错误: {e}")
        
        return {
            'success': True,
            'message': f'成功完成 {stock_code} 的完整下载流程'
        }
        
    except Exception as e:
        logger.error(f"完整下载流程失败: {stock_code}, 错误: {e}")
        return {
            'success': False,
            'message': f'下载失败: {str(e)}'
        }


def batch_complete_download_process(stock_codes: List[str], days: int = 1) -> Dict[str, any]:
    """
    批量完整下载流程
    
    Args:
        stock_codes: 股票代码列表
        days: 下载天数
        
    Returns:
        dict: 包含success、message和results的字典
    """
    results = []
    success_count = 0
    fail_count = 0
    
    for stock_code in stock_codes:
        result = complete_download_process(stock_code, days, update_record=False)
        results.append({
            'stock_code': stock_code,
            'success': result['success'],
            'message': result['message']
        })
        
        if result['success']:
            success_count += 1
        else:
            fail_count += 1
    
    return {
        'success': fail_count == 0,
        'message': f'批量下载完成: 成功 {success_count} 个, 失败 {fail_count} 个',
        'results': results,
        'success_count': success_count,
        'fail_count': fail_count
    }

