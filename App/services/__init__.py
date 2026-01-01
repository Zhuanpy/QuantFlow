"""
服务层模块

提供业务逻辑服务，所有路由应通过服务层访问业务功能
"""

# 基础类
from App.services.base import ServiceResponse, ErrorCode, BaseService

# 认证服务
from App.services.auth_service import AuthService

# 数据服务
from App.services.data_service import (
    DataService,
    DataValidationService,
    DataCleanService,
    data_service,
    validation_service,
    clean_service
)

# 策略服务
from App.services.strategy_service import StrategyService, strategy_service

# 交易服务
from App.services.trade_service import (
    TradeService,
    RiskManagementService,
    trade_service,
    risk_service as trade_risk_service  # 重命名避免冲突
)

# 报告服务
from App.services.report_service import ReportService, report_service

# 风险服务
from App.services.risk_service import RiskService, risk_service

__all__ = [
    # 基础类
    'ServiceResponse',
    'ErrorCode',
    'BaseService',

    # 认证
    'AuthService',

    # 数据
    'DataService',
    'DataValidationService',
    'DataCleanService',
    'data_service',
    'validation_service',
    'clean_service',

    # 策略
    'StrategyService',
    'strategy_service',

    # 交易
    'TradeService',
    'RiskManagementService',
    'trade_service',
    'trade_risk_service',

    # 报告
    'ReportService',
    'report_service',

    # 风险
    'RiskService',
    'risk_service',
]
