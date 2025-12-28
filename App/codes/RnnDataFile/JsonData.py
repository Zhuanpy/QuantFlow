# -*- coding: utf-8 -*-
"""
JSON数据访问层
提供JSON文件的读取、保存和查找功能
"""
import os
import json
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from pathlib import Path

from App.codes.utils.Normal import ReadSaveFile
from App.codes.RnnDataFile.stock_path import file_root

logger = logging.getLogger(__name__)


class MyJsonData:
    """
    JSON数据访问类
    提供JSON文件的读取、保存和查找功能
    """
    
    @staticmethod
    def loadJsonData(month: str, stock_code: str) -> Dict[str, Any]:
        """
        加载JSON数据
        
        Args:
            month: 月份（格式：'YYYY-MM'）
            stock_code: 股票代码
            
        Returns:
            dict: JSON数据，如果文件不存在返回空字典
        """
        data = ReadSaveFile.read_json(month, stock_code)
        return data if data is not None else {}
    
    @staticmethod
    def save_json(data: Dict[str, Any], month: str, stock_code: str) -> bool:
        """
        保存JSON数据
        
        Args:
            data: 要保存的字典数据
            month: 月份（格式：'YYYY-MM'）
            stock_code: 股票代码
            
        Returns:
            bool: 保存是否成功
        """
        return ReadSaveFile.save_json(data, month, stock_code)
    
    @staticmethod
    def find_previous_month_json_parser(month: str, stock_code: str) -> Tuple[Dict[str, Any], str]:
        """
        查找上一个月的JSON解析数据
        
        Args:
            month: 当前月份（格式：'YYYY-MM'）
            stock_code: 股票代码
            
        Returns:
            Tuple[Dict[str, Any], str]: (JSON数据, 上一个月份字符串)
            如果找不到，抛出 ValueError
        """
        # 计算上一个月
        try:
            current_date = datetime.strptime(month, "%Y-%m")
            # 计算前一个月的日期
            if current_date.month == 1:
                previous_date = current_date.replace(year=current_date.year - 1, month=12)
            else:
                previous_date = current_date.replace(month=current_date.month - 1)
            
            previous_month = previous_date.strftime("%Y-%m")
            
            # 尝试加载上一个月的JSON数据
            data = ReadSaveFile.read_json(previous_month, stock_code)
            
            if data is not None:
                logger.info(f"找到上一个月 {previous_month} 的JSON数据")
                return data, previous_month
            else:
                # 如果上一个月没有，继续往前查找
                logger.warning(f"上一个月 {previous_month} 没有JSON数据，继续往前查找")
                # 递归查找更早的月份
                for i in range(2, 13):  # 最多查找12个月
                    if previous_date.month == 1:
                        previous_date = previous_date.replace(year=previous_date.year - 1, month=12)
                    else:
                        previous_date = previous_date.replace(month=previous_date.month - 1)
                    
                    previous_month = previous_date.strftime("%Y-%m")
                    data = ReadSaveFile.read_json(previous_month, stock_code)
                    
                    if data is not None:
                        logger.info(f"找到历史月份 {previous_month} 的JSON数据")
                        return data, previous_month
                
                # 如果都找不到，抛出异常
                raise ValueError(f"无法找到股票 {stock_code} 在月份 {month} 之前的JSON数据")
                
        except ValueError as e:
            logger.error(f"查找上一个月JSON数据失败: {e}")
            raise
    
    @staticmethod
    def modify_nested_dict(original_dict: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        修改嵌套字典，将updates中的值合并到original_dict中
        
        Args:
            original_dict: 原始字典
            updates: 要更新的字典
            
        Returns:
            dict: 更新后的字典
        """
        result = original_dict.copy() if original_dict else {}
        
        def merge_dict(base: Dict, update: Dict):
            """递归合并字典"""
            for key, value in update.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    merge_dict(base[key], value)
                else:
                    base[key] = value
        
        merge_dict(result, updates)
        return result
    
    @staticmethod
    def get_json_path(month: str, stock_code: str) -> Path:
        """
        获取JSON文件路径
        
        Args:
            month: 月份（格式：'YYYY-MM'）
            stock_code: 股票代码
            
        Returns:
            Path: JSON文件路径
        """
        root = file_root()
        return Path(root) / 'App' / 'codes' / 'code_data' / 'RnnData' / month / 'json' / f'{stock_code}.json'


if __name__ == '__main__':
    # 测试代码
    month = '2024-01'
    code = '000001'
    
    # 测试加载
    data = MyJsonData.loadJsonData(month, code)
    print(f"加载数据: {len(data)} 个键")
    
    # 测试查找上一个月
    try:
        prev_data, prev_month = MyJsonData.find_previous_month_json_parser(month, code)
        print(f"找到上一个月 {prev_month} 的数据")
    except ValueError as e:
        print(f"未找到上一个月数据: {e}")

