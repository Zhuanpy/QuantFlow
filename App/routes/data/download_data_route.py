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
download_lock = threading.Lock()  # 用于保护全局变量的锁

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
    # 声明使用全局变量，记录下载状态、进度和停止下载标志
    global download_status, download_progress, stop_download

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
            'updated_at': datetime.now()
        })
        
        # 检查是否需要重置所有success记录
        # 如果record_date和今天不一样，说明数据不是最新的，需要重新下载
        latest_record = dlr.query.filter(
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).order_by(dlr.record_date.desc()).first()
        
        success_reset_count = 0
        if latest_record and latest_record.record_date != today:
            logging.info(f"检测到最新记录日期 {latest_record.record_date} 与今天 {today} 不同，重置所有success记录")
            
            # 将所有非忽略的success记录重置为pending
            success_reset_count = dlr.query.filter(
                dlr.download_status == 'success',
                dlr.end_date != date(2050, 1, 1),
                dlr.record_date != date(2050, 1, 1)
            ).update({
                'download_status': 'pending',
                'download_progress': 0.0,
                'updated_at': datetime.now()
            })
            
            logging.info(f"重置了 {success_reset_count} 条success记录为pending状态")
        
        db.session.commit()
        logging.info(f"重置了 {failed_reset_count} 条失败记录为pending状态")

        # 计算符合条件的数据条数（需要下载且日期在今天之前）
        print(f"[DEBUG] 查询条件:")
        print(f"[DEBUG] - download_status != 'success'")
        print(f"[DEBUG] - end_date <= {today}")
        print(f"[DEBUG] - record_date <= {today}")
        print(f"[DEBUG] - end_date != {date(2050, 1, 1)}")
        print(f"[DEBUG] - record_date != {date(2050, 1, 1)}")
        
        total_count = dlr.query.filter(
            dlr.download_status != 'success',  # 排除已下载成功的记录
            dlr.end_date <= today,  # 下载日期在今天或之前
            dlr.record_date <= today,  # 记录日期在今天或之前
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).count()
        
        print(f"[DEBUG] 查询结果: total_count = {total_count}")
        
        # 检查所有记录的状态分布
        all_records = dlr.query.count()
        success_records = dlr.query.filter(dlr.download_status == 'success').count()
        pending_records = dlr.query.filter(dlr.download_status == 'pending').count()
        failed_records = dlr.query.filter(dlr.download_status == 'failed').count()
        processing_records = dlr.query.filter(dlr.download_status == 'processing').count()
        
        print(f"[DEBUG] 记录状态分布:")
        print(f"[DEBUG] - 总记录数: {all_records}")
        print(f"[DEBUG] - 成功: {success_records}")
        print(f"[DEBUG] - 待下载: {pending_records}")
        print(f"[DEBUG] - 失败: {failed_records}")
        print(f"[DEBUG] - 处理中: {processing_records}")

        if total_count == 0:
            logging.info("没有需要下载的数据。")  # 若无数据，记录日志
            with download_lock:
                download_status = "无数据下载"  # 更新状态为无数据
            return

        print(f'需要下载 {total_count} 个股票数据...')  # 输出需要下载的数据条数
        logging.info(f"开始下载任务，总共需要下载 {total_count} 个股票")

        # 获取所有需要下载的记录
        records_to_download = dlr.query.filter(
            dlr.download_status != 'success',
            dlr.end_date <= today,
            dlr.record_date <= today,
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).all()
        
        print(f"[DEBUG] 获取到 {len(records_to_download)} 条记录")
        logging.info(f"获取到 {len(records_to_download)} 条需要下载的记录")

        # 遍历需要下载的数据记录
        for i, first_record in enumerate(records_to_download):
            logging.info(f"开始处理第 {i+1}/{len(records_to_download)} 个股票")
            print(f"[DEBUG] 处理第 {i+1}/{len(records_to_download)} 个股票")
            
            # 检查是否需要停止下载
            with download_lock:
                if stop_download:
                    download_status = "已停止"  # 更新状态为已停止
                    download_progress = 0  # 进度清零
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
            
            logging.info(f"正在下载{code_type_name} {stock_code} 的数据...")

            # 获取记录的结束日期并计算需要下载的天数
            record_ending = first_record.end_date
            days = (current - record_ending).days  # 计算自结束日期到当前日期的天数差

            # 如果end_date等于今天，允许下载今天的数据
            if days == 0:
                days = 1
            elif days < 0:
                logging.info(f'无最新1M数据: {stock_code}')  # 无更新数据，记录日志
                continue  # 跳过当前记录

            days = min(5, days)  # 限制下载天数最大值为5天
            
            # 更新下载状态为进行中
            first_record.update_download_status(status='processing')
            db.session.commit()

            # 重试机制：最多重试3次
            max_retries = 3
            retry_count = 0
            download_success = False
            
            print(f"[DEBUG] 开始重试机制，最大重试次数: {max_retries}")
            logging.info(f"开始重试机制，最大重试次数: {max_retries}")
            
            while retry_count < max_retries and not download_success:
                print(f"[DEBUG] 重试循环: retry_count={retry_count}, max_retries={max_retries}, download_success={download_success}")
                # 构建URL用于调试（放在try外面，确保变量可用）
                try:
                    from App.codes.downloads.download_utils import UrlCode
                    from config import Config
                    # 根据代码类型选择不同的URL模板
                    if is_board:
                        url_template = 'board_1m_multiple_days'
                        # 板块代码：URL模板是 format(days, code)，code 应该是原始代码（如 BK0420）
                        # 不需要使用 UrlCode，因为 URL 模板内部会处理（已包含 90. 前缀）
                        debug_url = Config.get_eastmoney_urls(url_template).format(days, stock_code)
                    else:
                        url_template = 'stock_1m_multiple_days'
                        # 股票代码：URL模板是 format(days, secid)，需要使用 UrlCode 转换
                        debug_url = Config.get_eastmoney_urls(url_template).format(days, UrlCode(stock_code))
                except Exception as url_error:
                    debug_url = f"构建URL失败: {url_error}"
                
                try:
                    if retry_count > 0:
                        # 使用指数退避策略：第1次重试等5秒，第2次等10秒，第3次等15秒
                        retry_delay = 5 * retry_count
                        logging.info(f"{code_type_name} {stock_code} 第 {retry_count + 1} 次重试下载，等待 {retry_delay} 秒...")
                        print(f"[DEBUG] {code_type_name} {stock_code} 第 {retry_count + 1} 次重试下载，等待 {retry_delay} 秒...")
                        time.sleep(retry_delay)
                    else:
                        logging.info(f"开始下载{code_type_name} {stock_code} 的 {days} 天数据...")
                        print(f"[DEBUG] 开始下载{code_type_name} {stock_code} 的 {days} 天数据...")
                    
                    # 下载数据，根据代码类型和指定的天数
                    print(f"[DEBUG] 调用 download_1m_by_type({stock_code}, {days}, {stock_type})")
                    print(f"[DEBUG] 尝试访问URL: {debug_url}")
                    logging.info(f"尝试访问URL: {debug_url}")
                    
                    data, ending = download_1m_by_type(stock_code, days, stock_type)
                    print(f"[DEBUG] 下载完成，数据形状: {data.shape if not data.empty else 'Empty'}, 结束日期: {ending}")

                    if data.empty:
                        # 对于板块数据，如果API返回rc=100（数据不存在），可能是正常情况
                        # 不需要重试，直接标记为失败但继续处理下一个
                        if is_board:
                            logging.warning(f'板块 {stock_code} 数据不可用（可能是非交易时间或板块代码无效）')
                            print(f"[DEBUG] 板块 {stock_code} 数据不可用，跳过重试")
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
                        print(f"[DEBUG] 数据为空，重试次数: {retry_count}/{max_retries}")
                        print(f"[DOWNLOAD_FAILED] {code_type_name}代码: {stock_code}, 重试次数: {retry_count}/{max_retries}")
                        print(f"[DOWNLOAD_FAILED] 访问URL: {debug_url}")
                        print(f"[DOWNLOAD_FAILED] 请在浏览器中测试此URL是否可以访问")
                        logging.error(f"下载失败 - {code_type_name}: {stock_code}, URL: {debug_url}")
                        
                        if retry_count < max_retries:
                            logging.warning(f'{code_type_name} {stock_code} 第 {retry_count} 次下载数据为空，准备重试...')
                            continue
                        else:
                            logging.error(f'{code_type_name} {stock_code} 下载失败，已达到最大重试次数 {max_retries}')
                            print(f"[DEBUG] 达到最大重试次数，标记为失败")
                            print(f"[FINAL_FAILED] {code_type_name} {stock_code} 最终下载失败")
                            print(f"[FINAL_FAILED] 失败URL: {debug_url}")
                            print(f"=" * 80)
                            
                            # 更新状态为失败
                            first_record.update_download_status(
                                status='failed',
                                error_msg=f'下载失败，已重试{max_retries}次（网络连接问题或数据源限制）\nURL: {debug_url}'
                            )
                            db.session.commit()
                            print(f"[DEBUG] 失败状态已提交到数据库")
                            
                            # 更新进度
                            with download_lock:
                                download_progress = int((i + 1) / len(records_to_download) * 100)
                            break  # 退出重试循环
                    else:
                        download_success = True
                        logging.info(f"{code_type_name} {stock_code} 下载成功，获得 {len(data)} 条记录")
                        print(f"[DEBUG] {code_type_name} {stock_code} 下载成功，获得 {len(data)} 条记录")
                
                except Exception as e:
                    retry_count += 1
                    print(f"[DEBUG] 下载异常，重试次数: {retry_count}/{max_retries}, 异常: {e}")
                    print(f"[EXCEPTION_FAILED] {code_type_name}代码: {stock_code}, 重试次数: {retry_count}/{max_retries}")
                    print(f"[EXCEPTION_FAILED] 访问URL: {debug_url}")
                    print(f"[EXCEPTION_FAILED] 异常信息: {str(e)}")
                    logging.error(f"下载异常 - {code_type_name}: {stock_code}, URL: {debug_url}, 错误: {e}")
                    
                    if retry_count < max_retries:
                        logging.warning(f'{code_type_name} {stock_code} 第 {retry_count} 次下载异常: {e}，准备重试...')
                        print(f"[DEBUG] {code_type_name} {stock_code} 第 {retry_count} 次下载异常: {e}，准备重试...")
                        continue
                    else:
                        logging.error(f'{code_type_name} {stock_code} 下载异常，已达到最大重试次数 {max_retries}: {e}')
                        print(f"[DEBUG] {code_type_name} {stock_code} 下载异常，已达到最大重试次数 {max_retries}: {e}")
                        print(f"[FINAL_EXCEPTION] {code_type_name} {stock_code} 最终下载失败（异常）")
                        print(f"[FINAL_EXCEPTION] 失败URL: {debug_url}")
                        print(f"[FINAL_EXCEPTION] 异常信息: {str(e)}")
                        print(f"=" * 80)
                        
                        # 更新状态为失败
                        first_record.update_download_status(
                            status='failed',
                            error_msg=f'下载异常，已重试{max_retries}次: {str(e)}\nURL: {debug_url}'
                        )
                        db.session.commit()
                        print(f"[DEBUG] 异常失败状态已提交到数据库")
                        
                        # 更新进度
                        with download_lock:
                            download_progress = int((i + 1) / len(records_to_download) * 100)
                        break  # 退出重试循环
            
            print(f"[DEBUG] 重试循环结束: download_success={download_success}")
            
            # 如果下载失败，跳过后续处理
            if not download_success:
                continue

            # 下载成功，继续处理数据
            if ending > record_ending:  # 若结束日期更新
                year_ = str(ending.year)

                try:
                    # 保存数据至本地 CSV 文件
                    save_1m_to_csv(data, stock_code)
                    logging.info(f'成功保存 {stock_code} 数据到CSV文件')

                except Exception as e:
                    logging.error(f'保存至CSV失败: {stock_code}, {e}')
                    # 标记为失败并继续下一个
                    first_record.update_download_status(
                        status='failed',
                        error_msg=f'保存至CSV失败: {str(e)}'
                    )
                    db.session.commit()
                    continue

                # 更新数据库记录，标记下载成功
                print(f"[DEBUG] 开始更新成功状态到数据库...")
                first_record.update_download_status(
                    status='success',
                    progress=100.0
                )
                first_record.end_date = ending
                first_record.record_date = current
                first_record.last_download_time = datetime.now()
                first_record.downloaded_records = len(data)
                db.session.commit()
                print(f"[DEBUG] 成功状态已提交到数据库")
                
                logging.info(f'成功下载 {stock_code} 的1分钟数据，共 {len(data)} 条记录')
            else:
                # 若无数据更新，仅更新记录日期
                first_record.record_date = current
                first_record.last_download_time = datetime.now()
                db.session.commit()

            # 更新下载进度
            with download_lock:
                download_progress = round((i + 1) * (100 / len(records_to_download)), 1)
            
            # 添加延迟，避免触发反爬虫机制
            # 增加延迟时间，降低被限流的风险
            import random
            delay = random.uniform(4, 7)  # 每次下载后随机等待4-7秒
            logging.info(f"等待 {delay:.1f} 秒后继续下一个股票...")
            time.sleep(delay)

        # 下载任务完成，更新下载状态和进度
        with download_lock:
            download_status = "已完成"  # 状态设为已完成
            download_progress = 100  # 进度设为 100%
        
        logging.info("下载任务完成")


@download_data_bp.route('/start_download', methods=['GET', 'POST'])
def start_download():
    """启动下载任务"""
    global download_thread, download_status, download_progress

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
        
        # 重置状态
        with download_lock:
            download_status = "初始化中"
            download_progress = 0
        
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


@download_data_bp.route('/get_download_details', methods=['GET'])
def get_download_details():
    """获取详细的下载状态信息"""
    try:
        today = date.today()
        
        # 获取最近的成功记录
        recent_success = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            dlr.last_download_time >= datetime.now() - timedelta(hours=1)  # 最近1小时
        ).order_by(dlr.last_download_time.desc()).limit(20).all()
        
        # 获取最近的失败记录
        recent_failed = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            dlr.updated_at >= datetime.now() - timedelta(hours=1)  # 最近1小时
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
                    'error': record.error_msg or '未知错误',
                    'time': record.updated_at.strftime('%H:%M:%S') if record.updated_at else '-'
                })
        
        return jsonify({
            "success": success_list,
            "failed": failed_list
        }), 200
        
    except Exception as e:
        logging.error(f"获取下载详情时发生错误: {e}")
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


@download_data_bp.route('/reset_failed_stocks', methods=['POST'])
def reset_failed_stocks():
    """重置失败的股票为待下载状态"""
    try:
        # 将所有失败的股票重置为pending状态
        reset_count = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).update({
            'download_status': 'pending',
            'download_progress': 0.0,
            'error_message': None,  # 清除错误信息
            'updated_at': datetime.now()
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
