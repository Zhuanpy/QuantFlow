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
    from App.routes.views import main_bp, dl_bp, rnn_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(dl_bp)
    app.register_blueprint(rnn_bp)

    # 问题/想法清单（合并自原 views.py + others/issues.py + others/route_stock_issue.py）
    from App.routes.issues_route import issue_bp
    app.register_blueprint(issue_bp)
    
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
        app.register_blueprint(dl_funds_awkward_bp, url_prefix='/funds')
    except ImportError as e:
        print(f"警告: 无法导入 download_top500_funds_awkward 蓝图: {e}")

    try:
        from App.routes.data.data_viewer_route import data_viewer_bp
        app.register_blueprint(data_viewer_bp)
    except ImportError as e:
        print(f"警告: 无法导入 data_viewer_route 蓝图: {e}")

    try:
        from App.routes.data.stock_detail_route import stock_detail_bp
        app.register_blueprint(stock_detail_bp)
    except ImportError as e:
        print(f"警告: 无法导入 stock_detail_route 蓝图: {e}")

    try:
        from App.routes.data.tdx_download_route import tdx_bp
        app.register_blueprint(tdx_bp)
    except ImportError as e:
        print(f"警告: 无法导入 tdx_download_route 蓝图: {e}")

    try:
        from App.routes.data.data_analysis_route import data_analysis_bp
        app.register_blueprint(data_analysis_bp)
    except ImportError as e:
        print(f"警告: 无法导入 data_analysis_route 蓝图: {e}")

    try:
        from App.routes.data.daily_viewer_route import daily_viewer_bp
        app.register_blueprint(daily_viewer_bp)
    except ImportError as e:
        print(f"警告: 无法导入 daily_viewer_route 蓝图: {e}")

    # 任务状态看板路由
    try:
        from App.routes.data.task_status_route import task_status_bp
        app.register_blueprint(task_status_bp)
    except ImportError as e:
        print(f"警告: 无法导入 task_status_route 蓝图: {e}")

    # 交易计划路由
    try:
        from App.routes.trade.trade_plan_route import trade_plan_bp
        app.register_blueprint(trade_plan_bp)
    except ImportError as e:
        print(f"警告: 无法导入 trade_plan_route 蓝图: {e}")

    # 持仓股票实时 15m 趋势
    try:
        from App.routes.trade.holdings_realtime_route import holdings_realtime_bp
        app.register_blueprint(holdings_realtime_bp)
    except ImportError as e:
        print(f"警告: 无法导入 holdings_realtime_route 蓝图: {e}")

    # 策略相关路由
    try:
        from App.routes.strategy.StockPoolManagement import stock_pool_bp
        app.register_blueprint(stock_pool_bp)
    except ImportError as e:
        print(f"警告: 无法导入 StockPoolManagement 蓝图: {e}")

    try:
        from App.routes.strategy.BoardTrendScoring import board_trend_bp
        app.register_blueprint(board_trend_bp)
    except ImportError as e:
        print(f"警告: 无法导入 BoardTrendScoring 蓝图: {e}")

    try:
        from App.routes.strategy.BoardData import board_data_bp
        app.register_blueprint(board_data_bp)
    except ImportError as e:
        print(f"警告: 无法导入 BoardData 蓝图: {e}")

    try:
        from App.routes.strategy.BoardPreference import board_pref_bp
        app.register_blueprint(board_pref_bp)
    except ImportError as e:
        print(f"警告: 无法导入 BoardPreference 蓝图: {e}")

    try:
        from App.routes.strategy.BoardOverview import board_overview_bp
        app.register_blueprint(board_overview_bp)
    except ImportError as e:
        print(f"警告: 无法导入 BoardOverview 蓝图: {e}")

    try:
        from App.routes.strategy.StockTrendScoring import stock_trend_bp
        app.register_blueprint(stock_trend_bp)
    except ImportError as e:
        print(f"警告: 无法导入 StockTrendScoring 蓝图: {e}")
    
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
    
    # 新闻 / 热点话题路由
    try:
        from App.routes.news.hotspots_route import news_hotspots_bp
        app.register_blueprint(news_hotspots_bp)
    except ImportError as e:
        print(f"警告: 无法导入 news.hotspots_route 蓝图: {e}")

    # 个股筛选器（/screener/）：基于 stock_dist_snapshot 的全市场分布筛选
    try:
        from App.routes.strategy.dist_filter import screener_bp
        app.register_blueprint(screener_bp)
    except ImportError as e:
        print(f"警告: 无法导入 strategy.dist_filter (screener_bp) 蓝图: {e}")

    # 个股统计列表（/stock_stats/）：整合分布快照 + RNN 预测 + 板块趋势
    try:
        from App.routes.strategy.stock_stats import stock_stats_bp
        app.register_blueprint(stock_stats_bp)
    except ImportError as e:
        print(f"警告: 无法导入 strategy.stock_stats (stock_stats_bp) 蓝图: {e}")

    # 注：原 others/issues.py 和 others/route_stock_issue.py 已并入
    # App.routes.issues_route，不再单独注册（避免 /issues 路由冲突）

