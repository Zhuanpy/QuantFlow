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

from flask import Blueprint, render_template, jsonify, copy_current_request_context, current_app
import threading
from datetime import date
from App.exts import db
from concurrent.futures import ThreadPoolExecutor, as_completed

from App.models.strategy.StockRecordModels import Top500FundRecord
from App.models.data.FundsAwkward import save_funds_holdings_to_csv as funds_holdings_to_csv

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


def funds_holdings_to_csv(data, download_date):
    """将基金持仓数据保存到CSV文件"""
    try:
        # 创建下载目录 - 使用统一的路径计算方式
        from App.models.data.FundsAwkward import get_funds_data_directory
        csv_dir = get_funds_data_directory()
        os.makedirs(csv_dir, exist_ok=True)
        
        # 生成文件名
        csv_filename = f"funds_holdings_{download_date.strftime('%Y%m%d')}.csv"
        csv_path = os.path.join(csv_dir, csv_filename)
        
        # 确保数据格式正确
        if not data.empty:
            # 确保所有字符串字段都是UTF-8编码
            for col in data.select_dtypes(include=['object']).columns:
                data[col] = data[col].astype(str).str.encode('utf-8').str.decode('utf-8')
            
            # 使用线程锁来确保线程安全
            import tempfile
            import threading
            
            # 创建线程锁（全局变量，确保所有线程共享同一个锁）
            if not hasattr(funds_holdings_to_csv, '_file_lock'):
                funds_holdings_to_csv._file_lock = threading.Lock()
            
            # 创建临时文件来写入数据
            temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8-sig')
            temp_path = temp_file.name
            
            try:
                # 写入数据到临时文件
                data.to_csv(temp_path, index=False, encoding='utf-8-sig')
                temp_file.close()
                
                # 使用线程锁来安全地追加到目标文件
                with funds_holdings_to_csv._file_lock:
                    # 检查文件是否为空（是否需要写入header）
                    file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
                    
                    if not file_exists:
                        # 文件不存在或为空，写入完整数据（包括header）
                        with open(temp_path, 'r', encoding='utf-8-sig') as temp_read:
                            with open(csv_path, 'w', encoding='utf-8-sig') as target_file:
                                target_file.write(temp_read.read())
                        logging.info(f"基金持仓数据已保存到新文件: {csv_path}")
                    else:
                        # 文件存在且不为空，只写入数据行（不包括header）
                        with open(temp_path, 'r', encoding='utf-8-sig') as temp_read:
                            lines = temp_read.readlines()
                            # 跳过header行，只写入数据行
                            with open(csv_path, 'a', encoding='utf-8-sig') as target_file:
                                for line in lines[1:]:
                                    target_file.write(line)
                        logging.info(f"基金持仓数据已追加到现有文件: {csv_path}")
                
                logging.info(f"本次保存 {len(data)} 条记录")
                return True
                        
            finally:
                # 清理临时文件
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    
        else:
            logging.warning("没有数据需要保存")
            return False
        
    except Exception as e:
        logging.error(f"保存基金持仓数据到CSV时出错: {e}")
        return False


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
    
    with app.app_context(), download_lock:
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
    records_to_process = []
    
    for fund in all_funds:
        if should_download_fund(fund):
            records_to_process.append(fund)
    
    total_count = len(records_to_process)  # 记录总数
    
    with download_lock:
        task_state.total_funds = total_count
        task_state.waiting_count = total_count

    # 如果没有需要处理的记录，记录日志并更新任务状态
    if not records_to_process:
        logging.info("没有需要下载的基金数据。")
        with download_lock:
            task_state.status = "无数据下载"  # 更新状态为"无数据下载"
        return

    # 记录需要处理的基金记录总数
    logging.info(f"需要下载 {total_count} 条基金数据...")

    def process_single_fund(record):
        """处理单个基金的下载任务"""
        try:
            print(f"正在下载基金: {record.name} ({record.code})")

            # 下载基金持仓数据
            stocks_data = download_single_fund_data(record.code)
            
            # 检查数据完整性
            if stocks_data is None or len(stocks_data) == 0:
                logging.warning(f"基金 {record.name} ({record.code}) 无数据")
                # 确保在Flask应用上下文中更新数据库
                try:
                    with app.app_context():
                        record.update_download_status(status_failure, download_date)
                except Exception as db_error:
                    logging.error(f"更新数据库状态失败: {record.name} ({record.code}), 错误: {db_error}")
                return {'status': 'failed', 'record': record, 'reason': '无数据'}
            
            # 将数据转换为DataFrame
            import pandas as pd
            data = pd.DataFrame(stocks_data)
            
            # 添加基金信息到数据中
            data['fund_name'] = record.name
            data['fund_code'] = record.code
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
            
            # 将下载数据存入本地CSV文件
            save_success = funds_holdings_to_csv(data, download_date)
            
            if save_success:
                # 更新记录状态为成功，并记录下载日期
                try:
                    with app.app_context():
                        success = record.update_download_status(status_success, download_date)
                        if success:
                            logging.info(f"成功下载并更新数据库状态: {record.name} ({record.code}) -> {status_success}")
                        else:
                            logging.error(f"更新数据库状态失败: {record.name} ({record.code})")
                except Exception as db_error:
                    logging.error(f"更新数据库状态失败: {record.name} ({record.code}), 错误: {db_error}")
                    # 即使数据库更新失败，数据已经保存成功，所以返回成功状态
                return {'status': 'success', 'record': record}
            else:
                # 保存失败
                try:
                    with app.app_context():
                        failure = record.update_download_status(status_failure, download_date)
                        if failure:
                            logging.error(f"保存基金数据失败并更新数据库状态: {record.name} ({record.code}) -> {status_failure}")
                        else:
                            logging.error(f"保存失败且更新数据库状态也失败: {record.name} ({record.code})")
                except Exception as db_error:
                    logging.error(f"更新数据库状态失败: {record.name} ({record.code}), 错误: {db_error}")
                return {'status': 'failed', 'record': record, 'reason': '保存失败'}

        except Exception as e:
            # 捕获下载或存储过程中发生的异常，记录日志并更新状态为失败
            logging.error(f"下载失败: {record.name}, {record.code}, 错误: {e}")
            try:
                with app.app_context():
                    failure = record.update_download_status(status_failure, download_date)
                    if failure:
                        logging.error(f"异常处理中更新数据库状态成功: {record.name} ({record.code}) -> {status_failure}")
                    else:
                        logging.error(f"异常处理中更新数据库状态失败: {record.name} ({record.code})")
            except Exception as db_error:
                logging.error(f"异常处理中更新数据库状态失败: {record.name} ({record.code}), 错误: {db_error}")
            return {'status': 'failed', 'record': record, 'reason': str(e)}
        
        finally:
            # 每个基金处理完成后增加短暂延时，进一步降低对第三方网站的压力
            time.sleep(DOWNLOAD_CONFIG['post_process_delay'])

    # 使用线程池并发处理 - 减少并发数以降低对第三方网站的压力
    max_workers = min(DOWNLOAD_CONFIG['max_concurrent_workers'], len(records_to_process))
    processed_count = 0
    
    logging.info(f"使用 {max_workers} 个并发线程进行下载，降低对第三方网站的压力")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_record = {executor.submit(process_single_fund, record): record for record in records_to_process}
        
        # 处理完成的任务
        for future in as_completed(future_to_record):
            # 检查是否需要停止
            with download_lock:
                if task_state.stop:
                    task_state.status = "已停止"
                    logging.info("下载任务被用户中止，取消剩余任务。")
                    # 取消所有未完成的任务
                    for remaining_future in future_to_record:
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
