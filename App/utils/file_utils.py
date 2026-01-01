# -*- coding: utf-8 -*-
"""
文件工具模块

此模块已重构，核心功能已迁移到 path_manager.py
本文件保留所有导出以保持向后兼容性。

新代码建议直接使用:
    from App.utils.path_manager import (
        PathManager,
        get_path_manager,
        get_stock_data_path,
        get_processed_data_path,
        get_model_path,
        get_temp_path,
        ensure_data_directories
    )
"""

# 从 path_manager 导入所有功能（向后兼容）
from App.utils.path_manager import (
    PathManager,
    get_path_manager,
    ensure_data_directories,
    get_stock_data_path,
    get_processed_data_path,
    get_model_path,
    get_temp_path,
)

__all__ = [
    'PathManager',
    'get_path_manager',
    'ensure_data_directories',
    'get_stock_data_path',
    'get_processed_data_path',
    'get_model_path',
    'get_temp_path',
]
