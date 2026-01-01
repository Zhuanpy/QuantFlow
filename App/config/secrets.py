"""
敏感信息管理模块

此模块负责从环境变量中安全地读取敏感信息。
所有敏感信息都应该通过 .env 文件或系统环境变量配置，
而不是硬编码在代码中。

使用方法：
    from App.config.secrets import Secrets

    db_password = Secrets.get_db_password()
    eastmoney_cookie = Secrets.get_eastmoney_cookie()
"""

import os
from pathlib import Path
from typing import Optional


class Secrets:
    """敏感信息管理类"""

    _loaded = False
    _warned = set()  # 已警告过的配置项，避免重复警告
    _suppress_warnings = None  # 是否抑制警告（开发模式）

    @classmethod
    def _should_warn(cls, key: str) -> bool:
        """检查是否应该显示警告（每个key只警告一次）"""
        # 检查是否抑制警告
        if cls._suppress_warnings is None:
            cls._load_env()
            cls._suppress_warnings = os.getenv('SUPPRESS_SECRET_WARNINGS', 'false').lower() == 'true'

        if cls._suppress_warnings:
            return False

        if key in cls._warned:
            return False

        cls._warned.add(key)
        return True

    @classmethod
    def _load_env(cls) -> None:
        """加载 .env 文件（如果存在）"""
        if cls._loaded:
            return

        # 尝试加载 python-dotenv
        try:
            from dotenv import load_dotenv

            # 获取项目根目录
            project_root = Path(__file__).resolve().parent.parent.parent
            env_file = project_root / '.env'

            if env_file.exists():
                load_dotenv(env_file)
                print(f"[Secrets] 已加载环境变量文件: {env_file}")
            else:
                print(f"[Secrets] 未找到 .env 文件，使用系统环境变量")
        except ImportError:
            print("[Secrets] python-dotenv 未安装，使用系统环境变量")

        cls._loaded = True

    # ==================== 数据库配置 ====================

    @classmethod
    def get_db_host(cls) -> str:
        """获取数据库主机"""
        cls._load_env()
        return os.getenv('DB_HOST', 'localhost')

    @classmethod
    def get_db_port(cls) -> str:
        """获取数据库端口"""
        cls._load_env()
        return os.getenv('DB_PORT', '3306')

    @classmethod
    def get_db_user(cls) -> str:
        """获取数据库用户名"""
        cls._load_env()
        return os.getenv('DB_USER', 'root')

    @classmethod
    def get_db_password(cls) -> str:
        """获取数据库密码"""
        cls._load_env()
        password = os.getenv('DB_PASSWORD', '')
        if not password and cls._should_warn('DB_PASSWORD'):
            print("[Secrets] 警告: 未设置 DB_PASSWORD 环境变量")
        return password

    @classmethod
    def get_db_name(cls) -> str:
        """获取数据库名称"""
        cls._load_env()
        return os.getenv('DB_NAME', 'quanttradingsystem')

    @classmethod
    def get_database_uri(cls) -> str:
        """获取完整的数据库连接字符串"""
        cls._load_env()

        # 优先使用 DATABASE_URL 环境变量
        database_url = os.getenv('DATABASE_URL')
        if database_url:
            return database_url

        # 构建连接字符串
        host = cls.get_db_host()
        port = cls.get_db_port()
        user = cls.get_db_user()
        password = cls.get_db_password()
        db_name = cls.get_db_name()

        if password:
            return f'mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4'
        else:
            return f'mysql+pymysql://{user}@{host}:{port}/{db_name}?charset=utf8mb4'

    # ==================== Flask 配置 ====================

    @classmethod
    def get_secret_key(cls) -> str:
        """获取 Flask SECRET_KEY"""
        cls._load_env()
        key = os.getenv('SECRET_KEY', '')
        if (not key or key == 'dev-secret-key-change-in-production') and cls._should_warn('SECRET_KEY'):
            print("[Secrets] 警告: 请设置安全的 SECRET_KEY 环境变量")
        if not key:
            return 'dev-secret-key-change-in-production'
        return key

    # ==================== 东方财富配置 ====================

    @classmethod
    def get_eastmoney_cookie(cls) -> str:
        """获取东方财富 Cookie"""
        cls._load_env()
        cookie = os.getenv('EASTMONEY_COOKIE', '')
        if not cookie:
            # 返回默认的最小化 cookie
            return 'qgqp_b_id=default'
        return cookie

    @classmethod
    def get_eastmoney_funds_cookie(cls) -> str:
        """获取东方财富基金 Cookie（更完整的版本）"""
        cls._load_env()
        cookie = os.getenv('EASTMONEY_FUNDS_COOKIE', '')
        if not cookie:
            return cls.get_eastmoney_cookie()
        return cookie

    # ==================== 雪球配置 ====================

    @classmethod
    def get_xueqiu_cookies(cls) -> str:
        """获取雪球 Cookies"""
        cls._load_env()
        cookies = os.getenv('XUEQIU_COOKIES', '')
        if not cookies and cls._should_warn('XUEQIU_COOKIES'):
            print("[Secrets] 警告: 未设置 XUEQIU_COOKIES 环境变量")
        return cookies

    # ==================== 配置验证 ====================

    @classmethod
    def validate(cls) -> dict:
        """验证敏感信息配置是否完整"""
        cls._load_env()

        issues = []

        if not cls.get_db_password():
            issues.append("DB_PASSWORD 未设置")

        if cls.get_secret_key() == 'dev-secret-key-change-in-production':
            issues.append("SECRET_KEY 使用默认值")

        if not cls.get_eastmoney_cookie() or cls.get_eastmoney_cookie() == 'qgqp_b_id=default':
            issues.append("EASTMONEY_COOKIE 未设置")

        if not cls.get_xueqiu_cookies():
            issues.append("XUEQIU_COOKIES 未设置")

        return {
            'valid': len(issues) == 0,
            'issues': issues
        }

    @classmethod
    def print_status(cls) -> None:
        """打印敏感信息配置状态"""
        result = cls.validate()

        print("\n===== 敏感信息配置状态 =====")
        print(f"数据库主机: {cls.get_db_host()}")
        print(f"数据库端口: {cls.get_db_port()}")
        print(f"数据库用户: {cls.get_db_user()}")
        print(f"数据库密码: {'已设置' if cls.get_db_password() else '未设置'}")
        print(f"数据库名称: {cls.get_db_name()}")
        print(f"SECRET_KEY: {'已设置' if cls.get_secret_key() != 'dev-secret-key-change-in-production' else '使用默认值'}")
        print(f"东方财富Cookie: {'已设置' if cls.get_eastmoney_cookie() != 'qgqp_b_id=default' else '未设置'}")
        print(f"雪球Cookies: {'已设置' if cls.get_xueqiu_cookies() else '未设置'}")

        if not result['valid']:
            print("\n警告:")
            for issue in result['issues']:
                print(f"  - {issue}")
        else:
            print("\n所有敏感信息配置完整")
        print("================================\n")
