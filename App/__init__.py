"""
Flask应用工厂
"""
from flask import Flask
from flask.json.provider import DefaultJSONProvider
from App.exts import db
from App.utils.file_utils import ensure_data_directories
import os
from config import Config


class SafeJSONProvider(DefaultJSONProvider):
    """容忍 pandas/numpy 缺失值的 JSON 提供器。

    parquet/重采样出来的行常带 pd.NA（NAType）或 numpy 标量，直接 jsonify 会抛
    `Object of type NAType is not JSON serializable`。这里统一把缺失值转成 null、
    numpy 标量转成原生 Python 值，避免任一接口因单个脏值整体 500。
    """

    @staticmethod
    def default(o):
        import numpy as np
        import pandas as pd
        # numpy 标量 → 原生（NaN 转 None）
        if isinstance(o, np.generic):
            if isinstance(o, np.floating) and np.isnan(o):
                return None
            return o.item()
        # pd.NA / NaT / 其它 pandas 标量缺失值 → None
        try:
            if pd.isna(o):
                return None
        except (TypeError, ValueError):
            pass
        return DefaultJSONProvider.default(o)


def create_app(config_name='default'):
    """
    创建Flask应用实例
    
    Args:
        config_name: 配置名称（暂时未使用，保留用于未来扩展）
    
    Returns:
        Flask应用实例
    """
    app = Flask(__name__)

    # 用容忍 pandas/numpy 缺失值的 JSON 提供器（pd.NA / NaN → null），
    # 避免 parquet/重采样脏值让接口整体 500
    app.json = SafeJSONProvider(app)

    # 加载配置
    app.config.from_object(Config)
    
    # 初始化扩展
    db.init_app(app)
    
    # 确保数据目录存在
    ensure_data_directories()
    
    # 注册蓝图
    register_blueprints(app)

    # 预热交易日历：在主线程内完成 akshare(py_mini_racer/V8) 初始化并缓存，
    # 之后请求线程只读缓存，避免在 worker 线程里初始化 V8 触发 PartitionAlloc 崩溃。
    _warm_trading_calendar()

    # 持仓当日 1m 自动拉取（交易时段每 5 分钟）——后台 daemon 线程
    _maybe_start_holdings_autofetch(app)

    return app


def _warm_trading_calendar():
    """启动时（主线程）预拉交易日历，填充进程内缓存。失败不影响启动（回退周末判断）。"""
    try:
        from App.routes.data.download_data_route import get_trading_dates
        dates = get_trading_dates()
        print(f"交易日历预热: {'成功 %d 个交易日' % len(dates) if dates else '失败(回退周末判断)'}")
    except Exception as e:
        print(f"警告: 交易日历预热失败: {e}")


def _maybe_start_holdings_autofetch(app):
    """启动持仓 1m 自动拉取后台线程。

    debug=True 时 Werkzeug reloader 会在「父（监视）+ 子（服务）」两个进程各建一次 app，
    只在真正服务请求的子进程（环境变量 WERKZEUG_RUN_MAIN=='true'）里起线程，避免父进程
    也每 5 分钟拉一次。未走 reloader 的场景（该变量为 None）此处不起，改由首次访问
    /trade/holdings/realtime 时 ensure_started 兜底自启。
    """
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return
    try:
        from App.services.holdings_1m_autofetch import start_autofetch
        ok, msg = start_autofetch(app)
        print(f"持仓 1m 自动拉取: {msg}")
    except Exception as e:
        print(f"警告: 持仓 1m 自动拉取未启动: {e}")

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
        from App.routes.data.compare_route import compare_bp
        app.register_blueprint(compare_bp)
    except ImportError as e:
        print(f"警告: 无法导入 compare_route 蓝图: {e}")

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
        from App.routes.strategy.BoardPool import board_pool_bp
        app.register_blueprint(board_pool_bp)
    except ImportError as e:
        print(f"警告: 无法导入 BoardPool 蓝图: {e}")

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

