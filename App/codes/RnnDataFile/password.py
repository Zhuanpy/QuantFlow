# -*- coding: utf-8 -*-
"""
密码和配置参数模块
已迁移到 config.py，此文件保留以保持向后兼容性
"""
from config import Config


def sql_password():
    """获取SQL密码（已迁移到Config.DB_PASSWORD）"""
    return Config.get_sql_password()


class XueqiuParam:
    """雪球参数类（已迁移到Config）"""
    folder = "XueQiu"

    @classmethod
    def cookies(cls):
        """获取雪球Cookies"""
        return Config.get_xueqiu_cookies()

    @classmethod
    def headers(cls):
        """获取雪球请求头"""
        return Config.get_xueqiu_headers()

