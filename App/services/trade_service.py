"""
交易服务层
整合自动交易、订单管理、持仓管理等功能
"""

import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import sys
import time

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from App.exts import db
from config import Config

logger = logging.getLogger(__name__)


class TradeService:
    """
    交易服务类
    提供交易执行、订单管理、持仓管理功能
    """
    
    def __init__(self):
        self.config = Config()
        self.positions = {}  # 当前持仓
        self.orders = []     # 订单历史
        self.capital = 100000  # 初始资金
    
    def place_buy_order(self, stock_code: str, quantity: int, price: float = None, 
                       order_type: str = 'market') -> Dict[str, Any]:
        """
        下买单
        
        Args:
            stock_code: 股票代码
            quantity: 数量
            price: 价格（市价单可为None）
            order_type: 订单类型（market/limit）
            
        Returns:
            Dict: 订单信息
        """
        try:
            order_id = f"BUY_{stock_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            order = {
                'order_id': order_id,
                'stock_code': stock_code,
                'action': 'buy',
                'quantity': quantity,
                'price': price,
                'order_type': order_type,
                'status': 'pending',
                'created_at': datetime.now(),
                'executed_at': None,
                'executed_price': None
            }
            
            # 检查资金是否足够
            if price:
                required_capital = quantity * price
            else:
                # 市价单，使用当前价格估算
                current_price = self._get_current_price(stock_code)
                required_capital = quantity * current_price
            
            if required_capital > self.capital:
                order['status'] = 'rejected'
                order['error'] = '资金不足'
                logger.warning(f"买单被拒绝，资金不足: {stock_code}, 需要: {required_capital}, 可用: {self.capital}")
            else:
                # 模拟订单执行
                executed_price = price or self._get_current_price(stock_code)
                order['status'] = 'executed'
                order['executed_at'] = datetime.now()
                order['executed_price'] = executed_price
                
                # 更新资金和持仓
                self.capital -= quantity * executed_price
                if stock_code in self.positions:
                    self.positions[stock_code]['quantity'] += quantity
                    self.positions[stock_code]['avg_price'] = (
                        (self.positions[stock_code]['quantity'] * self.positions[stock_code]['avg_price'] + 
                         quantity * executed_price) / (self.positions[stock_code]['quantity'] + quantity)
                    )
                else:
                    self.positions[stock_code] = {
                        'quantity': quantity,
                        'avg_price': executed_price,
                        'first_buy_date': datetime.now()
                    }
                
                logger.info(f"买单执行成功: {stock_code}, 数量: {quantity}, 价格: {executed_price}")
            
            self.orders.append(order)
            return order
            
        except Exception as e:
            logger.error(f"下买单时发生错误: {e}")
            return {'error': str(e)}
    
    def place_sell_order(self, stock_code: str, quantity: int, price: float = None, 
                        order_type: str = 'market') -> Dict[str, Any]:
        """
        下卖单
        
        Args:
            stock_code: 股票代码
            quantity: 数量
            price: 价格（市价单可为None）
            order_type: 订单类型（market/limit）
            
        Returns:
            Dict: 订单信息
        """
        try:
            order_id = f"SELL_{stock_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            order = {
                'order_id': order_id,
                'stock_code': stock_code,
                'action': 'sell',
                'quantity': quantity,
                'price': price,
                'order_type': order_type,
                'status': 'pending',
                'created_at': datetime.now(),
                'executed_at': None,
                'executed_price': None
            }
            
            # 检查持仓是否足够
            if stock_code not in self.positions or self.positions[stock_code]['quantity'] < quantity:
                order['status'] = 'rejected'
                order['error'] = '持仓不足'
                logger.warning(f"卖单被拒绝，持仓不足: {stock_code}, 需要: {quantity}, 持有: {self.positions.get(stock_code, {}).get('quantity', 0)}")
            else:
                # 模拟订单执行
                executed_price = price or self._get_current_price(stock_code)
                order['status'] = 'executed'
                order['executed_at'] = datetime.now()
                order['executed_price'] = executed_price
                
                # 更新资金和持仓
                self.capital += quantity * executed_price
                self.positions[stock_code]['quantity'] -= quantity
                
                # 如果持仓为0，删除持仓记录
                if self.positions[stock_code]['quantity'] <= 0:
                    del self.positions[stock_code]
                
                logger.info(f"卖单执行成功: {stock_code}, 数量: {quantity}, 价格: {executed_price}")
            
            self.orders.append(order)
            return order
            
        except Exception as e:
            logger.error(f"下卖单时发生错误: {e}")
            return {'error': str(e)}
    
    def get_positions(self) -> Dict[str, Any]:
        """
        获取当前持仓
        
        Returns:
            Dict: 持仓信息
        """
        try:
            positions_info = {}
            total_value = 0
            
            for stock_code, position in self.positions.items():
                current_price = self._get_current_price(stock_code)
                market_value = position['quantity'] * current_price
                unrealized_pnl = market_value - (position['quantity'] * position['avg_price'])
                
                positions_info[stock_code] = {
                    'quantity': position['quantity'],
                    'avg_price': position['avg_price'],
                    'current_price': current_price,
                    'market_value': market_value,
                    'unrealized_pnl': unrealized_pnl,
                    'unrealized_pnl_pct': (unrealized_pnl / (position['quantity'] * position['avg_price'])) * 100,
                    'first_buy_date': position['first_buy_date']
                }
                
                total_value += market_value
            
            return {
                'positions': positions_info,
                'total_positions': len(positions_info),
                'total_value': total_value,
                'available_capital': self.capital,
                'total_capital': self.capital + total_value
            }
            
        except Exception as e:
            logger.error(f"获取持仓时发生错误: {e}")
            return {}
    
    def get_order_history(self, stock_code: str = None, start_date: datetime = None, 
                         end_date: datetime = None) -> List[Dict[str, Any]]:
        """
        获取订单历史
        
        Args:
            stock_code: 股票代码过滤
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            List[Dict]: 订单历史列表
        """
        try:
            filtered_orders = self.orders.copy()
            
            # 按股票代码过滤
            if stock_code:
                filtered_orders = [order for order in filtered_orders if order['stock_code'] == stock_code]
            
            # 按日期过滤
            if start_date:
                filtered_orders = [order for order in filtered_orders if order['created_at'] >= start_date]
            
            if end_date:
                filtered_orders = [order for order in filtered_orders if order['created_at'] <= end_date]
            
            # 按时间排序
            filtered_orders.sort(key=lambda x: x['created_at'], reverse=True)
            
            return filtered_orders
            
        except Exception as e:
            logger.error(f"获取订单历史时发生错误: {e}")
            return []
    
    def get_trading_summary(self) -> Dict[str, Any]:
        """
        获取交易汇总
        
        Returns:
            Dict: 交易汇总信息
        """
        try:
            # 计算交易统计
            total_orders = len(self.orders)
            executed_orders = len([order for order in self.orders if order['status'] == 'executed'])
            rejected_orders = len([order for order in self.orders if order['status'] == 'rejected'])
            
            # 计算盈亏
            total_buy_amount = sum([
                order['quantity'] * order['executed_price'] 
                for order in self.orders 
                if order['action'] == 'buy' and order['status'] == 'executed'
            ])
            
            total_sell_amount = sum([
                order['quantity'] * order['executed_price'] 
                for order in self.orders 
                if order['action'] == 'sell' and order['status'] == 'executed'
            ])
            
            realized_pnl = total_sell_amount - total_buy_amount
            
            # 计算未实现盈亏
            positions = self.get_positions()
            unrealized_pnl = sum([
                pos['unrealized_pnl'] for pos in positions.get('positions', {}).values()
            ])
            
            return {
                'total_orders': total_orders,
                'executed_orders': executed_orders,
                'rejected_orders': rejected_orders,
                'execution_rate': (executed_orders / total_orders * 100) if total_orders > 0 else 0,
                'total_buy_amount': total_buy_amount,
                'total_sell_amount': total_sell_amount,
                'realized_pnl': realized_pnl,
                'unrealized_pnl': unrealized_pnl,
                'total_pnl': realized_pnl + unrealized_pnl,
                'available_capital': self.capital,
                'total_capital': positions.get('total_capital', self.capital)
            }
            
        except Exception as e:
            logger.error(f"获取交易汇总时发生错误: {e}")
            return {}
    
    def execute_strategy_signals(self, signals: pd.DataFrame, stock_code: str, 
                               capital_ratio: float = 0.1) -> List[Dict[str, Any]]:
        """
        执行策略信号
        
        Args:
            signals: 包含信号的数据框
            stock_code: 股票代码
            capital_ratio: 每次交易使用的资金比例
            
        Returns:
            List[Dict]: 执行的订单列表
        """
        try:
            executed_orders = []
            
            for index, row in signals.iterrows():
                if 'final_signal' not in row:
                    continue
                
                signal = row['final_signal']
                price = row.get('close', 0)
                
                if signal == 1:  # 买入信号
                    # 计算可买数量
                    available_capital = self.capital * capital_ratio
                    quantity = int(available_capital / price)
                    
                    if quantity > 0:
                        order = self.place_buy_order(stock_code, quantity, price, 'market')
                        if order.get('status') == 'executed':
                            executed_orders.append(order)
                
                elif signal == -1:  # 卖出信号
                    # 检查是否有持仓
                    if stock_code in self.positions:
                        quantity = self.positions[stock_code]['quantity']
                        if quantity > 0:
                            order = self.place_sell_order(stock_code, quantity, price, 'market')
                            if order.get('status') == 'executed':
                                executed_orders.append(order)
            
            logger.info(f"策略信号执行完成，执行订单数: {len(executed_orders)}")
            return executed_orders
            
        except Exception as e:
            logger.error(f"执行策略信号时发生错误: {e}")
            return []
    
    def _get_current_price(self, stock_code: str) -> float:
        """
        获取当前价格（模拟）
        
        Args:
            stock_code: 股票代码
            
        Returns:
            float: 当前价格
        """
        # 这里应该调用实际的价格获取接口
        # 暂时返回模拟价格
        import random
        return round(random.uniform(10, 100), 2)


class RiskManagementService:
    """
    风险管理服务
    提供止损、止盈、仓位控制等功能
    """
    
    def __init__(self):
        self.stop_loss_ratio = 0.05  # 止损比例
        self.take_profit_ratio = 0.10  # 止盈比例
        self.max_position_ratio = 0.2  # 最大仓位比例
    
    def check_stop_loss(self, positions: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        检查止损条件
        
        Args:
            positions: 持仓信息
            
        Returns:
            List[Dict]: 需要止损的订单
        """
        try:
            stop_loss_orders = []
            
            for stock_code, position in positions.get('positions', {}).items():
                unrealized_pnl_pct = position['unrealized_pnl_pct']
                
                if unrealized_pnl_pct <= -self.stop_loss_ratio * 100:
                    stop_loss_orders.append({
                        'stock_code': stock_code,
                        'action': 'sell',
                        'reason': 'stop_loss',
                        'quantity': position['quantity'],
                        'current_loss_pct': unrealized_pnl_pct
                    })
            
            return stop_loss_orders
            
        except Exception as e:
            logger.error(f"检查止损时发生错误: {e}")
            return []
    
    def check_take_profit(self, positions: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        检查止盈条件
        
        Args:
            positions: 持仓信息
            
        Returns:
            List[Dict]: 需要止盈的订单
        """
        try:
            take_profit_orders = []
            
            for stock_code, position in positions.get('positions', {}).items():
                unrealized_pnl_pct = position['unrealized_pnl_pct']
                
                if unrealized_pnl_pct >= self.take_profit_ratio * 100:
                    take_profit_orders.append({
                        'stock_code': stock_code,
                        'action': 'sell',
                        'reason': 'take_profit',
                        'quantity': position['quantity'],
                        'current_profit_pct': unrealized_pnl_pct
                    })
            
            return take_profit_orders
            
        except Exception as e:
            logger.error(f"检查止盈时发生错误: {e}")
            return []
    
    def calculate_position_size(self, capital: float, price: float, risk_per_trade: float = 0.02) -> int:
        """
        计算仓位大小
        
        Args:
            capital: 可用资金
            price: 股票价格
            risk_per_trade: 每笔交易风险比例
            
        Returns:
            int: 建议的购买数量
        """
        try:
            # 基于风险的资金管理
            risk_amount = capital * risk_per_trade
            stop_loss_amount = price * self.stop_loss_ratio
            
            if stop_loss_amount > 0:
                quantity = int(risk_amount / stop_loss_amount)
            else:
                quantity = 0
            
            # 检查最大仓位限制
            max_quantity = int(capital * self.max_position_ratio / price)
            quantity = min(quantity, max_quantity)
            
            return quantity
            
        except Exception as e:
            logger.error(f"计算仓位大小时发生错误: {e}")
            return 0


# 创建服务实例
trade_service = TradeService()
risk_service = RiskManagementService()
