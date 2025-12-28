#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用 pytdx 下载股票数据
作为东方财富API的可靠替代方案
"""

from pytdx.hq import TdxHq_API
import pandas as pd
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 已验证可用的服务器列表
AVAILABLE_SERVERS = [
    ('60.191.117.167', 7709),    # ✓ 可用
    ('115.238.56.198', 7709),    # ✓ 可用
    ('115.238.90.165', 7709),    # ✓ 可用
    # 备用服务器
    ('218.108.98.244', 7709),
    ('218.108.47.69', 7709),
    ('119.147.212.81', 7709),
    ('114.80.80.72', 7709),
]


def get_market(stock_code):
    """
    根据股票代码判断市场
    
    Parameters:
    stock_code: 股票代码，如 '000001' 或 '600000'
    
    Returns:
    int: 0-深圳市场，1-上海市场
    """
    if stock_code.startswith(('6', '5', '9')):  # 上海市场
        return 1
    else:  # 深圳市场（0, 3, 2开头）
        return 0


class PytdxDownloader:
    """pytdx 数据下载器"""
    
    def __init__(self):
        self.api = None
        self.current_server = None
    
    def connect(self):
        """连接到可用的服务器"""
        for server_ip, server_port in AVAILABLE_SERVERS:
            try:
                self.api = TdxHq_API()
                if self.api.connect(server_ip, server_port, time_out=5):
                    self.current_server = (server_ip, server_port)
                    logging.info(f"✓ 成功连接到服务器: {server_ip}:{server_port}")
                    return True
            except Exception as e:
                logging.debug(f"连接 {server_ip}:{server_port} 失败: {e}")
                continue
        
        logging.error("❌ 无法连接到任何服务器")
        return False
    
    def disconnect(self):
        """断开连接"""
        if self.api:
            try:
                self.api.disconnect()
                logging.debug("服务器连接已断开")
            except:
                pass
    
    def get_1m_data(self, stock_code, days=5, use_batch=False):
        """
        获取股票1分钟K线数据
        
        Parameters:
        stock_code: 股票代码，如 '000001' 或 '600000'
        days: 获取天数，默认5天
        use_batch: 是否使用分批模式（突破800根限制），默认False
        
        Returns:
        tuple: (DataFrame, end_date) - 数据和最后日期
        """
        try:
            # 判断市场
            market = get_market(stock_code)
            
            # 如果未连接，尝试连接
            if not self.api or not self.current_server:
                if not self.connect():
                    return pd.DataFrame(), None
            
            # 计算需要获取的K线数量（每天约240根1分钟线）
            bars_needed = days * 240
            
            # 如果不需要分批，且数据量<=800，直接获取
            if not use_batch and bars_needed <= 800:
                return self._get_1m_data_single(stock_code, market, bars_needed)
            
            # 数据量超过800或明确要求分批，使用分批模式
            elif bars_needed > 800 or use_batch:
                logging.info(f"数据量 {bars_needed} 根超过800，使用分批模式")
                return self._get_1m_data_batch(stock_code, market, bars_needed)
            
            # 默认单次获取
            else:
                return self._get_1m_data_single(stock_code, market, bars_needed)
            
        except Exception as e:
            logging.error(f"获取 {stock_code} 数据失败: {e}")
            return pd.DataFrame(), None
    
    def _get_1m_data_single(self, stock_code, market, count):
        """单次获取数据（最多800根）"""
        count = min(count, 800)
        
        logging.info(f"正在获取 {stock_code} 的数据（单次，{count} 根）...")
        
        # 获取1分钟线数据
        data = self.api.get_security_bars(
            8,  # 8表示1分钟线
            market,
            stock_code,
            0,  # 从第0根开始
            count
        )
        
        if not data:
            logging.warning(f"股票 {stock_code} 返回空数据")
            return pd.DataFrame(), None
        
        # 转换为DataFrame
        df = self._process_dataframe(data)
        
        # 获取最后日期
        end_date = df['date'].max().date() if not df.empty else None
        
        logging.info(f"✓ 成功获取 {stock_code} 的 {len(df)} 条记录")
        
        return df, end_date
    
    def _get_1m_data_batch(self, stock_code, market, total_bars):
        """
        分批获取数据（突破800根限制）
        
        Parameters:
        total_bars: 总共需要的K线数量
        """
        all_data = []
        batch_size = 800
        batches_needed = (total_bars + batch_size - 1) // batch_size
        
        logging.info(f"分批获取 {stock_code}: 总计 {total_bars} 根，分 {batches_needed} 批")
        
        for i in range(batches_needed):
            start_pos = i * batch_size
            count = min(batch_size, total_bars - start_pos)
            
            logging.debug(f"批次 {i+1}/{batches_needed}: 位置 {start_pos}, 数量 {count}")
            
            data = self.api.get_security_bars(
                8,  # 1分钟线
                market,
                stock_code,
                start_pos,
                count
            )
            
            if data:
                all_data.extend(data)
                logging.debug(f"  ✓ 批次 {i+1} 获取 {len(data)} 条")
            else:
                logging.warning(f"  ✗ 批次 {i+1} 返回空数据")
                break
            
            # 添加小延迟避免请求过快
            if i < batches_needed - 1:
                import time
                time.sleep(0.1)
        
        if not all_data:
            logging.warning(f"股票 {stock_code} 返回空数据")
            return pd.DataFrame(), None
        
        # 转换为DataFrame
        df = self._process_dataframe(all_data)
        
        # 获取最后日期
        end_date = df['date'].max().date() if not df.empty else None
        
        logging.info(f"✓ 分批成功获取 {stock_code} 的 {len(df)} 条记录")
        
        return df, end_date
    
    def _process_dataframe(self, data):
        """处理原始数据为标准DataFrame"""
        df = pd.DataFrame(data)
        
        if df.empty:
            return df
        
        # 重命名列，匹配东方财富的格式
        df = df.rename(columns={
            'datetime': 'date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'vol': 'volume',
            'amount': 'money'
        })
        
        # 转换日期格式
        df['date'] = pd.to_datetime(df['date'])
        
        # 确保包含所有必需的列
        if 'money' not in df.columns:
            df['money'] = 0
        
        # 去重并排序
        df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
        
        return df
    
    def __enter__(self):
        """上下文管理器：进入"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器：退出"""
        self.disconnect()


def download_stock_1m_pytdx(stock_code, days=5):
    """
    便捷函数：使用 pytdx 下载股票1分钟数据
    
    Parameters:
    stock_code: 股票代码
    days: 获取天数
    
    Returns:
    tuple: (DataFrame, end_date)
    """
    with PytdxDownloader() as downloader:
        return downloader.get_1m_data(stock_code, days)


# 测试代码
if __name__ == "__main__":
    print("=" * 80)
    print("pytdx 数据下载测试")
    print("=" * 80)
    print()
    
    # 测试1: 单次获取（≤800根）
    print("测试 1: 平安银行 (000001) - 单次获取 2天数据")
    print("-" * 80)
    with PytdxDownloader() as downloader:
        df, end_date = downloader.get_1m_data('000001', days=2)
        if not df.empty:
            print(f"✓ 成功获取 {len(df)} 条数据")
            print(f"最后日期: {end_date}")
            print(f"时间跨度: {(df['date'].max() - df['date'].min()).days} 天")
            print(f"数据列: {df.columns.tolist()}")
            print(df.head(3))
        else:
            print("✗ 获取失败")
    
    print()
    print("=" * 80)
    
    # 测试2: 分批获取（>800根）
    print("测试 2: 贵州茅台 (600519) - 分批获取 10天数据")
    print("-" * 80)
    with PytdxDownloader() as downloader:
        df2, end_date2 = downloader.get_1m_data('600519', days=10, use_batch=True)
        if not df2.empty:
            print(f"✓ 成功获取 {len(df2)} 条数据")
            print(f"最后日期: {end_date2}")
            print(f"时间跨度: {(df2['date'].max() - df2['date'].min()).days} 天")
            print("\n最近5条:")
            print(df2.tail())
        else:
            print("✗ 获取失败")
    
    print()
    print("=" * 80)
    
    # 测试3: 使用便捷函数
    print("测试 3: 使用便捷函数 (1天数据)")
    print("-" * 80)
    df3, end_date3 = download_stock_1m_pytdx('000001', days=1)
    if not df3.empty:
        print(f"✓ 成功获取 {len(df3)} 条数据")
        print(f"最后日期: {end_date3}")
    else:
        print("✗ 获取失败")
    
    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)
    print()
    print("💡 说明:")
    print("  - pytdx 单次最多 800 根K线（约3天）")
    print("  - days <= 3: 自动单次获取")
    print("  - days > 3: 自动分批获取")
    print("  - 可通过 use_batch=True 强制分批模式")

