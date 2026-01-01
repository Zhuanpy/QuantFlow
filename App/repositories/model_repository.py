# -*- coding: utf-8 -*-
"""
模型数据仓库

提供模型相关数据的访问和操作
"""

import os
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path

import pandas as pd

from App.repositories.base_repository import BaseRepository
from App.utils.path_manager import get_path_manager

logger = logging.getLogger(__name__)


class ModelRepository(BaseRepository):
    """
    模型数据仓库

    提供模型文件和预测数据的访问和操作
    """

    def __init__(self, model_class=None):
        """
        初始化模型数据仓库

        Args:
            model_class: 模型记录类（可选）
        """
        # 模型仓库主要处理文件，不一定需要数据库模型
        self.model_class = model_class
        self.path_manager = get_path_manager()

    # ==================== 模型文件操作 ====================

    def get_model_path(self, model_type: str, model_name: str) -> str:
        """
        获取模型文件路径

        Args:
            model_type: 模型类型 ('trained', 'checkpoints')
            model_name: 模型名称

        Returns:
            str: 模型文件路径
        """
        return self.path_manager.get_model_path(model_type, model_name)

    def model_exists(self, model_type: str, model_name: str) -> bool:
        """
        检查模型文件是否存在

        Args:
            model_type: 模型类型
            model_name: 模型名称

        Returns:
            bool: 是否存在
        """
        path = self.get_model_path(model_type, model_name)
        return os.path.exists(path)

    def list_models(self, model_type: str = 'trained') -> List[Dict[str, Any]]:
        """
        列出所有模型

        Args:
            model_type: 模型类型

        Returns:
            List[Dict]: 模型信息列表
        """
        try:
            base_path = Path(self.path_manager.get_model_path(model_type, ''))
            if not base_path.exists():
                return []

            models = []
            for file_path in base_path.iterdir():
                if file_path.is_file() and file_path.suffix in ['.h5', '.pkl', '.pt', '.pth', '.keras']:
                    stat = file_path.stat()
                    models.append({
                        'name': file_path.name,
                        'type': model_type,
                        'path': str(file_path),
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime),
                    })

            return sorted(models, key=lambda x: x['modified'], reverse=True)
        except Exception as e:
            logger.error(f"列出模型失败: {e}")
            return []

    def delete_model(self, model_type: str, model_name: str) -> bool:
        """
        删除模型文件

        Args:
            model_type: 模型类型
            model_name: 模型名称

        Returns:
            bool: 是否删除成功
        """
        try:
            path = self.get_model_path(model_type, model_name)
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"模型已删除: {path}")
                return True
            return False
        except Exception as e:
            logger.error(f"删除模型失败: {e}")
            return False

    # ==================== 预测数据操作 ====================

    def save_prediction(self, stock_code: str, prediction_data: Dict[str, Any],
                        model_name: str = None) -> bool:
        """
        保存预测数据

        Args:
            stock_code: 股票代码
            prediction_data: 预测数据
            model_name: 模型名称

        Returns:
            bool: 是否保存成功
        """
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{stock_code}_{timestamp}.json"
            path = self.path_manager.get_model_path('predictions', filename)

            data = {
                'stock_code': stock_code,
                'model_name': model_name,
                'timestamp': timestamp,
                'predictions': prediction_data,
            }

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"预测数据已保存: {path}")
            return True
        except Exception as e:
            logger.error(f"保存预测数据失败: {e}")
            return False

    def get_latest_prediction(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取最新的预测数据

        Args:
            stock_code: 股票代码

        Returns:
            Optional[Dict]: 预测数据或 None
        """
        try:
            base_path = Path(self.path_manager.get_model_path('predictions', ''))
            if not base_path.exists():
                return None

            # 查找该股票的所有预测文件
            prediction_files = list(base_path.glob(f"{stock_code}_*.json"))
            if not prediction_files:
                return None

            # 按修改时间排序，获取最新的
            latest_file = max(prediction_files, key=lambda x: x.stat().st_mtime)

            with open(latest_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"获取预测数据失败: {e}")
            return None

    def get_predictions_by_date(self, stock_code: str, date_str: str) -> List[Dict[str, Any]]:
        """
        获取指定日期的预测数据

        Args:
            stock_code: 股票代码
            date_str: 日期字符串 (YYYYMMDD)

        Returns:
            List[Dict]: 预测数据列表
        """
        try:
            base_path = Path(self.path_manager.get_model_path('predictions', ''))
            if not base_path.exists():
                return []

            prediction_files = list(base_path.glob(f"{stock_code}_{date_str}_*.json"))
            predictions = []

            for file_path in prediction_files:
                with open(file_path, 'r', encoding='utf-8') as f:
                    predictions.append(json.load(f))

            return sorted(predictions, key=lambda x: x.get('timestamp', ''), reverse=True)
        except Exception as e:
            logger.error(f"获取预测数据失败: {e}")
            return []

    # ==================== RNN 数据操作 ====================

    def get_rnn_json_path(self, months: str, stock_code: str) -> str:
        """
        获取 RNN JSON 文件路径

        Args:
            months: 月份标识
            stock_code: 股票代码

        Returns:
            str: 文件路径
        """
        return self.path_manager.get_rnn_json_path(months, stock_code)

    def load_rnn_json(self, months: str, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        加载 RNN JSON 数据

        Args:
            months: 月份标识
            stock_code: 股票代码

        Returns:
            Optional[Dict]: JSON 数据或 None
        """
        try:
            path = self.get_rnn_json_path(months, stock_code)
            if not os.path.exists(path):
                return None

            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载 RNN JSON 失败: {e}")
            return None

    def save_rnn_json(self, months: str, stock_code: str, data: Dict[str, Any]) -> bool:
        """
        保存 RNN JSON 数据

        Args:
            months: 月份标识
            stock_code: 股票代码
            data: 要保存的数据

        Returns:
            bool: 是否保存成功
        """
        try:
            path = self.get_rnn_json_path(months, stock_code)

            # 确保目录存在
            os.makedirs(os.path.dirname(path), exist_ok=True)

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"RNN JSON 数据已保存: {path}")
            return True
        except Exception as e:
            logger.error(f"保存 RNN JSON 失败: {e}")
            return False

    # ==================== 模型性能记录 ====================

    def save_model_metrics(self, model_name: str, metrics: Dict[str, Any]) -> bool:
        """
        保存模型性能指标

        Args:
            model_name: 模型名称
            metrics: 性能指标

        Returns:
            bool: 是否保存成功
        """
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{model_name}_metrics_{timestamp}.json"
            path = self.path_manager.get_model_path('metrics', filename)

            data = {
                'model_name': model_name,
                'timestamp': timestamp,
                'metrics': metrics,
            }

            # 确保目录存在
            os.makedirs(os.path.dirname(path), exist_ok=True)

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"模型性能指标已保存: {path}")
            return True
        except Exception as e:
            logger.error(f"保存模型性能指标失败: {e}")
            return False

    def get_model_metrics_history(self, model_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取模型性能指标历史

        Args:
            model_name: 模型名称
            limit: 返回数量限制

        Returns:
            List[Dict]: 性能指标历史
        """
        try:
            base_path = Path(self.path_manager.get_model_path('metrics', ''))
            if not base_path.exists():
                return []

            metrics_files = list(base_path.glob(f"{model_name}_metrics_*.json"))
            metrics_files = sorted(metrics_files, key=lambda x: x.stat().st_mtime, reverse=True)
            metrics_files = metrics_files[:limit]

            history = []
            for file_path in metrics_files:
                with open(file_path, 'r', encoding='utf-8') as f:
                    history.append(json.load(f))

            return history
        except Exception as e:
            logger.error(f"获取模型性能指标历史失败: {e}")
            return []
