# -*- coding: utf-8 -*-
"""
RNN 运行模型模块

此模块已重构，核心功能已迁移到以下子模块：
- rnn_base.py: 基类和共享变量
- data_loader.py: 数据加载和预处理
- model_predictor.py: 模型预测
- data_updater.py: 数据更新
- trade_executor.py: 交易执行
- prediction_engine.py: 预测引擎

本文件保留所有导出以保持向后兼容性。

新代码建议直接使用:
    from App.codes.RnnModel.prediction_engine import PredictionCommon
    from App.codes.RnnModel.data_loader import ModelData
    from App.codes.RnnModel.model_predictor import DlModel
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# 配置
plt.rcParams['font.sans-serif'] = ['FangSong']
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

# ==================== 向后兼容导入 ====================

# 从 rnn_base 导入基类
from App.codes.RnnModel.rnn_base import RnnBase

# 从各子模块导入类
from App.codes.RnnModel.data_loader import ModelData
from App.codes.RnnModel.model_predictor import DlModel
from App.codes.RnnModel.data_updater import UpdateData
from App.codes.RnnModel.trade_executor import TradingAction
from App.codes.RnnModel.prediction_engine import PredictionCommon

# 向后兼容别名
Parsers = RnnBase


# ==================== 模块导出 ====================

__all__ = [
    # 基类
    'Parsers',
    'RnnBase',
    # 功能类
    'ModelData',
    'DlModel',
    'UpdateData',
    'TradingAction',
    'PredictionCommon',
]


if __name__ == '__main__':
    stock_ = '002466'
    # 示例用法：
    # month_ = '2022-02'
    # _date = '2024-01-15'
    # rm = PredictionCommon(stock=stock_, month_parsers=month_, monitor=False, check_date=_date)
    # rm.single_stock()
