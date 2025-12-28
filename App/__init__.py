"""
Flask应用工厂
"""
from flask import Flask
from App.exts import db
from App.utils.file_utils import ensure_data_directories
import os
from config import Config

def create_app(config_name='default'):
    """
    创建Flask应用实例
    
    Args:
        config_name: 配置名称（暂时未使用，保留用于未来扩展）
    
    Returns:
        Flask应用实例
    """
    app = Flask(__name__)
    
    # 加载配置
    app.config.from_object(Config)
    
    # 初始化扩展
    db.init_app(app)
    
    # 确保数据目录存在
    ensure_data_directories()
    
    # 注册蓝图
    register_blueprints(app)
    
    return app

def register_blueprints(app):
    """注册所有蓝图"""
    # 主页面路由
    from App.routes.views import main_bp, dl_bp, rnn_bp, issue_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(dl_bp)
    app.register_blueprint(rnn_bp)
    app.register_blueprint(issue_bp)  # views.py 中的 issue_bp 保持原名称
    
    # 认证路由
    from App.routes.auth_routes import auth_bp
    app.register_blueprint(auth_bp)
    
    # 主路由（main.py中的main_bp）
    from App.routes.main import main_bp as main_route_bp
    app.register_blueprint(main_route_bp)
    
    # 数据相关路由
    try:
        from App.routes.data.download_data_route import download_data_bp
        app.register_blueprint(download_data_bp)
    except ImportError as e:
        print(f"警告: 无法导入 download_data_route 蓝图: {e}")
        print("提示: 缺少 App.codes.downloads 模块，某些下载功能可能不可用")
    
    try:
        from App.routes.data.process_15m_data_route import process_data_bp
        app.register_blueprint(process_data_bp)
    except ImportError as e:
        print(f"警告: 无法导入 process_15m_data_route 蓝图: {e}")
    
    try:
        from App.routes.data.data_management import data_bp
        app.register_blueprint(data_bp)
    except ImportError as e:
        print(f"警告: 无法导入 data_management 蓝图: {e}")
    
    try:
        from App.routes.data.fund_analysis_route import fund_analysis_bp
        app.register_blueprint(fund_analysis_bp)
    except ImportError as e:
        print(f"警告: 无法导入 fund_analysis_route 蓝图: {e}")
    
    try:
        from App.routes.data.download_EastMoney import dl_eastmoney_bp
        app.register_blueprint(dl_eastmoney_bp)
    except ImportError as e:
        print(f"警告: 无法导入 download_EastMoney 蓝图: {e}")
    
    try:
        from App.routes.data.download_fund_route import download_fund_bp
        app.register_blueprint(download_fund_bp)
    except ImportError as e:
        print(f"警告: 无法导入 download_fund_route 蓝图: {e}")
    
    try:
        from App.routes.data.download_top500_funds_awkward import dl_funds_awkward_bp
        app.register_blueprint(dl_funds_awkward_bp)
    except ImportError as e:
        print(f"警告: 无法导入 download_top500_funds_awkward 蓝图: {e}")
    
    # 策略相关路由
    try:
        from App.routes.strategy.StockPoolManagement import stock_pool_bp
        app.register_blueprint(stock_pool_bp)
    except ImportError as e:
        print(f"警告: 无法导入 StockPoolManagement 蓝图: {e}")
    
    try:
        from App.routes.strategy.RnnData import RnnData
        app.register_blueprint(RnnData)
    except ImportError as e:
        print(f"警告: 无法导入 RnnData 蓝图: {e}")
    
    try:
        from App.routes.strategy.rnn_data import rnn_bp as strategy_rnn_bp
        app.register_blueprint(strategy_rnn_bp)
    except ImportError:
        pass  # 如果模块不存在，跳过
    
    try:
        from App.routes.strategy.RnnStrategies import rnn_strategies_bp
        app.register_blueprint(rnn_strategies_bp)
    except ImportError:
        pass  # 如果模块不存在，跳过
    
    # 交易相关路由
    try:
        from App.routes.trade.trading_route import trading_bp
        app.register_blueprint(trading_bp)
    except ImportError:
        pass
    
    try:
        from App.routes.trade.trading_strategies import trading_strategies_bp
        app.register_blueprint(trading_strategies_bp)
    except ImportError:
        pass
    
    # 其他路由
    try:
        from App.routes.others.issues import issue_bp as others_issue_bp
        app.register_blueprint(others_issue_bp)  # 蓝图名称已改为 others_issue_bp
    except ImportError:
        pass
    
    try:
        from App.routes.others.route_stock_issue import issue_bp as stock_issue_bp
        app.register_blueprint(stock_issue_bp)  # 蓝图名称已改为 stock_issue_bp
    except ImportError:
        pass

