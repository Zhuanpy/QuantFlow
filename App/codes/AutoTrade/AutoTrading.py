# -*- coding: utf-8 -*-
"""
自动化交易模块
使用 pywinauto 和 pyautogui 控制交易软件进行自动化交易
"""
import sys
import os
import logging
from pathlib import Path
from typing import Tuple, Optional, Dict, Any
from time import sleep

import cv2
import pandas as pd
import pyautogui as ag
from pywinauto import Application
from pywinauto.keyboard import send_keys

from autotrading_utils import match_screenshot
from ..MySql.DataBaseStockPool import TableStockPool
from config import Config
from functools import lru_cache
from datetime import datetime, timedelta

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

# 常量配置
class TradingConfig:
    """交易配置常量"""
    # 鼠标位置限制（用于安全停止）
    MOUSE_X_LIMIT = 1800
    MOUSE_Y_LIMIT = 665
    
    # 模板匹配阈值
    MATCH_THRESHOLD = 0.9
    
    # 默认等待时间
    DEFAULT_SLEEP = 0.1
    CLICK_SLEEP = 0.5
    
    # 买入金额限制（约5000元）
    BUY_AMOUNT = 5000
    MIN_SHARES = 100  # 最小买入股数（1手）
    SHARES_MULTIPLE = 100  # 股数必须是100的倍数
    
    # 价格限制
    MAX_PRICE = 200
    
    # 筛选条件
    DEFAULT_SCORE_THRESHOLD = -5
    EXCLUDED_CLASSIFICATIONS = ['创业板', '科创板']
    EXCLUDED_INDUSTRIES = ['房地产', '煤炭采选']
    
    # 数据访问配置
    DATA_CACHE_TTL = 300  # 缓存有效期（秒）
    MAX_RETRIES = 3  # 最大重试次数
    RETRY_DELAY = 1  # 重试延迟（秒）


class StockPoolDataAccess:
    """股票池数据访问层，提供缓存、错误处理和优化"""
    
    _cache_data = None
    _cache_timestamp = None
    
    @classmethod
    def _is_cache_valid(cls) -> bool:
        """检查缓存是否有效"""
        if cls._cache_data is None or cls._cache_timestamp is None:
            return False
        
        elapsed = (datetime.now() - cls._cache_timestamp).total_seconds()
        return elapsed < TradingConfig.DATA_CACHE_TTL
    
    @classmethod
    def _load_from_database(cls, use_cache: bool = True) -> pd.DataFrame:
        """
        从数据库加载股票池数据
        
        Args:
            use_cache: 是否使用缓存
            
        Returns:
            pd.DataFrame: 股票池数据
            
        Raises:
            Exception: 如果加载失败
        """
        # 检查缓存
        if use_cache and cls._is_cache_valid():
            logger.debug("使用缓存的股票池数据")
            return cls._cache_data.copy()
        
        # 从数据库加载
        retry_count = 0
        last_error = None
        
        while retry_count < TradingConfig.MAX_RETRIES:
            try:
                logger.info(f"从数据库加载股票池数据 (尝试 {retry_count + 1}/{TradingConfig.MAX_RETRIES})")
                data = TableStockPool.load_StockPool()
                
                # 数据验证
                if data is None or data.empty:
                    raise ValueError("股票池数据为空")
                
                # 验证必需的列
                required_columns = ['id', 'name', 'code', 'Classification', 'Industry', 
                                  'RnnModel', 'close', 'Position', 'BoardBoll']
                missing_columns = [col for col in required_columns if col not in data.columns]
                if missing_columns:
                    raise ValueError(f"缺少必需的列: {missing_columns}")
                
                # 更新缓存
                cls._cache_data = data.copy()
                cls._cache_timestamp = datetime.now()
                
                logger.info(f"成功加载股票池数据: {len(data)} 条记录")
                return data
                
            except Exception as e:
                last_error = e
                retry_count += 1
                logger.warning(f"加载股票池数据失败 (尝试 {retry_count}/{TradingConfig.MAX_RETRIES}): {e}")
                
                if retry_count < TradingConfig.MAX_RETRIES:
                    sleep(TradingConfig.RETRY_DELAY * retry_count)  # 指数退避
                else:
                    logger.error(f"加载股票池数据失败，已重试 {TradingConfig.MAX_RETRIES} 次")
                    raise Exception(f"无法加载股票池数据: {str(last_error)}")
        
        raise Exception(f"无法加载股票池数据: {str(last_error)}")
    
    @classmethod
    def get_stock_pool(cls, use_cache: bool = True, 
                      columns: Optional[list] = None) -> pd.DataFrame:
        """
        获取股票池数据
        
        Args:
            use_cache: 是否使用缓存
            columns: 要返回的列，如果为None则返回所有列
            
        Returns:
            pd.DataFrame: 股票池数据
        """
        data = cls._load_from_database(use_cache)
        
        if columns:
            missing_columns = [col for col in columns if col not in data.columns]
            if missing_columns:
                logger.warning(f"请求的列不存在: {missing_columns}")
                columns = [col for col in columns if col in data.columns]
            
            if columns:
                return data[columns].copy()
            else:
                logger.warning("没有有效的列，返回所有列")
                return data.copy()
        
        return data.copy()
    
    @classmethod
    def get_filtered_pool(cls, score: float = TradingConfig.DEFAULT_SCORE_THRESHOLD,
                          max_price: float = TradingConfig.MAX_PRICE,
                          position: int = 0,
                          excluded_classifications: Optional[list] = None,
                          excluded_industries: Optional[list] = None,
                          sort_by: str = 'RnnModel',
                          ascending: bool = True) -> pd.DataFrame:
        """
        获取筛选后的股票池
        
        Args:
            score: RnnModel评分阈值
            max_price: 最大价格
            position: 持仓状态 (0=未持仓, 1=已持仓)
            excluded_classifications: 排除的分类列表
            excluded_industries: 排除的行业列表
            sort_by: 排序字段
            ascending: 是否升序
            
        Returns:
            pd.DataFrame: 筛选后的股票池数据
        """
        try:
            data = cls.get_stock_pool(use_cache=True)
            
            # 应用筛选条件
            filtered = data[
                (data['RnnModel'] < score) &
                (data['close'] < max_price) &
                (data['Position'] == position)
            ].copy()
            
            # 排除分类
            if excluded_classifications:
                filtered = filtered[~filtered['Classification'].isin(excluded_classifications)]
            
            # 排除行业
            if excluded_industries:
                filtered = filtered[~filtered['Industry'].isin(excluded_industries)]
            
            # 排序
            if sort_by in filtered.columns:
                filtered = filtered.sort_values(by=sort_by, ascending=ascending)
            
            logger.info(f"筛选股票池: 原始 {len(data)} 条, 筛选后 {len(filtered)} 条")
            return filtered
            
        except Exception as e:
            logger.error(f"筛选股票池失败: {e}")
            raise
    
    @classmethod
    def get_position_stocks(cls, trade_method: int = 1) -> pd.DataFrame:
        """
        获取持仓股票
        
        Args:
            trade_method: 交易方法 (1=模拟, 2=实盘)
            
        Returns:
            pd.DataFrame: 持仓股票数据
        """
        try:
            data = cls.get_stock_pool(use_cache=True)
            
            position = data[
                (data['Position'] == 1) &
                (data['TradeMethod'] == trade_method)
            ].copy()
            
            logger.info(f"获取持仓股票: {len(position)} 只")
            return position
            
        except Exception as e:
            logger.error(f"获取持仓股票失败: {e}")
            raise
    
    @classmethod
    def clear_cache(cls) -> None:
        """清除缓存"""
        cls._cache_data = None
        cls._cache_timestamp = None
        logger.info("股票池缓存已清除")
    
    @classmethod
    def refresh_cache(cls) -> pd.DataFrame:
        """刷新缓存"""
        cls.clear_cache()
        return cls._load_from_database(use_cache=False)


def get_target_file_path(filename: str) -> str:
    """
    获取目标文件路径
    
    Args:
        filename: 文件名
        
    Returns:
        str: 完整文件路径
    """
    project_root = Config.get_project_root()
    return os.path.join(project_root, 'App', 'codes', 'AutoTrade', 'targetfile', filename)


def clic_location(screen: str, template: str) -> Tuple[int, int]:
    """
    根据模板匹配点击位置，返回点击的坐标点
    
    Args:
        screen: 当前屏幕截图的路径
        template: 模板图片的路径
        
    Returns:
        Tuple[int, int]: 点击位置的坐标点 (x, y)
        
    Raises:
        ValueError: 如果无法读取图片或匹配失败
    """
    try:
        # 读取模板图片并获取其尺寸
        template_img = cv2.imread(template)
        if template_img is None:
            raise ValueError(f"无法读取模板图片: {template}")
        
        template_size = template_img.shape[:2]
        
        # 计算模板图片中心点坐标
        temp_x = int(template_size[1] / 2)
        temp_y = int(template_size[0] / 2)
        
        # 匹配模板并获取匹配结果
        result = match_screenshot(screen, template)
        if result is None:
            raise ValueError(f"模板匹配失败: {template}")
        
        min_val, max_val, min_loc, max_loc = result
        
        # 检查匹配度
        if max_val < TradingConfig.MATCH_THRESHOLD:
            logger.warning(f"模板匹配度较低: {max_val:.2f}, 阈值: {TradingConfig.MATCH_THRESHOLD}")
        
        # 计算点击位置的坐标
        x = max_loc[0] + temp_x
        y = max_loc[1] + temp_y
        
        return x, y
        
    except Exception as e:
        logger.error(f"计算点击位置失败: {e}")
        raise


def get_screenshot() -> str:
    """
    获取当前屏幕截图并保存
    
    Returns:
        str: 截图文件路径
    """
    try:
        screenshot_path = get_target_file_path('screenshot.jpg')
        img = ag.screenshot()
        img.save(screenshot_path)
        return screenshot_path
    except Exception as e:
        logger.error(f"截图失败: {e}")
        raise


def start_trading_app(app_path: Optional[str] = None) -> Tuple[Application, Any]:
    """
    启动交易应用程序并判断是否成功打开交易界面
    
    Args:
        app_path: 交易软件路径，如果为None则从配置读取
        
    Returns:
        Tuple[Application, WindowSpecification]: 应用程序对象和窗口对象
        
    Raises:
        RuntimeError: 如果无法启动交易软件
    """
    if app_path is None:
        # 从配置读取或使用默认路径
        app_path = os.getenv(
            'TRADING_APP_PATH',
            os.path.join('E:/', 'MyApp', 'FinancialSoftware', 'TonghuashunApp', 'xiadan.exe')
        )
    
    if not os.path.exists(app_path):
        raise FileNotFoundError(f"交易软件路径不存在: {app_path}")
    
    max_retries = 5
    retry_count = 0
    app_ = None
    win_ = None
    
    while retry_count < max_retries:
        try:
            logger.info(f"尝试启动交易软件 (第 {retry_count + 1} 次)...")
            app_ = Application(backend='uia').start(app_path)
            win_ = app_.window(class_name="网上股票交易系统5.0")
            sleep(1)
            
            # 截屏判断是否打开交易平台
            target = get_screenshot()
            login_template = get_target_file_path('loginsuccess.jpg')
            result = match_screenshot(target, login_template)
            
            if result is None:
                logger.warning("无法匹配登录成功模板")
                retry_count += 1
                continue
            
            match_score = result[1]
            
            # 如果匹配大于阈值就判断顺利打开了
            if match_score > TradingConfig.MATCH_THRESHOLD:
                logger.info('成功打开交易平台')
                return app_, win_
            else:
                logger.warning(f'未能成功打开交易平台，匹配度: {match_score:.2f}，重试中...')
                retry_count += 1
                
        except Exception as e:
            logger.error(f"启动交易软件时出错: {e}")
            retry_count += 1
            if retry_count < max_retries:
                sleep(2)  # 等待后重试
            else:
                raise RuntimeError(f"无法启动交易软件，已重试 {max_retries} 次")
    
    raise RuntimeError(f"无法启动交易软件，已重试 {max_retries} 次")


class TongHuaShunAutoTrade:
    """同花顺自动化交易类"""
    
    def __init__(self, app_path: Optional[str] = None):
        """
        初始化自动化交易对象
        
        Args:
            app_path: 交易软件路径
        """
        try:
            self.app, self.win = start_trading_app(app_path)
            logger.info("自动化交易对象初始化成功")
        except Exception as e:
            logger.error(f"初始化自动化交易对象失败: {e}")
            raise
    
    def sleep2stop(self, s: float = TradingConfig.DEFAULT_SLEEP) -> bool:
        """
        根据鼠标位置决定是否停止程序并等待指定时间
        
        Args:
            s: 等待时间，默认为 0.1 秒
            
        Returns:
            bool: 如果程序继续运行则返回 True，如果程序停止则不会返回（系统退出）
        """
        try:
            x, y = ag.position()
            
            # 判断鼠标位置是否超过设定的范围（安全停止机制）
            if x > TradingConfig.MOUSE_X_LIMIT or y > TradingConfig.MOUSE_Y_LIMIT:
                logger.warning(f"检测到鼠标位置超出限制 ({x}, {y})，程序退出")
                sys.exit()
            
            sleep(s)
            return True
            
        except Exception as e:
            logger.error(f"sleep2stop 执行失败: {e}")
            return False
    
    def _click_template(self, template_name: str, double_click: bool = False, 
                       input_text: Optional[str] = None) -> bool:
        """
        通用的模板点击操作
        
        Args:
            template_name: 模板文件名
            double_click: 是否双击
            input_text: 要输入的文本，如果为None则不输入
            
        Returns:
            bool: 操作是否成功
        """
        try:
            screen = get_screenshot()
            template_path = get_target_file_path(template_name)
            
            x, y = clic_location(screen, template_path)
            ag.moveTo(x, y)
            self.sleep2stop()
            
            if double_click:
                ag.doubleClick()
            else:
                ag.click()
            
            self.sleep2stop()
            
            if input_text:
                send_keys(input_text)
                self.sleep2stop(TradingConfig.CLICK_SLEEP)
            
            return True
            
        except Exception as e:
            logger.error(f"点击模板失败: {template_name}, 错误: {e}")
            return False
    
    def buy_action(self, code_: str, num_: str, price_: Optional[str] = None) -> bool:
        """
        执行买入操作
        
        Args:
            code_: 证券代码
            num_: 买入数量
            price_: 买入价格，如果为None则按市场价买入
            
        Returns:
            bool: 操作是否成功
        """
        try:
            logger.info(f"开始买入操作: 代码={code_}, 数量={num_}, 价格={price_}")
            
            # 点击买入界面
            if not self._click_template('buy/f1.jpg'):
                return False
            
            # 输入证券代码
            if not self._click_template('buy/code.jpg', double_click=True, input_text=code_):
                return False
            
            # 输入买入价格（如果有提供）
            if price_:
                if not self._click_template('buy/price.jpg', double_click=True, input_text=price_):
                    return False
            
            # 输入买入数量
            if not self._click_template('buy/num.jpg', double_click=True, input_text=num_):
                return False
            
            # 确认买入提示
            if not self._click_template('buy/buy.jpg', double_click=True, input_text=num_):
                return False
            
            # 确认-买入-提示
            screen = get_screenshot()
            self.sleep2stop()
            
            try:
                x, y = clic_location(screen, get_target_file_path('buy/yes.jpg'))
                ag.moveTo(x, y)
                self.sleep2stop()
                ag.click()
                self.sleep2stop()
                
                logger.info(f"买入操作成功: {code_}")
                return True
                
            except Exception as e:
                logger.error(f"确认买入失败: {e}")
                return False
                
        except Exception as e:
            logger.error(f"买入操作失败: {code_}, 错误: {e}")
            return False
    
    def sell_action(self, code_: str, num_: str, price_: Optional[str] = None) -> bool:
        """
        执行卖出操作
        
        Args:
            code_: 证券代码
            num_: 卖出数量
            price_: 卖出价格，如果为None则按市场价卖出
            
        Returns:
            bool: 操作是否成功
        """
        try:
            logger.info(f"开始卖出操作: 代码={code_}, 数量={num_}, 价格={price_}")
            
            # 点击卖出界面
            if not self._click_template('sell/f2.jpg'):
                return False
            
            # 输入证券代码
            if not self._click_template('sell/sellcode.jpg', double_click=True, input_text=code_):
                return False
            
            # 输入卖出价格（如果有提供）
            if price_:
                if not self._click_template('sell/sellprice.jpg', double_click=True, input_text=price_):
                    return False
            
            # 输入卖出数量
            if not self._click_template('sell/sellnum.jpg', double_click=True, input_text=num_):
                return False
            
            # 确认-卖出
            if not self._click_template('sell/selling.jpg'):
                return False
            
            # 确认-卖出-提示
            screen = get_screenshot()
            try:
                x, y = clic_location(screen, get_target_file_path('sell/yes.jpg'))
                ag.moveTo(x, y)
                self.sleep2stop(TradingConfig.CLICK_SLEEP)
                ag.click()
                self.sleep2stop()
                
                logger.info(f"卖出操作成功: {code_}")
                return True
                
            except Exception as e:
                logger.error(f"确认卖出失败: {e}")
                return False
                
        except Exception as e:
            logger.error(f"卖出操作失败: {code_}, 错误: {e}")
            return False


class TradePoolFaker:
    """股票池交易类（模拟/测试版本）"""
    
    @classmethod
    def buy_num(cls, close: float) -> int:
        """
        根据收盘价计算可以买入的股票数量
        
        Args:
            close: 股票的收盘价
            
        Returns:
            int: 可以买入的股票数量，至少为 100 股，并且为 100 的倍数
        """
        if close <= 0:
            raise ValueError(f"收盘价必须大于0: {close}")
        
        num_ = TradingConfig.BUY_AMOUNT / close
        
        # 确保至少买入1手（100股）
        if num_ < TradingConfig.MIN_SHARES:
            num_ = TradingConfig.MIN_SHARES
        else:
            # 向上取整到100的倍数
            num_ = ((int(num_) // TradingConfig.SHARES_MULTIPLE) + 1) * TradingConfig.SHARES_MULTIPLE
        
        return int(num_)
    
    @classmethod
    def buy_pool(cls, score: int = TradingConfig.DEFAULT_SCORE_THRESHOLD, 
                 show_pool: bool = False) -> None:
        """
        根据给定的评分筛选股票池中的股票并执行买入操作
        
        Args:
            score: 筛选股票的评分阈值，默认值为 -5
            show_pool: 是否只显示筛选后的股票池而不执行买入，默认值为 False
        """
        try:
            # 使用改进的数据访问层
            filtered_pool = StockPoolDataAccess.get_filtered_pool(
                score=score,
                max_price=TradingConfig.MAX_PRICE,
                position=0,
                excluded_classifications=TradingConfig.EXCLUDED_CLASSIFICATIONS,
                excluded_industries=TradingConfig.EXCLUDED_INDUSTRIES,
                sort_by='RnnModel',
                ascending=True
            )
            
            # 只选择需要的列
            required_columns = ['id', 'name', 'code', 'Classification', 'Industry', 
                              'RnnModel', 'close', 'Position', 'BoardBoll']
            available_columns = [col for col in required_columns if col in filtered_pool.columns]
            filtered_pool = filtered_pool[available_columns]
            
            if filtered_pool.empty:
                logger.warning(f"未找到符合条件的股票，评分阈值: {score}")
                return
            
            if show_pool:
                print(filtered_pool)
                return
            
            logger.info(f"找到 {len(filtered_pool)} 只符合条件的股票，开始买入...")
            
            trade_ = TongHuaShunAutoTrade()
            success_count = 0
            fail_count = 0
            
            for i in filtered_pool.index:
                try:
                    code_ = filtered_pool.loc[i, 'code']
                    close = filtered_pool.loc[i, 'close']
                    num_ = cls.buy_num(close)
                    
                    logger.info(f"买入股票: 代码={code_}, 数量={num_}, 价格={close}")
                    
                    if trade_.buy_action(code_, str(num_), None):
                        success_count += 1
                    else:
                        fail_count += 1
                        logger.warning(f"买入失败: {code_}")
                        
                except Exception as e:
                    fail_count += 1
                    logger.error(f"买入股票时出错: {code_}, 错误: {e}")
            
            logger.info(f"买入操作完成: 成功 {success_count} 只, 失败 {fail_count} 只")
            
        except Exception as e:
            logger.error(f"买入股票池失败: {e}")
            raise
    
    @classmethod
    def sell_pool(cls) -> None:
        """
        根据股票池中的持仓信息执行卖出操作
        """
        try:
            # 使用改进的数据访问层
            position = StockPoolDataAccess.get_position_stocks(trade_method=1)
            
            if position.empty:
                logger.warning("未找到需要卖出的持仓")
                return
            
            logger.info(f"找到 {len(position)} 只持仓股票，开始卖出...")
            print(position.head())
            
            # 打开交易软件
            trade_ = TongHuaShunAutoTrade()
            success_count = 0
            fail_count = 0
            
            for index in position.index:
                try:
                    code_ = position.loc[index, 'code']
                    num_ = str(position.loc[index, 'PositionNum'])
                    
                    logger.info(f"卖出股票: 代码={code_}, 数量={num_}")
                    
                    if trade_.sell_action(code_, num_, None):
                        success_count += 1
                    else:
                        fail_count += 1
                        logger.warning(f"卖出失败: {code_}")
                        
                except Exception as e:
                    fail_count += 1
                    logger.error(f"卖出股票时出错: {code_}, 错误: {e}")
            
            logger.info(f"卖出操作完成: 成功 {success_count} 只, 失败 {fail_count} 只")
            
        except Exception as e:
            logger.error(f"卖出股票池失败: {e}")
            raise


class TradePoolReal:
    """股票池交易类（实际交易版本）"""
    
    def __init__(self, pool_loader):
        """
        初始化实际交易对象
        
        Args:
            pool_loader: 股票池数据加载器
        """
        self.pool_loader = pool_loader
    
    def _load_stock_data(self) -> pd.DataFrame:
        """加载股票池数据"""
        # 使用改进的数据访问层
        return StockPoolDataAccess.get_stock_pool(use_cache=True)
    
    def buy_num(self, close: float) -> int:
        """
        根据收盘价计算可以买入的股票数量
        
        Args:
            close: 股票的收盘价
            
        Returns:
            int: 可以买入的股票数量
        """
        if close <= 0:
            raise ValueError(f"收盘价必须大于0: {close}")
        
        num_ = max(TradingConfig.MIN_SHARES, int(TradingConfig.BUY_AMOUNT / close))
        num_ = ((num_ // TradingConfig.SHARES_MULTIPLE) + 1) * TradingConfig.SHARES_MULTIPLE if num_ > TradingConfig.MIN_SHARES else num_
        return num_
    
    def bottom_down_data(self) -> pd.DataFrame:
        """获取底部下跌数据"""
        data_ = self._load_stock_data()
        return data_[(data_['Trends'] == -1) & (data_['RnnModel'] < -4.5)]
    
    def bottom_up_data(self) -> pd.DataFrame:
        """获取底部上涨数据"""
        data_ = self._load_stock_data()
        return data_[(data_['Trends'] == 1) & (data_['RnnModel'] <= 1.5)]
    
    def position_data(self) -> pd.DataFrame:
        """获取持仓数据"""
        data_ = self._load_stock_data()
        return data_[(data_['Position'] == 1) & (data_['TradeMethod'] <= 1)]
    
    def buy_pool(self) -> None:
        """执行买入操作（待实现）"""
        logger.info("买入操作（待实现）")
        pass
    
    def sell_pool(self) -> None:
        """执行卖出操作（待实现）"""
        logger.info("卖出操作（待实现）")
        pass


if __name__ == '__main__':
    # 测试代码
    code = '002475'
    num = '300'
    price = '10'
    
    trade = TradePoolFaker()
    trade.buy_pool(score=-4, show_pool=True)
    
    # 安装依赖: py -3.10 -m pip install pyautogui
