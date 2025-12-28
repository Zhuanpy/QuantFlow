import os
from pathlib import Path
from config import Config
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def ensure_data_directories():
    """
    确保所有必要的数据目录都存在
    """
    # 获取项目根目录
    base_dir = Path(Config.get_project_root())
    
    # 定义需要创建的目录
    # 注意：password 文件夹已迁移到 config.py，不再需要创建
    directories = [
        base_dir / 'App' / 'codes' / 'code_data',
    ]
    
    # 创建目录
    for directory in directories:
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            print(f"Created directory: {directory}")

def get_stock_data_path(stock_code: str, data_type: str = '1m', year: str = None, quarter: str = None, create: bool = True) -> str:
    """
    获取股票数据的存储路径
    
    Args:
        stock_code: 股票代码
        data_type: 数据类型，可选 '1m', '15m', '15m_normal', '15m_standardized', 'daily', 'real_time'
        year: 年份，如果为None则使用当前年份
        quarter: 季度，如果为None则使用当前季度
        create: 是否创建目录
        
    Returns:
        str: 数据存储路径
    """
    # 获取当前年月
    now = datetime.now()
    
    # 使用传入的年份和季度，如果没有则使用当前日期
    if year is None:
        year = str(now.year)
    else:
        year = str(year)
    
    if quarter is None:
        # 计算当前季度
        quarter_num = (now.month - 1) // 3 + 1
        quarter_str = f"Q{quarter_num}"
    else:
        quarter_str = quarter
    
    # 根据数据类型构建不同的路径
    if data_type == '1m':
        # 1分钟数据按季度保存
        base_dir = os.path.join(Config.get_project_root(), 'data', 'data', 'quarters', year, quarter_str)
    elif data_type == '15m' or data_type == '15m_normal':
        # 15分钟原始数据统一保存到15m文件夹，不分季度
        base_dir = os.path.join(Config.get_project_root(), 'data', 'data', '15m')
    elif data_type == '15m_standardized':
        # 15分钟标准化数据统一保存到15m_standardized文件夹，不分季度
        base_dir = os.path.join(Config.get_project_root(), 'data', 'data', '15m_standardized')
    elif data_type == 'daily':
        # 日线数据保存到daily文件夹
        base_dir = os.path.join(Config.get_project_root(), 'data', 'data', 'daily', year, quarter_str)
    else:
        # 其他类型数据按季度保存
        base_dir = os.path.join(Config.get_project_root(), 'data', 'data', 'quarters', year, quarter_str)
    
    if create and not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"创建目录: {base_dir}")
    
    return os.path.join(base_dir, f"{stock_code}.csv")

def get_processed_data_path(data_type: str, filename: str, create: bool = True) -> str:
    """
    获取处理后数据的存储路径
    
    Args:
        data_type: 数据类型，可选 'features', 'signals', 'indicators'
        filename: 文件名
        create: 是否创建目录
        
    Returns:
        str: 数据存储路径
    """
    base_dir = os.path.join(Config.get_project_root(), 'data', 'processed', data_type)
    
    if create and not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"创建目录: {base_dir}")
    
    return os.path.join(base_dir, filename)

def get_model_path(model_type: str, filename: str, create: bool = True) -> str:
    """
    获取模型相关文件的存储路径
    
    Args:
        model_type: 模型类型，可选 'trained', 'checkpoints', 'predictions'
        filename: 文件名
        create: 是否创建目录
        
    Returns:
        str: 模型文件存储路径
    """
    base_dir = os.path.join(Config.get_project_root(), 'data', 'models', model_type)
    
    if create and not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"创建目录: {base_dir}")
    
    return os.path.join(base_dir, filename)

def get_temp_path(filename: str, create: bool = True) -> str:
    """
    获取临时文件的存储路径
    
    Args:
        filename: 文件名
        create: 是否创建目录
        
    Returns:
        str: 临时文件存储路径
    """
    base_dir = os.path.join(Config.get_project_root(), 'data', 'temp')
    
    if create and not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"创建目录: {base_dir}")
    
    return os.path.join(base_dir, filename) 