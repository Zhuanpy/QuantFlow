"""
服务层基础模块

提供统一的响应格式和基类
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ErrorCode(Enum):
    """错误码枚举"""
    # 通用错误 (1000-1099)
    SUCCESS = "0000"
    UNKNOWN_ERROR = "1000"
    VALIDATION_ERROR = "1001"
    NOT_FOUND = "1002"
    PERMISSION_DENIED = "1003"

    # 认证错误 (1100-1199)
    AUTH_FAILED = "1100"
    TOKEN_EXPIRED = "1101"
    TOKEN_INVALID = "1102"
    USER_NOT_FOUND = "1103"
    USER_LOCKED = "1104"
    PASSWORD_WRONG = "1105"

    # 数据错误 (1200-1299)
    DATA_NOT_FOUND = "1200"
    DATA_INVALID = "1201"
    DATA_DUPLICATE = "1202"
    DOWNLOAD_FAILED = "1203"

    # 策略错误 (1300-1399)
    STRATEGY_ERROR = "1300"
    SIGNAL_ERROR = "1301"
    BACKTEST_ERROR = "1302"

    # 交易错误 (1400-1499)
    TRADE_ERROR = "1400"
    INSUFFICIENT_FUNDS = "1401"
    INSUFFICIENT_POSITION = "1402"
    ORDER_REJECTED = "1403"

    # 报告错误 (1500-1599)
    REPORT_ERROR = "1500"
    REPORT_GENERATION_FAILED = "1501"

    # 风险错误 (1600-1699)
    RISK_ERROR = "1600"
    RISK_LIMIT_EXCEEDED = "1601"


@dataclass
class ServiceResponse:
    """
    统一服务响应格式

    所有服务方法应返回此格式的响应

    Attributes:
        success: 操作是否成功
        message: 响应消息
        data: 响应数据
        error_code: 错误码（可选）
        errors: 错误详情列表（可选）

    Usage:
        # 成功响应
        return ServiceResponse.ok(data={'user': user_info}, message='登录成功')

        # 失败响应
        return ServiceResponse.fail(
            message='用户名或密码错误',
            error_code=ErrorCode.AUTH_FAILED
        )
    """
    success: bool
    message: str
    data: Any = None
    error_code: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            'success': self.success,
            'message': self.message,
            'data': self.data
        }
        if self.error_code:
            result['error_code'] = self.error_code
        if self.errors:
            result['errors'] = self.errors
        return result

    @classmethod
    def ok(cls, data: Any = None, message: str = "操作成功") -> 'ServiceResponse':
        """创建成功响应"""
        return cls(
            success=True,
            message=message,
            data=data,
            error_code=ErrorCode.SUCCESS.value
        )

    @classmethod
    def fail(cls, message: str = "操作失败", error_code: ErrorCode = None,
             errors: List[str] = None, data: Any = None) -> 'ServiceResponse':
        """创建失败响应"""
        return cls(
            success=False,
            message=message,
            data=data,
            error_code=error_code.value if error_code else ErrorCode.UNKNOWN_ERROR.value,
            errors=errors or []
        )

    @classmethod
    def from_exception(cls, e: Exception, message: str = None) -> 'ServiceResponse':
        """从异常创建失败响应"""
        error_message = message or str(e)
        logger.error(f"Service exception: {error_message}", exc_info=True)
        return cls(
            success=False,
            message=error_message,
            error_code=ErrorCode.UNKNOWN_ERROR.value,
            errors=[str(e)]
        )


class BaseService:
    """
    服务基类

    所有服务类应继承此基类
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def _log_operation(self, operation: str, **kwargs):
        """记录操作日志"""
        self.logger.info(f"Operation: {operation}, params: {kwargs}")

    def _handle_exception(self, e: Exception, operation: str) -> ServiceResponse:
        """统一异常处理"""
        self.logger.error(f"Error in {operation}: {e}", exc_info=True)
        return ServiceResponse.from_exception(e, f"{operation}失败")
