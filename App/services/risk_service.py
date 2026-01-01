"""
风险服务层

提供风险评估、风险控制、VaR计算等功能
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path
import sys

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from App.services.base import BaseService, ServiceResponse, ErrorCode

logger = logging.getLogger(__name__)


class RiskService(BaseService):
    """
    风险服务类

    提供风险管理功能：
    - 风险评估：VaR、最大回撤、波动率计算
    - 风险控制：止损止盈检查、仓位限制
    - 风险预警：超限预警、集中度预警
    """

    # 风险参数默认值
    DEFAULT_STOP_LOSS_RATIO = 0.05       # 止损比例 5%
    DEFAULT_TAKE_PROFIT_RATIO = 0.10     # 止盈比例 10%
    DEFAULT_MAX_POSITION_RATIO = 0.20    # 单只股票最大仓位 20%
    DEFAULT_MAX_TOTAL_POSITION = 0.80    # 最大总仓位 80%
    DEFAULT_VAR_CONFIDENCE = 0.95        # VaR置信度 95%

    def __init__(self):
        super().__init__()
        self.stop_loss_ratio = self.DEFAULT_STOP_LOSS_RATIO
        self.take_profit_ratio = self.DEFAULT_TAKE_PROFIT_RATIO
        self.max_position_ratio = self.DEFAULT_MAX_POSITION_RATIO
        self.max_total_position = self.DEFAULT_MAX_TOTAL_POSITION

    def assess_portfolio_risk(self, positions: Dict[str, Any],
                               historical_data: pd.DataFrame = None) -> ServiceResponse:
        """
        评估投资组合风险

        Args:
            positions: 持仓信息
            historical_data: 历史价格数据

        Returns:
            ServiceResponse: 包含风险评估结果
        """
        try:
            self._log_operation("assess_portfolio_risk")

            position_details = positions.get('positions', {})
            total_value = positions.get('total_value', 0)
            total_capital = positions.get('total_capital', 0)

            # 计算持仓集中度
            concentration_risk = self._calculate_concentration_risk(position_details, total_value)

            # 计算仓位风险
            position_risk = self._calculate_position_risk(total_value, total_capital)

            # 计算未实现盈亏风险
            unrealized_pnl_risk = self._calculate_unrealized_pnl_risk(position_details)

            # 综合风险评分 (0-100, 越高风险越大)
            overall_risk_score = self._calculate_overall_risk_score(
                concentration_risk, position_risk, unrealized_pnl_risk
            )

            # 风险等级
            risk_level = self._get_risk_level(overall_risk_score)

            assessment = {
                'overall_score': round(overall_risk_score, 2),
                'risk_level': risk_level,
                'components': {
                    'concentration_risk': concentration_risk,
                    'position_risk': position_risk,
                    'unrealized_pnl_risk': unrealized_pnl_risk
                },
                'positions_at_risk': self._identify_positions_at_risk(position_details),
                'recommendations': self._generate_risk_recommendations(
                    concentration_risk, position_risk, unrealized_pnl_risk
                ),
                'assessed_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=assessment, message="风险评估完成")

        except Exception as e:
            return self._handle_exception(e, "评估投资组合风险")

    def calculate_var(self, returns: pd.Series, confidence: float = None,
                       holding_period: int = 1) -> ServiceResponse:
        """
        计算 Value at Risk (VaR)

        Args:
            returns: 收益率序列
            confidence: 置信度（默认95%）
            holding_period: 持有期（天）

        Returns:
            ServiceResponse: 包含VaR计算结果
        """
        try:
            self._log_operation("calculate_var", confidence=confidence, holding_period=holding_period)

            if returns is None or len(returns) == 0:
                return ServiceResponse.fail(
                    message="收益率数据为空",
                    error_code=ErrorCode.VALIDATION_ERROR
                )

            confidence = confidence or self.DEFAULT_VAR_CONFIDENCE

            # 历史模拟法 VaR
            historical_var = self._calculate_historical_var(returns, confidence)

            # 参数法 VaR（假设正态分布）
            parametric_var = self._calculate_parametric_var(returns, confidence)

            # 调整持有期
            if holding_period > 1:
                historical_var *= np.sqrt(holding_period)
                parametric_var *= np.sqrt(holding_period)

            # 条件VaR (CVaR / Expected Shortfall)
            cvar = self._calculate_cvar(returns, confidence)

            result = {
                'confidence': confidence,
                'holding_period': holding_period,
                'historical_var': round(historical_var, 4),
                'parametric_var': round(parametric_var, 4),
                'cvar': round(cvar, 4),
                'interpretation': {
                    'historical_var': f"在{confidence*100:.0f}%置信度下，{holding_period}天最大损失不超过 {historical_var*100:.2f}%",
                    'cvar': f"如果损失超过VaR，预期平均损失为 {cvar*100:.2f}%"
                },
                'calculated_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=result, message="VaR计算完成")

        except Exception as e:
            return self._handle_exception(e, "计算VaR")

    def check_stop_loss(self, positions: Dict[str, Any]) -> ServiceResponse:
        """
        检查止损条件

        Args:
            positions: 持仓信息

        Returns:
            ServiceResponse: 包含需要止损的股票列表
        """
        try:
            self._log_operation("check_stop_loss")

            position_details = positions.get('positions', {})
            stop_loss_alerts = []

            for stock_code, position in position_details.items():
                unrealized_pnl_pct = position.get('unrealized_pnl_pct', 0) / 100

                if unrealized_pnl_pct <= -self.stop_loss_ratio:
                    stop_loss_alerts.append({
                        'stock_code': stock_code,
                        'action': 'sell',
                        'reason': 'stop_loss',
                        'quantity': position.get('quantity', 0),
                        'current_loss_pct': f"{unrealized_pnl_pct*100:.2f}%",
                        'threshold': f"{-self.stop_loss_ratio*100:.2f}%",
                        'urgency': 'high'
                    })

            result = {
                'alerts_count': len(stop_loss_alerts),
                'alerts': stop_loss_alerts,
                'threshold': self.stop_loss_ratio,
                'checked_at': datetime.now().isoformat()
            }

            message = f"发现 {len(stop_loss_alerts)} 个止损预警" if stop_loss_alerts else "无止损预警"
            return ServiceResponse.ok(data=result, message=message)

        except Exception as e:
            return self._handle_exception(e, "检查止损条件")

    def check_take_profit(self, positions: Dict[str, Any]) -> ServiceResponse:
        """
        检查止盈条件

        Args:
            positions: 持仓信息

        Returns:
            ServiceResponse: 包含需要止盈的股票列表
        """
        try:
            self._log_operation("check_take_profit")

            position_details = positions.get('positions', {})
            take_profit_alerts = []

            for stock_code, position in position_details.items():
                unrealized_pnl_pct = position.get('unrealized_pnl_pct', 0) / 100

                if unrealized_pnl_pct >= self.take_profit_ratio:
                    take_profit_alerts.append({
                        'stock_code': stock_code,
                        'action': 'sell',
                        'reason': 'take_profit',
                        'quantity': position.get('quantity', 0),
                        'current_profit_pct': f"{unrealized_pnl_pct*100:.2f}%",
                        'threshold': f"{self.take_profit_ratio*100:.2f}%",
                        'urgency': 'medium'
                    })

            result = {
                'alerts_count': len(take_profit_alerts),
                'alerts': take_profit_alerts,
                'threshold': self.take_profit_ratio,
                'checked_at': datetime.now().isoformat()
            }

            message = f"发现 {len(take_profit_alerts)} 个止盈机会" if take_profit_alerts else "无止盈机会"
            return ServiceResponse.ok(data=result, message=message)

        except Exception as e:
            return self._handle_exception(e, "检查止盈条件")

    def check_position_limits(self, positions: Dict[str, Any],
                               proposed_trade: Dict[str, Any] = None) -> ServiceResponse:
        """
        检查仓位限制

        Args:
            positions: 当前持仓
            proposed_trade: 拟交易订单（可选）

        Returns:
            ServiceResponse: 包含仓位检查结果
        """
        try:
            self._log_operation("check_position_limits", proposed_trade=proposed_trade)

            position_details = positions.get('positions', {})
            total_value = positions.get('total_value', 0)
            total_capital = positions.get('total_capital', 0)

            violations = []
            warnings = []

            # 检查单只股票仓位
            for stock_code, position in position_details.items():
                market_value = position.get('market_value', 0)
                position_ratio = market_value / total_capital if total_capital > 0 else 0

                if position_ratio > self.max_position_ratio:
                    violations.append({
                        'type': 'single_position_exceeded',
                        'stock_code': stock_code,
                        'current_ratio': f"{position_ratio*100:.2f}%",
                        'limit': f"{self.max_position_ratio*100:.2f}%"
                    })
                elif position_ratio > self.max_position_ratio * 0.8:
                    warnings.append({
                        'type': 'single_position_warning',
                        'stock_code': stock_code,
                        'current_ratio': f"{position_ratio*100:.2f}%",
                        'limit': f"{self.max_position_ratio*100:.2f}%"
                    })

            # 检查总仓位
            total_position_ratio = total_value / total_capital if total_capital > 0 else 0
            if total_position_ratio > self.max_total_position:
                violations.append({
                    'type': 'total_position_exceeded',
                    'current_ratio': f"{total_position_ratio*100:.2f}%",
                    'limit': f"{self.max_total_position*100:.2f}%"
                })

            # 检查拟交易订单
            trade_allowed = True
            if proposed_trade:
                trade_check = self._check_proposed_trade(
                    proposed_trade, positions, total_capital
                )
                if not trade_check['allowed']:
                    trade_allowed = False
                    violations.extend(trade_check.get('violations', []))

            result = {
                'compliant': len(violations) == 0,
                'trade_allowed': trade_allowed,
                'violations': violations,
                'warnings': warnings,
                'current_position_ratio': f"{total_position_ratio*100:.2f}%",
                'checked_at': datetime.now().isoformat()
            }

            if violations:
                return ServiceResponse.fail(
                    message=f"发现 {len(violations)} 个仓位违规",
                    error_code=ErrorCode.RISK_LIMIT_EXCEEDED,
                    data=result
                )

            return ServiceResponse.ok(data=result, message="仓位检查通过")

        except Exception as e:
            return self._handle_exception(e, "检查仓位限制")

    def calculate_position_size(self, capital: float, price: float,
                                 risk_per_trade: float = 0.02) -> ServiceResponse:
        """
        计算建议仓位大小

        Args:
            capital: 可用资金
            price: 股票价格
            risk_per_trade: 每笔交易风险比例

        Returns:
            ServiceResponse: 包含建议仓位
        """
        try:
            self._log_operation("calculate_position_size",
                               capital=capital, price=price, risk_per_trade=risk_per_trade)

            if capital <= 0 or price <= 0:
                return ServiceResponse.fail(
                    message="资金或价格必须大于0",
                    error_code=ErrorCode.VALIDATION_ERROR
                )

            # 基于风险的仓位计算
            risk_amount = capital * risk_per_trade
            stop_loss_amount = price * self.stop_loss_ratio

            if stop_loss_amount > 0:
                risk_based_quantity = int(risk_amount / stop_loss_amount)
            else:
                risk_based_quantity = 0

            # 基于最大仓位限制
            max_quantity = int(capital * self.max_position_ratio / price)

            # 取较小值
            recommended_quantity = min(risk_based_quantity, max_quantity)

            # 调整为100股的整数倍（A股规则）
            recommended_quantity = (recommended_quantity // 100) * 100

            result = {
                'recommended_quantity': recommended_quantity,
                'risk_based_quantity': risk_based_quantity,
                'max_allowed_quantity': max_quantity,
                'estimated_cost': recommended_quantity * price,
                'max_loss_per_trade': recommended_quantity * price * self.stop_loss_ratio,
                'position_ratio': (recommended_quantity * price / capital * 100) if capital > 0 else 0,
                'calculated_at': datetime.now().isoformat()
            }

            return ServiceResponse.ok(data=result, message="仓位计算完成")

        except Exception as e:
            return self._handle_exception(e, "计算仓位大小")

    def set_risk_parameters(self, stop_loss: float = None, take_profit: float = None,
                             max_position: float = None, max_total: float = None) -> ServiceResponse:
        """
        设置风险参数

        Args:
            stop_loss: 止损比例
            take_profit: 止盈比例
            max_position: 单只股票最大仓位
            max_total: 最大总仓位

        Returns:
            ServiceResponse: 设置结果
        """
        try:
            self._log_operation("set_risk_parameters")

            if stop_loss is not None:
                if 0 < stop_loss < 1:
                    self.stop_loss_ratio = stop_loss
                else:
                    return ServiceResponse.fail(
                        message="止损比例必须在0到1之间",
                        error_code=ErrorCode.VALIDATION_ERROR
                    )

            if take_profit is not None:
                if 0 < take_profit < 1:
                    self.take_profit_ratio = take_profit
                else:
                    return ServiceResponse.fail(
                        message="止盈比例必须在0到1之间",
                        error_code=ErrorCode.VALIDATION_ERROR
                    )

            if max_position is not None:
                if 0 < max_position <= 1:
                    self.max_position_ratio = max_position
                else:
                    return ServiceResponse.fail(
                        message="最大仓位比例必须在0到1之间",
                        error_code=ErrorCode.VALIDATION_ERROR
                    )

            if max_total is not None:
                if 0 < max_total <= 1:
                    self.max_total_position = max_total
                else:
                    return ServiceResponse.fail(
                        message="最大总仓位必须在0到1之间",
                        error_code=ErrorCode.VALIDATION_ERROR
                    )

            result = {
                'stop_loss_ratio': self.stop_loss_ratio,
                'take_profit_ratio': self.take_profit_ratio,
                'max_position_ratio': self.max_position_ratio,
                'max_total_position': self.max_total_position
            }

            return ServiceResponse.ok(data=result, message="风险参数设置成功")

        except Exception as e:
            return self._handle_exception(e, "设置风险参数")

    # ==================== 私有方法 ====================

    def _calculate_historical_var(self, returns: pd.Series, confidence: float) -> float:
        """历史模拟法计算VaR"""
        return -np.percentile(returns, (1 - confidence) * 100)

    def _calculate_parametric_var(self, returns: pd.Series, confidence: float) -> float:
        """参数法计算VaR（正态分布假设）"""
        from scipy import stats
        mean = returns.mean()
        std = returns.std()
        z_score = stats.norm.ppf(1 - confidence)
        return -(mean + z_score * std)

    def _calculate_cvar(self, returns: pd.Series, confidence: float) -> float:
        """计算条件VaR (CVaR / Expected Shortfall)"""
        var = self._calculate_historical_var(returns, confidence)
        return -returns[returns <= -var].mean() if len(returns[returns <= -var]) > 0 else var

    def _calculate_concentration_risk(self, positions: Dict, total_value: float) -> Dict:
        """计算持仓集中度风险"""
        if total_value <= 0 or not positions:
            return {'score': 0, 'max_concentration': 0, 'hhi': 0}

        concentrations = []
        for pos in positions.values():
            concentration = pos.get('market_value', 0) / total_value
            concentrations.append(concentration)

        max_concentration = max(concentrations) if concentrations else 0
        hhi = sum(c ** 2 for c in concentrations)  # Herfindahl-Hirschman Index

        # 风险评分 (0-100)
        score = min(100, max_concentration * 200 + hhi * 100)

        return {
            'score': round(score, 2),
            'max_concentration': round(max_concentration * 100, 2),
            'hhi': round(hhi, 4)
        }

    def _calculate_position_risk(self, total_value: float, total_capital: float) -> Dict:
        """计算仓位风险"""
        if total_capital <= 0:
            return {'score': 0, 'position_ratio': 0}

        position_ratio = total_value / total_capital
        score = min(100, position_ratio / self.max_total_position * 100)

        return {
            'score': round(score, 2),
            'position_ratio': round(position_ratio * 100, 2)
        }

    def _calculate_unrealized_pnl_risk(self, positions: Dict) -> Dict:
        """计算未实现盈亏风险"""
        total_loss = 0
        positions_in_loss = 0

        for pos in positions.values():
            unrealized_pnl = pos.get('unrealized_pnl', 0)
            if unrealized_pnl < 0:
                total_loss += abs(unrealized_pnl)
                positions_in_loss += 1

        # 风险评分基于亏损仓位占比
        loss_ratio = positions_in_loss / len(positions) if positions else 0
        score = min(100, loss_ratio * 100)

        return {
            'score': round(score, 2),
            'total_unrealized_loss': round(total_loss, 2),
            'positions_in_loss': positions_in_loss
        }

    def _calculate_overall_risk_score(self, concentration: Dict, position: Dict,
                                       unrealized: Dict) -> float:
        """计算综合风险评分"""
        weights = {'concentration': 0.4, 'position': 0.3, 'unrealized': 0.3}
        return (
            concentration.get('score', 0) * weights['concentration'] +
            position.get('score', 0) * weights['position'] +
            unrealized.get('score', 0) * weights['unrealized']
        )

    def _get_risk_level(self, score: float) -> str:
        """根据分数获取风险等级"""
        if score < 30:
            return 'low'
        elif score < 60:
            return 'medium'
        elif score < 80:
            return 'high'
        else:
            return 'critical'

    def _identify_positions_at_risk(self, positions: Dict) -> List[Dict]:
        """识别风险持仓"""
        at_risk = []
        for stock_code, pos in positions.items():
            unrealized_pnl_pct = pos.get('unrealized_pnl_pct', 0) / 100

            if unrealized_pnl_pct <= -self.stop_loss_ratio * 0.8:
                at_risk.append({
                    'stock_code': stock_code,
                    'risk_type': 'approaching_stop_loss',
                    'unrealized_pnl_pct': f"{unrealized_pnl_pct*100:.2f}%"
                })

        return at_risk

    def _generate_risk_recommendations(self, concentration: Dict, position: Dict,
                                        unrealized: Dict) -> List[str]:
        """生成风险建议"""
        recommendations = []

        if concentration.get('score', 0) > 60:
            recommendations.append("持仓过于集中，建议分散投资降低风险")

        if position.get('score', 0) > 80:
            recommendations.append("总仓位过高，建议适当减仓")

        if unrealized.get('positions_in_loss', 0) > 0:
            recommendations.append(f"有 {unrealized['positions_in_loss']} 个持仓处于亏损状态，请关注止损")

        if not recommendations:
            recommendations.append("当前风险水平可控，建议保持现有策略")

        return recommendations

    def _check_proposed_trade(self, trade: Dict, positions: Dict,
                               total_capital: float) -> Dict:
        """检查拟交易订单是否违规"""
        violations = []
        stock_code = trade.get('stock_code')
        quantity = trade.get('quantity', 0)
        price = trade.get('price', 0)
        action = trade.get('action')

        if action == 'buy':
            trade_value = quantity * price
            current_position = positions.get('positions', {}).get(stock_code, {})
            current_value = current_position.get('market_value', 0)
            new_value = current_value + trade_value

            new_ratio = new_value / total_capital if total_capital > 0 else 0

            if new_ratio > self.max_position_ratio:
                violations.append({
                    'type': 'proposed_trade_exceeds_limit',
                    'stock_code': stock_code,
                    'new_ratio': f"{new_ratio*100:.2f}%",
                    'limit': f"{self.max_position_ratio*100:.2f}%"
                })

        return {
            'allowed': len(violations) == 0,
            'violations': violations
        }


# 创建服务实例
risk_service = RiskService()
