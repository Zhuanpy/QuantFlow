import logging
import time
import threading
from datetime import date, datetime
import pandas as pd
import os

from flask import Blueprint, render_template, jsonify, copy_current_request_context, current_app
from App.codes.downloads.DlEastMoney import DownloadData
from App.models.data.FundsAwkward import save_funds_holdings_to_csv, get_funds_holdings_from_csv

# 创建蓝图
download_fund_bp = Blueprint('download_fund_bp', __name__)

# 下载状态和进度的存储
fund_download_status = "未开始"
fund_download_progress = 0
fund_download_thread = None
stop_fund_download = False
fund_download_lock = threading.Lock()

# 基金代码列表（这里可以根据需要修改）
FUND_CODES = [
    {'code': '000001', 'name': '华夏成长'},
    {'code': '000002', 'name': '华夏成长混合'},
    {'code': '000003', 'name': '华夏成长混合'},
    # 可以添加更多基金代码
]

def get_fund_data_save_path():
    """获取基金数据保存路径"""
    # 建议保存路径：data/funds_holdings/
    base_path = os.path.join(os.getcwd(), 'data', 'funds_holdings')
    os.makedirs(base_path, exist_ok=True)
    return base_path

def save_fund_data_to_csv(data: pd.DataFrame, fund_code: str, date_str: str = None):
    """保存基金数据到CSV文件"""
    try:
        if date_str is None:
            date_str = date.today().strftime('%Y%m%d')
        
        save_path = get_fund_data_save_path()
        filename = f"{fund_code}_{date_str}.csv"
        file_path = os.path.join(save_path, filename)
        
        data.to_csv(file_path, index=False, encoding='utf-8-sig')
        logging.info(f"成功保存基金 {fund_code} 数据到CSV文件: {file_path}")
        return file_path
    except Exception as e:
        logging.error(f"保存基金 {fund_code} 数据到CSV失败: {e}")
        return None

def download_fund_holdings():
    """下载基金持仓数据的主函数"""
    global fund_download_status, fund_download_progress, stop_fund_download
    
    with fund_download_lock:
        fund_download_status = "进行中"
        fund_download_progress = 0
        stop_fund_download = False
    
    today = date.today()
    date_str = today.strftime('%Y%m%d')
    
    with current_app.app_context():
        total_funds = len(FUND_CODES)
        
        if total_funds == 0:
            logging.info("没有需要下载的基金数据")
            with fund_download_lock:
                fund_download_status = "无数据下载"
            return
        
        logging.info(f"开始下载 {total_funds} 个基金的持仓数据...")
        
        for i, fund in enumerate(FUND_CODES):
            # 检查是否需要停止下载
            with fund_download_lock:
                if stop_fund_download:
                    fund_download_status = "已停止"
                    fund_download_progress = 0
                    return
            
            try:
                fund_code = fund['code']
                fund_name = fund['name']
                
                logging.info(f"正在下载基金: {fund_name} ({fund_code})")
                
                # 下载基金持仓数据
                data = DownloadData.funds_awkward(fund_code)
                
                if data.empty:
                    logging.warning(f"基金 {fund_name} ({fund_code}) 无数据")
                    continue
                
                # 添加基金信息
                data['fund_name'] = fund_name
                data['fund_code'] = fund_code
                data['download_date'] = today.strftime('%Y-%m-%d')
                
                # 保存到CSV文件
                save_success = save_funds_holdings_to_csv(data, today)
                
                if save_success:
                    logging.info(f"成功下载并保存基金 {fund_name} ({fund_code}) 数据")
                else:
                    logging.error(f"基金 {fund_name} ({fund_code}) CSV保存失败")
                
            except Exception as e:
                logging.error(f"下载基金 {fund.get('name', 'Unknown')} ({fund.get('code', 'Unknown')}) 时发生错误: {e}")
                continue
            
            # 更新进度
            with fund_download_lock:
                fund_download_progress = round((i + 1) * (100 / total_funds), 1)
            
            # 延时避免请求过于频繁
            time.sleep(2)
        
        # 下载完成
        with fund_download_lock:
            fund_download_status = "已完成"
            fund_download_progress = 100

@download_fund_bp.route('/fund-holdings-page')
def fund_holdings_page():
    """基金持仓数据下载页面"""
    return render_template('data/download_fund_data.html')

@download_fund_bp.route('/start-fund-holdings', methods=['GET', 'POST'])
def start_fund_holdings():
    """开始基金持仓数据下载"""
    global fund_download_thread
    
    if fund_download_thread is None or not fund_download_thread.is_alive():
        @copy_current_request_context
        def run_download():
            download_fund_holdings()
        
        fund_download_thread = threading.Thread(target=run_download)
        fund_download_thread.start()
        return jsonify({"message": "基金持仓数据下载已开始"}), 200
    else:
        return jsonify({"message": "基金持仓数据下载正在进行中"}), 400

@download_fund_bp.route('/fund-holdings-status', methods=['GET'])
def get_fund_holdings_status():
    """获取基金持仓数据下载状态"""
    return jsonify({
        "status": fund_download_status, 
        "progress": fund_download_progress
    }), 200

@download_fund_bp.route('/stop-fund-holdings', methods=['GET', 'POST'])
def stop_fund_holdings_request():
    """停止基金持仓数据下载"""
    global stop_fund_download
    
    with fund_download_lock:
        stop_fund_download = True
        fund_download_status = "请求停止中"
    
    return jsonify({"message": "基金持仓数据下载已请求停止"}), 200

@download_fund_bp.route('/fund-holdings-statistics', methods=['GET'])
def get_fund_holdings_statistics():
    """获取基金持仓数据下载统计数据"""
    try:
        today = date.today()
        date_str = today.strftime('%Y%m%d')
        table_name = f"fund_holdings_{date_str}"
        
        # 尝试获取今天的基金数据统计
        try:
            df = get_funds_holdings_from_csv(today)
            total_records = len(df) if not df.empty else 0
            unique_funds = df['fund_code'].nunique() if not df.empty else 0
            unique_stocks = df['stock_code'].nunique() if not df.empty else 0
        except:
            total_records = 0
            unique_funds = 0
            unique_stocks = 0
        
        return jsonify({
            "total_funds": len(FUND_CODES),
            "total_records": total_records,
            "unique_funds": unique_funds,
            "unique_stocks": unique_stocks,
            "download_date": today.strftime('%Y-%m-%d')
        }), 200
        
    except Exception as e:
        logging.error(f"获取基金持仓数据下载统计数据时发生错误: {e}")
        return jsonify({"error": str(e)}), 500 