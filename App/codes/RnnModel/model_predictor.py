# -*- coding: utf-8 -*-
"""
模型预测模块

提供 RNN 模型的预测功能
"""

import numpy as np
import pandas as pd
from keras.models import load_model
from keras import backend as k

from App.codes.RnnModel.rnn_base import RnnBase
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.codes.parsers.RnnParser import (
    ModelName, XColumn, Signal,
    nextCycleLengthMax, nextCycleAmplitudeMax, CycleAmplitudeMax
)


class DlModel(RnnBase):
    """
    深度学习模型预测类

    提供 RNN 模型的加载和预测功能
    """

    def __init__(self, model_alpha: float = 1):
        """
        初始化模型预测器

        Args:
            model_alpha: 模型预测值的调整系数
        """
        super().__init__()
        self.predict_data = None
        self.model_alpha = model_alpha
        self.model_name = ModelName
        self.X = XColumn()

    def normal2value(self, data: float, match: str) -> float:
        """
        将归一化值转换回原始值

        Args:
            data: 归一化后的值
            match: JSON 配置中的匹配键

        Returns:
            float: 原始值
        """
        high = self.jsons[match]['num_max']
        low = self.jsons[match]['num_min']
        num_normal = data * (high - low) + low
        return num_normal

    def predictive_value(self, model_name: str, x: np.ndarray) -> float:
        """
        使用模型进行预测

        Args:
            model_name: 模型名称
            x: 输入数据

        Returns:
            float: 预测值
        """
        k.clear_session()
        file_name = f'{model_name}_{self.stock_code}.h5'
        path = StockDataPath.model_path(self.month_parsers, file_name)
        model = load_model(path)
        val = model.predict(x)
        val = val[0][0]
        return val

    def x_data(self, columns: list) -> np.ndarray:
        """
        准备模型输入数据

        Args:
            columns: 特征列名列表

        Returns:
            np.ndarray: 格式化后的输入数据
        """
        x = self.predict_data[columns].tail(30)
        x = pd.concat([x[[Signal]], x], axis=1)
        x = x.to_numpy()
        h = 30 - x.shape[0]
        w = 30 - x.shape[1]

        ht = h // 2
        hl = h - ht

        wl = w // 2
        wr = w - wl

        x = np.pad(x, ((ht, hl), (wr, wl)), 'constant', constant_values=(0, 0))
        x.shape = (1, 30, 30, 1)

        return x

    def cycle_length(self) -> int:
        """
        预测周期长度

        Returns:
            int: 预测的周期长度
        """
        x = self.x_data(self.X[0])
        y = self.predictive_value(self.model_name[0], x)
        y = round(self.normal2value(data=y, match=nextCycleLengthMax) * self.model_alpha)
        return y

    def cycle_change(self) -> float:
        """
        预测周期振幅

        Returns:
            float: 预测的周期振幅
        """
        x = self.x_data(self.X[1])
        y = self.predictive_value(self.model_name[1], x)
        y = round(self.normal2value(data=y, match=nextCycleAmplitudeMax) * self.model_alpha, 3)
        return y

    def bar_change(self) -> float:
        """
        预测 Bar 振幅

        Returns:
            float: 预测的 Bar 振幅
        """
        x = self.x_data(self.X[2])
        y = self.predictive_value(self.model_name[2], x)
        y = round(self.normal2value(data=y, match=CycleAmplitudeMax) * self.model_alpha, 3)
        return y

    def bar_volume(self, vol_parser: float) -> int:
        """
        预测 Bar 成交量

        Args:
            vol_parser: 成交量解析器参数

        Returns:
            int: 预测的成交量
        """
        x = self.x_data(self.X[3])
        y = self.predictive_value(self.model_name[3], x)

        try:
            y = round(self.normal2value(y, 'EndDaily1mVolMax5') * self.model_alpha / vol_parser / 100)
        except Exception as ex:
            y = 0
            print(f'Prediction bar volume error: \n{ex}')

        return y
