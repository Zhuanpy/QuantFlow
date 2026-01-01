# -*- coding: utf-8 -*-
"""
交易记录仓库

提供交易记录的访问和操作
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import and_, or_, func
from sqlalchemy.exc import SQLAlchemyError

from App.repositories.base_repository import BaseRepository
from App.models import db

logger = logging.getLogger(__name__)


class TradeRepository(BaseRepository):
    """
    交易记录仓库

    提供交易记录的 CRUD 操作和业务查询
    """

    def __init__(self, model_class=None):
        """
        初始化交易记录仓库

        Args:
            model_class: 交易模型类，默认延迟导入
        """
        if model_class is None:
            # 延迟导入避免循环依赖
            try:
                from App.models.trade.TradeRecord import TradeRecord
                model_class = TradeRecord
            except ImportError:
                # 如果模型不存在，使用占位类
                model_class = None
                logger.warning("TradeRecord 模型未找到")

        if model_class:
            super().__init__(model_class)
        else:
            self.model_class = None

    # ==================== 交易记录查询 ====================

    def get_by_user(self, user_id: int, limit: int = None) -> List:
        """
        获取用户的交易记录

        Args:
            user_id: 用户ID
            limit: 最大返回数量

        Returns:
            List: 交易记录列表
        """
        if not self.model_class:
            return []

        try:
            query = self.model_class.query.filter_by(user_id=user_id)
            query = query.order_by(self.model_class.trade_time.desc())
            if limit:
                query = query.limit(limit)
            return query.all()
        except SQLAlchemyError as e:
            logger.error(f"查询用户交易记录失败: {e}")
            return []

    def get_by_stock(self, stock_code: str, user_id: int = None) -> List:
        """
        获取股票的交易记录

        Args:
            stock_code: 股票代码
            user_id: 用户ID（可选）

        Returns:
            List: 交易记录列表
        """
        if not self.model_class:
            return []

        try:
            query = self.model_class.query.filter_by(stock_code=stock_code)
            if user_id:
                query = query.filter_by(user_id=user_id)
            return query.order_by(self.model_class.trade_time.desc()).all()
        except SQLAlchemyError as e:
            logger.error(f"查询股票交易记录失败: {e}")
            return []

    def get_by_date_range(self, user_id: int, start_date: datetime,
                          end_date: datetime) -> List:
        """
        获取日期范围内的交易记录

        Args:
            user_id: 用户ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            List: 交易记录列表
        """
        if not self.model_class:
            return []

        try:
            return self.model_class.query.filter(
                and_(
                    self.model_class.user_id == user_id,
                    self.model_class.trade_time >= start_date,
                    self.model_class.trade_time <= end_date
                )
            ).order_by(self.model_class.trade_time.asc()).all()
        except SQLAlchemyError as e:
            logger.error(f"查询交易记录失败: {e}")
            return []

    def get_by_type(self, user_id: int, trade_type: str) -> List:
        """
        根据交易类型获取记录

        Args:
            user_id: 用户ID
            trade_type: 交易类型 ('buy', 'sell')

        Returns:
            List: 交易记录列表
        """
        if not self.model_class:
            return []

        try:
            return self.model_class.query.filter(
                and_(
                    self.model_class.user_id == user_id,
                    self.model_class.trade_type == trade_type
                )
            ).order_by(self.model_class.trade_time.desc()).all()
        except SQLAlchemyError as e:
            logger.error(f"查询交易记录失败: {e}")
            return []

    # ==================== 交易记录创建 ====================

    def create_trade(self, user_id: int, stock_code: str, trade_type: str,
                     price: float, quantity: int, **kwargs) -> Optional[Any]:
        """
        创建交易记录

        Args:
            user_id: 用户ID
            stock_code: 股票代码
            trade_type: 交易类型
            price: 交易价格
            quantity: 交易数量
            **kwargs: 其他属性

        Returns:
            Optional: 交易记录或 None
        """
        if not self.model_class:
            return None

        try:
            return self.create(
                user_id=user_id,
                stock_code=stock_code,
                trade_type=trade_type,
                price=price,
                quantity=quantity,
                trade_time=datetime.now(),
                **kwargs
            )
        except Exception as e:
            logger.error(f"创建交易记录失败: {e}")
            return None

    # ==================== 统计查询 ====================

    def get_total_profit(self, user_id: int, stock_code: str = None) -> float:
        """
        计算总盈亏

        Args:
            user_id: 用户ID
            stock_code: 股票代码（可选）

        Returns:
            float: 总盈亏金额
        """
        if not self.model_class:
            return 0.0

        try:
            query = self.model_class.query.filter_by(user_id=user_id)
            if stock_code:
                query = query.filter_by(stock_code=stock_code)

            records = query.all()

            total_profit = 0.0
            for record in records:
                if hasattr(record, 'profit'):
                    total_profit += float(record.profit or 0)

            return total_profit
        except SQLAlchemyError as e:
            logger.error(f"计算总盈亏失败: {e}")
            return 0.0

    def get_trade_summary(self, user_id: int) -> Dict[str, Any]:
        """
        获取交易汇总

        Args:
            user_id: 用户ID

        Returns:
            Dict: 交易汇总信息
        """
        if not self.model_class:
            return {}

        try:
            records = self.get_by_user(user_id)

            buy_count = sum(1 for r in records if getattr(r, 'trade_type', '') == 'buy')
            sell_count = sum(1 for r in records if getattr(r, 'trade_type', '') == 'sell')

            total_buy_amount = sum(
                float(getattr(r, 'price', 0) or 0) * int(getattr(r, 'quantity', 0) or 0)
                for r in records if getattr(r, 'trade_type', '') == 'buy'
            )

            total_sell_amount = sum(
                float(getattr(r, 'price', 0) or 0) * int(getattr(r, 'quantity', 0) or 0)
                for r in records if getattr(r, 'trade_type', '') == 'sell'
            )

            return {
                'total_trades': len(records),
                'buy_count': buy_count,
                'sell_count': sell_count,
                'total_buy_amount': total_buy_amount,
                'total_sell_amount': total_sell_amount,
                'net_amount': total_sell_amount - total_buy_amount,
            }
        except Exception as e:
            logger.error(f"获取交易汇总失败: {e}")
            return {}

    def get_holding_stocks(self, user_id: int) -> Dict[str, Dict[str, Any]]:
        """
        获取持仓股票

        Args:
            user_id: 用户ID

        Returns:
            Dict: 持仓股票信息，按股票代码分组
        """
        if not self.model_class:
            return {}

        try:
            records = self.get_by_user(user_id)

            holdings = {}
            for record in records:
                stock_code = getattr(record, 'stock_code', '')
                trade_type = getattr(record, 'trade_type', '')
                quantity = int(getattr(record, 'quantity', 0) or 0)
                price = float(getattr(record, 'price', 0) or 0)

                if stock_code not in holdings:
                    holdings[stock_code] = {
                        'quantity': 0,
                        'avg_cost': 0.0,
                        'total_cost': 0.0,
                    }

                if trade_type == 'buy':
                    old_quantity = holdings[stock_code]['quantity']
                    old_cost = holdings[stock_code]['total_cost']

                    holdings[stock_code]['quantity'] += quantity
                    holdings[stock_code]['total_cost'] = old_cost + (price * quantity)

                    if holdings[stock_code]['quantity'] > 0:
                        holdings[stock_code]['avg_cost'] = (
                            holdings[stock_code]['total_cost'] /
                            holdings[stock_code]['quantity']
                        )

                elif trade_type == 'sell':
                    holdings[stock_code]['quantity'] -= quantity
                    if holdings[stock_code]['quantity'] > 0:
                        holdings[stock_code]['total_cost'] = (
                            holdings[stock_code]['avg_cost'] *
                            holdings[stock_code]['quantity']
                        )
                    else:
                        holdings[stock_code]['total_cost'] = 0
                        holdings[stock_code]['avg_cost'] = 0

            # 过滤掉零持仓
            return {k: v for k, v in holdings.items() if v['quantity'] > 0}

        except Exception as e:
            logger.error(f"获取持仓股票失败: {e}")
            return {}
