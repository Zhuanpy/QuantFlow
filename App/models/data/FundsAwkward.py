"""
基金重仓数据模型
提供保存基金重仓数据到本地CSV文件的功能
"""
import pandas as pd
import os
import logging
from datetime import date

logger = logging.getLogger(__name__)


def get_funds_data_directory():
    """
    获取基金数据保存目录
    
    Returns:
        str: 基金数据保存目录路径
    """
    # 在项目根目录下创建 data/funds_holdings 目录
    # 使用更简单的方法：从当前工作目录开始
    import os
    current_dir = os.getcwd()
    funds_dir = os.path.join(current_dir, 'data', 'funds_holdings')
    
    # 确保目录存在
    os.makedirs(funds_dir, exist_ok=True)
    
    return funds_dir


def save_funds_holdings_to_csv(df: pd.DataFrame, download_date: date = None) -> bool:
    """
    将基金数据保存到本地CSV文件。
    
    Args:
        df: pandas.DataFrame 数据框，包含需要保存的数据
        download_date: 下载日期，如果为None则使用当前日期
        
    Returns:
        bool: 保存是否成功
    """
    try:
        if download_date is None:
            download_date = date.today()
        
        # 生成文件名
        date_str = download_date.strftime('%Y%m%d')
        filename = f"funds_holdings_{date_str}.csv"
        
        # 获取保存目录
        save_dir = get_funds_data_directory()
        file_path = os.path.join(save_dir, filename)
        
        # 保存到CSV文件
        df.to_csv(file_path, index=False, encoding='utf-8-sig')
        
        logger.info(f"成功保存基金重仓数据到文件: {file_path}，共 {len(df)} 条记录")
        return True

    except Exception as e:
        logger.error(f"保存基金重仓数据到CSV文件时发生错误: {e}")
        return False


def get_funds_holdings_from_csv(download_date: date = None, limit_funds: int = None) -> pd.DataFrame:
    """
    从本地CSV文件中获取基金重仓数据。
    
    Args:
        download_date: 下载日期，如果为None则使用当前日期
        limit_funds: 限制基金数量，如果为None则返回所有基金数据
        
    Returns:
        pd.DataFrame: 基金重仓数据
    """
    try:
        if download_date is None:
            download_date = date.today()
        
        # 生成文件名
        date_str = download_date.strftime('%Y%m%d')
        filename = f"funds_holdings_{date_str}.csv"
        
        # 获取文件路径
        save_dir = get_funds_data_directory()
        file_path = os.path.join(save_dir, filename)
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return pd.DataFrame()
        
        # 读取CSV文件
        df = pd.read_csv(file_path, encoding='utf-8-sig')
        
        # 如果指定了基金数量限制，则只取前N只基金的数据
        if limit_funds is not None and not df.empty:
            # 获取前N只基金的代码
            top_funds = df['fund_code'].value_counts().head(limit_funds).index.tolist()
            # 筛选这些基金的数据
            df = df[df['fund_code'].isin(top_funds)]
            logger.info(f"限制为前 {limit_funds} 只基金，实际获取 {len(top_funds)} 只基金的数据")
        
        logger.info(f"成功从文件 {file_path} 获取基金重仓数据，共 {len(df)} 条记录")
        return df
        
    except Exception as e:
        logger.error(f"从CSV文件获取基金重仓数据时发生错误: {e}")
        return pd.DataFrame()


def get_funds_holdings_by_stock(download_date: date = None, stock_code: str = None) -> pd.DataFrame:
    """
    从本地CSV文件中获取特定股票的基金重仓数据。
    
    Args:
        download_date: 下载日期，如果为None则使用当前日期
        stock_code: 股票代码
        
    Returns:
        pd.DataFrame: 特定股票的基金重仓数据
    """
    try:
        df = get_funds_holdings_from_csv(download_date)
        
        if df.empty or stock_code is None:
            return df
        
        # 筛选特定股票的数据
        result_df = df[df['stock_code'] == stock_code]
        
        logger.info(f"成功获取股票 {stock_code} 的基金重仓数据，共 {len(result_df)} 条记录")
        return result_df
        
    except Exception as e:
        logger.error(f"获取股票 {stock_code} 的基金重仓数据时发生错误: {e}")
        return pd.DataFrame()


def get_funds_holdings_by_fund(download_date: date = None, fund_code: str = None) -> pd.DataFrame:
    """
    从本地CSV文件中获取特定基金的持仓数据。
    
    Args:
        download_date: 下载日期，如果为None则使用当前日期
        fund_code: 基金代码
        
    Returns:
        pd.DataFrame: 特定基金的持仓数据
    """
    try:
        df = get_funds_holdings_from_csv(download_date)
        
        if df.empty or fund_code is None:
            return df
        
        # 筛选特定基金的数据
        result_df = df[df['fund_code'] == fund_code]
        
        logger.info(f"成功获取基金 {fund_code} 的持仓数据，共 {len(result_df)} 条记录")
        return result_df
        
    except Exception as e:
        logger.error(f"获取基金 {fund_code} 的持仓数据时发生错误: {e}")
        return pd.DataFrame()


def list_available_dates():
    """
    列出可用的数据日期
    
    Returns:
        list: 可用的日期列表（字符串格式：YYYYMMDD）
    """
    try:
        # 获取保存目录
        save_dir = get_funds_data_directory()
        
        if not os.path.exists(save_dir):
            logger.warning(f"基金数据目录不存在: {save_dir}")
            return []
        
        # 获取所有CSV文件
        csv_files = [f for f in os.listdir(save_dir) if f.startswith('funds_holdings_') and f.endswith('.csv')]
        
        # 提取日期
        dates = []
        for file in csv_files:
            try:
                # 从文件名提取日期：funds_holdings_20251019.csv
                date_str = file.replace('funds_holdings_', '').replace('.csv', '')
                dates.append(date_str)
            except Exception as e:
                logger.warning(f"解析文件名失败: {file}, 错误: {e}")
                continue
        
        # 按日期排序（降序）
        dates.sort(reverse=True)
        
        logger.info(f"找到 {len(dates)} 个可用日期")
        return dates
        
    except Exception as e:
        logger.error(f"获取可用日期失败: {e}")
        return []




def get_latest_data() -> pd.DataFrame:
    """
    获取最新的基金重仓数据
    
    Returns:
        pd.DataFrame: 最新的基金重仓数据
    """
    try:
        available_dates = list_available_dates()
        
        if not available_dates:
            logger.warning("没有找到可用的数据文件")
            return pd.DataFrame()
        
        # 获取最新日期的数据
        latest_date = available_dates[0]
        return get_funds_holdings_from_csv(latest_date)
        
    except Exception as e:
        logger.error(f"获取最新数据时发生错误: {e}")
        return pd.DataFrame()
