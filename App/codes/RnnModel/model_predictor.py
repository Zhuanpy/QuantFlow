# -*- coding: utf-8 -*-
"""
模型预测模块

提供 RNN 模型的预测功能
"""

import os
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd
from keras.models import load_model

from App.codes.RnnModel.rnn_base import RnnBase
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.codes.parsers.RnnParser import (
    ModelName, XColumn, Signal,
    nextCycleLengthMax, nextCycleAmplitudeMax, CycleAmplitudeMax
)


# ==================== 模型缓存 ====================
# 每次预测都 load_model 太慢；按 (month, file_name) 缓存模型对象
# 用 OrderedDict + 容量上限实现简易 LRU，避免占用过多内存
_MODEL_CACHE_CAPACITY = 64
_model_cache: "OrderedDict[tuple, object]" = OrderedDict()
_cache_lock = threading.Lock()


def _build_model_from_weights(month_parsers: str, file_name: str):
    """
    用 create_model() 重建网络拓扑，再加载同名 weight 文件。
    用于规避旧 .h5（Keras 2.x 序列化）在 Keras 3 下加载失败的问题：
    旧 Conv2D 层的 input_shape 反序列化后会多 batch 维度，导致 kernel shape 不匹配。

    权重文件命名（按时间顺序的两种约定）：
      新（Keras 3 强制）：weight_{model_name}_{stock_code}.weights.h5
      旧（Keras 2 时代）：weight_{model_name}_{stock_code}.h5
    """
    from App.codes.RnnModel.RnnCreationModel import create_model
    model = create_model()
    # file_name 形如 'CycleLength4_002475.h5'
    base = file_name[:-3] if file_name.endswith('.h5') else file_name
    candidates = [
        f'weight_{base}.weights.h5',
        f'weight_{base}.h5',
    ]
    for cand in candidates:
        weight_path = StockDataPath.model_weight_path(month_parsers, cand)
        if os.path.exists(weight_path):
            model.load_weights(weight_path)
            return model
    raise FileNotFoundError(
        f'权重文件不存在，已尝试: {candidates}\n'
        f'（旧 .h5 在新 Keras 下无法直接加载，需要 weight 文件重建）'
    )


def _get_cached_model(month_parsers: str, file_name: str):
    """
    按 (month, file_name) 缓存模型。命中则返回，未命中则 load 并放入缓存。
    超出容量时按 LRU 淘汰最旧条目。

    加载策略：先尝试 load_model（新格式），失败则回退到从 weights 重建。
    """
    key = (month_parsers, file_name)
    with _cache_lock:
        if key in _model_cache:
            _model_cache.move_to_end(key)
            return _model_cache[key]

    # load 不在锁内，避免阻塞其他读
    path = StockDataPath.model_path(month_parsers, file_name)
    try:
        model = load_model(path)
    except (ValueError, TypeError, OSError):
        # 旧 .h5 格式不兼容，从 weights 重建
        model = _build_model_from_weights(month_parsers, file_name)

    with _cache_lock:
        _model_cache[key] = model
        _model_cache.move_to_end(key)
        while len(_model_cache) > _MODEL_CACHE_CAPACITY:
            _model_cache.popitem(last=False)
    return model


def clear_model_cache():
    """清空模型缓存。训练完新模型后建议调用，避免读到旧版本。"""
    with _cache_lock:
        _model_cache.clear()


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
        使用模型进行预测（命中缓存时 O(1) 返回模型对象）

        Args:
            model_name: 模型名称
            x: 输入数据

        Returns:
            float: 预测值
        """
        file_name = f'{model_name}_{self.stock_code}.h5'
        model = _get_cached_model(self.month_parsers, file_name)
        val = model.predict(x, verbose=0)
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
