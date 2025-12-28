# -*- coding: utf-8 -*-
"""
股票数据路径管理模块
已迁移到使用 config.py 统一管理路径
"""
import os
from config import Config


def file_root():
    """返回项目根目录路径"""
    return Config.get_project_root()


class StockDataPath:
    """股票数据路径管理类"""
    data_path = None
    
    @classmethod
    def _get_data_path(cls):
        """获取数据路径（延迟初始化）"""
        if cls.data_path is None:
            cls.data_path = os.path.join(file_root(), 'App', 'codes', 'code_data')
        return cls.data_path
    
    @classmethod
    def json_data_path(cls, months: str, code: str) -> str:
        """获取JSON数据文件路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'RnnData', months, 'json', f'{code}.json')
    
    @classmethod
    def month_1m_data_path(cls, month_parser: str) -> str:
        """获取指定月份的1分钟数据路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'RnnData', month_parser, '1m')
    
    @classmethod
    def monitor_1m_data_path(cls, stock_code: str) -> str:
        """获取监控1分钟数据路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'input', 'monitor', f'{stock_code}.csv')
    
    @classmethod
    def model_path(cls, month_parser: str, file_name: str) -> str:
        """获取模型文件路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'RnnData', month_parser, 'model', file_name)
    
    @classmethod
    def model_weight_path(cls, month_parser: str, file_name: str) -> str:
        """获取模型权重文件路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'RnnData', month_parser, 'weight', file_name)
    
    @classmethod
    def train_data_path(cls, month_parser: str, file_name: str) -> str:
        """获取训练数据文件路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'RnnData', month_parser, 'train_data', file_name)
    
    @classmethod
    def columns_name_path(cls) -> str:
        """获取列名配置文件路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'columns')
    
    @classmethod
    def rnnData_folder_path(cls) -> str:
        """获取RNN数据文件夹路径"""
        data_path = cls._get_data_path()
        return os.path.join(data_path, 'RnnData')
    
    @classmethod
    def get_stock_data_directory(cls) -> str:
        """获取股票数据根目录"""
        # 返回 data/data 目录
        return os.path.join(file_root(), 'data', 'data')


class AnalysisDataPath:
    """分析数据路径管理类"""
    data_path = None
    
    @classmethod
    def _get_data_path(cls):
        """获取数据路径（延迟初始化）"""
        if cls.data_path is None:
            cls.data_path = os.path.join(file_root(), 'App', 'codes', 'code_data')
        return cls.data_path
    
    @classmethod
    def macd_predict_path(cls, file_name: str) -> str:
        """
        获取MACD预测图片路径
        
        Args:
            file_name: 文件名（如 '000001.jpg'）
            
        Returns:
            str: 完整文件路径
        """
        data_path = cls._get_data_path()
        predict_dir = os.path.join(data_path, 'AnalysisData', 'macd', 'predict')
        os.makedirs(predict_dir, exist_ok=True)
        return os.path.join(predict_dir, file_name)
    
    @classmethod
    def macd_model_path(cls, file_name: str) -> str:
        """
        获取MACD模型文件路径
        
        Args:
            file_name: 文件名（如 'model.h5'）
            
        Returns:
            str: 完整文件路径
        """
        data_path = cls._get_data_path()
        model_dir = os.path.join(data_path, 'AnalysisData', 'macd', 'model')
        os.makedirs(model_dir, exist_ok=True)
        return os.path.join(model_dir, file_name)
    
    @classmethod
    def macd_predict_trends_path(cls, file_name: str) -> str:
        """
        获取MACD预测趋势图片路径
        
        Args:
            file_name: 文件名（如 '_up_000001.jpg'）
            
        Returns:
            str: 完整文件路径
        """
        data_path = cls._get_data_path()
        trends_dir = os.path.join(data_path, 'AnalysisData', 'macd', 'predict', 'trends')
        os.makedirs(trends_dir, exist_ok=True)
        return os.path.join(trends_dir, file_name)
    
    @classmethod
    def macd_train_path(cls, folder: str, file_name: str) -> str:
        """
        获取MACD训练数据路径
        
        Args:
            folder: 文件夹名称（如 '_up', 'down_', '_down', 'up_'）
            file_name: 文件名（如 '000001.jpg' 或 '000001.npy'）
            
        Returns:
            str: 完整文件路径
        """
        data_path = cls._get_data_path()
        train_dir = os.path.join(data_path, 'AnalysisData', 'macd', 'train', folder)
        os.makedirs(train_dir, exist_ok=True)
        return os.path.join(train_dir, file_name)


def get_stock_data_path(stock_code: str, data_type: str = '1m') -> str:
    """获取股票数据文件路径（兼容函数）"""
    from App.utils.file_utils import get_stock_data_path as get_path
    return get_path(stock_code, data_type)

