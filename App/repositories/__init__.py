# -*- coding: utf-8 -*-
"""
Repository 层

提供数据访问层，分离业务逻辑和数据库操作：
- base_repository: 基础仓库类
- stock_repository: 股票数据仓库
- trade_repository: 交易记录仓库
- model_repository: 模型数据仓库
"""

from App.repositories.base_repository import BaseRepository
from App.repositories.stock_repository import StockRepository
from App.repositories.trade_repository import TradeRepository
from App.repositories.model_repository import ModelRepository

__all__ = [
    'BaseRepository',
    'StockRepository',
    'TradeRepository',
    'ModelRepository',
]
