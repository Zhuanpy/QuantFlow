# -*- coding: utf-8 -*-
"""
配置文件 - 向后兼容入口

配置已整合到 App/config/ 模块，此文件保留用于向后兼容。

推荐使用方式：
    from App.config import Config, Secrets

兼容使用方式（本文件）：
    from config import Config
"""
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 从统一配置模块导入
from App.config import Config, Secrets

# 导出供外部使用
__all__ = ['Config', 'Secrets']
