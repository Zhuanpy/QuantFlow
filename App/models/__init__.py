"""
数据模型模块

包含所有数据模型，按功能分类：
- data: 基础数据模型
- evaluation: 评估模型
- strategy: 策略模型
- trade: 交易模型
"""

# 导入基础数据模型
from .data.basic_info import StockCodes, StockClassification
from .data.Stock1m import RecordStockMinute
from .data import (
    StockDaily,
    Stock1m,
    Stock15m,
    FundsAwkward
)

# 导入评估模型
from .evaluation import (
    CountBoard,
    CountStockPool,
    StrategyPerformance,
    RiskMetrics
)

# 导入策略模型
from .strategy import (
    RnnTrainingRecords,
    RnnRunningRecord,
    Top500FundRecord,
    Issue
)

# 导入交易模型
from .trade import (
    TradeRecord,
    Position,
    Account,
    TradeSignal
)

# 导出所有模型
__all__ = [
    # 基础数据模型
    'StockCodes',
    'StockClassification',
    'StockDaily',
    'Stock1m',
    'Stock15m',
    'FundsAwkward',
    'RecordStockMinute',
    
    # 评估模型
    'CountBoard',
    'CountStockPool',
    'StrategyPerformance',
    'RiskMetrics',
    
    # 策略模型
    'RnnTrainingRecords',
    'RnnRunningRecord',
    'Top500FundRecord',
    'Issue',
    
    # 交易模型
    'TradeRecord',
    'Position',
    'Account',
    'TradeSignal'
]
