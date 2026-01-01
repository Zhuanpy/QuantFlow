# -*- coding: utf-8 -*-
"""
配置模块

统一导出配置类，使用方式：
    from App.config import Config, Secrets
"""
from .secrets import Secrets
from .settings import Config

__all__ = ['Config', 'Secrets']
