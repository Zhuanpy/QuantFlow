"""
基金重仓数据下载路由
提供基金重仓数据的下载功能
"""
import logging
import time
import requests
import re
import os
from bs4 import BeautifulSoup

from flask import Blueprint, render_template, jsonify, copy_current_request_context, current_app, request
import threading
from datetime import date
from App.exts import db
from concurrent.futures import ThreadPoolExecutor, as_completed

from App.models.strategy.StockRecordModels import Top500FundRecord

# 下载配置参数 - 可根据需要调整以降低对第三方网站的压力
DOWNLOAD_CONFIG = {
    'max_concurrent_workers': 2,      # 最大并发线程数（原为5，现改为2）
    'download_delay': 3,              # 每次下载前等待秒数（原为1，现改为3）
    'post_process_delay': 1,          # 每个基金处理完成后等待秒数
    'request_timeout': 10,            # 请求超时时间（秒）
    'api_timeout': 8,                 # API请求超时时间（秒）
    'webpage_timeout': 12,            # 网页请求超时时间（秒）
}

# 创建蓝图
dl_funds_awkward_bp = Blueprint('dl_funds_awkward_bp', __name__)


# 封装任务状态
class DownloadTaskState:
    def __init__(self):
        self.status = "未开始"
        self.progress = 0
        self.stop = False
        self.total_funds = 0
        self.success_count = 0
        self.failure_count = 0
        self.waiting_count = 0

    def reset(self):
        self.status = "未开始"
        self.progress = 0
        self.stop = False
        self.total_funds = 0
        self.success_count = 0
        self.failure_count = 0
        self.waiting_count = 0


task_state = DownloadTaskState()
download_thread = None
download_lock = threading.Lock()


def should_download_fund(fund_record, days_interval=15):
    """
    判断基金是否需要重新下载
    
    Args:
        fund_record: 基金记录对象
        days_interval: 下载间隔天数，默认15天
        
    Returns:
        bool: 是否需要下载
    """
    if not fund_record.date:
        return True
    
    days_since_last = (date.today() - fund_record.date).days
    return days_since_last >= days_interval


def get_download_statistics():
    """
    获取下载统计数据
    
    Returns:
        dict: 包含等待、成功、失败数量的字典
    """
    try:
        # 获取所有基金记录
        all_funds = Top500FundRecord.query.all()
        
        waiting_count = 0
        success_count = 0
        failure_count = 0
        
        # 添加调试日志
        logging.info(f"开始统计 {len(all_funds)} 个基金的状态")
        
        for fund in all_funds:
            if should_download_fund(fund):
                waiting_count += 1
                logging.debug(f"基金 {fund.code} 需要下载")
            elif fund.status and fund.status.startswith('success-'):
                success_count += 1
                logging.debug(f"基金 {fund.code} 下载成功: {fund.status}")
            elif fund.status and fund.status.startswith('failure-'):
                failure_count += 1
                logging.debug(f"基金 {fund.code} 下载失败: {fund.status}")
            else:
                logging.debug(f"基金 {fund.code} 状态未知: {fund.status}")
        
        # 同时返回内存中的实时统计信息
        with download_lock:
            memory_waiting = task_state.waiting_count
            memory_success = task_state.success_count
            memory_failure = task_state.failure_count
            memory_total = task_state.total_funds
        
        # 添加调试日志
        logging.info(f"数据库统计: 等待={waiting_count}, 成功={success_count}, 失败={failure_count}")
        logging.info(f"内存统计: 等待={memory_waiting}, 成功={memory_success}, 失败={memory_failure}, 总计={memory_total}")
        
        # 判断使用哪种统计数据
        if task_state.status == "已完成" or task_state.status == "无数据下载" or task_state.status == "未开始":
            # 下载已完成、无数据下载或未开始，使用数据库数据
            result = {
                'waiting': waiting_count,
                'success': success_count,
                'failure': failure_count,
                'total': len(all_funds)
            }
            logging.info(f"状态为 '{task_state.status}'，使用数据库统计数据")
        else:
            # 正在下载中，使用内存数据
            result = {
                'waiting': memory_waiting if memory_total > 0 else waiting_count,
                'success': memory_success if memory_total > 0 else success_count,
                'failure': memory_failure if memory_total > 0 else failure_count,
                'total': memory_total if memory_total > 0 else len(all_funds)
            }
            logging.info(f"状态为 '{task_state.status}'，使用内存统计数据")
        
        logging.info(f"最终统计结果: {result}")
        return result
        
    except Exception as e:
        logging.error(f"获取下载统计数据时发生错误: {e}")
        return {'waiting': 0, 'success': 0, 'failure': 0, 'total': 0}


def download_single_fund_data(fund_code):
    """下载单个基金的重仓股票数据"""
    try:
        print(f"正在下载基金 {fund_code} 的数据...")
        
        # 增加延时，降低对第三方网站的压力
        time.sleep(DOWNLOAD_CONFIG['download_delay'])  # 每次下载前等待指定秒数
        
        # 尝试使用API接口获取数据（更快）
        api_url = f"http://fund.eastmoney.com/api/FundPosition/{fund_code}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Referer': f'http://fund.eastmoney.com/{fund_code}.html',
            'X-Requested-With': 'XMLHttpRequest'
        }
        
        try:
            # 首先尝试API接口
            response = requests.get(api_url, headers=headers, timeout=DOWNLOAD_CONFIG['api_timeout'])
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data and 'data' in data and 'stockList' in data['data']:
                        stocks = []
                        for stock in data['data']['stockList']:
                            if 'stockCode' in stock and 'stockName' in stock:
                                stocks.append({
                                    'stock_code': stock['stockCode'],
                                    'stock_name': stock['stockName'],
                                    'position': float(stock.get('position', 0)),
                                    'change': float(stock.get('change', 0)),
                                    'fund_code': fund_code
                                })
                        if stocks:
                            print(f"基金 {fund_code} API成功提取 {len(stocks)} 只股票")
                            return stocks
                except:
                    pass  # API失败，回退到网页解析
        except:
            pass  # API请求失败，回退到网页解析
        
        # 回退到网页解析方式
        url = f"http://fund.eastmoney.com/{fund_code}.html"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive'
        }
        
        # 获取页面源码
        response = requests.get(url, headers=headers, timeout=DOWNLOAD_CONFIG['webpage_timeout'])
        response.encoding = 'utf-8'
        
        if response.status_code != 200:
            print(f"基金 {fund_code} 页面访问失败: {response.status_code}")
            return None
        
        # 使用BeautifulSoup解析，确保编码正确
        soup = BeautifulSoup(response.content, 'html.parser', from_encoding='utf-8')
        
        # 查找股票持仓表格
        stock_table = soup.find('table', class_='ui-table-hover')
        if not stock_table:
            print(f"基金 {fund_code} 未找到股票持仓表格")
            return None
        
        # 查找所有股票行
        stock_rows = stock_table.find_all('tr')[1:]  # 跳过表头
        
        stocks = []
        for row in stock_rows:
            cells = row.find_all('td')
            if len(cells) >= 3:
                # 提取股票链接和名称
                stock_link = cells[0].find('a')
                if stock_link:
                    stock_name = stock_link.get_text(strip=True)
                    stock_href = stock_link.get('href', '')
                    
                    # 从链接中提取股票代码
                    stock_code = None
                    if '/unify/r/' in stock_href:
                        # 格式: /unify/r/1.688123 或 /unify/r/0.002222
                        code_match = re.search(r'/unify/r/\d+\.(\d{6})', stock_href)
                        if code_match:
                            stock_code = code_match.group(1)
                    
                    # 提取持仓占比
                    position_text = cells[1].get_text(strip=True)
                    position = position_text.replace('%', '') if '%' in position_text else '0'
                    
                    # 提取涨跌幅
                    change_text = cells[2].get_text(strip=True)
                    change = change_text.replace('%', '') if '%' in change_text else '0'
                    
                    if stock_code and stock_name:
                        stocks.append({
                            'stock_code': stock_code,
                            'stock_name': stock_name,
                            'position': float(position),
                            'change': float(change),
                            'fund_code': fund_code
                        })
        
        if stocks:
            print(f"基金 {fund_code} 网页解析成功提取 {len(stocks)} 只股票")
            return stocks
        else:
            print(f"基金 {fund_code} 未提取到股票数据")
            return None
            
    except Exception as e:
        print(f"下载基金 {fund_code} 数据时出错: {e}")
        return None


def download_fund_data():
    """
    下载基金持仓数据任务（优化版本）。

    此函数用于从数据库中获取需要处理的基金记录，使用并发方式下载数据并存储至数据库，
    并动态更新下载任务的状态和进度。
    """
    logging.info("[FUND_DOWNLOAD] download_fund_data() 函数开始执行")
    print("[FUND_DOWNLOAD] download_fund_data() 函数开始执行")
    
    # 获取当前日期作为下载日期
    download_date = date.today()
    # 设置任务成功和失败的标志状态
    status_success = f'success-{download_date}'  # 成功状态标志
    status_failure = f'failure-{download_date}'  # 失败状态标志
    print(f"[FUND_DOWNLOAD] 成功状态标志: {status_success}")
    logging.info(f"[FUND_DOWNLOAD] 成功状态标志: {status_success}")

    # 获取Flask应用实例，用于多线程环境中的数据库操作
    from App import create_app
    app = create_app()
    logging.info("[FUND_DOWNLOAD] Flask应用实例已创建")
    print("[FUND_DOWNLOAD] Flask应用实例已创建")

    # 初始化 Flask 应用的上下文，并获取下载任务的锁，确保多线程操作安全
    logging.info("[FUND_DOWNLOAD] 准备进入应用上下文")
    print("[FUND_DOWNLOAD] 准备进入应用上下文")

    with app.app_context():
        with download_lock:
            task_state.reset()  # 重置任务状态
            task_state.status = "进行中"  # 设置初始状态为"进行中"
            logging.info(f"[FUND_DOWNLOAD] 任务状态已设置为: {task_state.status}")
            print(f"[FUND_DOWNLOAD] 任务状态已设置为: {task_state.status}")

        # 从数据库查询所有需要下载的基金记录（15天间隔逻辑）
        logging.info("[FUND_DOWNLOAD] 开始查询基金记录")
        print("[FUND_DOWNLOAD] 开始查询基金记录")
        all_funds = Top500FundRecord.query.all()
        logging.info(f"[FUND_DOWNLOAD] 查询到 {len(all_funds)} 个基金记录")
        print(f"[FUND_DOWNLOAD] 查询到 {len(all_funds)} 个基金记录")

        # 提取需要处理的基金ID列表（而不是对象，避免跨线程会话问题）
        records_to_process_ids = []
        for fund in all_funds:
            if should_download_fund(fund):
                records_to_process_ids.append(fund.id)

        total_count = len(records_to_process_ids)  # 记录总数

        with download_lock:
            task_state.total_funds = total_count
            task_state.waiting_count = total_count

        # 如果没有需要处理的记录，记录日志并更新任务状态
        if not records_to_process_ids:
            logging.info("没有需要下载的基金数据。")
            with download_lock:
                task_state.status = "无数据下载"  # 更新状态为"无数据下载"
            return

        # 记录需要处理的基金记录总数
        logging.info(f"需要下载 {total_count} 条基金数据...")

        # 清空当日 CSV，避免与上一次（可能不完整）的运行结果混在一起
        # 后续每个 worker 通过 save_funds_holdings_to_csv 追加写入
        from App.models.data.FundsAwkward import reset_funds_holdings_csv
        reset_funds_holdings_csv(download_date)

    def process_single_fund(fund_id):
        """处理单个基金的下载任务"""
        try:
            # 在工作线程中重新查询记录，避免跨线程会话问题
            with app.app_context():
                record = Top500FundRecord.query.get(fund_id)
                if not record:
                    logging.error(f"未找到基金记录 ID: {fund_id}")
                    return {'status': 'failed', 'fund_id': fund_id, 'reason': '记录不存在'}

                fund_name = record.name
                fund_code = record.code

                print(f"正在下载基金: {fund_name} ({fund_code})")

                # 下载基金持仓数据
                stocks_data = download_single_fund_data(fund_code)

                # 检查数据完整性
                if stocks_data is None or len(stocks_data) == 0:
                    logging.warning(f"基金 {fund_name} ({fund_code}) 无数据")
                    record.update_download_status(status_failure, download_date)
                    return {'status': 'failed', 'fund_id': fund_id, 'reason': '无数据'}

                # 将数据转换为DataFrame
                import pandas as pd
                data = pd.DataFrame(stocks_data)

                # 添加基金信息到数据中
                data['fund_name'] = fund_name
                data['fund_code'] = fund_code
                data['download_date'] = download_date.strftime('%Y-%m-%d')

                # 重命名列以匹配原有格式
                data = data.rename(columns={
                    'stock_code': 'stock_code',
                    'stock_name': 'stock_name',
                    'position': 'holdings_ratio',
                    'change': 'change_percent'
                })

                # 添加缺失的字段
                data['market_value'] = 'N/A'
                data['shares'] = 'N/A'

                # 确保数据格式正确
                data = data[['stock_name', 'stock_code', 'fund_name', 'fund_code', 'download_date', 'holdings_ratio', 'market_value', 'shares']]

                print(f"下载数据: {len(data)} 条记录")

                # 将下载数据存入本地CSV文件（使用导入的函数）
                from App.models.data.FundsAwkward import save_funds_holdings_to_csv
                save_success = save_funds_holdings_to_csv(data, download_date)

                if save_success:
                    # 更新记录状态为成功，并记录下载日期
                    success = record.update_download_status(status_success, download_date)
                    if success:
                        logging.info(f"成功下载并更新数据库状态: {fund_name} ({fund_code}) -> {status_success}")
                    else:
                        logging.error(f"更新数据库状态失败: {fund_name} ({fund_code})")
                    return {'status': 'success', 'fund_id': fund_id}
                else:
                    # 保存失败
                    record.update_download_status(status_failure, download_date)
                    logging.error(f"保存基金数据失败: {fund_name} ({fund_code})")
                    return {'status': 'failed', 'fund_id': fund_id, 'reason': '保存失败'}

        except Exception as e:
            # 捕获下载或存储过程中发生的异常，记录日志并更新状态为失败
            logging.error(f"下载失败: fund_id={fund_id}, 错误: {e}")
            try:
                with app.app_context():
                    record = Top500FundRecord.query.get(fund_id)
                    if record:
                        record.update_download_status(status_failure, download_date)
            except Exception as db_error:
                logging.error(f"异常处理中更新数据库状态失败: fund_id={fund_id}, 错误: {db_error}")
            return {'status': 'failed', 'fund_id': fund_id, 'reason': str(e)}

        finally:
            # 每个基金处理完成后增加短暂延时，进一步降低对第三方网站的压力
            time.sleep(DOWNLOAD_CONFIG['post_process_delay'])

    # 使用线程池并发处理 - 减少并发数以降低对第三方网站的压力
    max_workers = min(DOWNLOAD_CONFIG['max_concurrent_workers'], len(records_to_process_ids))
    processed_count = 0

    logging.info(f"使用 {max_workers} 个并发线程进行下载，降低对第三方网站的压力")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务（传递基金ID而不是对象）
        future_to_fund_id = {executor.submit(process_single_fund, fund_id): fund_id for fund_id in records_to_process_ids}
        
        # 处理完成的任务
        for future in as_completed(future_to_fund_id):
            # 检查是否需要停止
            with download_lock:
                if task_state.stop:
                    task_state.status = "已停止"
                    logging.info("下载任务被用户中止，取消剩余任务。")
                    # 取消所有未完成的任务
                    for remaining_future in future_to_fund_id:
                        if not remaining_future.done():
                            remaining_future.cancel()
                    return
            
            try:
                result = future.result()
                processed_count += 1
                
                # 更新统计信息
                with download_lock:
                    if result['status'] == 'success':
                        task_state.success_count += 1
                    else:
                        task_state.failure_count += 1
                    task_state.waiting_count -= 1
                    
                    # 更新进度
                    task_state.progress = min(round(processed_count * (100 / total_count), 1), 100)
                    logging.info(f"下载进度更新: {task_state.progress}%")
                
            except Exception as e:
                logging.error(f"处理基金下载任务时发生错误: {e}")
                with download_lock:
                    task_state.failure_count += 1
                    task_state.waiting_count -= 1

    # 下载完成后，更新任务状态为"已完成"并设置进度为 100%
    with download_lock:
        task_state.status = "已完成"  # 设置状态为"已完成"
        task_state.progress = 100  # 设置进度为 100%
        logging.info("所有基金数据下载任务已完成。")

    # 自动把当日基金持仓聚合写回 data_stock_daily，无需用户手动触发
    # 失败不影响下载主流程，仅记录日志
    try:
        from App.models.data.StockDaily import update_fund_holdings_data
        with app.app_context():
            ok = update_fund_holdings_data(download_date.strftime('%Y%m%d'))
        if ok:
            logging.info(f"已自动同步 {download_date} 基金持仓到 data_stock_daily")
        else:
            logging.warning(f"自动同步 {download_date} 基金持仓到 data_stock_daily 未成功（无数据或全部未匹配）")
    except Exception as e:
        logging.error(f"自动同步基金持仓到日K失败: {e}")


@dl_funds_awkward_bp.route("/download_funds_awake_index")
@dl_funds_awkward_bp.route("/")  # 添加根路径，这样 /funds/ 也能访问
def download_funds_awake_index():
    """显示下载任务的状态页面。"""
    return render_template("data/download_fund_data.html", status=task_state.status, progress=task_state.progress)


@dl_funds_awkward_bp.route("/start_download", methods=["GET", "POST"])
def start_download():
    global download_thread

    logging.info("[FUND_DOWNLOAD] 接收到开始下载请求")
    print("[FUND_DOWNLOAD] 接收到开始下载请求")
    
    if download_thread is None or not download_thread.is_alive():
        logging.info("[FUND_DOWNLOAD] 准备启动新的下载线程")
        print("[FUND_DOWNLOAD] 准备启动新的下载线程")
        
        # 重置停止标志和任务状态
        with download_lock:
            task_state.stop = False
            task_state.status = "进行中"
            task_state.reset()  # 重置所有计数器
            logging.info(f"[FUND_DOWNLOAD] 任务状态已重置，当前状态: {task_state.status}")
            print(f"[FUND_DOWNLOAD] 任务状态已重置，当前状态: {task_state.status}")
        
        @copy_current_request_context
        def run_download():
            try:
                logging.info("[FUND_DOWNLOAD] 下载线程已启动，开始执行 download_fund_data()")
                print("[FUND_DOWNLOAD] 下载线程已启动，开始执行 download_fund_data()")
                download_fund_data()
                logging.info("[FUND_DOWNLOAD] download_fund_data() 执行完成")
                print("[FUND_DOWNLOAD] download_fund_data() 执行完成")
            except Exception as e:
                logging.error(f"[FUND_DOWNLOAD] 下载线程异常: {e}")
                print(f"[FUND_DOWNLOAD] 下载线程异常: {e}")
                import traceback
                traceback.print_exc()

        download_thread = threading.Thread(target=run_download)
        download_thread.start()
        logging.info("[FUND_DOWNLOAD] 下载线程已提交启动")
        print("[FUND_DOWNLOAD] 下载线程已提交启动")
        return jsonify({"message": "下载已开始"}), 200
    else:
        logging.warning("[FUND_DOWNLOAD] 下载任务已在运行中")
        print("[FUND_DOWNLOAD] 下载任务已在运行中")
        return jsonify({"message": "下载正在进行中"}), 400


@dl_funds_awkward_bp.route("/stop_download_route", methods=["GET", "POST"])
def stop_download_route():
    """终止下载任务的接口。"""
    with download_lock:
        task_state.stop = True
        task_state.status = "已停止"
    logging.info("下载任务请求停止，将取消所有未开始的任务。")
    return jsonify({"message": "下载任务已停止。"})


@dl_funds_awkward_bp.route("/status", methods=["GET"])
def status():
    """获取当前下载任务的状态和进度。"""
    with download_lock:
        return jsonify({
            "status": task_state.status, 
            "progress": task_state.progress,
            "total_funds": task_state.total_funds,
            "success_count": task_state.success_count,
            "failure_count": task_state.failure_count,
            "waiting_count": task_state.waiting_count
        })


@dl_funds_awkward_bp.route("/statistics", methods=["GET"])
def get_statistics():
    """获取下载统计数据。"""
    stats = get_download_statistics()
    return jsonify(stats)


@dl_funds_awkward_bp.route("/reset_fund_status", methods=["POST"])
def reset_fund_status():
    """重置基金下载状态，用于15天间隔重新下载。"""
    try:
        # 重置所有基金的状态，使其可以重新下载
        funds = Top500FundRecord.query.all()
        for fund in funds:
            fund.status = None
            fund.date = None

        db.session.commit()
        logging.info("成功重置所有基金下载状态")
        return jsonify({"message": "成功重置基金下载状态"}), 200
    except Exception as e:
        logging.error(f"重置基金下载状态时发生错误: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@dl_funds_awkward_bp.route("/reset_failed_funds", methods=["POST"])
def reset_failed_funds():
    """重置下载失败的基金状态，允许重新下载。"""
    try:
        # 查找所有下载失败的基金
        failed_funds = Top500FundRecord.query.filter(
            Top500FundRecord.status.like('failure-%')
        ).all()

        failed_count = len(failed_funds)

        if failed_count == 0:
            return jsonify({
                "success": True,
                "message": "没有下载失败的基金需要重置",
                "reset_count": 0
            }), 200

        # 重置失败基金的状态
        for fund in failed_funds:
            fund.status = None
            fund.date = None

        db.session.commit()
        logging.info(f"成功重置 {failed_count} 个下载失败的基金状态")
        return jsonify({
            "success": True,
            "message": f"成功重置 {failed_count} 个下载失败的基金，可以重新下载",
            "reset_count": failed_count
        }), 200
    except Exception as e:
        logging.error(f"重置失败基金状态时发生错误: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@dl_funds_awkward_bp.route("/open_funds_data_folder", methods=["POST"])
def open_funds_data_folder():
    """打开数据文件夹"""
    try:
        import subprocess
        import platform
        
        # 获取数据文件夹路径 - 使用正确的路径
        from App.models.data.FundsAwkward import get_funds_data_directory
        data_folder = get_funds_data_directory()
        
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
        
        logging.info(f"成功打开数据文件夹: {data_folder}")
        return jsonify({"success": True, "message": "数据文件夹已打开"}), 200

    except Exception as e:
        logging.error(f"打开数据文件夹时发生错误: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== 基金列表管理 API ====================

@dl_funds_awkward_bp.route("/api/funds", methods=["GET"])
def api_get_funds():
    """获取基金列表（带分页和搜索）"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        search = request.args.get('search', '', type=str)
        status_filter = request.args.get('status', '', type=str)

        per_page = min(per_page, 100)

        query = Top500FundRecord.query

        # 搜索
        if search:
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    Top500FundRecord.code.like(f'%{search}%'),
                    Top500FundRecord.name.like(f'%{search}%')
                )
            )

        # 状态筛选
        if status_filter == 'success':
            query = query.filter(Top500FundRecord.status.like('success-%'))
        elif status_filter == 'failed':
            query = query.filter(Top500FundRecord.status.like('failure-%'))
        elif status_filter in ('pending', 'waiting'):
            # 等待下载：status为空或不是success开头
            query = query.filter(
                (Top500FundRecord.status == None) |
                ((~Top500FundRecord.status.like('success-%')) & (~Top500FundRecord.status.like('failure-%')))
            )

        # 排序
        query = query.order_by(Top500FundRecord.id.desc())

        # 分页
        total = query.count()
        total_pages = (total + per_page - 1) // per_page if total > 0 else 1
        funds = query.offset((page - 1) * per_page).limit(per_page).all()

        # 转换为前端期望的格式
        funds_data = []
        for f in funds:
            funds_data.append({
                'id': f.id,
                'fund_code': f.code,
                'fund_name': f.name,
                'selection': f.selection if f.selection is not None else 1,
                'status': f.status,
                'last_download_time': f.date.strftime('%Y-%m-%d') if f.date else None
            })

        return jsonify({
            'success': True,
            'data': funds_data,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': total_pages,
                'has_prev': page > 1,
                'has_next': page < total_pages
            }
        })

    except Exception as e:
        logging.error(f"获取基金列表失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/funds/<int:fund_id>", methods=["GET"])
def api_get_fund(fund_id):
    """获取单个基金详情"""
    try:
        fund = Top500FundRecord.query.get(fund_id)
        if not fund:
            return jsonify({'success': False, 'message': '基金不存在'}), 404
        return jsonify({
            'success': True,
            'data': {
                'id': fund.id,
                'fund_code': fund.code,
                'fund_name': fund.name,
                'status': fund.status,
                'last_download_time': fund.date.strftime('%Y-%m-%d') if fund.date else None
            }
        })
    except Exception as e:
        logging.error(f"获取基金详情失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/funds", methods=["POST"])
def api_add_fund():
    """添加基金"""
    try:
        data = request.get_json()
        # 支持两种字段名格式
        code = data.get('fund_code', data.get('code', '')).strip()
        name = data.get('fund_name', data.get('name', '')).strip()

        if not code:
            return jsonify({'success': False, 'message': '基金代码不能为空'}), 400
        if not name:
            return jsonify({'success': False, 'message': '基金名称不能为空'}), 400

        # 检查是否已存在
        existing = Top500FundRecord.query.filter_by(code=code).first()
        if existing:
            return jsonify({'success': False, 'message': f'基金 {code} 已存在'}), 400

        # 创建新记录
        new_fund = Top500FundRecord(
            code=code,
            name=name,
            selection=data.get('selection', 1),
            status=None,
            date=None
        )

        db.session.add(new_fund)
        db.session.commit()

        logging.info(f"成功添加基金: {name} ({code})")
        return jsonify({
            'success': True,
            'message': f'成功添加基金 {name}'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"添加基金失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/funds/<int:fund_id>", methods=["PUT"])
def api_update_fund(fund_id):
    """更新基金信息"""
    try:
        fund = Top500FundRecord.query.get(fund_id)
        if not fund:
            return jsonify({'success': False, 'message': '基金不存在'}), 404

        data = request.get_json()

        # 支持两种字段名格式
        if 'fund_name' in data or 'name' in data:
            fund.name = data.get('fund_name', data.get('name', '')).strip()
        if 'fund_code' in data or 'code' in data:
            # 检查新代码是否与其他基金冲突
            new_code = data.get('fund_code', data.get('code', '')).strip()
            if new_code and new_code != fund.code:
                existing = Top500FundRecord.query.filter_by(code=new_code).first()
                if existing:
                    return jsonify({'success': False, 'message': f'基金代码 {new_code} 已被使用'}), 400
                fund.code = new_code
        if 'selection' in data:
            fund.selection = int(data['selection'])

        from datetime import datetime
        fund.updated_at = datetime.utcnow()
        db.session.commit()

        logging.info(f"成功更新基金: {fund.name} ({fund.code})")
        return jsonify({
            'success': True,
            'message': f'成功更新基金 {fund.name}'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"更新基金失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/funds/<int:fund_id>", methods=["DELETE"])
def api_delete_fund(fund_id):
    """删除基金"""
    try:
        fund = Top500FundRecord.query.get(fund_id)
        if not fund:
            return jsonify({'success': False, 'message': '基金不存在'}), 404

        fund_name = fund.name
        fund_code = fund.code

        db.session.delete(fund)
        db.session.commit()

        logging.info(f"成功删除基金: {fund_name} ({fund_code})")
        return jsonify({
            'success': True,
            'message': f'成功删除基金 {fund_name}'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"删除基金失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/funds/batch_delete", methods=["POST"])
def api_batch_delete_funds():
    """批量删除基金"""
    try:
        data = request.get_json()
        ids = data.get('ids', [])

        if not ids:
            return jsonify({'success': False, 'message': '请选择要删除的基金'}), 400

        deleted_count = Top500FundRecord.query.filter(
            Top500FundRecord.id.in_(ids)
        ).delete(synchronize_session=False)
        db.session.commit()

        logging.info(f"批量删除了 {deleted_count} 个基金")
        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 个基金',
            'deleted_count': deleted_count
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"批量删除基金失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/funds/batch_add", methods=["POST"])
def api_batch_add_funds():
    """批量添加基金"""
    try:
        data = request.get_json()
        funds_data = data.get('funds', [])

        if not funds_data:
            return jsonify({'success': False, 'message': '请提供基金数据'}), 400

        added_count = 0
        skipped_count = 0
        errors = []

        for fund_info in funds_data:
            # 支持两种字段名格式
            code = fund_info.get('fund_code', fund_info.get('code', '')).strip()
            name = fund_info.get('fund_name', fund_info.get('name', '')).strip()

            if not code or not name:
                skipped_count += 1
                continue

            # 检查是否已存在
            existing = Top500FundRecord.query.filter_by(code=code).first()
            if existing:
                skipped_count += 1
                continue

            try:
                new_fund = Top500FundRecord(
                    code=code,
                    name=name,
                    selection=1,
                    status=None,
                    date=None
                )
                db.session.add(new_fund)
                added_count += 1
            except Exception as e:
                errors.append(f"{code}: {str(e)}")
                skipped_count += 1

        db.session.commit()

        logging.info(f"批量添加基金: 成功 {added_count}, 跳过 {skipped_count}")
        return jsonify({
            'success': True,
            'message': f'成功添加 {added_count} 个基金，跳过 {skipped_count} 个',
            'added_count': added_count,
            'skipped_count': skipped_count,
            'errors': errors[:5]  # 只返回前5个错误
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"批量添加基金失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 基金重仓分析 API ====================

@dl_funds_awkward_bp.route("/api/analysis/files", methods=["GET"])
def api_get_analysis_files():
    """获取可用的基金持仓数据文件列表"""
    try:
        import glob
        from config import Config

        # 获取基金持仓数据目录
        project_root = Config.get_project_root()
        data_dir = os.path.join(project_root, 'data', 'funds_holdings')
        files = glob.glob(os.path.join(data_dir, "funds_holdings_*.csv"))

        file_list = []
        for f in sorted(files, reverse=True):
            filename = os.path.basename(f)
            # 解析日期
            date_str = filename.replace("funds_holdings_", "").replace(".csv", "")
            file_list.append({
                'filename': filename,
                'date': date_str,
                'path': f
            })

        return jsonify({
            'success': True,
            'data': file_list
        })
    except Exception as e:
        logging.error(f"获取分析文件列表失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/analysis/funds_selection", methods=["GET"])
def api_get_funds_selection():
    """获取基金选择状态统计"""
    try:
        total = Top500FundRecord.query.count()
        selected = Top500FundRecord.query.filter_by(selection=1).count()

        return jsonify({
            'success': True,
            'data': {
                'total': total,
                'selected': selected,
                'unselected': total - selected
            }
        })
    except Exception as e:
        logging.error(f"获取基金选择状态失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/analysis/select_top", methods=["POST"])
def api_select_top_funds():
    """选择前N只基金用于分析"""
    try:
        data = request.get_json()
        top_n = data.get('top_n', 500)
        action = data.get('action', 'select')  # 'select', 'select_all', 'unselect_all'

        if action == 'unselect_all':
            # 取消全选
            Top500FundRecord.query.update({Top500FundRecord.selection: 0})
            db.session.commit()
            return jsonify({
                'success': True,
                'message': '已取消全部选择',
                'selected_count': 0
            })

        if action == 'select_all':
            # 全选所有基金
            Top500FundRecord.query.update({Top500FundRecord.selection: 1})
            db.session.commit()
            total = Top500FundRecord.query.count()
            return jsonify({
                'success': True,
                'message': f'已选择全部 {total} 只基金',
                'selected_count': total
            })

        # 选择前N只基金
        # 先重置所有selection为0
        Top500FundRecord.query.update({Top500FundRecord.selection: 0})

        # 获取前N只基金（按id排序，即按添加顺序）
        top_funds = Top500FundRecord.query.order_by(Top500FundRecord.id.asc()).limit(top_n).all()

        for fund in top_funds:
            fund.selection = 1

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'已选择前 {len(top_funds)} 只基金',
            'selected_count': len(top_funds)
        })
    except Exception as e:
        db.session.rollback()
        logging.error(f"选择基金失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/analysis/toggle_selection/<int:fund_id>", methods=["POST"])
def api_toggle_fund_selection(fund_id):
    """切换单个基金的选择状态"""
    try:
        fund = Top500FundRecord.query.get(fund_id)
        if not fund:
            return jsonify({'success': False, 'message': '基金不存在'}), 404

        fund.selection = 1 if fund.selection == 0 else 0
        db.session.commit()

        return jsonify({
            'success': True,
            'selection': fund.selection
        })
    except Exception as e:
        db.session.rollback()
        logging.error(f"切换基金选择状态失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/analysis/holdings", methods=["POST"])
def api_analyze_holdings():
    """分析基金重仓股票"""
    try:
        import pandas as pd
        from config import Config

        data = request.get_json()
        date_str = data.get('date')  # 格式: YYYYMMDD
        use_selected = data.get('use_selected', True)  # 是否只使用选中的基金
        min_holdings = data.get('min_holdings', 1)  # 最少被持仓次数

        if not date_str:
            return jsonify({'success': False, 'message': '请选择数据日期'}), 400

        # 构建文件路径
        project_root = Config.get_project_root()
        data_dir = os.path.join(project_root, 'data', 'funds_holdings')
        file_path = os.path.join(data_dir, f"funds_holdings_{date_str}.csv")

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'数据文件不存在: {date_str}'}), 404

        # 读取CSV数据
        df = pd.read_csv(file_path, encoding='utf-8-sig')

        # 如果只使用选中的基金，进行过滤
        if use_selected:
            selected_funds = Top500FundRecord.query.filter_by(selection=1).all()
            if not selected_funds:
                return jsonify({
                    'success': False,
                    'message': '请先选择要分析的基金（点击"选择前500只"按钮）'
                }), 400
            selected_codes = [f.code for f in selected_funds]
            df = df[df['fund_code'].astype(str).isin([str(c) for c in selected_codes])]

        if df.empty:
            return jsonify({
                'success': True,
                'data': [],
                'summary': {
                    'total_stocks': 0,
                    'total_funds': 0,
                    'total_records': 0,
                    'date': date_str
                }
            })

        # 统计每只股票被多少基金持仓
        stock_stats = df.groupby(['stock_code', 'stock_name']).agg({
            'fund_code': 'nunique',  # 持有该股票的基金数量
            'holdings_ratio': lambda x: x[x != 'N/A'].astype(float).mean() if any(x != 'N/A') else 0  # 平均持仓比例
        }).reset_index()

        stock_stats.columns = ['stock_code', 'stock_name', 'fund_count', 'avg_ratio']

        # 过滤最少持仓次数
        stock_stats = stock_stats[stock_stats['fund_count'] >= min_holdings]

        # 按基金持仓数量排序
        stock_stats = stock_stats.sort_values('fund_count', ascending=False)

        # 转换为列表
        result = []
        for _, row in stock_stats.iterrows():
            result.append({
                'stock_code': str(row['stock_code']),
                'stock_name': row['stock_name'],
                'fund_count': int(row['fund_count']),
                'avg_ratio': round(float(row['avg_ratio']), 2) if row['avg_ratio'] else 0
            })

        # 统计信息
        total_funds = df['fund_code'].nunique()
        total_stocks = len(result)

        return jsonify({
            'success': True,
            'data': result,
            'summary': {
                'total_stocks': total_stocks,
                'total_funds': total_funds,
                'total_records': len(df),
                'date': date_str
            }
        })

    except Exception as e:
        logging.error(f"分析基金重仓失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/analysis/stock_detail/<stock_code>", methods=["POST"])
def api_get_stock_detail(stock_code):
    """获取某只股票被哪些基金持仓的详情"""
    try:
        import pandas as pd
        from config import Config

        data = request.get_json()
        date_str = data.get('date')

        if not date_str:
            return jsonify({'success': False, 'message': '请选择数据日期'}), 400

        # 构建文件路径
        project_root = Config.get_project_root()
        data_dir = os.path.join(project_root, 'data', 'funds_holdings')
        file_path = os.path.join(data_dir, f"funds_holdings_{date_str}.csv")

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'数据文件不存在'}), 404

        # 读取CSV数据
        df = pd.read_csv(file_path, encoding='utf-8-sig')

        # 筛选该股票
        stock_df = df[df['stock_code'].astype(str) == str(stock_code)]

        if stock_df.empty:
            return jsonify({'success': False, 'message': '未找到该股票的持仓信息'}), 404

        result = []
        for _, row in stock_df.iterrows():
            result.append({
                'fund_code': str(row['fund_code']),
                'fund_name': row['fund_name'],
                'holdings_ratio': row['holdings_ratio'] if row['holdings_ratio'] != 'N/A' else None,
                'market_value': row['market_value'] if row['market_value'] != 'N/A' else None,
                'shares': row['shares'] if row['shares'] != 'N/A' else None
            })

        stock_name = stock_df.iloc[0]['stock_name']

        return jsonify({
            'success': True,
            'stock_code': stock_code,
            'stock_name': stock_name,
            'data': result
        })

    except Exception as e:
        logging.error(f"获取股票持仓详情失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 高级分析 API ====================

@dl_funds_awkward_bp.route("/api/adv/hot_stocks", methods=["GET"])
def api_adv_hot_stocks():
    """获取热门股票分析（被最多基金持有的股票）"""
    try:
        import pandas as pd
        from config import Config

        date_str = request.args.get('date')
        if not date_str:
            return jsonify({'success': False, 'message': '请指定日期'}), 400

        # 构建文件路径
        project_root = Config.get_project_root()
        data_dir = os.path.join(project_root, 'data', 'funds_holdings')
        file_path = os.path.join(data_dir, f"funds_holdings_{date_str}.csv")

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'数据文件不存在: {date_str}'}), 404

        # 读取CSV数据
        df = pd.read_csv(file_path, encoding='utf-8-sig')

        if df.empty:
            return jsonify({
                'success': True,
                'total_funds': 0,
                'total_stocks': 0,
                'total_records': 0,
                'top_stocks': []
            })

        # 统计每只股票被多少基金持有
        stock_fund_count = df.groupby(['stock_code', 'stock_name']).agg({
            'fund_code': 'count',
            'holdings_ratio': ['mean', 'sum', 'max']
        }).round(2)

        stock_fund_count.columns = ['fund_count', 'avg_ratio', 'total_ratio', 'max_ratio']
        stock_fund_count = stock_fund_count.reset_index()
        stock_fund_count = stock_fund_count.sort_values('fund_count', ascending=False)

        # 取前20名
        top_stocks = stock_fund_count.head(20).to_dict('records')

        return jsonify({
            'success': True,
            'date': date_str,
            'total_stocks': len(stock_fund_count),
            'total_funds': df['fund_code'].nunique(),
            'total_records': len(df),
            'top_stocks': top_stocks
        })

    except Exception as e:
        logging.error(f"获取热门股票分析失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/adv/fund_distribution", methods=["GET"])
def api_adv_fund_distribution():
    """获取基金分布分析（基金持仓股票数量分布）"""
    try:
        import pandas as pd
        from config import Config

        date_str = request.args.get('date')
        if not date_str:
            return jsonify({'success': False, 'message': '请指定日期'}), 400

        project_root = Config.get_project_root()
        data_dir = os.path.join(project_root, 'data', 'funds_holdings')
        file_path = os.path.join(data_dir, f"funds_holdings_{date_str}.csv")

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'数据文件不存在'}), 404

        df = pd.read_csv(file_path, encoding='utf-8-sig')

        if df.empty:
            return jsonify({
                'success': True,
                'distribution_stats': {},
                'top_funds': []
            })

        # 统计每个基金持有的股票数量
        fund_stock_count = df.groupby(['fund_code', 'fund_name']).size().reset_index(name='stock_count')

        # 计算分布统计
        distribution_stats = fund_stock_count['stock_count'].describe().to_dict()

        # 获取持仓股票最多的基金
        top_funds = fund_stock_count.sort_values('stock_count', ascending=False).head(10).to_dict('records')

        return jsonify({
            'success': True,
            'date': date_str,
            'distribution_stats': distribution_stats,
            'top_funds': top_funds
        })

    except Exception as e:
        logging.error(f"获取基金分布分析失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/adv/industry_analysis", methods=["GET"])
def api_adv_industry_analysis():
    """获取行业/市场分析"""
    try:
        import pandas as pd
        from config import Config

        date_str = request.args.get('date')
        if not date_str:
            return jsonify({'success': False, 'message': '请指定日期'}), 400

        project_root = Config.get_project_root()
        data_dir = os.path.join(project_root, 'data', 'funds_holdings')
        file_path = os.path.join(data_dir, f"funds_holdings_{date_str}.csv")

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'数据文件不存在'}), 404

        df = pd.read_csv(file_path, encoding='utf-8-sig')

        if df.empty:
            return jsonify({
                'success': True,
                'market_stats': []
            })

        # 根据股票代码判断市场
        def get_market(stock_code):
            stock_code = str(stock_code)
            if stock_code.startswith('6'):
                return '沪市主板'
            elif stock_code.startswith('0'):
                return '深市主板'
            elif stock_code.startswith('3'):
                return '创业板'
            elif stock_code.startswith('8'):
                return '北交所'
            elif stock_code.startswith('BK'):
                return '板块指数'
            else:
                return '其他'

        df['market'] = df['stock_code'].apply(get_market)

        # 按市场统计
        market_stats = df.groupby('market').agg({
            'stock_code': 'nunique',
            'fund_code': 'nunique',
            'holdings_ratio': ['mean', 'sum']
        }).round(2)

        market_stats.columns = ['stock_count', 'fund_count', 'avg_ratio', 'total_ratio']
        market_stats = market_stats.reset_index()
        market_stats = market_stats.sort_values('total_ratio', ascending=False)

        return jsonify({
            'success': True,
            'date': date_str,
            'market_stats': market_stats.to_dict('records')
        })

    except Exception as e:
        logging.error(f"获取市场分析失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@dl_funds_awkward_bp.route("/api/adv/save_to_daily", methods=["POST"])
def api_adv_save_to_daily():
    """将基金持仓分析结果保存到daily_stock_data表"""
    try:
        from App.models.data.StockDaily import update_fund_holdings_data

        data = request.get_json()
        analysis_date = data.get('date') if data else None

        success = update_fund_holdings_data(analysis_date)

        if success:
            return jsonify({
                'success': True,
                'message': '基金持仓数据已成功保存到daily_stock_data表'
            })
        else:
            return jsonify({
                'success': False,
                'message': '保存基金持仓数据失败'
            }), 500

    except Exception as e:
        logging.error(f"保存基金持仓数据失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
