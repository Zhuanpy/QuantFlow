from App.codes.downloads.DlStockData import RMDownloadData, StockType, download_1m_by_type
import threading
import os
from App.codes.utils.Normal import ResampleData
from flask import render_template, current_app, jsonify, Blueprint, copy_current_request_context, request, flash
from App.models.data.Stock1m import RecordStockMinute as dlr
from App.models.data.basic_info import StockCodes
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.exts import db
import pandas as pd
from datetime import date, datetime, timedelta
import logging
import time

from App.codes.RnnDataFile.save_download import save_1m_to_csv, complete_download_process, batch_complete_download_process

# 创建蓝图
download_data_bp = Blueprint('download_data_bp', __name__)

# 下载状态和进度的存储
download_status = "未开始"
download_progress = 0
download_thread = None
stop_download = False  # 用于控制下载停止
download_max_days = 5  # 最大下载天数，默认5天
download_force = False  # 是否强制重新下载（忽略当天已下载成功的记录）
download_lock = threading.Lock()  # 用于保护全局变量的锁

# 流水线状态跟踪
pipeline_current_stock = ""       # 当前处理的股票代码
pipeline_current_step = 0         # 当前步骤 0=空闲, 1=日K, 2=1分钟, 3=15分钟
pipeline_step_name = ""           # 当前步骤名称
pipeline_stock_index = 0          # 当前第几只股票
pipeline_total_stocks = 0         # 总股票数

# 股票代码缓存
stock_code_cache = {}


def get_stock_code_by_id(stock_code_id):
    """
    根据股票代码ID获取股票代码
    
    Args:
        stock_code_id: 股票代码ID
        
    Returns:
        str: 股票代码，如果未找到返回None
    """
    if stock_code_id in stock_code_cache:
        return stock_code_cache[stock_code_id]
    
    try:
        stock = StockCodes.query.get(stock_code_id)
        if stock:
            stock_code_cache[stock_code_id] = stock.code
            return stock.code
        else:
            logging.warning(f"未找到股票代码ID {stock_code_id} 对应的股票信息")
            return None
    except Exception as e:
        logging.error(f"获取股票代码时发生错误: {e}")
        return None


def download_file():
    # 声明使用全局变量，记录下载状态、进度、停止下载标志和最大下载天数
    global download_status, download_progress, stop_download, download_max_days, download_force
    global pipeline_current_stock, pipeline_current_step, pipeline_step_name
    global pipeline_stock_index, pipeline_total_stocks

    logging.info(f"download_file() 启动，download_max_days = {download_max_days}")

    # 使用下载锁，初始化下载状态和进度
    with download_lock:
        download_status = "进行中"  # 下载状态为进行中
        download_progress = 0  # 进度初始化为 0
        stop_download = False  # 重置停止下载的标志

    """启动下载任务"""
    today = date.today()  # 获取今天的日期
    current = datetime.now().date()  # 获取当前日期（不含时间部分）

    # 使用应用上下文以便于访问数据库和其他应用资源
    with current_app.app_context():
        
        # 重置失败的股票为待下载状态
        logging.info("开始重置失败的股票为待下载状态...")
        
        # 将所有失败的股票重置为pending状态
        failed_reset_count = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).update({
            'download_status': 'pending',
            'download_progress': 0.0,
            'error_message': None,  # 清除错误信息
            'updated_at': datetime.utcnow()
        })

        # 检查是否需要重置所有success记录
        # 如果record_date和今天不一样，说明数据不是最新的，需要重新下载
        # 或者用户勾选了"强制重新下载"
        latest_record = dlr.query.filter(
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).order_by(dlr.record_date.desc()).first()

        success_reset_count = 0
        need_reset = download_force or (latest_record and latest_record.record_date != today)

        if need_reset:
            if download_force:
                logging.info("用户选择强制重新下载，重置所有success记录")
            else:
                logging.info(f"检测到最新记录日期 {latest_record.record_date} 与今天 {today} 不同，重置所有success记录")

            # 将所有非忽略的success记录重置为pending
            success_reset_count = dlr.query.filter(
                dlr.download_status == 'success',
                dlr.end_date != date(2050, 1, 1),
                dlr.record_date != date(2050, 1, 1)
            ).update({
                'download_status': 'pending',
                'download_progress': 0.0,
                'updated_at': datetime.utcnow()
            })

            logging.info(f"重置了 {success_reset_count} 条success记录为pending状态")
        
        db.session.commit()
        logging.info(f"重置了 {failed_reset_count} 条失败记录为pending状态")

        # 计算符合条件的数据条数（需要下载且日期在今天之前）
        total_count = dlr.query.filter(
            dlr.download_status != 'success',  # 排除已下载成功的记录
            dlr.end_date <= today,  # 下载日期在今天或之前
            dlr.record_date <= today,  # 记录日期在今天或之前
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).count()
        
        if total_count == 0:
            logging.info("没有需要下载的数据。")  # 若无数据，记录日志
            with download_lock:
                download_status = "无数据下载"  # 更新状态为无数据
            return

        logging.info(f"开始下载任务，总共需要下载 {total_count} 个股票")

        # 获取所有需要下载的记录
        records_to_download = dlr.query.filter(
            dlr.download_status != 'success',
            dlr.end_date <= today,
            dlr.record_date <= today,
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).all()
        
        logging.info(f"获取到 {len(records_to_download)} 条需要下载的记录")

        # 遍历需要下载的数据记录
        for i, first_record in enumerate(records_to_download):
            logging.info(f"开始处理第 {i+1}/{len(records_to_download)} 个股票")

            # 更新流水线状态
            with download_lock:
                pipeline_stock_index = i + 1
                pipeline_total_stocks = len(records_to_download)

            # 检查是否需要停止下载
            with download_lock:
                if stop_download:
                    download_status = "已停止"  # 更新状态为已停止
                    download_progress = 0  # 进度清零
                    pipeline_current_step = 0
                    pipeline_step_name = ""
                    return  # 终止下载

            if not first_record:
                logging.info("记录为空，跳过。")  # 若无记录，记录日志
                continue

            # 获取股票代码
            stock_code = get_stock_code_by_id(first_record.stock_code_id)
            if not stock_code:
                logging.error(f'无法获取股票代码ID {first_record.stock_code_id} 对应的股票代码')
                # 标记为失败并继续下一个
                first_record.update_download_status(
                    status='failed',
                    error_msg=f'无法获取股票代码ID {first_record.stock_code_id} 对应的股票代码'
                )
                db.session.commit()

                # 更新进度
                with download_lock:
                    download_progress = int((i + 1) / len(records_to_download) * 100)
                continue

            # 判断代码类型：板块代码以 'BK' 开头
            is_board = stock_code.startswith('BK')
            stock_type = StockType.BOARD_1M if is_board else StockType.STOCK_1M
            code_type_name = "板块" if is_board else "股票"

            # 更新流水线当前股票
            with download_lock:
                pipeline_current_stock = stock_code

            logging.info(f"正在下载{code_type_name} {stock_code} 的数据...")

            # ===== STEP 1/3: 下载日K数据 =====
            with download_lock:
                pipeline_current_step = 1
                pipeline_step_name = "下载日K数据"
                download_status = f"进行中 - {stock_code} Step1/3 日K"

            if not is_board:
                try:
                    from App.codes.downloads.DlAkshare import AkshareDownloader
                    from App.models.data.StockDaily import StockDaily, save_daily_stock_data_to_sql
                    from App.models.data.DailyTaskStatus import DailyTaskStatus

                    ak_downloader = AkshareDownloader()
                    if ak_downloader.akshare_available:
                        daily_df, daily_end = ak_downloader.get_daily_data(stock_code, days=download_max_days + 30)

                        if not daily_df.empty:
                            save_daily_stock_data_to_sql(stock_code, daily_df)
                            # 标记今日的日K任务完成
                            DailyTaskStatus.mark_task(stock_code, today, 'is_daily_processed')
                            logging.info(f"[STEP1] {stock_code} 日K数据下载成功，{len(daily_df)} 条")
                        else:
                            logging.warning(f"[STEP1] {stock_code} 日K数据为空")
                    else:
                        logging.warning(f"[STEP1] AKShare不可用，跳过日K下载")
                except Exception as e:
                    logging.error(f"[STEP1] {stock_code} 日K数据下载失败: {e}")
                    # Step 1 失败不影响后续步骤
            else:
                logging.info(f"[STEP1] 板块 {stock_code} 跳过日K数据下载")

            # ===== STEP 2/3: 下载1分钟数据 =====
            with download_lock:
                pipeline_current_step = 2
                pipeline_step_name = "下载1分钟数据"
                download_status = f"进行中 - {stock_code} Step2/3 1分钟"

            # 直接使用用户设置的下载天数
            record_ending = first_record.end_date
            days = download_max_days  # 使用用户设置的天数

            logging.info(f"下载 {stock_code} 最近 {days} 天的数据...")
            
            # 更新下载状态为进行中
            first_record.update_download_status(status='processing')
            db.session.commit()

            # 重试机制：最多重试3次
            max_retries = 3
            retry_count = 0
            download_success = False
            
            logging.info(f"开始重试机制，最大重试次数: {max_retries}")
            
            while retry_count < max_retries and not download_success:
                # 构建URL用于调试（放在try外面，确保变量可用）
                try:
                    from App.codes.downloads.download_utils import UrlCode
                    from config import Config
                    # 根据代码类型选择不同的URL模板
                    # lmt 参数需要计算为 days * 240（每天约240条记录）
                    lmt = min(days * 240, 2000)
                    if is_board:
                        url_template = 'board_1m_multiple_days'
                        # 板块代码：URL模板是 format(lmt, code)
                        debug_url = Config.get_eastmoney_urls(url_template).format(lmt, stock_code)
                    else:
                        url_template = 'stock_1m_multiple_days'
                        # 股票代码：URL模板是 format(lmt, secid)
                        debug_url = Config.get_eastmoney_urls(url_template).format(lmt, UrlCode(stock_code))
                except Exception as url_error:
                    debug_url = f"构建URL失败: {url_error}"
                
                try:
                    if retry_count > 0:
                        # 使用指数退避策略：第1次重试等3秒，第2次等6秒，第3次等9秒
                        retry_delay = 3 * retry_count
                        logging.info(f"{code_type_name} {stock_code} 第 {retry_count + 1} 次重试下载，等待 {retry_delay} 秒...")
                        time.sleep(retry_delay)
                    else:
                        logging.info(f"开始下载{code_type_name} {stock_code} 的 {days} 天数据...")
                    
                    # 下载数据，根据代码类型和指定的天数
                    logging.info(f"尝试访问URL: {debug_url}")
                    
                    data, ending = download_1m_by_type(stock_code, days, stock_type)

                    if data.empty:
                        # 对于板块数据，如果API返回rc=100（数据不存在），可能是正常情况
                        # 不需要重试，直接标记为失败但继续处理下一个
                        if is_board:
                            logging.warning(f'板块 {stock_code} 数据不可用（可能是非交易时间或板块代码无效）')
                            # 标记为失败，但使用特殊的错误消息
                            first_record.update_download_status(
                                status='failed',
                                error_msg=f'板块数据不可用（API返回rc=100，可能是非交易时间或板块代码无效）\nURL: {debug_url}'
                            )
                            db.session.commit()
                            # 更新进度
                            with download_lock:
                                download_progress = int((i + 1) / len(records_to_download) * 100)
                            break  # 退出重试循环，继续下一个
                        
                        # 股票数据继续重试逻辑
                        retry_count += 1
                        logging.error(f"下载失败 - {code_type_name}: {stock_code}, URL: {debug_url}")
                        
                        if retry_count < max_retries:
                            logging.warning(f'{code_type_name} {stock_code} 第 {retry_count} 次下载数据为空，准备重试...')
                            continue
                        else:
                            logging.error(f'{code_type_name} {stock_code} 下载失败，已达到最大重试次数 {max_retries}')
                            # 更新状态为失败
                            first_record.update_download_status(
                                status='failed',
                                error_msg=f'下载失败，已重试{max_retries}次（网络连接问题或数据源限制）\nURL: {debug_url}'
                            )
                            db.session.commit()
                            # 更新进度
                            with download_lock:
                                download_progress = int((i + 1) / len(records_to_download) * 100)
                            break  # 退出重试循环
                    else:
                        download_success = True
                        logging.info(f"{code_type_name} {stock_code} 下载成功，获得 {len(data)} 条记录")
                
                except Exception as e:
                    retry_count += 1
                    logging.error(f"下载异常 - {code_type_name}: {stock_code}, URL: {debug_url}, 错误: {e}")
                    
                    if retry_count < max_retries:
                        logging.warning(f'{code_type_name} {stock_code} 第 {retry_count} 次下载异常: {e}，准备重试...')
                        continue
                    else:
                        logging.error(f'{code_type_name} {stock_code} 下载异常，已达到最大重试次数 {max_retries}: {e}')
                        # 更新状态为失败
                        first_record.update_download_status(
                            status='failed',
                            error_msg=f'下载异常，已重试{max_retries}次: {str(e)}\nURL: {debug_url}'
                        )
                        db.session.commit()
                        # 更新进度
                        with download_lock:
                            download_progress = int((i + 1) / len(records_to_download) * 100)
                        break  # 退出重试循环
            
            # 如果下载失败，跳过后续处理
            if not download_success:
                continue

            # 下载成功，继续处理数据
            try:
                # 保存数据至本地 CSV 文件（追加合并模式）
                save_1m_to_csv(data, stock_code)
                logging.info(f'成功保存 {stock_code} 数据到CSV文件，共 {len(data)} 条记录')

            except Exception as e:
                logging.error(f'保存至CSV失败: {stock_code}, {e}')
                # 标记为失败并继续下一个
                first_record.update_download_status(
                    status='failed',
                    error_msg=f'保存至CSV失败: {str(e)}'
                )
                db.session.commit()
                continue

            # 标记 DailyTaskStatus: 1分钟数据已下载
            try:
                from App.models.data.DailyTaskStatus import DailyTaskStatus
                data['date'] = pd.to_datetime(data['date'])
                trade_dates = data['date'].dt.date.unique()
                for d in trade_dates:
                    DailyTaskStatus.mark_task(stock_code, d, 'is_1m_downloaded')
                # 同时标记今天（确保 daily_viewer 能看到今日状态）
                if today not in trade_dates:
                    DailyTaskStatus.mark_task(stock_code, today, 'is_1m_downloaded')
                logging.info(f"[STEP2] 已标记 {stock_code} 的 {len(trade_dates)} 个交易日 is_1m_downloaded")
            except Exception as e:
                logging.warning(f"[STEP2] 标记1m任务状态失败: {stock_code}, {e}")

            # 更新数据库记录，标记下载成功
            first_record.update_download_status(
                status='success',
                progress=100.0
            )
            # 更新 end_date 为下载数据中的最新日期
            if ending > record_ending:
                first_record.end_date = ending
            first_record.record_date = current
            first_record.last_download_time = datetime.now()
            first_record.downloaded_records = len(data)
            db.session.commit()
            logging.info(f'[STEP2] 成功下载 {stock_code} 的1分钟数据（{days}天），共 {len(data)} 条记录')

            # ===== STEP 3/3: 处理15分钟数据（加载完整历史1m数据） =====
            with download_lock:
                pipeline_current_step = 3
                pipeline_step_name = "处理15分钟数据"
                download_status = f"进行中 - {stock_code} Step3/3 15分钟"

            try:
                from App.codes.utils.Normal import ResampleData
                from App.codes.Signals.StatisticsMacd import SignalMethod
                from App.utils.file_utils import get_stock_data_path
                from App.models.data.DailyTaskStatus import DailyTaskStatus
                from App.codes.RnnDataFile.save_download import get_quarter_from_month

                # === 加载完整历史1m数据（当前季度 + 上一季度） ===
                data['date'] = pd.to_datetime(data['date'])
                last_date = data['date'].max()
                cur_year = str(last_date.year)
                cur_quarter = get_quarter_from_month(last_date.month)

                # 计算上一季度
                q_num = int(cur_quarter[1])
                if q_num == 1:
                    prev_year, prev_quarter = str(int(cur_year) - 1), 'Q4'
                else:
                    prev_year, prev_quarter = cur_year, f'Q{q_num - 1}'

                # 读取当前季度完整1m文件
                cur_1m_path = get_stock_data_path(stock_code, data_type='1m', year=cur_year, quarter=cur_quarter, create=False)
                cur_1m_csv = cur_1m_path.replace('.parquet', '.csv')

                df_1m_full = None
                for p in [cur_1m_path, cur_1m_csv]:
                    if os.path.exists(p):
                        df_1m_full = pd.read_parquet(p) if p.endswith('.parquet') else pd.read_csv(p)
                        df_1m_full['date'] = pd.to_datetime(df_1m_full['date'])
                        break

                if df_1m_full is None:
                    df_1m_full = data.copy()

                # 尝试读取上一季度的1m数据（用于MACD历史计算）
                prev_1m_path = get_stock_data_path(stock_code, data_type='1m', year=prev_year, quarter=prev_quarter, create=False)
                prev_1m_csv = prev_1m_path.replace('.parquet', '.csv')
                prev_found = False
                for p in [prev_1m_path, prev_1m_csv]:
                    if os.path.exists(p):
                        df_1m_prev = pd.read_parquet(p) if p.endswith('.parquet') else pd.read_csv(p)
                        df_1m_prev['date'] = pd.to_datetime(df_1m_prev['date'])
                        df_1m_full = pd.concat([df_1m_prev, df_1m_full]).sort_values('date').reset_index(drop=True)
                        df_1m_full = df_1m_full.drop_duplicates(subset=['date'], keep='last')
                        prev_found = True
                        break
                # 记录当前季度起始日期（用于过滤）
                cur_quarter_start = data['date'].min()

                # === 重采样完整数据 1m -> 15m ===
                data_15m_full = ResampleData.resample_1m_data(df_1m_full, '15m')

                if not data_15m_full.empty:
                    # 计算 MACD 信号
                    macd_success = False
                    try:
                        data_15m_full = SignalMethod.signal_by_MACD_3ema(data_15m_full, df_1m_full)
                        macd_success = True
                    except Exception as macd_err:
                        logging.warning(f"[STEP3] {stock_code} MACD计算失败: {macd_err}")

                    # 只保留当前季度的数据
                    data_15m = data_15m_full[data_15m_full['date'] >= cur_quarter_start].copy()

                    # 清理旧列
                    if 'SignalTimes' in data_15m.columns:
                        data_15m = data_15m.drop(columns=['SignalTimes'])

                    # 保存15分钟数据（追加合并模式）
                    file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')

                    # 检查已有15m文件（parquet或csv）
                    existing_15m_path = file_path_15m
                    if file_path_15m.endswith('.parquet') and not os.path.exists(file_path_15m):
                        csv_fallback = file_path_15m.replace('.parquet', '.csv')
                        if os.path.exists(csv_fallback):
                            existing_15m_path = csv_fallback

                    if os.path.exists(existing_15m_path):
                        try:
                            if existing_15m_path.endswith('.parquet'):
                                try:
                                    existing_15m = pd.read_parquet(existing_15m_path)
                                except ImportError:
                                    existing_15m = None
                                    logging.warning(f"[STEP3] {stock_code} pyarrow不可用，跳过读取旧parquet")
                            else:
                                existing_15m = pd.read_csv(existing_15m_path, parse_dates=['date'])
                            if existing_15m is not None:
                                existing_15m['date'] = pd.to_datetime(existing_15m['date'])
                                if 'SignalTimes' in existing_15m.columns:
                                    existing_15m = existing_15m.drop(columns=['SignalTimes'])
                                data_15m['date'] = pd.to_datetime(data_15m['date'])
                                data_15m = pd.concat([existing_15m, data_15m], ignore_index=True)
                                data_15m = data_15m.drop_duplicates(subset=['date'], keep='last')
                                data_15m = data_15m.sort_values('date').reset_index(drop=True)
                        except Exception as merge_err:
                            logging.warning(f"[STEP3] {stock_code} 合并旧15m失败: {merge_err}")

                    # 保存文件（优先parquet，pyarrow不可用时回退CSV）
                    try:
                        if file_path_15m.endswith('.parquet'):
                            try:
                                data_15m.to_parquet(file_path_15m, index=False, engine='pyarrow')
                                logging.info(f"[STEP3] {stock_code} parquet保存成功: {len(data_15m)} 条")
                            except ImportError:
                                csv_path = file_path_15m.replace('.parquet', '.csv')
                                data_15m.to_csv(csv_path, index=False)
                                logging.info(f"[STEP3] {stock_code} pyarrow不可用，回退CSV保存: {len(data_15m)} 条")
                        else:
                            data_15m.to_csv(file_path_15m, index=False)
                            logging.info(f"[STEP3] {stock_code} CSV保存成功: {len(data_15m)} 条")
                    except Exception as save_err:
                        logging.error(f"[STEP3] {stock_code} 文件保存失败: {save_err}")

                    # 标记 DailyTaskStatus
                    try:
                        trade_dates = data['date'].dt.date.unique()
                        all_dates = list(trade_dates)
                        if today not in all_dates:
                            all_dates.append(today)
                        for d in all_dates:
                            DailyTaskStatus.mark_task(stock_code, d, 'is_15m_generated')
                            if macd_success:
                                DailyTaskStatus.mark_task(stock_code, d, 'is_macd_calculated')
                        logging.info(f"[STEP3] {stock_code} DailyTaskStatus标记成功 (15m=True, macd={macd_success})")
                    except Exception as mark_err:
                        logging.error(f"[STEP3] {stock_code} DailyTaskStatus标记失败: {mark_err}")

                    logging.info(f"[STEP3] {stock_code} 15分钟处理完成，{len(data_15m)} 条")
                else:
                    logging.warning(f"[STEP3] {stock_code} 15分钟重采样结果为空")

            except Exception as e:
                logging.error(f"[STEP3] {stock_code} 处理异常: {e}")
                # Step 3 失败不影响已保存的1m数据

            # 重置流水线步骤
            with download_lock:
                pipeline_current_step = 0
                pipeline_step_name = ""

            # 更新下载进度
            with download_lock:
                download_progress = round((i + 1) * (100 / len(records_to_download)), 1)

            # 添加延迟，避免触发反爬虫机制
            import random
            delay = random.uniform(2, 3.5)  # 每次下载后随机等待2-3.5秒
            logging.info(f"等待 {delay:.1f} 秒后继续下一个股票...")
            time.sleep(delay)

        # 下载任务完成，更新下载状态和进度
        with download_lock:
            download_status = "已完成"  # 状态设为已完成
            download_progress = 100  # 进度设为 100%
            pipeline_current_step = 0
            pipeline_step_name = ""
            pipeline_current_stock = ""

        logging.info("下载任务完成")


@download_data_bp.route('/start_download', methods=['GET', 'POST'])
def start_download():
    """启动下载任务"""
    global download_thread, download_status, download_progress, download_max_days, download_force
    global pipeline_current_stock, pipeline_current_step, pipeline_step_name
    global pipeline_stock_index, pipeline_total_stocks

    try:
        # 检查是否有正在运行的下载任务
        if download_thread is not None and download_thread.is_alive():
            logging.warning("下载任务已在运行中")
            return jsonify({
                "success": False,
                "message": "下载正在进行中",
                "status": download_status,
                "progress": download_progress
            }), 400

        # 获取用户设置的下载天数和是否强制重下
        if request.is_json:
            data = request.get_json()
            days = data.get('days', 5)
            force = data.get('force', False)
        else:
            days = request.form.get('days', 5, type=int)
            force = request.form.get('force', False, type=bool)

        # 限制天数范围为1-5天
        download_max_days = max(1, min(5, int(days)))
        download_force = force
        logging.info(f"用户设置下载天数为: {download_max_days}, 强制重下: {download_force}")

        # 重置状态
        with download_lock:
            download_status = "初始化中"
            download_progress = 0
            pipeline_current_stock = ""
            pipeline_current_step = 0
            pipeline_step_name = ""
            pipeline_stock_index = 0
            pipeline_total_stocks = 0
        
        @copy_current_request_context
        def run_download():
            """在后台线程中运行下载任务"""
            try:
                download_file()
            except Exception as e:
                logging.error(f"下载任务执行失败: {e}", exc_info=True)
                with download_lock:
                    download_status = f"下载失败: {str(e)}"
                    download_progress = 0

        # 启动下载线程
        download_thread = threading.Thread(target=run_download, daemon=True)
        download_thread.start()
        
        logging.info("下载任务已启动")
        return jsonify({
            "success": True,
            "message": "下载已开始",
            "status": download_status,
            "progress": download_progress
        }), 200
        
    except Exception as e:
        logging.error(f"启动下载任务失败: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"启动下载失败: {str(e)}"
        }), 500


@download_data_bp.route('/get_download_status', methods=['GET'])
def get_download_status():
    return jsonify({"status": download_status, "progress": download_progress}), 200


@download_data_bp.route('/get_today_task_status', methods=['GET'])
def get_today_task_status():
    """获取今日 DailyTaskStatus 各步骤完成统计"""
    try:
        from App.models.data.DailyTaskStatus import DailyTaskStatus
        today = date.today()

        T = DailyTaskStatus
        total = T.query.filter(T.date == today).count()

        if total == 0:
            return jsonify({
                'date': today.strftime('%Y-%m-%d'),
                'total': 0,
                'daily': 0, 'dl_1m': 0, 'gen_15m': 0, 'macd': 0,
                'cleaned': 0, 'volume': 0, 'feature': 0, 'rnn': 0,
            }), 200

        daily = T.query.filter(T.date == today, T.is_daily_processed == True).count()
        dl_1m = T.query.filter(T.date == today, T.is_1m_downloaded == True).count()
        gen_15m = T.query.filter(T.date == today, T.is_15m_generated == True).count()
        macd = T.query.filter(T.date == today, T.is_macd_calculated == True).count()
        cleaned = T.query.filter(T.date == today, T.is_1m_cleaned == True).count()
        volume = T.query.filter(T.date == today, T.is_volume_processed == True).count()
        feature = T.query.filter(T.date == today, T.is_feature_generated == True).count()
        rnn = T.query.filter(T.date == today, T.is_rnn_predicted == True).count()

        return jsonify({
            'date': today.strftime('%Y-%m-%d'),
            'total': total,
            'daily': daily,
            'dl_1m': dl_1m,
            'gen_15m': gen_15m,
            'macd': macd,
            'cleaned': cleaned,
            'volume': volume,
            'feature': feature,
            'rnn': rnn,
        }), 200

    except Exception as e:
        logging.error(f"获取今日任务状态失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/get_pipeline_status', methods=['GET'])
def get_pipeline_status():
    """获取流水线详细状态（含当前步骤信息）"""
    with download_lock:
        return jsonify({
            "status": download_status,
            "progress": download_progress,
            "current_stock": pipeline_current_stock,
            "current_step": pipeline_current_step,
            "step_name": pipeline_step_name,
            "stock_index": pipeline_stock_index,
            "total_stocks": pipeline_total_stocks,
        }), 200


@download_data_bp.route('/get_download_details', methods=['GET'])
def get_download_details():
    """获取详细的下载状态信息（最近下载的记录）"""
    try:
        # 注意：数据库中的时间戳使用UTC时间，所以这里也使用UTC时间进行比较
        utc_now = datetime.utcnow()

        # 获取最近的成功记录（最近1小时，用于实时显示）
        recent_success = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            dlr.last_download_time >= utc_now - timedelta(hours=1)
        ).order_by(dlr.last_download_time.desc()).limit(20).all()

        # 获取最近的失败记录
        recent_failed = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            dlr.updated_at >= utc_now - timedelta(hours=1)
        ).order_by(dlr.updated_at.desc()).limit(20).all()

        # 格式化成功记录
        success_list = []
        for record in recent_success:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                success_list.append({
                    'code': stock_code,
                    'records': record.downloaded_records or 0,
                    'time': record.last_download_time.strftime('%H:%M:%S') if record.last_download_time else '-'
                })

        # 格式化失败记录
        failed_list = []
        for record in recent_failed:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                failed_list.append({
                    'code': stock_code,
                    'error': record.error_message or '未知错误',
                    'time': record.updated_at.strftime('%H:%M:%S') if record.updated_at else '-'
                })

        return jsonify({
            "success": success_list,
            "failed": failed_list
        }), 200

    except Exception as e:
        logging.error(f"获取下载详情时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_success_stocks', methods=['GET'])
def get_success_stocks():
    """获取下载成功的股票列表（带分页）"""
    try:
        today = date.today()

        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = min(per_page, 100)

        # 查询所有下载成功的股票
        success_query = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).order_by(dlr.end_date.desc(), dlr.last_download_time.desc())

        total_count = success_query.count()
        success_records = success_query.offset((page - 1) * per_page).limit(per_page).all()

        success_list = []
        for record in success_records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                success_list.append({
                    'code': stock_code,
                    'records': record.downloaded_records or 0,
                    'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else '-',
                    'time': record.last_download_time.strftime('%Y-%m-%d %H:%M') if record.last_download_time else '-'
                })

        return jsonify({
            "success": success_list,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page
        }), 200

    except Exception as e:
        logging.error(f"获取成功下载股票列表时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_failed_stocks', methods=['GET'])
def get_failed_stocks():
    """获取下载失败的股票列表（带分页）"""
    try:
        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = min(per_page, 100)

        # 查询下载失败的股票
        failed_query = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).order_by(dlr.updated_at.desc())

        total_count = failed_query.count()
        failed_records = failed_query.offset((page - 1) * per_page).limit(per_page).all()

        failed_list = []
        for record in failed_records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                failed_list.append({
                    'code': stock_code,
                    'error': record.error_message or '未知错误',
                    'time': record.updated_at.strftime('%Y-%m-%d %H:%M') if record.updated_at else '-'
                })

        return jsonify({
            "failed": failed_list,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page
        }), 200

    except Exception as e:
        logging.error(f"获取失败下载股票列表时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_download_statistics', methods=['GET'])
def get_download_statistics():
    """获取下载统计数据"""
    try:
        today = date.today()
        
        # 统计各种状态的记录数量
        # 只统计需要下载的记录（排除被忽略的股票）
        pending_count = dlr.query.filter(
            dlr.download_status == 'pending',
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).count()
        
        success_count = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).count()
        
        failed_count = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).count()
        
        processing_count = dlr.query.filter(
            dlr.download_status == 'processing',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).count()
        
        # 计算总数（排除被忽略的股票）
        total_count = dlr.query.filter(
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).count()
        
        return jsonify({
            "pending": pending_count,
            "success": success_count,
            "failed": failed_count,
            "processing": processing_count,
            "total": total_count
        }), 200
        
    except Exception as e:
        logging.error(f"获取下载统计数据时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_pending_stocks', methods=['GET'])
def get_pending_stocks():
    """获取等待下载的股票列表（包括数据过期需要更新的）"""
    try:
        today = date.today()

        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        # 限制每页最大数量
        per_page = min(per_page, 100)

        # 查询需要下载的股票：
        # 1. 状态为 pending 的
        # 2. 或者 end_date 不是今天（数据已过期需要更新）
        from sqlalchemy import or_
        pending_query = dlr.query.filter(
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            or_(
                dlr.download_status == 'pending',
                dlr.download_status == 'failed',
                dlr.end_date < today  # 数据过期需要更新
            )
        ).order_by(dlr.end_date.asc())  # 按过期时间排序，最旧的排前面

        # 获取总数
        total_count = pending_query.count()

        # 分页获取数据
        pending_records = pending_query.offset((page - 1) * per_page).limit(per_page).all()

        # 格式化数据
        pending_list = []
        for record in pending_records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                days_behind = (today - record.end_date).days if record.end_date else 0
                # 确定状态描述
                if record.download_status == 'pending':
                    status_text = '等待下载'
                elif record.download_status == 'failed':
                    status_text = '下载失败'
                elif days_behind > 0:
                    status_text = '数据过期'
                else:
                    status_text = '待更新'

                pending_list.append({
                    'code': stock_code,
                    'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else '-',
                    'record_date': record.record_date.strftime('%Y-%m-%d') if record.record_date else '-',
                    'days_behind': days_behind,
                    'status': record.download_status,
                    'status_text': status_text
                })

        return jsonify({
            "pending": pending_list,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page
        }), 200

    except Exception as e:
        logging.error(f"获取等待下载股票列表时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/reset_failed_stocks', methods=['POST'])
def reset_failed_stocks():
    """重置失败的股票为待下载状态"""
    try:
        # 将所有失败的股票重置为pending状态
        # 注意：使用UTC时间以保持与模型定义一致
        reset_count = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).update({
            'download_status': 'pending',
            'download_progress': 0.0,
            'error_message': None,  # 清除错误信息
            'updated_at': datetime.utcnow()
        })
        
        db.session.commit()
        
        logging.info(f"重置了 {reset_count} 条失败记录为pending状态")
        
        return jsonify({
            'message': f'成功重置 {reset_count} 条失败记录为待下载状态',
            'reset_count': reset_count
        })
        
    except Exception as e:
        logging.error(f"重置失败股票失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/stop_download_request', methods=['GET', 'POST'])
def stop_download_request():
    global stop_download, download_status, download_progress

    with download_lock:
        stop_download = True  # 设置标志为 True，要求下载停止
        download_status = "请求停止中"  # 更新状态
        download_progress = 0  # 重置进度
    
    logging.info("用户请求停止下载")
    return jsonify({"message": "下载已请求停止"}), 200


# def load_progress():
@download_data_bp.route('/load_progress', methods=['GET'])
def load_progress():
    return render_template('data/progress.html')


@download_data_bp.route('/download_index_page')
def download_index_page():
    return render_template('data/股票下载.html')


@download_data_bp.route('/daily_renew_data', methods=['GET', 'POST'])
def daily_renew_data():
    # 下载股票每天的1M 数据
    rdd = RMDownloadData()
    rdd.daily_renew_data()
    return render_template('index.html')


@download_data_bp.route('/resample_to_daily_data', methods=['GET', 'POST'])
def resample_to_daily_data():
    month = None
    stock_code = None
    data_daily = None  # 默认情况下数据为空

    if request.method == 'POST':
        # 从表单获取参数
        stock_code = request.form.get('stock_code')
        month = request.form.get('month')

        if month and stock_code:
            file_name = f'{stock_code}.csv'

            data_path = StockDataPath.month_1m_data_path(month)

            data_1m = pd.read_csv(data_path)
            data_daily = ResampleData.resample_1m_data(data_1m, 'd')

            try:
                # 假设 ResampleData 和 pd.read_csv 是正确配置的

                flash("文件转换成功！", "success")
            except Exception as e:
                flash(f"文件转换失败: {e}", "danger")

    return render_template('data/resample_to_daily_data.html', stock_code=stock_code, month=month, data_daily=data_daily)


@download_data_bp.route('/download_stock_1m_close_data_today', methods=['GET', 'POST'])
def download_stock_1m_close_data_today():
    if request.method == 'POST':
        stock_code = request.form.get('stock_code')
        if stock_code:
            try:
                # 使用完整的下载流程
                result = complete_download_process(stock_code, days=1)
                
                if result['success']:
                    flash(f"成功完成 {stock_code} 的完整下载流程: {result['message']}", "success")
                    
                    # 获取各种数据类型的文件路径
                    from App.utils.file_utils import get_stock_data_path
                    file_path_1m = get_stock_data_path(stock_code, data_type='1m')
                    file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')
                    file_path_daily = get_stock_data_path(stock_code, data_type='daily')
                    
                    # 从数据库查询刚刚保存的日线数据
                    daily_data_from_db = None
                    try:
                        from App.models.data.StockDaily import StockDaily
                        from datetime import datetime, timedelta
                        import pandas as pd
                        
                        # 查询最近7天的日线数据
                        end_date = datetime.now().date()
                        start_date = end_date - timedelta(days=7)
                        
                        daily_records = StockDaily.query.filter(
                            StockDaily.stock_code == stock_code,
                            StockDaily.date >= start_date,
                            StockDaily.date <= end_date
                        ).order_by(StockDaily.date.desc()).all()
                        
                        if daily_records:
                            # 转换为DataFrame格式
                            data_list = [{
                                'date': record.date.strftime('%Y-%m-%d'),
                                'open': record.open,
                                'high': record.high,
                                'low': record.low,
                                'close': record.close,
                                'volume': record.volume,
                                'money': record.money
                            } for record in daily_records]
                            
                            daily_data_from_db = pd.DataFrame(data_list)
                            
                    except Exception as e:
                        logging.warning(f"查询日线数据失败: {str(e)}")
                        daily_data_from_db = None
                    
                    return render_template('data/success.html',
                                         file_path_1m=file_path_1m,
                                         file_path_15m=file_path_15m,
                                         file_path_daily=file_path_daily,
                                         daily_data_from_db=daily_data_from_db,
                                         stock_code=stock_code)
                else:
                    flash(f"下载流程部分失败: {result['message']}", "warning")
                    
            except Exception as e:
                flash(f"下载失败: {str(e)}", "danger")
    
    return render_template('data/success.html',
                         file_path_1m=None,
                         file_path_15m=None,
                         file_path_daily=None,
                         daily_data_from_db=None,
                         stock_code=None)


@download_data_bp.route('/download_stock_1m_close_data', methods=['GET', 'POST'])
def download_stock_1m_close_data():
    if request.method == 'POST':
        stock_code = request.form.get('stock_code')
        days = int(request.form.get('days', 5))
        if stock_code:
            try:
                # 使用完整的下载流程
                result = complete_download_process(stock_code, days=days)

                if result['success']:
                    flash(f"成功完成 {stock_code} 的完整下载流程（{days}天数据）: {result['message']}", "success")

                    # 获取各种数据类型的文件路径
                    from App.utils.file_utils import get_stock_data_path
                    from App.utils.path_manager import get_path_manager
                    import os

                    pm = get_path_manager()

                    # 智能查找1分钟数据文件路径
                    file_path_1m = None
                    current_year = int(pm.get_current_year())
                    quarters_to_check = []
                    for year in [current_year, current_year - 1]:
                        for q in ['Q4', 'Q3', 'Q2', 'Q1']:
                            quarters_to_check.append((str(year), q))

                    for year, quarter in quarters_to_check:
                        path = get_stock_data_path(stock_code, data_type='1m', year=year, quarter=quarter, create=False)
                        if os.path.exists(path):
                            file_path_1m = path
                            break

                    if not file_path_1m:
                        file_path_1m = get_stock_data_path(stock_code, data_type='1m')

                    file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')

                    # 智能查找日线数据
                    file_path_daily = None
                    for year, quarter in quarters_to_check:
                        path = get_stock_data_path(stock_code, data_type='daily', year=year, quarter=quarter, create=False)
                        if os.path.exists(path):
                            file_path_daily = path
                            break
                    if not file_path_daily:
                        file_path_daily = get_stock_data_path(stock_code, data_type='daily')

                    # ========== 数据验证部分 ==========
                    validation_result = {
                        'status': 'success',
                        'issues': [],
                        'stats': {}
                    }

                    data_1m_preview = None
                    if os.path.exists(file_path_1m):
                        try:
                            if file_path_1m.endswith('.parquet'):
                                df_1m = pd.read_parquet(file_path_1m)
                            else:
                                df_1m = pd.read_csv(file_path_1m, encoding='utf-8-sig')
                            df_1m['date'] = pd.to_datetime(df_1m['date'])

                            # 统计信息
                            validation_result['stats']['total_records'] = len(df_1m)
                            validation_result['stats']['first_time'] = df_1m['date'].min().strftime('%Y-%m-%d %H:%M')
                            validation_result['stats']['last_time'] = df_1m['date'].max().strftime('%Y-%m-%d %H:%M')

                            # 按天统计记录数
                            df_1m['trade_date'] = df_1m['date'].dt.date
                            daily_counts = df_1m.groupby('trade_date').size().to_dict()
                            validation_result['stats']['daily_counts'] = {str(k): v for k, v in daily_counts.items()}

                            # 检查数据质量
                            zero_open_count = (df_1m['open'] == 0).sum()
                            if zero_open_count > 0:
                                validation_result['issues'].append(f"⚠️ 发现 {zero_open_count} 条 open=0 的数据")
                                validation_result['status'] = 'warning'

                            from datetime import time as dt_time
                            first_time = df_1m['date'].min().time()
                            if first_time < dt_time(9, 30):
                                validation_result['issues'].append(f"⚠️ 数据包含 9:30 之前的记录（首条时间: {first_time}）")
                                validation_result['status'] = 'warning'

                            for date_str, count in daily_counts.items():
                                if count < 200:
                                    validation_result['issues'].append(f"⚠️ {date_str} 只有 {count} 条记录（正常约240条）")
                                    validation_result['status'] = 'warning'

                            if (df_1m['close'] <= 0).any():
                                validation_result['issues'].append("❌ 存在 close<=0 的异常数据")
                                validation_result['status'] = 'error'

                            # 准备全部数据
                            def convert_row(row):
                                return {
                                    'date': row['date'].strftime('%Y-%m-%d %H:%M') if hasattr(row['date'], 'strftime') else str(row['date']),
                                    'open': float(row['open']),
                                    'close': float(row['close']),
                                    'high': float(row['high']),
                                    'low': float(row['low']),
                                    'volume': int(row['volume']),
                                    'money': int(row['money'])
                                }

                            data_1m_preview = {
                                'all': [convert_row(row) for _, row in df_1m.iterrows()],
                                'total_count': len(df_1m)
                            }

                            if not validation_result['issues']:
                                validation_result['issues'].append("✅ 数据质量检查通过")

                        except Exception as e:
                            logging.error(f"读取1分钟数据失败: {str(e)}")
                            validation_result['issues'].append(f"❌ 读取CSV失败: {str(e)}")
                            validation_result['status'] = 'error'
                    else:
                        validation_result['issues'].append(f"❌ 1分钟数据文件不存在: {file_path_1m}")
                        validation_result['status'] = 'error'

                    # 从数据库查询日线数据
                    daily_data_from_db = None
                    try:
                        from App.models.data.StockDaily import StockDaily

                        end_date = datetime.now().date()
                        start_date = end_date - timedelta(days=7)

                        daily_records = StockDaily.query.filter(
                            StockDaily.stock_code == stock_code,
                            StockDaily.date >= start_date,
                            StockDaily.date <= end_date
                        ).order_by(StockDaily.date.desc()).all()

                        if daily_records:
                            data_list = [{
                                'date': record.date.strftime('%Y-%m-%d'),
                                'open': f"{record.open:.2f}",
                                'high': f"{record.high:.2f}",
                                'low': f"{record.low:.2f}",
                                'close': f"{record.close:.2f}",
                                'volume': f"{record.volume:,}",
                                'money': f"{record.money:,}"
                            } for record in daily_records]

                            daily_data_from_db = pd.DataFrame(data_list)

                    except Exception as e:
                        logging.warning(f"查询日线数据失败: {str(e)}")
                        daily_data_from_db = None

                    return render_template('data/success.html',
                                         file_path=file_path_1m,
                                         file_path_15m=file_path_15m,
                                         file_path_daily=file_path_daily,
                                         daily_data_from_db=daily_data_from_db,
                                         stock_code=stock_code,
                                         validation_result=validation_result,
                                         data_1m_preview=data_1m_preview)
                else:
                    flash(f"下载流程部分失败: {result['message']}", "warning")

            except Exception as e:
                flash(f"下载失败: {str(e)}", "danger")

    return render_template('data/success.html',
                         file_path=None,
                         file_path_15m=None,
                         file_path_daily=None,
                         daily_data_from_db=None,
                         stock_code=None,
                         validation_result=None,
                         data_1m_preview=None)


# @download_data_bp.route('/download_stock_daily_data', methods=['GET', 'POST'])
# def download_stock_daily_data():
#     if request.method == 'POST':
#         stock_code = request.form.get('stock_code')
#         if stock_code:
#             try:
#                 # 下载日线数据
#                 data, _ = download_1m_by_type(stock_code, 1, StockType.STOCK_1M)
#                 if not data.empty:
#                     # 转换为日线数据
#                     daily_data = ResampleData.resample_1m_data(data, 'd')
#                     daily_data = daily_data.fillna({'open': 0.0, 'close': 0.0,
#                                                   'high': 0.0, 'low': 0.0,
#                                                   'volume': 0, 'money': 0})
#                     # 保存数据
#                     save_daily_stock_data_to_sql(stock_code, daily_data)
#                     flash(f"成功下载 {stock_code} 的日线数据", "success")
#                 else:
#                     flash(f"未找到 {stock_code} 的数据", "warning")
#             except Exception as e:
#                 flash(f"下载失败: {str(e)}", "danger")
#     return render_template('download/success.html')


@download_data_bp.route('/download_fund_holdings', methods=['GET', 'POST'])
def download_fund_holdings():
    if request.method == 'POST':
        try:
            # 下载基金持仓数据
            rdd = RMDownloadData()
            rdd.download_fund_holdings()
            flash("成功下载基金持仓数据", "success")
        except Exception as e:
            flash(f"下载失败: {str(e)}", "danger")
    return render_template('data/success.html',
                         file_path_1m=None,
                         file_path_15m=None,
                         file_path_daily=None,
                         daily_data_from_db=None,
                         stock_code=None)


@download_data_bp.route('/download_minute_data_page')
def download_minute_data_page():
    return render_template('data/download_minute_data.html')

@download_data_bp.route('/open_data_folder', methods=['POST'])
def open_data_folder():
    """打开1分钟数据文件夹"""
    try:
        import subprocess
        import platform
        
        # 获取1分钟数据文件夹路径
        from config import Config
        data_folder = os.path.join(Config.get_project_root(), 'data', 'data', 'quarters')
        
        # 确保文件夹存在
        os.makedirs(data_folder, exist_ok=True)
        
        # 根据操作系统打开文件夹
        system = platform.system()
        
        if system == "Windows":
            # Windows explorer命令即使成功也可能返回非零状态，所以不使用check=True
            result = subprocess.run(['explorer', data_folder], capture_output=True, text=True)
            if result.returncode != 0 and result.stderr:
                # 只有在有错误输出时才认为是真正的错误
                raise Exception(f"打开文件夹失败: {result.stderr}")
        elif system == "Darwin":  # macOS
            subprocess.run(['open', data_folder], check=True)
        elif system == "Linux":
            subprocess.run(['xdg-open', data_folder], check=True)
        else:
            return jsonify({"success": False, "message": f"不支持的操作系统: {system}"}), 400
        
        logging.info(f"成功打开1分钟数据文件夹: {data_folder}")
        return jsonify({"success": True, "message": "数据文件夹已打开"}), 200
        
    except Exception as e:
        logging.error(f"打开1分钟数据文件夹时发生错误: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@download_data_bp.route('/resample_to_daily', methods=['POST'])
def resample_to_daily():
    """将已下载的1分钟数据重新采样为日线数据并保存到daily_stock_data表"""
    try:
        from App.codes.RnnDataFile.save_download import save_1m_to_daily
        from App.codes.RnnDataFile.stock_path import get_stock_data_path
        import glob
        
        logging.info("开始重新采样1分钟数据为日线数据...")
        
        # 获取所有已下载的1分钟数据文件
        data_folder = StockDataPath.get_stock_data_directory()
        csv_files = glob.glob(os.path.join(data_folder, "1m", "*.csv"))
        
        if not csv_files:
            return jsonify({
                "success": False, 
                "message": "没有找到1分钟数据文件"
            }), 400
        
        processed_count = 0
        success_count = 0
        error_count = 0
        error_details = []
        
        for csv_file in csv_files:
            try:
                # 从文件名提取股票代码
                filename = os.path.basename(csv_file)
                stock_code = filename.replace('.csv', '')
                
                # 读取1分钟数据
                df_1m = pd.read_csv(csv_file)
                
                if df_1m.empty:
                    logging.warning(f"股票 {stock_code} 的1分钟数据为空，跳过")
                    continue
                
                # 确保列名正确
                if 'date' not in df_1m.columns:
                    logging.warning(f"股票 {stock_code} 的1分钟数据缺少date列，跳过")
                    continue
                
                # 重新采样为日线数据并保存
                save_1m_to_daily(df_1m, stock_code)
                success_count += 1
                logging.info(f"成功处理股票 {stock_code} 的日线数据")
                
            except Exception as e:
                error_count += 1
                error_msg = f"处理股票 {stock_code} 时出错: {str(e)}"
                error_details.append(error_msg)
                logging.error(error_msg)
            
            processed_count += 1
        
        result_message = f"重新采样完成！处理了 {processed_count} 个文件，成功 {success_count} 个，失败 {error_count} 个"
        
        if error_details:
            result_message += f"\n错误详情: {'; '.join(error_details[:5])}"  # 只显示前5个错误
        
        logging.info(result_message)
        
        return jsonify({
            "success": True,
            "message": result_message,
            "processed_count": processed_count,
            "success_count": success_count,
            "error_count": error_count
        }), 200
        
    except Exception as e:
        logging.error(f"重新采样1分钟数据为日线数据时发生错误: {e}")
        return jsonify({
            "success": False, 
            "message": f"重新采样失败: {str(e)}"
        }), 500


@download_data_bp.route('/complete_download_single', methods=['POST'])
def complete_download_single():
    """
    单个股票完整下载流程API
    """
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')
        days = data.get('days', 1)
        
        if not stock_code:
            return jsonify({
                "success": False,
                "message": "股票代码不能为空"
            }), 400
        
        # 执行完整下载流程
        result = complete_download_process(stock_code, days)
        
        return jsonify({
            "success": result['success'],
            "message": result['message'],
            "steps": result['steps'],
            "data_info": result['data_info']
        }), 200
        
    except Exception as e:
        logging.error(f"完整下载流程API错误: {e}")
        return jsonify({
            "success": False,
            "message": f"下载失败: {str(e)}"
        }), 500


@download_data_bp.route('/complete_download_test', methods=['GET'])
def complete_download_test():
    """
    完整下载流程测试页面
    """
    return render_template('data/complete_download_test.html')


@download_data_bp.route('/set_download_end_date', methods=['POST'])
def set_download_end_date():
    """
    设置所有股票的下载结束日期，用于强制重新下载数据

    请求参数:
        end_date: 结束日期，格式 YYYY-MM-DD
    """
    try:
        data = request.get_json()
        end_date_str = data.get('end_date')

        if not end_date_str:
            return jsonify({
                "success": False,
                "message": "请提供结束日期 (end_date)"
            }), 400

        try:
            new_end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({
                "success": False,
                "message": "日期格式错误，请使用 YYYY-MM-DD 格式"
            }), 400

        # 更新所有非忽略股票的 end_date
        # 注意：使用UTC时间以保持与模型定义一致
        update_count = dlr.query.filter(
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).update({
            'end_date': new_end_date,
            'download_status': 'pending',  # 重置为待下载状态
            'download_progress': 0.0,
            'updated_at': datetime.utcnow()
        })

        db.session.commit()

        logging.info(f"成功将 {update_count} 条记录的 end_date 设置为 {new_end_date}")

        return jsonify({
            "success": True,
            "message": f"成功将 {update_count} 条记录的结束日期设置为 {end_date_str}，状态已重置为待下载",
            "updated_count": update_count,
            "new_end_date": end_date_str
        }), 200

    except Exception as e:
        db.session.rollback()
        logging.error(f"设置下载结束日期时发生错误: {e}")
        return jsonify({
            "success": False,
            "message": f"设置失败: {str(e)}"
        }), 500


@download_data_bp.route('/download_records_editor')
def download_records_editor():
    """下载记录编辑页面"""
    return render_template('data/download_records_editor.html')


@download_data_bp.route('/api/download_records', methods=['GET'])
def api_get_download_records():
    """获取下载记录列表（带分页和搜索）"""
    try:
        # 获取参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        search = request.args.get('search', '', type=str)
        status_filter = request.args.get('status', '', type=str)

        per_page = min(per_page, 100)

        # 构建查询
        query = dlr.query

        # 状态筛选
        if status_filter:
            query = query.filter(dlr.download_status == status_filter)

        # 搜索（需要通过 stock_code_id 关联查找）
        if search:
            # 先查找匹配的股票ID
            matching_stock_ids = db.session.query(StockCodes.id).filter(
                StockCodes.code.like(f'%{search}%')
            ).all()
            matching_ids = [s[0] for s in matching_stock_ids]
            if matching_ids:
                query = query.filter(dlr.stock_code_id.in_(matching_ids))
            else:
                # 没有匹配的股票，返回空结果
                return jsonify({
                    'records': [],
                    'total': 0,
                    'page': page,
                    'per_page': per_page,
                    'total_pages': 0
                })

        # 排除被忽略的记录
        query = query.filter(
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        )

        # 排序
        query = query.order_by(dlr.updated_at.desc())

        # 分页
        total = query.count()
        records = query.offset((page - 1) * per_page).limit(per_page).all()

        # 格式化数据
        result = []
        for record in records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            result.append({
                'id': record.id,
                'stock_code_id': record.stock_code_id,
                'stock_code': stock_code or f'ID:{record.stock_code_id}',
                'download_status': record.download_status,
                'download_progress': record.download_progress,
                'error_message': record.error_message,
                'start_date': record.start_date.strftime('%Y-%m-%d') if record.start_date else None,
                'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else None,
                'record_date': record.record_date.strftime('%Y-%m-%d') if record.record_date else None,
                'total_records': record.total_records,
                'downloaded_records': record.downloaded_records,
                'last_download_time': record.last_download_time.strftime('%Y-%m-%d %H:%M:%S') if record.last_download_time else None,
                'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else None,
                'updated_at': record.updated_at.strftime('%Y-%m-%d %H:%M:%S') if record.updated_at else None
            })

        return jsonify({
            'records': result,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })

    except Exception as e:
        logging.error(f"获取下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/<int:record_id>', methods=['GET'])
def api_get_download_record(record_id):
    """获取单条下载记录"""
    try:
        record = dlr.query.get(record_id)
        if not record:
            return jsonify({'error': '记录不存在'}), 404

        stock_code = get_stock_code_by_id(record.stock_code_id)

        return jsonify({
            'id': record.id,
            'stock_code_id': record.stock_code_id,
            'stock_code': stock_code or f'ID:{record.stock_code_id}',
            'download_status': record.download_status,
            'download_progress': record.download_progress,
            'error_message': record.error_message,
            'start_date': record.start_date.strftime('%Y-%m-%d') if record.start_date else None,
            'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else None,
            'record_date': record.record_date.strftime('%Y-%m-%d') if record.record_date else None,
            'total_records': record.total_records,
            'downloaded_records': record.downloaded_records,
            'last_download_time': record.last_download_time.strftime('%Y-%m-%d %H:%M:%S') if record.last_download_time else None
        })

    except Exception as e:
        logging.error(f"获取下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records', methods=['POST'])
def api_add_download_record():
    """添加下载记录"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')

        if not stock_code:
            return jsonify({'error': '股票代码不能为空'}), 400

        # 查找股票ID
        stock = StockCodes.query.filter_by(code=stock_code).first()
        if not stock:
            return jsonify({'error': f'未找到股票代码: {stock_code}'}), 404

        # 检查是否已存在
        existing = dlr.query.filter_by(stock_code_id=stock.id).first()
        if existing:
            return jsonify({'error': f'股票 {stock_code} 的下载记录已存在'}), 400

        # 创建新记录
        new_record = dlr(
            stock_code_id=stock.id,
            download_status=data.get('download_status', 'pending'),
            download_progress=0.0,
            start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date() if data.get('start_date') else None,
            end_date=datetime.strptime(data['end_date'], '%Y-%m-%d').date() if data.get('end_date') else date.today(),
            record_date=date.today(),
            total_records=0,
            downloaded_records=0
        )

        db.session.add(new_record)
        db.session.commit()

        logging.info(f"成功添加下载记录: {stock_code}")

        return jsonify({
            'success': True,
            'message': f'成功添加股票 {stock_code} 的下载记录',
            'id': new_record.id
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"添加下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/<int:record_id>', methods=['PUT'])
def api_update_download_record(record_id):
    """更新下载记录"""
    try:
        record = dlr.query.get(record_id)
        if not record:
            return jsonify({'error': '记录不存在'}), 404

        data = request.get_json()

        # 更新字段
        if 'download_status' in data:
            record.download_status = data['download_status']
        if 'end_date' in data and data['end_date']:
            record.end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date()
        if 'start_date' in data and data['start_date']:
            record.start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
        if 'error_message' in data:
            record.error_message = data['error_message']

        record.updated_at = datetime.utcnow()
        db.session.commit()

        stock_code = get_stock_code_by_id(record.stock_code_id)
        logging.info(f"成功更新下载记录: {stock_code}")

        return jsonify({
            'success': True,
            'message': f'成功更新记录 {stock_code}'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"更新下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/<int:record_id>', methods=['DELETE'])
def api_delete_download_record(record_id):
    """删除下载记录"""
    try:
        record = dlr.query.get(record_id)
        if not record:
            return jsonify({'error': '记录不存在'}), 404

        stock_code = get_stock_code_by_id(record.stock_code_id)

        db.session.delete(record)
        db.session.commit()

        logging.info(f"成功删除下载记录: {stock_code}")

        return jsonify({
            'success': True,
            'message': f'成功删除记录 {stock_code}'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"删除下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/batch_delete', methods=['POST'])
def api_batch_delete_download_records():
    """批量删除下载记录"""
    try:
        data = request.get_json()
        ids = data.get('ids', [])

        if not ids:
            return jsonify({'error': '请选择要删除的记录'}), 400

        deleted_count = dlr.query.filter(dlr.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()

        logging.info(f"批量删除了 {deleted_count} 条下载记录")

        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 条记录',
            'deleted_count': deleted_count
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"批量删除下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/batch_update_status', methods=['POST'])
def api_batch_update_status():
    """批量更新下载状态"""
    try:
        data = request.get_json()
        ids = data.get('ids', [])
        new_status = data.get('status')

        if not ids:
            return jsonify({'error': '请选择要更新的记录'}), 400
        if not new_status:
            return jsonify({'error': '请指定新状态'}), 400

        updated_count = dlr.query.filter(dlr.id.in_(ids)).update({
            'download_status': new_status,
            'updated_at': datetime.utcnow()
        }, synchronize_session=False)
        db.session.commit()

        logging.info(f"批量更新了 {updated_count} 条记录状态为 {new_status}")

        return jsonify({
            'success': True,
            'message': f'成功更新 {updated_count} 条记录状态为 {new_status}',
            'updated_count': updated_count
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"批量更新下载记录状态失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/available_stocks', methods=['GET'])
def api_get_available_stocks():
    """获取可添加的股票列表（尚未在下载记录中的）"""
    try:
        search = request.args.get('search', '', type=str)
        limit = request.args.get('limit', 20, type=int)

        # 获取已有下载记录的股票ID
        existing_ids = db.session.query(dlr.stock_code_id).all()
        existing_ids = [s[0] for s in existing_ids]

        # 查询未添加的股票
        query = StockCodes.query.filter(~StockCodes.id.in_(existing_ids))

        if search:
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    StockCodes.code.like(f'%{search}%'),
                    StockCodes.name.like(f'%{search}%')
                )
            )

        stocks = query.limit(limit).all()

        return jsonify({
            'stocks': [
                {'id': s.id, 'code': s.code, 'name': s.name}
                for s in stocks
            ]
        })

    except Exception as e:
        logging.error(f"获取可用股票列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/complete_download_batch', methods=['POST'])
def complete_download_batch():
    """
    批量股票完整下载流程API
    """
    try:
        data = request.get_json()
        stock_codes = data.get('stock_codes', [])
        days = data.get('days', 1)
        
        if not stock_codes:
            return jsonify({
                "success": False,
                "message": "股票代码列表不能为空"
            }), 400
        
        if not isinstance(stock_codes, list):
            return jsonify({
                "success": False,
                "message": "股票代码必须是列表格式"
            }), 400
        
        # 执行批量下载流程
        result = batch_complete_download_process(stock_codes, days)
        
        return jsonify({
            "success": True,
            "message": f"批量下载完成: 成功 {result['success']}, 失败 {result['failed']}",
            "total": result['total'],
            "success_count": result['success'],
            "failed_count": result['failed'],
            "details": result['details']
        }), 200
        
    except Exception as e:
        logging.error(f"批量完整下载流程API错误: {e}")
        return jsonify({
            "success": False,
            "message": f"批量下载失败: {str(e)}"
        }), 500
