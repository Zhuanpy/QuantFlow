# -*- coding: utf-8 -*-
"""
数据保存模块
提供将下载的数据保存到CSV、数据库等功能
"""
import os
import pandas as pd
import logging
from datetime import datetime
from typing import Dict, List, Optional
from config import Config

logger = logging.getLogger(__name__)


def get_quarter_from_month(month: int) -> str:
    """根据月份获取季度"""
    if month <= 3:
        return 'Q1'
    elif month <= 6:
        return 'Q2'
    elif month <= 9:
        return 'Q3'
    else:
        return 'Q4'


def save_1m_to_csv(data: pd.DataFrame, stock_code: str) -> bool:
    """
    将1分钟数据保存到Parquet文件（追加合并模式）

    根据数据中最后一条记录的时间来确定保存的年份和季度，
    而不是使用当前日期。如果文件已存在，会与现有数据合并，
    去重后保存。

    Args:
        data: 1分钟数据DataFrame
        stock_code: 股票代码

    Returns:
        bool: 保存是否成功
    """
    try:
        from App.utils.file_utils import get_stock_data_path

        if data.empty:
            logger.warning(f"数据为空，跳过保存: {stock_code}")
            return False

        # 确保 date 列是 datetime 类型
        data = data.copy()
        data['date'] = pd.to_datetime(data['date'])

        # 从数据中提取年份和季度（基于数据的最后一条记录）
        last_date = data['date'].max()
        year = str(last_date.year)
        quarter = get_quarter_from_month(last_date.month)
        logger.info(f"根据数据时间确定保存路径: {stock_code} -> {year}/{quarter}")

        file_path = get_stock_data_path(stock_code, data_type='1m', year=year, quarter=quarter, create=True)

        # 兼容：如果旧CSV文件存在，读取旧数据后删除
        csv_path = file_path.replace('.parquet', '.csv')
        if os.path.exists(csv_path) and not os.path.exists(file_path):
            try:
                existing_data = pd.read_csv(csv_path, encoding='utf-8-sig')
                existing_data['date'] = pd.to_datetime(existing_data['date'])
                data = pd.concat([existing_data, data], ignore_index=True)
                data = data.drop_duplicates(subset=['date'], keep='last')
                data = data.sort_values('date').reset_index(drop=True)
                logger.info(f"从旧CSV迁移数据: {stock_code}, 合计 {len(data)} 条")
            except Exception as e:
                logger.warning(f"读取旧CSV文件失败: {stock_code}, 错误: {e}")

        # 检查parquet文件是否存在，如果存在则合并数据
        if os.path.exists(file_path):
            try:
                existing_data = pd.read_parquet(file_path)
                existing_data['date'] = pd.to_datetime(existing_data['date'])

                original_count = len(existing_data)

                # 合并数据
                merged_data = pd.concat([existing_data, data], ignore_index=True)

                # 去重（基于 date 列）并排序
                merged_data = merged_data.drop_duplicates(subset=['date'], keep='last')
                merged_data = merged_data.sort_values('date').reset_index(drop=True)

                new_count = len(merged_data) - original_count
                logger.info(f"合并数据: {stock_code}, 原有 {original_count} 条, 新增 {new_count} 条, 合计 {len(merged_data)} 条")

                data = merged_data
            except Exception as e:
                logger.warning(f"读取现有文件失败，将覆盖保存: {stock_code}, 错误: {e}")

        data.to_parquet(file_path, index=False, engine='pyarrow')
        logger.info(f"成功保存1分钟数据: {stock_code} -> {file_path}")
        return True
    except Exception as e:
        logger.error(f"保存1分钟数据失败: {stock_code}, 错误: {e}")
        return False


def save_1m_to_daily(data: pd.DataFrame, stock_code: str) -> bool:
    """
    将1分钟数据转换为日线数据并保存（追加合并模式）

    根据数据中最后一条记录的时间来确定保存的年份和季度。
    如果文件已存在，会与现有数据合并，去重后保存。

    Args:
        data: 1分钟数据DataFrame
        stock_code: 股票代码

    Returns:
        bool: 保存是否成功
    """
    try:
        from App.codes.utils.Normal import ResampleData
        from App.utils.file_utils import get_stock_data_path

        if data.empty:
            logger.warning(f"数据为空，跳过保存日线数据: {stock_code}")
            return False

        # 转换为日线数据
        daily_data = ResampleData.resample_1m_data(data, freq='daily')

        if daily_data.empty:
            logger.warning(f"转换后日线数据为空: {stock_code}")
            return False

        # 确保 date 列是 datetime 类型
        daily_data = daily_data.copy()
        daily_data['date'] = pd.to_datetime(daily_data['date'])

        # 从数据中提取年份和季度
        last_date = pd.to_datetime(data['date']).max()
        year = str(last_date.year)
        quarter = get_quarter_from_month(last_date.month)

        # 保存到文件
        file_path = get_stock_data_path(stock_code, data_type='daily', year=year, quarter=quarter, create=True)

        # 检查文件是否存在，如果存在则合并数据
        if os.path.exists(file_path):
            try:
                existing_data = pd.read_csv(file_path, encoding='utf-8-sig')
                existing_data['date'] = pd.to_datetime(existing_data['date'])

                original_count = len(existing_data)

                # 合并数据
                merged_data = pd.concat([existing_data, daily_data], ignore_index=True)

                # 去重（基于 date 列）并排序
                merged_data = merged_data.drop_duplicates(subset=['date'], keep='last')
                merged_data = merged_data.sort_values('date').reset_index(drop=True)

                new_count = len(merged_data) - original_count
                logger.info(f"合并日线数据: {stock_code}, 原有 {original_count} 条, 新增 {new_count} 条, 合计 {len(merged_data)} 条")

                daily_data = merged_data
            except Exception as e:
                logger.warning(f"读取现有日线文件失败，将覆盖保存: {stock_code}, 错误: {e}")

        daily_data.to_csv(file_path, index=False, encoding='utf-8-sig')

        logger.info(f"成功保存日线数据: {stock_code} -> {file_path}")
        return True
    except Exception as e:
        logger.error(f"保存日线数据失败: {stock_code}, 错误: {e}")
        return False


def save_1m_to_mysql(stock_code: str, year: str, data: pd.DataFrame) -> bool:
    """已废弃：1m 数据已统一落本地 parquet（save_1m_to_csv 在同一处调用）。

    历史上写入 per-year MySQL（bind 'data1m{year}'），但这些 bind 从未配置进
    SQLALCHEMY_BINDS，每次都失败再被 except 吞掉，纯粹刷日志。
    保留函数签名以免破坏旧调用方，但内部已 no-op。
    """
    logger.debug(f"save_1m_to_mysql({stock_code}, {year}) skipped — 已统一到本地 parquet")
    return True


def complete_download_process(stock_code: str, days: int = 1, update_record: bool = True) -> Dict[str, any]:
    """
    完整的下载流程：下载1分钟数据，转换为15分钟和日线数据，并保存

    Args:
        stock_code: 股票代码或板块代码（BK开头）
        days: 下载天数
        update_record: 是否更新记录

    Returns:
        dict: 包含success和message的字典
    """
    try:
        from App.codes.downloads.DlEastMoney import DownloadData
        from App.codes.utils.Normal import ResampleData
        from App.utils.file_utils import get_stock_data_path

        # 判断是股票还是板块
        is_board = stock_code.upper().startswith('BK')
        code_type = "板块" if is_board else "股票"

        logger.info(f"开始完整下载流程: {code_type} {stock_code}, 天数: {days}")

        # 根据类型选择下载方法
        if is_board:
            data_1m = DownloadData.board_1m_multiple(stock_code, days)
        else:
            data_1m = DownloadData.stock_1m_days(stock_code, days)
        
        if data_1m.empty:
            return {
                'success': False,
                'message': f'下载失败: 未获取到{code_type} {stock_code} 的数据'
            }
        
        # 提取数据中的交易日期（用于标记任务状态）
        data_1m['date'] = pd.to_datetime(data_1m['date'])
        trade_dates = data_1m['date'].dt.date.unique()

        # 保存1分钟数据
        save_1m_to_csv(data_1m, stock_code)

        # 标记1分钟数据下载完成
        _mark_task_status(stock_code, trade_dates, 'is_1m_downloaded')

        # 转换为15分钟数据并保存为Parquet
        try:
            data_15m = ResampleData.resample_1m_data(data_1m, freq='15m')
            file_path_15m = get_stock_data_path(stock_code, data_type='15m', create=True)

            # 如果已有15m文件，追加合并
            if os.path.exists(file_path_15m):
                try:
                    existing_15m = pd.read_parquet(file_path_15m)
                    existing_15m['date'] = pd.to_datetime(existing_15m['date'])
                    data_15m['date'] = pd.to_datetime(data_15m['date'])
                    data_15m = pd.concat([existing_15m, data_15m], ignore_index=True)
                    data_15m = data_15m.drop_duplicates(subset=['date'], keep='last')
                    data_15m = data_15m.sort_values('date').reset_index(drop=True)
                except Exception:
                    pass

            data_15m.to_parquet(file_path_15m, index=False, engine='pyarrow')
            logger.info(f"成功保存15分钟数据: {stock_code}")

            # 标记15分钟数据生成完成
            _mark_task_status(stock_code, trade_dates, 'is_15m_generated')
        except Exception as e:
            logger.warning(f"保存15分钟数据失败: {stock_code}, 错误: {e}")

        # 转换为日线数据并保存为 CSV
        save_1m_to_daily(data_1m, stock_code)

        # 同步把日线数据写入 MySQL（data_stock_daily），图表/打分都从这张表读
        if not is_board:
            try:
                from App.models.data.StockDaily import save_daily_stock_data_to_sql
                daily_df = ResampleData.resample_1m_data(data_1m, freq='daily')
                if daily_df is not None and not daily_df.empty:
                    save_daily_stock_data_to_sql(stock_code, daily_df)
                    logger.info(f"成功保存日线数据到 MySQL: {stock_code}, {len(daily_df)} 行")
            except Exception as e:
                logger.warning(f"保存日线到 data_stock_daily 失败: {stock_code}, 错误: {e}")

        # 保存到数据库
        try:
            # 使用数据的时间而不是当前时间
            if not data_1m.empty and 'date' in data_1m.columns:
                last_date = pd.to_datetime(data_1m['date']).max()
                year = str(last_date.year)
            else:
                year = datetime.now().strftime('%Y')
            save_1m_to_mysql(stock_code, year, data_1m)
        except Exception as e:
            logger.warning(f"保存到数据库失败: {stock_code}, 错误: {e}")

        return {
            'success': True,
            'message': f'成功完成{code_type} {stock_code} 的完整下载流程，共 {len(data_1m)} 条记录'
        }

    except Exception as e:
        logger.error(f"完整下载流程失败: {stock_code}, 错误: {e}")
        return {
            'success': False,
            'message': f'下载失败: {str(e)}'
        }


def batch_complete_download_process(stock_codes: List[str], days: int = 1) -> Dict[str, any]:
    """
    批量完整下载流程
    
    Args:
        stock_codes: 股票代码列表
        days: 下载天数
        
    Returns:
        dict: 包含success、message和results的字典
    """
    results = []
    success_count = 0
    fail_count = 0
    
    for stock_code in stock_codes:
        result = complete_download_process(stock_code, days, update_record=False)
        results.append({
            'stock_code': stock_code,
            'success': result['success'],
            'message': result['message']
        })
        
        if result['success']:
            success_count += 1
        else:
            fail_count += 1
    
    return {
        'success': fail_count == 0,
        'message': f'批量下载完成: 成功 {success_count} 个, 失败 {fail_count} 个',
        'results': results,
        'success_count': success_count,
        'fail_count': fail_count
    }


def _mark_task_status(stock_code: str, trade_dates, task_name: str):
    """
    标记每日任务状态（内部辅助函数）

    Args:
        stock_code: 股票代码
        trade_dates: 交易日期列表
        task_name: 任务字段名
    """
    try:
        from App.models.data.DailyTaskStatus import DailyTaskStatus
        for d in trade_dates:
            DailyTaskStatus.mark_task(stock_code, d, task_name)
        logger.info(f"已标记 {stock_code} 的 {len(trade_dates)} 个交易日 {task_name} 完成")
    except Exception as e:
        logger.warning(f"标记任务状态失败: {stock_code} {task_name}, 错误: {e}")

