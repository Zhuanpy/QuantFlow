"""
策略服务层
整合信号生成、技术指标计算、策略执行等功能
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import sys

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from App.exts import db
from config import Config

logger = logging.getLogger(__name__)


class StrategyService:
    """
    策略服务类
    提供技术指标计算和信号生成功能
    """
    
    def __init__(self):
        self.config = Config()
    
    def calculate_macd(self, data: pd.DataFrame, short_period: int = 12, 
                      mid_period: int = 20, long_period: int = 30, 
                      signal_period: int = 9) -> pd.DataFrame:
        """
        计算MACD指标
        
        Args:
            data: 包含close价格的数据框
            short_period: 短期EMA周期
            mid_period: 中期EMA周期
            long_period: 长期EMA周期
            signal_period: 信号线周期
            
        Returns:
            pd.DataFrame: 包含MACD指标的数据框
        """
        try:
            if data.empty or 'close' not in data.columns:
                logger.error("数据为空或缺少close列")
                return data
            
            # 复制数据避免修改原始数据
            result = data.copy()
            
            # 计算EMA
            result['ema_short'] = result['close'].ewm(span=short_period, adjust=False).mean()
            result['ema_mid'] = result['close'].ewm(span=mid_period, adjust=False).mean()
            result['ema_long'] = result['close'].ewm(span=long_period, adjust=False).mean()
            
            # 计算DIFF
            result['diff'] = result['ema_short'] - result['ema_long']
            result['diff_sm'] = result['ema_short'] - result['ema_mid']
            result['diff_ml'] = result['ema_mid'] - result['ema_long']
            
            # 计算DEA（信号线）
            result['dea'] = result['diff'].ewm(span=signal_period, adjust=False).mean()
            
            # 计算MACD柱线
            result['macd'] = (result['diff'] - result['dea']) * 2
            
            logger.info(f"MACD计算完成，数据长度: {len(result)}")
            return result
            
        except Exception as e:
            logger.error(f"计算MACD时发生错误: {e}")
            return data
    
    def calculate_bollinger_bands(self, data: pd.DataFrame, period: int = 20, 
                                 std_dev: float = 2.0) -> pd.DataFrame:
        """
        计算布林带指标
        
        Args:
            data: 包含close价格的数据框
            period: 移动平均周期
            std_dev: 标准差倍数
            
        Returns:
            pd.DataFrame: 包含布林带指标的数据框
        """
        try:
            if data.empty or 'close' not in data.columns:
                logger.error("数据为空或缺少close列")
                return data
            
            result = data.copy()
            
            # 计算移动平均线
            result['bb_middle'] = result['close'].rolling(window=period).mean()
            
            # 计算标准差
            bb_std = result['close'].rolling(window=period).std()
            
            # 计算上下轨
            result['bb_upper'] = result['bb_middle'] + (bb_std * std_dev)
            result['bb_lower'] = result['bb_middle'] - (bb_std * std_dev)
            
            # 计算布林带宽度和百分比B
            result['bb_width'] = result['bb_upper'] - result['bb_lower']
            result['bb_percent'] = (result['close'] - result['bb_lower']) / result['bb_width']
            
            logger.info(f"布林带计算完成，数据长度: {len(result)}")
            return result
            
        except Exception as e:
            logger.error(f"计算布林带时发生错误: {e}")
            return data
    
    def calculate_rsi(self, data: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        计算RSI指标
        
        Args:
            data: 包含close价格的数据框
            period: RSI计算周期
            
        Returns:
            pd.DataFrame: 包含RSI指标的数据框
        """
        try:
            if data.empty or 'close' not in data.columns:
                logger.error("数据为空或缺少close列")
                return data
            
            result = data.copy()
            
            # 计算价格变化
            delta = result['close'].diff()
            
            # 分离上涨和下跌
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            # 计算平均上涨和下跌
            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()
            
            # 计算RSI
            rs = avg_gain / avg_loss
            result['rsi'] = 100 - (100 / (1 + rs))
            
            logger.info(f"RSI计算完成，数据长度: {len(result)}")
            return result
            
        except Exception as e:
            logger.error(f"计算RSI时发生错误: {e}")
            return data
    
    def generate_macd_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        基于MACD生成交易信号
        
        Args:
            data: 包含MACD指标的数据框
            
        Returns:
            pd.DataFrame: 包含交易信号的数据框
        """
        try:
            if data.empty or 'macd' not in data.columns or 'dea' not in data.columns:
                logger.error("数据为空或缺少MACD指标")
                return data
            
            result = data.copy()
            
            # 生成买卖信号
            result['macd_signal'] = 0  # 0: 无信号, 1: 买入, -1: 卖出
            
            # MACD金叉（MACD线上穿DEA线）
            result.loc[(result['macd'] > result['dea']) & 
                      (result['macd'].shift(1) <= result['dea'].shift(1)), 'macd_signal'] = 1
            
            # MACD死叉（MACD线下穿DEA线）
            result.loc[(result['macd'] < result['dea']) & 
                      (result['macd'].shift(1) >= result['dea'].shift(1)), 'macd_signal'] = -1
            
            # 计算信号强度
            result['macd_strength'] = abs(result['macd'] - result['dea'])
            
            logger.info(f"MACD信号生成完成，买入信号: {(result['macd_signal'] == 1).sum()}, "
                       f"卖出信号: {(result['macd_signal'] == -1).sum()}")
            return result
            
        except Exception as e:
            logger.error(f"生成MACD信号时发生错误: {e}")
            return data
    
    def generate_bollinger_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        基于布林带生成交易信号
        
        Args:
            data: 包含布林带指标的数据框
            
        Returns:
            pd.DataFrame: 包含交易信号的数据框
        """
        try:
            if data.empty or 'bb_upper' not in data.columns or 'bb_lower' not in data.columns:
                logger.error("数据为空或缺少布林带指标")
                return data
            
            result = data.copy()
            
            # 生成买卖信号
            result['bb_signal'] = 0  # 0: 无信号, 1: 买入, -1: 卖出
            
            # 价格触及下轨，可能反弹（买入信号）
            result.loc[result['close'] <= result['bb_lower'], 'bb_signal'] = 1
            
            # 价格触及上轨，可能回落（卖出信号）
            result.loc[result['close'] >= result['bb_upper'], 'bb_signal'] = -1
            
            # 计算布林带位置
            result['bb_position'] = (result['close'] - result['bb_lower']) / (result['bb_upper'] - result['bb_lower'])
            
            logger.info(f"布林带信号生成完成，买入信号: {(result['bb_signal'] == 1).sum()}, "
                       f"卖出信号: {(result['bb_signal'] == -1).sum()}")
            return result
            
        except Exception as e:
            logger.error(f"生成布林带信号时发生错误: {e}")
            return data
    
    def generate_rsi_signals(self, data: pd.DataFrame, oversold: float = 30, 
                           overbought: float = 70) -> pd.DataFrame:
        """
        基于RSI生成交易信号
        
        Args:
            data: 包含RSI指标的数据框
            oversold: 超卖阈值
            overbought: 超买阈值
            
        Returns:
            pd.DataFrame: 包含交易信号的数据框
        """
        try:
            if data.empty or 'rsi' not in data.columns:
                logger.error("数据为空或缺少RSI指标")
                return data
            
            result = data.copy()
            
            # 生成买卖信号
            result['rsi_signal'] = 0  # 0: 无信号, 1: 买入, -1: 卖出
            
            # RSI超卖，可能反弹（买入信号）
            result.loc[result['rsi'] <= oversold, 'rsi_signal'] = 1
            
            # RSI超买，可能回落（卖出信号）
            result.loc[result['rsi'] >= overbought, 'rsi_signal'] = -1
            
            logger.info(f"RSI信号生成完成，买入信号: {(result['rsi_signal'] == 1).sum()}, "
                       f"卖出信号: {(result['rsi_signal'] == -1).sum()}")
            return result
            
        except Exception as e:
            logger.error(f"生成RSI信号时发生错误: {e}")
            return data
    
    def combine_signals(self, data: pd.DataFrame, weights: Dict[str, float] = None) -> pd.DataFrame:
        """
        组合多个技术指标信号
        
        Args:
            data: 包含多个信号的数据框
            weights: 各信号权重，默认等权重
            
        Returns:
            pd.DataFrame: 包含组合信号的数据框
        """
        try:
            if data.empty:
                logger.error("数据为空")
                return data
            
            result = data.copy()
            
            # 默认权重
            if weights is None:
                weights = {
                    'macd_signal': 0.4,
                    'bb_signal': 0.3,
                    'rsi_signal': 0.3
                }
            
            # 计算加权组合信号
            signal_columns = [col for col in data.columns if col.endswith('_signal')]
            available_signals = [col for col in signal_columns if col in data.columns]
            
            if not available_signals:
                logger.warning("没有找到可用的信号列")
                return result
            
            # 计算组合信号
            combined_signal = 0
            total_weight = 0
            
            for signal_col in available_signals:
                weight = weights.get(signal_col, 1.0 / len(available_signals))
                combined_signal += result[signal_col] * weight
                total_weight += weight
            
            if total_weight > 0:
                result['combined_signal'] = combined_signal / total_weight
                
                # 生成最终信号
                result['final_signal'] = 0
                result.loc[result['combined_signal'] > 0.5, 'final_signal'] = 1  # 买入
                result.loc[result['combined_signal'] < -0.5, 'final_signal'] = -1  # 卖出
            
            logger.info(f"信号组合完成，最终买入信号: {(result['final_signal'] == 1).sum()}, "
                       f"卖出信号: {(result['final_signal'] == -1).sum()}")
            return result
            
        except Exception as e:
            logger.error(f"组合信号时发生错误: {e}")
            return data
    
    def backtest_strategy(self, data: pd.DataFrame, initial_capital: float = 100000) -> Dict[str, Any]:
        """
        回测策略
        
        Args:
            data: 包含价格和信号的数据框
            initial_capital: 初始资金
            
        Returns:
            Dict: 回测结果
        """
        try:
            if data.empty or 'close' not in data.columns or 'final_signal' not in data.columns:
                logger.error("数据为空或缺少必要列")
                return {}
            
            # 复制数据
            backtest_data = data.copy()
            
            # 初始化变量
            capital = initial_capital
            position = 0
            trades = []
            equity_curve = []
            
            for i, row in backtest_data.iterrows():
                signal = row['final_signal']
                price = row['close']
                
                # 执行交易
                if signal == 1 and position == 0:  # 买入信号且无持仓
                    shares = int(capital / price)
                    if shares > 0:
                        position = shares
                        capital -= shares * price
                        trades.append({
                            'date': row.name,
                            'action': 'buy',
                            'price': price,
                            'shares': shares,
                            'capital': capital
                        })
                
                elif signal == -1 and position > 0:  # 卖出信号且有持仓
                    capital += position * price
                    trades.append({
                        'date': row.name,
                        'action': 'sell',
                        'price': price,
                        'shares': position,
                        'capital': capital
                    })
                    position = 0
                
                # 记录权益曲线
                current_value = capital + (position * price)
                equity_curve.append({
                    'date': row.name,
                    'equity': current_value,
                    'position': position
                })
            
            # 计算回测指标
            equity_df = pd.DataFrame(equity_curve)
            equity_df.set_index('date', inplace=True)
            
            # 计算收益率
            total_return = (equity_df['equity'].iloc[-1] - initial_capital) / initial_capital
            
            # 计算最大回撤
            equity_df['peak'] = equity_df['equity'].expanding().max()
            equity_df['drawdown'] = (equity_df['equity'] - equity_df['peak']) / equity_df['peak']
            max_drawdown = equity_df['drawdown'].min()
            
            # 计算夏普比率（简化版）
            returns = equity_df['equity'].pct_change().dropna()
            sharpe_ratio = returns.mean() / returns.std() if returns.std() > 0 else 0
            
            result = {
                'initial_capital': initial_capital,
                'final_capital': equity_df['equity'].iloc[-1],
                'total_return': total_return,
                'max_drawdown': max_drawdown,
                'sharpe_ratio': sharpe_ratio,
                'total_trades': len(trades),
                'trades': trades,
                'equity_curve': equity_df
            }
            
            logger.info(f"回测完成，总收益率: {total_return:.2%}, 最大回撤: {max_drawdown:.2%}")
            return result
            
        except Exception as e:
            logger.error(f"回测策略时发生错误: {e}")
            return {}


# 创建服务实例
strategy_service = StrategyService()
