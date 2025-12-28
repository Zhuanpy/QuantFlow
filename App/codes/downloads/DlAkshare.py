#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用 AKShare 下载股票数据
提供更灵活的数据获取方案，无单次数量限制
"""

import pandas as pd
import logging
from datetime import datetime, timedelta

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class AkshareDownloader:
    """AKShare 数据下载器"""
    
    def __init__(self):
        self.akshare_available = False
        self._check_akshare()
    
    def _check_akshare(self):
        """检查 AKShare 是否可用"""
        try:
            import akshare as ak
            self.ak = ak
            self.akshare_available = True
            logging.debug("AKShare 模块加载成功")
        except ImportError:
            logging.error("❌ AKShare 未安装")
            logging.error("请安装: pip install akshare")
            self.akshare_available = False
    
    def get_1m_data(self, stock_code, days=None, start_date=None, end_date=None, max_retries=3):
        """
        获取股票1分钟K线数据（带重试机制）
        
        Parameters:
        stock_code: 股票代码，如 '000001' 或 '600000'
        days: 获取最近N天的数据（如果指定，会覆盖 start_date）
        start_date: 开始日期，格式 'YYYY-MM-DD'（可选）
        end_date: 结束日期，格式 'YYYY-MM-DD'（可选）
        max_retries: 最大重试次数，默认3次
        
        Returns:
        tuple: (DataFrame, end_date) - 数据和最后日期
        
        注意:
        - AKShare 无单次数量限制，可获取大量历史数据
        - 但获取时间会比 pytdx 慢一些
        - 可能遇到反爬虫限制，会自动重试
        """
        if not self.akshare_available:
            logging.error("❌ AKShare 不可用")
            return pd.DataFrame(), None
        
        # 处理日期参数
        if days:
            # 如果指定了天数，计算日期范围
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=days)
            logging.info(f"获取最近 {days} 天的数据")
        elif start_date and end_date:
            # 使用指定的日期范围
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            days = (end_dt - start_dt).days
            logging.info(f"获取 {start_date} 至 {end_date} 的数据")
        else:
            # 默认获取最近5天
            days = 5
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=days)
            logging.info(f"使用默认参数，获取最近 {days} 天的数据")
        
        # 重试机制
        for retry_count in range(max_retries):
            try:
                if retry_count > 0:
                    # 重试前等待，避免请求过快
                    import time
                    wait_time = retry_count * 3  # 3秒、6秒、9秒
                    logging.warning(f"第 {retry_count + 1}/{max_retries} 次尝试，等待 {wait_time} 秒...")
                    time.sleep(wait_time)
                
                logging.info(f"正在获取 {stock_code} 的1分钟数据...")
                
                # AKShare 获取分钟数据
                # period 参数: "1", "5", "15", "30", "60"
                # adjust 参数: "" 不复权, "qfq" 前复权, "hfq" 后复权
                df = self.ak.stock_zh_a_hist_min_em(
                    symbol=stock_code,
                    period="1",      # 1分钟
                    adjust=""        # 不复权
                )
                
                if df.empty:
                    logging.warning(f"股票 {stock_code} 返回空数据")
                    if retry_count < max_retries - 1:
                        continue  # 重试
                    return pd.DataFrame(), None
                
                # 处理数据格式
                df = self._process_dataframe(df, start_dt, end_dt)
                
                if df.empty:
                    logging.warning(f"股票 {stock_code} 筛选后无数据")
                    return pd.DataFrame(), None
                
                # 获取最后日期
                end_date_value = df['date'].max().date() if not df.empty else None
                
                logging.info(f"✓ 成功获取 {stock_code} 的 {len(df)} 条记录")
                
                return df, end_date_value
                
            except Exception as e:
                logging.error(f"获取 {stock_code} 数据失败 (尝试 {retry_count + 1}/{max_retries}): {e}")
                
                if retry_count == max_retries - 1:
                    # 最后一次尝试失败，记录详细错误
                    import traceback
                    logging.error(f"详细错误: {traceback.format_exc()}")
                    return pd.DataFrame(), None
                else:
                    # 还有重试机会，继续
                    continue
        
        # 所有重试都失败
        logging.error(f"❌ 所有 {max_retries} 次尝试都失败")
        return pd.DataFrame(), None
    
    def _process_dataframe(self, df, start_dt, end_dt):
        """处理原始数据为标准DataFrame"""
        if df.empty:
            return df
        
        # 重命名列，匹配东方财富和 pytdx 的格式
        column_mapping = {
            '时间': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'money'
        }
        
        df = df.rename(columns=column_mapping)
        
        # 确保包含所有必需的列
        required_columns = ['date', 'open', 'close', 'high', 'low', 'volume']
        for col in required_columns:
            if col not in df.columns:
                logging.error(f"缺少必需列: {col}")
                logging.error(f"可用列: {df.columns.tolist()}")
                return pd.DataFrame()
        
        # 转换日期格式
        df['date'] = pd.to_datetime(df['date'])
        
        # 确保 money 列存在
        if 'money' not in df.columns:
            df['money'] = 0
        
        # 筛选日期范围
        df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]
        
        # 只保留需要的列
        columns_to_keep = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
        df = df[columns_to_keep]
        
        # 去重并排序
        df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
        
        return df
    
    def get_daily_data(self, stock_code, days=365, adjust=""):
        """
        获取股票日线数据
        
        Parameters:
        stock_code: 股票代码
        days: 获取天数，默认365天
        adjust: 复权类型，"" 不复权, "qfq" 前复权, "hfq" 后复权
        
        Returns:
        tuple: (DataFrame, end_date)
        """
        if not self.akshare_available:
            logging.error("❌ AKShare 不可用")
            return pd.DataFrame(), None
        
        try:
            logging.info(f"正在获取 {stock_code} 的日线数据...")
            
            # 计算日期范围
            end_date_str = datetime.now().strftime("%Y%m%d")
            start_date_str = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            
            # 获取日线数据
            df = self.ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date_str,
                end_date=end_date_str,
                adjust=adjust
            )
            
            if df.empty:
                logging.warning(f"股票 {stock_code} 返回空数据")
                return pd.DataFrame(), None
            
            # 重命名列
            column_mapping = {
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'money'
            }
            
            df = df.rename(columns=column_mapping)
            df['date'] = pd.to_datetime(df['date'])
            
            if 'money' not in df.columns:
                df['money'] = 0
            
            # 排序
            df = df.sort_values('date').reset_index(drop=True)
            
            end_date_value = df['date'].max().date() if not df.empty else None
            
            logging.info(f"✓ 成功获取 {stock_code} 的 {len(df)} 条日线数据")
            
            return df, end_date_value
            
        except Exception as e:
            logging.error(f"获取 {stock_code} 日线数据失败: {e}")
            return pd.DataFrame(), None


def download_stock_1m_akshare(stock_code, days=5):
    """
    便捷函数：使用 AKShare 下载股票1分钟数据
    
    Parameters:
    stock_code: 股票代码
    days: 获取天数，默认5天
    
    Returns:
    tuple: (DataFrame, end_date)
    
    示例:
    >>> df, end_date = download_stock_1m_akshare('000001', days=7)
    >>> print(f"获取 {len(df)} 条数据，最后日期: {end_date}")
    """
    downloader = AkshareDownloader()
    return downloader.get_1m_data(stock_code, days=days)


def download_stock_daily_akshare(stock_code, days=365, adjust=""):
    """
    便捷函数：使用 AKShare 下载股票日线数据
    
    Parameters:
    stock_code: 股票代码
    days: 获取天数，默认365天
    adjust: 复权类型，"" 不复权, "qfq" 前复权, "hfq" 后复权
    
    Returns:
    tuple: (DataFrame, end_date)
    
    示例:
    >>> df, end_date = download_stock_daily_akshare('600519', days=90)
    >>> print(f"获取 {len(df)} 条数据")
    """
    downloader = AkshareDownloader()
    return downloader.get_daily_data(stock_code, days=days, adjust=adjust)


# 测试代码
if __name__ == "__main__":
    print("=" * 80)
    print("AKShare 股票数据下载测试")
    print("=" * 80)
    print()
    
    # 测试1: 获取1分钟数据
    print("测试 1: 平安银行 (000001) - 获取1分钟数据")
    print("-" * 80)
    downloader = AkshareDownloader()
    
    if downloader.akshare_available:
        df1, end_date1 = downloader.get_1m_data('000001', days=2)
        if not df1.empty:
            print(f"✓ 成功获取 {len(df1)} 条数据")
            print(f"最后日期: {end_date1}")
            print(f"时间跨度: {(df1['date'].max() - df1['date'].min()).days} 天")
            print(f"数据列: {df1.columns.tolist()}")
            print("\n数据预览（前5条）:")
            print(df1.head())
            print("\n数据预览（后5条）:")
            print(df1.tail())
        else:
            print("✗ 获取失败")
    else:
        print("✗ AKShare 未安装，请运行: pip install akshare")
    
    print()
    print("=" * 80)
    
    # 添加延迟，避免请求过快
    if downloader.akshare_available:
        import time
        print("等待 5 秒，避免请求过快...")
        time.sleep(5)
    
    # 测试2: 获取更多天数的1分钟数据
    print("测试 2: 贵州茅台 (600519) - 获取10天1分钟数据")
    print("-" * 80)
    
    if downloader.akshare_available:
        df2, end_date2 = downloader.get_1m_data('600519', days=10)
        if not df2.empty:
            print(f"✓ 成功获取 {len(df2)} 条数据")
            print(f"最后日期: {end_date2}")
            print(f"时间跨度: {(df2['date'].max() - df2['date'].min()).days} 天")
            print("\n最近5条数据:")
            print(df2.tail())
        else:
            print("✗ 获取失败")
    
    print()
    print("=" * 80)
    
    # 添加延迟
    if downloader.akshare_available:
        import time
        print("等待 5 秒...")
        time.sleep(5)
    
    # 测试3: 获取日线数据
    print("测试 3: 平安银行 (000001) - 获取日线数据")
    print("-" * 80)
    
    if downloader.akshare_available:
        df3, end_date3 = downloader.get_daily_data('000001', days=30)
        if not df3.empty:
            print(f"✓ 成功获取 {len(df3)} 条日线数据")
            print(f"最后日期: {end_date3}")
            print("\n数据预览:")
            print(df3.tail())
        else:
            print("✗ 获取失败")
    
    print()
    print("=" * 80)
    
    # 添加延迟
    if downloader.akshare_available:
        import time
        print("等待 5 秒...")
        time.sleep(5)
    
    # 测试4: 使用便捷函数
    print("测试 4: 使用便捷函数")
    print("-" * 80)
    
    df4, end_date4 = download_stock_1m_akshare('000001', days=1)
    if not df4.empty:
        print(f"✓ 便捷函数成功: {len(df4)} 条数据")
    else:
        print("✗ 便捷函数失败（可能是 AKShare 未安装）")
    
    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)
    print()
    print("💡 AKShare 特点:")
    print("  ✅ 无单次数量限制")
    print("  ✅ 可获取大量历史数据")
    print("  ✅ 数据来源稳定（东方财富）")
    print("  ✅ 接口简单易用")
    print("  ⚠️ 速度比 pytdx 慢（HTTP 协议）")
    print("  ⚠️ 可能有反爬虫限制")
    print()
    print("📦 安装方法:")
    print("  pip install akshare")
    print()
    print("🔗 官方文档:")
    print("  https://akshare.akfamily.xyz/")

