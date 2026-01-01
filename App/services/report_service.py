"""
报告服务层

提供交易报告、策略报告、数据分析报告等生成功能
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path
import sys

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from App.services.base import BaseService, ServiceResponse, ErrorCode
from App.exts import db

logger = logging.getLogger(__name__)


class ReportService(BaseService):
    """
    报告服务类

    提供各类报告生成功能：
    - 交易报告：交易记录、盈亏统计
    - 策略报告：回测结果、信号统计
    - 数据报告：数据质量、下载统计
    - 持仓报告：当前持仓、风险敞口
    """

    def __init__(self):
        super().__init__()

    def generate_trading_report(self, start_date: datetime = None,
                                 end_date: datetime = None,
                                 stock_code: str = None) -> ServiceResponse:
        """
        生成交易报告

        Args:
            start_date: 开始日期
            end_date: 结束日期
            stock_code: 股票代码（可选，不传则生成全部）

        Returns:
            ServiceResponse: 包含交易报告数据
        """
        try:
            self._log_operation("generate_trading_report",
                               start_date=start_date, end_date=end_date, stock_code=stock_code)

            # 设置默认日期范围
            if not end_date:
                end_date = datetime.now()
            if not start_date:
                start_date = end_date - timedelta(days=30)

            # 获取交易服务
            from App.services.trade_service import trade_service

            # 获取订单历史
            orders = trade_service.get_order_history(stock_code, start_date, end_date)

            # 计算统计数据
            total_orders = len(orders)
            buy_orders = [o for o in orders if o.get('action') == 'buy']
            sell_orders = [o for o in orders if o.get('action') == 'sell']
            executed_orders = [o for o in orders if o.get('status') == 'executed']

            # 计算盈亏
            total_buy_amount = sum(
                o.get('quantity', 0) * o.get('executed_price', 0)
                for o in buy_orders if o.get('status') == 'executed'
            )
            total_sell_amount = sum(
                o.get('quantity', 0) * o.get('executed_price', 0)
                for o in sell_orders if o.get('status') == 'executed'
            )

            # 按日期分组统计
            daily_stats = self._calculate_daily_stats(orders)

            report = {
                'report_type': 'trading',
                'period': {
                    'start_date': start_date.isoformat() if start_date else None,
                    'end_date': end_date.isoformat() if end_date else None
                },
                'summary': {
                    'total_orders': total_orders,
                    'buy_orders': len(buy_orders),
                    'sell_orders': len(sell_orders),
                    'executed_orders': len(executed_orders),
                    'execution_rate': (len(executed_orders) / total_orders * 100) if total_orders > 0 else 0,
                    'total_buy_amount': total_buy_amount,
                    'total_sell_amount': total_sell_amount,
                    'net_amount': total_sell_amount - total_buy_amount
                },
                'daily_stats': daily_stats,
                'generated_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=report, message="交易报告生成成功")

        except Exception as e:
            return self._handle_exception(e, "生成交易报告")

    def generate_strategy_report(self, strategy_name: str = None,
                                  backtest_result: Dict = None) -> ServiceResponse:
        """
        生成策略报告

        Args:
            strategy_name: 策略名称
            backtest_result: 回测结果数据

        Returns:
            ServiceResponse: 包含策略报告数据
        """
        try:
            self._log_operation("generate_strategy_report", strategy_name=strategy_name)

            if not backtest_result:
                return ServiceResponse.fail(
                    message="未提供回测结果",
                    error_code=ErrorCode.VALIDATION_ERROR
                )

            # 解析回测结果
            initial_capital = backtest_result.get('initial_capital', 0)
            final_capital = backtest_result.get('final_capital', 0)
            total_return = backtest_result.get('total_return', 0)
            max_drawdown = backtest_result.get('max_drawdown', 0)
            sharpe_ratio = backtest_result.get('sharpe_ratio', 0)
            total_trades = backtest_result.get('total_trades', 0)

            # 计算额外指标
            profit_factor = self._calculate_profit_factor(backtest_result.get('trades', []))
            win_rate = self._calculate_win_rate(backtest_result.get('trades', []))

            report = {
                'report_type': 'strategy',
                'strategy_name': strategy_name or 'Unknown',
                'performance': {
                    'initial_capital': initial_capital,
                    'final_capital': final_capital,
                    'total_return': f"{total_return:.2%}",
                    'total_return_value': total_return,
                    'max_drawdown': f"{max_drawdown:.2%}",
                    'max_drawdown_value': max_drawdown,
                    'sharpe_ratio': round(sharpe_ratio, 2),
                    'profit_factor': round(profit_factor, 2),
                    'win_rate': f"{win_rate:.2%}",
                    'total_trades': total_trades
                },
                'risk_metrics': {
                    'max_drawdown': max_drawdown,
                    'sharpe_ratio': sharpe_ratio,
                    'risk_adjusted_return': total_return / abs(max_drawdown) if max_drawdown != 0 else 0
                },
                'generated_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=report, message="策略报告生成成功")

        except Exception as e:
            return self._handle_exception(e, "生成策略报告")

    def generate_data_quality_report(self, stock_code: str = None) -> ServiceResponse:
        """
        生成数据质量报告

        Args:
            stock_code: 股票代码（可选）

        Returns:
            ServiceResponse: 包含数据质量报告
        """
        try:
            self._log_operation("generate_data_quality_report", stock_code=stock_code)

            from App.services.data_service import data_service

            # 获取数据统计
            stats = data_service.get_data_statistics()

            report = {
                'report_type': 'data_quality',
                'stock_code': stock_code,
                'statistics': {
                    'total_records': stats.get('total_records', 0),
                    'pending_records': stats.get('pending_records', 0),
                    'success_records': stats.get('success_records', 0),
                    'failed_records': stats.get('failed_records', 0),
                    'recent_downloads': stats.get('recent_downloads', 0),
                    'success_rate': f"{stats.get('success_rate', 0):.2f}%"
                },
                'quality_score': self._calculate_quality_score(stats),
                'recommendations': self._generate_data_recommendations(stats),
                'generated_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=report, message="数据质量报告生成成功")

        except Exception as e:
            return self._handle_exception(e, "生成数据质量报告")

    def generate_position_report(self) -> ServiceResponse:
        """
        生成持仓报告

        Returns:
            ServiceResponse: 包含持仓报告数据
        """
        try:
            self._log_operation("generate_position_report")

            from App.services.trade_service import trade_service

            positions = trade_service.get_positions()
            summary = trade_service.get_trading_summary()

            # 计算持仓集中度
            total_value = positions.get('total_value', 0)
            position_details = positions.get('positions', {})

            concentration = {}
            for code, pos in position_details.items():
                market_value = pos.get('market_value', 0)
                concentration[code] = (market_value / total_value * 100) if total_value > 0 else 0

            report = {
                'report_type': 'position',
                'summary': {
                    'total_positions': positions.get('total_positions', 0),
                    'total_value': total_value,
                    'available_capital': positions.get('available_capital', 0),
                    'total_capital': positions.get('total_capital', 0),
                    'position_ratio': (total_value / positions.get('total_capital', 1) * 100)
                        if positions.get('total_capital', 0) > 0 else 0
                },
                'positions': position_details,
                'concentration': concentration,
                'pnl': {
                    'realized_pnl': summary.get('realized_pnl', 0),
                    'unrealized_pnl': summary.get('unrealized_pnl', 0),
                    'total_pnl': summary.get('total_pnl', 0)
                },
                'generated_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=report, message="持仓报告生成成功")

        except Exception as e:
            return self._handle_exception(e, "生成持仓报告")

    def generate_summary_report(self, period_days: int = 30) -> ServiceResponse:
        """
        生成综合汇总报告

        Args:
            period_days: 报告周期（天数）

        Returns:
            ServiceResponse: 包含汇总报告数据
        """
        try:
            self._log_operation("generate_summary_report", period_days=period_days)

            end_date = datetime.now()
            start_date = end_date - timedelta(days=period_days)

            # 获取各项报告
            trading_report = self.generate_trading_report(start_date, end_date)
            position_report = self.generate_position_report()
            data_report = self.generate_data_quality_report()

            report = {
                'report_type': 'summary',
                'period': {
                    'days': period_days,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                },
                'trading': trading_report.data.get('summary', {}) if trading_report.success else {},
                'positions': position_report.data.get('summary', {}) if position_report.success else {},
                'data_quality': data_report.data.get('statistics', {}) if data_report.success else {},
                'generated_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=report, message="汇总报告生成成功")

        except Exception as e:
            return self._handle_exception(e, "生成汇总报告")

    def _calculate_daily_stats(self, orders: List[Dict]) -> List[Dict]:
        """计算每日统计"""
        daily_stats = {}

        for order in orders:
            created_at = order.get('created_at')
            if not created_at:
                continue

            date_key = created_at.strftime('%Y-%m-%d') if hasattr(created_at, 'strftime') else str(created_at)[:10]

            if date_key not in daily_stats:
                daily_stats[date_key] = {
                    'date': date_key,
                    'buy_count': 0,
                    'sell_count': 0,
                    'buy_amount': 0,
                    'sell_amount': 0
                }

            if order.get('action') == 'buy' and order.get('status') == 'executed':
                daily_stats[date_key]['buy_count'] += 1
                daily_stats[date_key]['buy_amount'] += order.get('quantity', 0) * order.get('executed_price', 0)
            elif order.get('action') == 'sell' and order.get('status') == 'executed':
                daily_stats[date_key]['sell_count'] += 1
                daily_stats[date_key]['sell_amount'] += order.get('quantity', 0) * order.get('executed_price', 0)

        return list(daily_stats.values())

    def _calculate_profit_factor(self, trades: List[Dict]) -> float:
        """计算盈亏比"""
        total_profit = 0
        total_loss = 0

        # 配对买卖订单计算盈亏
        buy_stack = []
        for trade in trades:
            if trade.get('action') == 'buy':
                buy_stack.append(trade)
            elif trade.get('action') == 'sell' and buy_stack:
                buy_trade = buy_stack.pop(0)
                pnl = (trade.get('price', 0) - buy_trade.get('price', 0)) * trade.get('shares', 0)
                if pnl > 0:
                    total_profit += pnl
                else:
                    total_loss += abs(pnl)

        return total_profit / total_loss if total_loss > 0 else float('inf') if total_profit > 0 else 0

    def _calculate_win_rate(self, trades: List[Dict]) -> float:
        """计算胜率"""
        wins = 0
        total = 0

        buy_stack = []
        for trade in trades:
            if trade.get('action') == 'buy':
                buy_stack.append(trade)
            elif trade.get('action') == 'sell' and buy_stack:
                buy_trade = buy_stack.pop(0)
                pnl = (trade.get('price', 0) - buy_trade.get('price', 0)) * trade.get('shares', 0)
                total += 1
                if pnl > 0:
                    wins += 1

        return wins / total if total > 0 else 0

    def _calculate_quality_score(self, stats: Dict) -> float:
        """计算数据质量分数 (0-100)"""
        success_rate = stats.get('success_rate', 0)
        return min(100, success_rate)

    def _generate_data_recommendations(self, stats: Dict) -> List[str]:
        """生成数据质量建议"""
        recommendations = []

        if stats.get('failed_records', 0) > 0:
            recommendations.append(f"建议重试 {stats['failed_records']} 条失败的下载记录")

        if stats.get('pending_records', 0) > 10:
            recommendations.append(f"有 {stats['pending_records']} 条待处理记录，建议尽快处理")

        if stats.get('success_rate', 0) < 90:
            recommendations.append("数据下载成功率低于90%，建议检查网络连接和数据源配置")

        if not recommendations:
            recommendations.append("数据质量良好，无需特别处理")

        return recommendations


# 创建服务实例
report_service = ReportService()
