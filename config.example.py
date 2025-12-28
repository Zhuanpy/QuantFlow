"""
项目配置文件模板
复制此文件为 config.py 并填入你的实际配置信息
"""
import os
from pathlib import Path

class Config:
    """应用配置类"""
    
    # 获取项目根目录
    @staticmethod
    def get_project_root():
        """获取项目根目录路径"""
        # 获取当前文件的目录（config.py所在目录）
        current_file = Path(__file__).resolve()
        # 返回项目根目录
        return str(current_file.parent)
    
    # Flask配置
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # 数据库配置
    # 支持通过环境变量配置，格式：mysql+pymysql://用户名:密码@主机:端口/数据库名
    # 或者分别设置：DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = os.getenv('DB_PORT', '3306')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')  # 默认空密码，请通过环境变量或修改此处设置
    # 示例：DB_PASSWORD = os.getenv('DB_PASSWORD', 'your_password_here')
    # 如果直接在这里设置密码，将 'your_password_here' 替换为你的实际MySQL密码
    DB_NAME = os.getenv('DB_NAME', 'quanttradingsystem')
    
    # 构建数据库连接字符串
    if DB_PASSWORD:
        DATABASE_URI = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4'
    else:
        DATABASE_URI = f'mysql+pymysql://{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4'
    
    # 如果设置了 DATABASE_URL 环境变量，优先使用
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', DATABASE_URI)
    
    # 多数据库绑定配置（用于支持 __bind_key__）
    SQLALCHEMY_BINDS = {
        'quanttradingsystem': SQLALCHEMY_DATABASE_URI,
        # 如果需要其他数据库，可以在这里添加
        # 'datadaily': f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/datadaily?charset=utf8mb4',
    }
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = os.getenv('SQLALCHEMY_ECHO', 'False').lower() == 'true'
    
    # 其他配置
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

