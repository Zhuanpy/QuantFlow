# -*- coding: utf-8 -*-
"""
SQL工具函数
提供股票信息查询等常用功能
"""
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# 尝试导入 Flask 相关模块（如果可用）
try:
    from App.exts import db
    from App.models.data.basic_info import StockCodes
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.warning("Flask 模块不可用，sql_utils 功能可能受限")


def Stocks(stock: str) -> Tuple[str, str, Optional[int]]:
    """
    根据股票代码或名称获取股票信息
    
    Args:
        stock: 股票代码或股票名称
        
    Returns:
        Tuple[str, str, Optional[int]]: (stock_name, stock_code, stock_id)
        如果找不到，返回 (stock, stock, None)
    
    示例:
        >>> name, code, stock_id = Stocks('000001')
        >>> name, code, stock_id = Stocks('平安银行')
    """
    if not FLASK_AVAILABLE:
        logger.warning("Flask 模块不可用，返回默认值")
        return stock, stock, None
    
    try:
        # 尝试通过代码查找
        stock_record = StockCodes.query.filter_by(code=stock).first()
        
        # 如果通过代码找不到，尝试通过名称查找
        if not stock_record:
            stock_record = StockCodes.query.filter_by(name=stock).first()
        
        # 如果还是找不到，尝试通过 EsCode 查找
        if not stock_record:
            stock_record = StockCodes.query.filter_by(EsCode=stock).first()
        
        if stock_record:
            stock_name = stock_record.name or stock
            stock_code = stock_record.code or stock
            stock_id = stock_record.id
            logger.debug(f"找到股票: {stock_name} ({stock_code}), ID: {stock_id}")
            return stock_name, stock_code, stock_id
        else:
            logger.warning(f"未找到股票信息: {stock}，返回默认值")
            return stock, stock, None
            
    except Exception as e:
        logger.error(f"查询股票信息失败: {e}", exc_info=True)
        return stock, stock, None


def get_stock_by_code(stock_code: str) -> Optional[StockCodes]:
    """
    根据股票代码获取股票记录
    
    Args:
        stock_code: 股票代码
        
    Returns:
        StockCodes: 股票记录对象，如果找不到返回 None
    """
    if not FLASK_AVAILABLE:
        return None
    
    try:
        return StockCodes.query.filter_by(code=stock_code).first()
    except Exception as e:
        logger.error(f"查询股票代码失败: {e}")
        return None


def get_stock_by_name(stock_name: str) -> Optional[StockCodes]:
    """
    根据股票名称获取股票记录
    
    Args:
        stock_name: 股票名称
        
    Returns:
        StockCodes: 股票记录对象，如果找不到返回 None
    """
    if not FLASK_AVAILABLE:
        return None
    
    try:
        return StockCodes.query.filter_by(name=stock_name).first()
    except Exception as e:
        logger.error(f"查询股票名称失败: {e}")
        return None


def get_stock_by_escode(escode: str) -> Optional[StockCodes]:
    """
    根据东方财富代码获取股票记录
    
    Args:
        escode: 东方财富代码
        
    Returns:
        StockCodes: 股票记录对象，如果找不到返回 None
    """
    if not FLASK_AVAILABLE:
        return None
    
    try:
        return StockCodes.query.filter_by(EsCode=escode).first()
    except Exception as e:
        logger.error(f"查询东方财富代码失败: {e}")
        return None


if __name__ == '__main__':
    # 测试代码
    name, code, stock_id = Stocks('000001')
    print(f"股票名称: {name}, 股票代码: {code}, 股票ID: {stock_id}")

