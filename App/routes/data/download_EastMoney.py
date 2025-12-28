from flask import render_template, request, redirect, url_for, flash, Blueprint, jsonify
from App.codes.downloads.DlEastMoney import DownloadData
from App.utils.file_utils import get_stock_data_path, get_processed_data_path
import pandas as pd
import logging
import os
import subprocess
import platform
import time

logger = logging.getLogger(__name__)

dl_eastmoney_bp = Blueprint('download_eastmoney_data', __name__)

def check_file_permissions(file_path: str) -> bool:
    """
    检查文件权限
    
    Args:
        file_path: 文件路径
        
    Returns:
        bool: 是否有权限
    """
    try:
        directory = os.path.dirname(file_path)
        
        # 检查目录权限
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        # 检查目录写权限
        if not os.access(directory, os.W_OK):
            logger.error(f"目录没有写权限: {directory}")
            return False
        
        # 如果文件存在，检查文件权限
        if os.path.exists(file_path):
            if not os.access(file_path, os.W_OK):
                logger.error(f"文件没有写权限: {file_path}")
                return False
        
        return True
    except Exception as e:
        logger.error(f"权限检查失败: {e}")
        return False

def fix_file_permissions(file_path: str) -> bool:
    """
    尝试修复文件权限
    
    Args:
        file_path: 文件路径
        
    Returns:
        bool: 是否修复成功
    """
    try:
        if os.path.exists(file_path):
            # 尝试修改文件权限
            os.chmod(file_path, 0o666)  # 读写权限
            logger.info(f"已修改文件权限: {file_path}")
        
        directory = os.path.dirname(file_path)
        if os.path.exists(directory):
            # 尝试修改目录权限
            os.chmod(directory, 0o755)  # 读写执行权限
            logger.info(f"已修改目录权限: {directory}")
        
        return True
    except Exception as e:
        logger.error(f"权限修复失败: {e}")
        return False

def save_stock_data(data: pd.DataFrame, stock_code: str, data_type: str = '1m') -> str:
    """
    保存股票数据到文件
    
    Args:
        data: 股票数据DataFrame
        stock_code: 股票代码
        data_type: 数据类型，默认为'1m'
        
    Returns:
        str: 保存的文件路径
    """
    try:
        # 获取保存路径
        file_path = get_stock_data_path(stock_code, data_type=data_type)
        
        logger.info(f"准备保存数据到: {file_path}")
        
        # 检查权限
        if not check_file_permissions(file_path):
            logger.warning(f"权限检查失败，尝试修复权限: {file_path}")
            if not fix_file_permissions(file_path):
                raise PermissionError(f"无法修复文件权限: {file_path}")
        
        # 检查目录是否存在，如果不存在则创建
        directory = os.path.dirname(file_path)
        if not os.path.exists(directory):
            logger.info(f"创建目录: {directory}")
            os.makedirs(directory, exist_ok=True)
        
        # 检查文件是否被占用
        if os.path.exists(file_path):
            try:
                # 尝试以追加模式打开文件来检查是否被占用
                with open(file_path, 'a'):
                    pass
                logger.info(f"文件 {file_path} 未被占用，可以写入")
            except PermissionError:
                logger.warning(f"文件 {file_path} 被占用，尝试重命名")
                # 如果文件被占用，尝试重命名
                backup_path = f"{file_path}.backup_{int(time.time())}"
                try:
                    os.rename(file_path, backup_path)
                    logger.info(f"原文件已重命名为: {backup_path}")
                except Exception as e:
                    logger.error(f"重命名文件失败: {e}")
                    raise PermissionError(f"文件 {file_path} 被占用且无法重命名: {e}")
        
        # 如果文件存在，读取并合并数据
        if os.path.exists(file_path):
            try:
                existing_data = pd.read_csv(file_path)
                existing_data['date'] = pd.to_datetime(existing_data['date'])
                data['date'] = pd.to_datetime(data['date'])
                # 合并数据，新数据放在后面，这样去重时会保留最新的数据
                combined_data = pd.concat([existing_data, data]).drop_duplicates(subset=['date'], keep='last')
                combined_data = combined_data.sort_values('date')
            except Exception as e:
                logger.warning(f"读取现有文件失败，将覆盖文件: {e}")
                combined_data = data
        else:
            combined_data = data
        
        # 保存数据
        combined_data.to_csv(file_path, index=False)
        logger.info(f"成功保存数据: {stock_code}, 类型: {data_type}, 路径: {file_path}")
        
        return file_path
        
    except PermissionError as e:
        logger.error(f"权限错误: {stock_code}, 类型: {data_type}, 错误: {str(e)}")
        raise PermissionError(f"没有权限写入文件 {file_path}: {str(e)}")
    except Exception as e:
        logger.error(f"保存数据失败: {stock_code}, 类型: {data_type}, 错误: {str(e)}")
        raise

@dl_eastmoney_bp.route('/download_stock_1m_close_data_today_eastmoney', methods=['GET', 'POST'])
def download_stock_1m_close_data_today_eastmoney():
    if request.method == 'POST':
        stock_code = request.form.get('stock_code')
        logger.info(f"开始下载股票数据: {stock_code}")

        if not stock_code:
            flash('请填写完整信息！')
            return redirect(url_for('download_eastmoney_data.download_stock_1m_close_data_today_eastmoney'))
        
        try:
            # 使用完整的下载流程（包含15分钟数据转换）
            from App.codes.RnnDataFile.save_download import complete_download_process
            
            result = complete_download_process(stock_code, days=1)
            
            if result['success']:
                flash(f"成功完成 {stock_code} 的完整下载流程: {result['message']}", "success")
                
                # 获取各种数据类型的文件路径
                file_path_1m = get_stock_data_path(stock_code, data_type='1m')
                file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')
                file_path_daily = get_stock_data_path(stock_code, data_type='daily')
                
                # 从数据库查询刚刚保存的日线数据
                daily_data_from_db = None
                try:
                    from App.models.data.StockDaily import StockDaily
                    from datetime import datetime, timedelta
                    
                    # 查询最近7天的日线数据
                    end_date = datetime.now().date()
                    start_date = end_date - timedelta(days=7)
                    
                    daily_records = StockDaily.query.filter(
                        StockDaily.stock_code == stock_code,
                        StockDaily.date >= start_date,
                        StockDaily.date <= end_date
                    ).order_by(StockDaily.date.desc()).all()
                    
                    if daily_records:
                        # 转换为DataFrame用于显示
                        import pandas as pd
                        daily_data_list = []
                        for record in daily_records:
                            daily_data_list.append({
                                'date': record.date.strftime('%Y-%m-%d'),
                                'open': f"{record.open:.2f}",
                                'close': f"{record.close:.2f}",
                                'high': f"{record.high:.2f}",
                                'low': f"{record.low:.2f}",
                                'volume': f"{record.volume:,}",
                                'money': f"{record.money:,}"
                            })
                        
                        daily_data_from_db = pd.DataFrame(daily_data_list)
                        logger.info(f"成功查询到 {len(daily_records)} 条日线数据用于验证")
                    else:
                        logger.warning(f"未找到 {stock_code} 的日线数据")
                        
                except Exception as e:
                    logger.error(f"查询日线数据失败: {str(e)}")
                
                # 准备显示信息
                data_info = result.get('data_info', {})
                display_message = f"""
                下载完成！处理结果：
                • 1分钟数据: {data_info.get('1m_records', 0)} 条
                • 15分钟数据: {data_info.get('15m_records', 0)} 条  
                • 日线数据: {data_info.get('daily_records', 0)} 条
                """
                
                return render_template('data/success.html', 
                                     data_html=display_message, 
                                     file_path=file_path_1m,
                                     file_path_15m=file_path_15m,
                                     file_path_daily=file_path_daily,
                                     stock_code=stock_code,
                                     daily_data_from_db=daily_data_from_db)
            else:
                flash(f"下载流程部分失败: {result['message']}", "warning")
                return redirect(url_for('download_eastmoney_data.download_stock_1m_close_data_today_eastmoney'))
            
        except Exception as e:
            logger.error(f"下载数据失败: {stock_code}, 错误: {str(e)}")
            flash(f'下载失败: {str(e)}')
            return redirect(url_for('download_eastmoney_data.download_stock_1m_close_data_today_eastmoney'))
    
    # 处理 GET 请求
    return render_template('data/股票下载.html')

@dl_eastmoney_bp.route('/download_stock_1m_5days_data', methods=['GET', 'POST'])
def download_stock_1m_5days_data():
    if request.method == 'POST':
        stock_code = request.form.get('stock_code')
        logger.info(f"开始下载5天股票数据: {stock_code}")

        if not stock_code:
            flash('请填写完整信息！')
            return redirect(url_for('download_eastmoney_data.download_stock_1m_close_data_today_eastmoney'))
        
        try:
            # 下载数据
            data = DownloadData.stock_1m_days(stock_code)
            
            # 保存数据
            file_path = save_stock_data(data, stock_code, '1m')
            
            flash('数据获取并保存成功！')
            data_html = data.to_html(classes='table table-striped', index=False)
            return render_template('data/success.html', 
                                 data_html=data_html, 
                                 file_path=file_path,
                                 stock_code=stock_code)
            
        except Exception as e:
            logger.error(f"下载数据失败: {stock_code}, 错误: {str(e)}")
            flash(f'下载失败: {str(e)}')
            return redirect(url_for('download_eastmoney_data.download_stock_1m_5days_data'))

@dl_eastmoney_bp.route('/download_board_1m_close_data_today', methods=['GET', 'POST'])
def download_board_1m_close_data_today():
    if request.method == 'POST':
        board_code = request.form.get('stock_code')
        logger.info(f"开始下载板块数据: {board_code}")

        if not board_code:
            flash('请填写完整信息！')
            return redirect(url_for('download_eastmoney_data.download_board_1m_close_data_today'))
        
        try:
            # 下载数据
            data = DownloadData.board_1m_data(board_code)
            
            # 保存数据
            file_path = save_stock_data(data, board_code, 'board_1m')
            
            flash('数据获取并保存成功！')
            data_html = data.to_html(classes='table table-striped', index=False)
            return render_template('data/success.html', 
                                 data_html=data_html, 
                                 file_path=file_path,
                                 stock_code=board_code)
            
        except Exception as e:
            logger.error(f"下载数据失败: {board_code}, 错误: {str(e)}")
            flash(f'下载失败: {str(e)}')
            return redirect(url_for('download_eastmoney_data.download_board_1m_close_data_today'))

@dl_eastmoney_bp.route('/download_board_1m_close_data_multiple_days', methods=['GET', 'POST'])
def download_board_1m_close_data_multiple_days():
    if request.method == 'POST':
        board_code = request.form.get('stock_code')
        logger.info(f"开始下载多日板块数据: {board_code}")

        if not board_code:
            flash('请填写完整信息！')
            return redirect(url_for('download_eastmoney_data.download_board_1m_close_data_today'))
        
        try:
            # 下载数据
            data = DownloadData.board_1m_multiple(board_code)
            
            # 保存数据
            file_path = save_stock_data(data, board_code, 'board_1m')
            
            flash('数据获取并保存成功！')
            data_html = data.to_html(classes='table table-striped', index=False)
            return render_template('data/success.html', 
                                 data_html=data_html, 
                                 file_path=file_path,
                                 stock_code=board_code)
            
        except Exception as e:
            logger.error(f"下载数据失败: {board_code}, 错误: {str(e)}")
            flash(f'下载失败: {str(e)}')
            return redirect(url_for('download_eastmoney_data.download_board_1m_close_data_multiple_days'))

@dl_eastmoney_bp.route('/download_funds_awkward_data', methods=['GET', 'POST'])
def download_funds_awkward_data():
    if request.method == 'POST':
        fund_code = request.form.get('stock_code')
        logger.info(f"开始下载基金数据: {fund_code}")

        if not fund_code:
            flash('请填写完整信息！')
            return redirect(url_for('download_eastmoney_data.download_board_1m_close_data_today'))
        
        try:
            # 下载数据
            data = DownloadData.funds_awkward(fund_code)
            
            # 保存数据
            file_path = get_stock_data_path(fund_code, data_type='funds')
            data.to_csv(file_path, index=False)
            
            flash('数据获取并保存成功！')
            data_html = data.to_html(classes='table table-striped', index=False)
            return render_template('data/success.html', 
                                 data_html=data_html, 
                                 file_path=file_path,
                                 stock_code=fund_code)
            
        except Exception as e:
            logger.error(f"下载数据失败: {fund_code}, 错误: {str(e)}")
            flash(f'下载失败: {str(e)}')
            return redirect(url_for('download_eastmoney_data.download_funds_awkward_data'))

@dl_eastmoney_bp.route('/download_funds_awkward_data_by_driver', methods=['GET', 'POST'])
def download_funds_awkward_data_by_driver():
    if request.method == 'POST':
        fund_code = request.form.get('stock_code')
        logger.info(f"开始下载基金数据(driver): {fund_code}")

        if not fund_code:
            flash('请填写完整信息！')
            return redirect(url_for('download_eastmoney_data.download_board_1m_close_data_today'))
        
        try:
            # 下载数据
            data = DownloadData.funds_awkward_by_driver(fund_code)
            
            # 保存数据
            file_path = get_stock_data_path(fund_code, data_type='funds')
            data.to_csv(file_path, index=False)
            
            flash('数据获取并保存成功！')
            data_html = data.to_html(classes='table table-striped', index=False)
            return render_template('data/success.html', 
                                 data_html=data_html, 
                                 file_path=file_path,
                                 stock_code=fund_code)
            
        except Exception as e:
            logger.error(f"下载数据失败: {fund_code}, 错误: {str(e)}")
            flash(f'下载失败: {str(e)}')
            return redirect(url_for('download_eastmoney_data.download_funds_awkward_data_by_driver'))


@dl_eastmoney_bp.route('/api/open-folder', methods=['POST'])
def open_folder():
    """
    打开文件夹API
    """
    try:
        data = request.get_json()
        folder_path = data.get('path')
        
        logger.info(f"收到打开文件夹请求: {folder_path}")
        
        if not folder_path:
            logger.warning("路径为空")
            return jsonify({'success': False, 'message': '路径不能为空'}), 400
        
        # 规范化路径
        folder_path = os.path.normpath(folder_path)
        logger.info(f"规范化后的路径: {folder_path}")
        
        if not os.path.exists(folder_path):
            logger.warning(f"文件夹不存在: {folder_path}")
            return jsonify({'success': False, 'message': f'文件夹不存在: {folder_path}'}), 404
        
        if not os.path.isdir(folder_path):
            logger.warning(f"路径不是文件夹: {folder_path}")
            return jsonify({'success': False, 'message': f'路径不是文件夹: {folder_path}'}), 400
        
        # 根据操作系统选择打开命令
        system = platform.system()
        logger.info(f"操作系统: {system}")
        
        if system == 'Windows':
            logger.info(f"使用Windows explorer打开: {folder_path}")
            subprocess.run(['explorer', folder_path], check=True)
        elif system == 'Darwin':  # macOS
            logger.info(f"使用macOS open打开: {folder_path}")
            subprocess.run(['open', folder_path], check=True)
        elif system == 'Linux':
            logger.info(f"使用Linux xdg-open打开: {folder_path}")
            subprocess.run(['xdg-open', folder_path], check=True)
        else:
            logger.error(f"不支持的操作系统: {system}")
            return jsonify({'success': False, 'message': f'不支持的操作系统: {system}'}), 400
        
        logger.info(f"成功打开文件夹: {folder_path}")
        return jsonify({'success': True, 'message': '文件夹已打开'}), 200
        
    except subprocess.CalledProcessError as e:
        logger.error(f"打开文件夹失败: {e}")
        return jsonify({'success': False, 'message': f'打开文件夹失败: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"打开文件夹异常: {e}")
        return jsonify({'success': False, 'message': f'打开文件夹异常: {str(e)}'}), 500


@dl_eastmoney_bp.route('/api/open-file', methods=['POST'])
def open_file():
    """
    打开文件API
    """
    try:
        data = request.get_json()
        file_path = data.get('path')
        
        logger.info(f"收到打开文件请求: {file_path}")
        
        if not file_path:
            logger.warning("路径为空")
            return jsonify({'success': False, 'message': '路径不能为空'}), 400
        
        # 规范化路径
        file_path = os.path.normpath(file_path)
        logger.info(f"规范化后的路径: {file_path}")
        
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return jsonify({'success': False, 'message': f'文件不存在: {file_path}'}), 404
        
        if not os.path.isfile(file_path):
            logger.warning(f"路径不是文件: {file_path}")
            return jsonify({'success': False, 'message': f'路径不是文件: {file_path}'}), 400
        
        # 根据操作系统选择打开命令
        system = platform.system()
        logger.info(f"操作系统: {system}")
        
        if system == 'Windows':
            logger.info(f"使用Windows start打开: {file_path}")
            subprocess.run(['start', '', file_path], shell=True, check=True)
        elif system == 'Darwin':  # macOS
            logger.info(f"使用macOS open打开: {file_path}")
            subprocess.run(['open', file_path], check=True)
        elif system == 'Linux':
            logger.info(f"使用Linux xdg-open打开: {file_path}")
            subprocess.run(['xdg-open', file_path], check=True)
        else:
            logger.error(f"不支持的操作系统: {system}")
            return jsonify({'success': False, 'message': f'不支持的操作系统: {system}'}), 400
        
        logger.info(f"成功打开文件: {file_path}")
        return jsonify({'success': True, 'message': '文件已打开'}), 200
        
    except subprocess.CalledProcessError as e:
        logger.error(f"打开文件失败: {e}")
        return jsonify({'success': False, 'message': f'打开文件失败: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"打开文件异常: {e}")
        return jsonify({'success': False, 'message': f'打开文件异常: {str(e)}'}), 500


@dl_eastmoney_bp.route('/api/check-permissions', methods=['POST'])
def check_permissions():
    """
    检查文件权限API
    """
    try:
        data = request.get_json()
        file_path = data.get('path')
        
        if not file_path:
            return jsonify({'success': False, 'message': '路径不能为空'}), 400
        
        # 检查权限
        has_permission = check_file_permissions(file_path)
        
        if has_permission:
            return jsonify({
                'success': True, 
                'message': '权限正常',
                'has_permission': True
            }), 200
        else:
            # 尝试修复权限
            fixed = fix_file_permissions(file_path)
            return jsonify({
                'success': fixed,
                'message': '权限修复成功' if fixed else '权限修复失败',
                'has_permission': fixed
            }), 200 if fixed else 500
        
    except Exception as e:
        logger.error(f"权限检查异常: {e}")
        return jsonify({'success': False, 'message': f'权限检查异常: {str(e)}'}), 500


@dl_eastmoney_bp.route('/path_test', methods=['GET'])
def path_test():
    """
    路径测试页面
    """
    return render_template('data/path_test.html')
