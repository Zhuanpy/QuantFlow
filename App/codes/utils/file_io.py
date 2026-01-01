# -*- coding: utf-8 -*-
"""
文件读写模块

提供 JSON、CSV 等文件的读写操作
"""

import os
import json
import pandas as pd
from typing import Optional, Dict, List, Any
from pathlib import Path


class ReadSaveFile:
    """文件读写工具类"""

    @classmethod
    def read_json(cls, months: str, code: str) -> Optional[Dict]:
        """
        读取 RNN 数据的 JSON 文件

        Args:
            months: 月份标识
            code: 股票代码

        Returns:
            Dict: JSON 内容，读取失败返回 None
        """
        try:
            from config import Config
            # 构建JSON文件路径: App/codes/code_data/RnnData/{months}/json/{code}.json
            project_root = Config.get_project_root()
            path = os.path.join(
                project_root,
                'App', 'codes', 'code_data', 'RnnData',
                months, 'json', f'{code}.json'
            )
            with open(path, 'r', encoding='utf-8') as lf:
                return json.load(lf)
        except (ValueError, FileNotFoundError, ImportError) as e:
            print(f"读取JSON文件失败: {e}")
            return None

    @classmethod
    def read_json_by_path(cls, path: str) -> Optional[Dict]:
        """
        根据路径读取 JSON 文件

        Args:
            path: 文件路径

        Returns:
            Dict: JSON 内容，读取失败返回 None
        """
        try:
            with open(path, 'r', encoding='utf-8') as lf:
                return json.load(lf)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            print(f"读取JSON文件失败: {path}, {e}")
            return None

    @classmethod
    def save_json(cls, dic: dict, months: str, code: str) -> bool:
        """
        保存 JSON 文件到 RNN 数据目录

        Args:
            dic: 要保存的字典
            months: 月份标识
            code: 股票代码

        Returns:
            bool: 保存是否成功
        """
        try:
            from config import Config
            # 构建JSON文件路径: App/codes/code_data/RnnData/{months}/json/{code}.json
            project_root = Config.get_project_root()
            path = os.path.join(
                project_root,
                'App', 'codes', 'code_data', 'RnnData',
                months, 'json', f'{code}.json'
            )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(dic, f, ensure_ascii=False, indent=2)
            return True
        except (ImportError, IOError) as e:
            print(f"保存JSON文件失败: {e}")
            return False

    @classmethod
    def save_json_by_path(cls, dic: dict, path: str) -> bool:
        """
        保存 JSON 文件到指定路径

        Args:
            dic: 要保存的字典
            path: 文件路径

        Returns:
            bool: 保存是否成功
        """
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(dic, f, ensure_ascii=False, indent=2)
            return True
        except IOError as e:
            print(f"保存JSON文件失败: {path}, {e}")
            return False

    @classmethod
    def read_all_file(cls, path: str, ends: str) -> List[str]:
        """
        读取目录下所有指定后缀的文件名

        Args:
            path: 目录路径
            ends: 文件后缀 (如 '.csv', '.json')

        Returns:
            List[str]: 文件名列表
        """
        fl = []
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.endswith(ends):
                    fl.append(f)
        return fl

    @classmethod
    def find_all_file(cls, path: str) -> List[List[str]]:
        """
        查找目录下所有文件（包括子目录）

        Args:
            path: 目录路径

        Returns:
            List[List[str]]: 每个目录的文件列表
        """
        fl = []
        for p, dir_list, files in os.walk(path):
            fl.append(files)
        return fl

    @classmethod
    def list_files(cls, path: str, extension: str = None) -> List[str]:
        """
        列出目录下的文件

        Args:
            path: 目录路径
            extension: 可选的文件扩展名过滤

        Returns:
            List[str]: 文件路径列表
        """
        result = []
        for root, dirs, files in os.walk(path):
            for f in files:
                if extension is None or f.endswith(extension):
                    result.append(os.path.join(root, f))
        return result


def rename_and_merge_csv_files(folder_path: str) -> None:
    """
    遍历指定目录及其子目录，将所有 .csv.csv 文件重命名为 .csv
    如果目标文件已存在，则将两个文件内容合并（基于去重）

    Args:
        folder_path: 根目录路径
    """
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.csv.csv'):
                old_path = os.path.join(root, file)
                new_path = os.path.join(root, file.replace('.csv.csv', '.csv'))

                if os.path.exists(new_path):
                    # 合并两个文件的内容
                    print(f"合并文件: {old_path} -> {new_path}")
                    old_data = pd.read_csv(old_path)
                    existing_data = pd.read_csv(new_path)
                    combined_data = pd.concat([existing_data, old_data]).drop_duplicates()
                    combined_data.to_csv(new_path, index=False)
                    os.remove(old_path)
                else:
                    # 重命名文件
                    os.rename(old_path, new_path)
                    print(f"重命名: {old_path} -> {new_path}")


# 便捷函数
read_json = ReadSaveFile.read_json_by_path
save_json = ReadSaveFile.save_json_by_path
list_files = ReadSaveFile.list_files
