# -*- coding: utf-8 -*-
"""
个股完整 3 步下载服务

镜像 /download_minute_data_page 的盘后下载流水线，但是同步、可调用、无线程/全局状态：
    Step 1  日 K   → data_stock_daily（MySQL）           pytdx → AKShare 回退
    Step 2  1 分钟 → data/quarters/<year>/<quarter>/<code>.parquet
    Step 3  15 分钟→ data/15m/<code>.parquet            （viewer_15m_route 也是读这里）

主要供 /stock_trend 详情页的"📥 下载数据并纳入"按钮使用，
让 15m 图表 / 个股打分 都能直接命中标准路径上的数据。
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)


def download_stock_full(stock_code: str, days: int = 240) -> Dict[str, Any]:
    """完整 3 步下载（个股；板块 BK* 不支持，请走板块流程）。

    Args:
        stock_code: 6 位股票代码
        days: 1m 下载天数（日 K 取 days+30，再视已有数据做扩展）

    Returns:
        dict 含字段：
            success: bool
            message: 拼出的简短摘要
            steps:   {daily, m1, m15}  每一步的人话状态
    """
    from App.codes.RnnDataFile.save_download import (
        save_1m_to_csv, get_quarter_from_month,
    )
    from App.codes.utils.Normal import ResampleData
    from App.codes.downloads.DlStockData import download_1m_by_type, StockType
    from App.utils.file_utils import get_stock_data_path
    from App.models.data.StockDaily import save_daily_stock_data_to_sql

    code = (stock_code or '').strip()
    if not code:
        return {'success': False, 'message': 'stock_code 不能为空',
                'steps': {'daily': '-', 'm1': '-', 'm15': '-'}}
    if code.upper().startswith('BK'):
        return {'success': False, 'message': '该流程仅支持个股，板块请走板块流程',
                'steps': {'daily': '-', 'm1': '-', 'm15': '-'}}

    steps: Dict[str, str] = {'daily': '-', 'm1': '-', 'm15': '-'}

    # ---------------- STEP 1: 日 K ----------------
    daily_df = pd.DataFrame()
    daily_days = max(days + 30, 60)

    try:
        from App.codes.downloads.DlPytdx import download_daily_pytdx
        daily_df, _ = download_daily_pytdx(code, days=daily_days)
        if daily_df is not None and not daily_df.empty:
            logger.info(f'[STEP1] {code} pytdx 日 K 成功，{len(daily_df)} 条')
    except ImportError:
        logger.info('[STEP1] pytdx 未安装，跳过')
    except Exception as e:
        logger.warning(f'[STEP1] {code} pytdx 失败: {e}')

    if daily_df is None or daily_df.empty:
        try:
            from App.codes.downloads.DlAkshare import AkshareDownloader
            ak = AkshareDownloader()
            if getattr(ak, 'akshare_available', False):
                daily_df, _ = ak.get_daily_data(code, days=daily_days)
                if daily_df is not None and not daily_df.empty:
                    logger.info(f'[STEP1] {code} AKShare 日 K 成功，{len(daily_df)} 条')
        except Exception as e:
            logger.warning(f'[STEP1] {code} AKShare 失败: {e}')

    if daily_df is not None and not daily_df.empty:
        try:
            save_daily_stock_data_to_sql(code, daily_df)
            steps['daily'] = f'{len(daily_df)} 行入库 data_stock_daily'
        except Exception as e:
            steps['daily'] = f'入库失败: {e}'
            logger.error(f'[STEP1] {code} 入库失败: {e}')
    else:
        steps['daily'] = '所有数据源均无日 K'

    # ---------------- STEP 2: 1 分钟 ----------------
    try:
        data_1m, _end = download_1m_by_type(code, days, StockType.STOCK_1M)
    except Exception as e:
        logger.exception(f'[STEP2] {code} 1m 下载异常')
        steps['m1'] = f'1m 下载异常: {e}'
        return {
            'success': False,
            'message': f'1m 下载异常：{code} | {e}',
            'steps': steps,
        }

    if data_1m is None or data_1m.empty:
        steps['m1'] = '1m 下载失败（数据源无返回）'
        return {
            'success': False,
            'message': f'1m 下载失败：{code}（日 K：{steps["daily"]} ｜ 1m：失败）',
            'steps': steps,
        }

    try:
        save_1m_to_csv(data_1m, code)
        steps['m1'] = f'{len(data_1m)} 行 1m 已写入季度 Parquet'
    except Exception as e:
        steps['m1'] = f'1m 保存失败: {e}'
        logger.error(f'[STEP2] {code} 保存 1m 失败: {e}')

    # ---------------- STEP 3: 15 分钟 ----------------
    try:
        data_1m = data_1m.copy()
        data_1m['date'] = pd.to_datetime(data_1m['date'])
        last_date = data_1m['date'].max()
        cur_year = str(last_date.year)
        cur_quarter = get_quarter_from_month(last_date.month)

        # 把上一季度的 1m parquet 也合并进来，让 15m 序列连续（够算 MA60）
        q_num = int(cur_quarter[1])
        if q_num == 1:
            prev_year, prev_quarter = str(int(cur_year) - 1), 'Q4'
        else:
            prev_year, prev_quarter = cur_year, f'Q{q_num - 1}'

        df_1m_full = data_1m
        prev_path = get_stock_data_path(
            code, data_type='1m', year=prev_year, quarter=prev_quarter, create=False)
        for candidate in [prev_path, prev_path.replace('.parquet', '.csv')]:
            if os.path.exists(candidate):
                try:
                    if candidate.endswith('.parquet'):
                        df_prev = pd.read_parquet(candidate)
                    else:
                        df_prev = pd.read_csv(candidate)
                    df_prev['date'] = pd.to_datetime(df_prev['date'])
                    df_1m_full = (pd.concat([df_prev, df_1m_full], ignore_index=True)
                                  .drop_duplicates(subset=['date'], keep='last')
                                  .sort_values('date').reset_index(drop=True))
                except Exception as e:
                    logger.warning(f'[STEP3] {code} 读上一季度 1m 失败 {candidate}: {e}')
                break

        data_15m = ResampleData.resample_1m_data(df_1m_full, '15m')
        if data_15m is None or data_15m.empty:
            steps['m15'] = '15m 重采样结果为空'
        else:
            file_path_15m = get_stock_data_path(
                code, data_type='15m_normal', create=True)
            # 与已有 15m 合并去重
            if os.path.exists(file_path_15m):
                try:
                    existing = pd.read_parquet(file_path_15m)
                    existing['date'] = pd.to_datetime(existing['date'])
                    data_15m['date'] = pd.to_datetime(data_15m['date'])
                    data_15m = (pd.concat([existing, data_15m], ignore_index=True)
                                .drop_duplicates(subset=['date'], keep='last')
                                .sort_values('date').reset_index(drop=True))
                except Exception as e:
                    logger.warning(f'[STEP3] {code} 读已有 15m parquet 失败: {e}')

            try:
                data_15m.to_parquet(file_path_15m, index=False, engine='pyarrow')
                steps['m15'] = f'{len(data_15m)} 根 15m 已写入 {os.path.basename(file_path_15m)}'
            except ImportError:
                csv_path = file_path_15m.replace('.parquet', '.csv')
                data_15m.to_csv(csv_path, index=False)
                steps['m15'] = f'{len(data_15m)} 根 15m 已写入 CSV（pyarrow 不可用）'
    except Exception as e:
        logger.exception(f'[STEP3] {code} 15m 处理异常')
        steps['m15'] = f'15m 处理失败: {e}'

    return {
        'success': True,
        'message': (f'完成 {code} ｜ 日 K: {steps["daily"]} ｜ '
                    f'1m: {steps["m1"]} ｜ 15m: {steps["m15"]}'),
        'steps': steps,
    }
