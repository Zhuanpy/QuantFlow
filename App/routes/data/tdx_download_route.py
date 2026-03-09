"""
通达信数据下载和管理路由
支持：
1. 使用 pytdx 从网络下载数据
2. 读取本地通达信软件目录的数据文件
"""
from flask import Blueprint, render_template, request, jsonify
import os
import struct
import pandas as pd
from datetime import datetime
import logging

# 创建蓝图
tdx_bp = Blueprint('tdx_bp', __name__)

# 通达信默认安装路径
DEFAULT_TDX_PATHS = [
    r'C:\new_tdx',
    r'C:\tdx',
    r'D:\new_tdx',
    r'D:\tdx',
    r'E:\new_tdx',
]


def find_tdx_path():
    """自动查找通达信安装路径"""
    for path in DEFAULT_TDX_PATHS:
        if os.path.exists(path):
            vipdoc = os.path.join(path, 'vipdoc')
            if os.path.exists(vipdoc):
                return path
    return None


def parse_tdx_day_file(file_path):
    """
    解析通达信日线数据文件 (.day)

    文件格式：每条记录32字节
    - 日期(4字节整数) YYYYMMDD
    - 开盘价(4字节整数) 单位：分
    - 最高价(4字节整数) 单位：分
    - 最低价(4字节整数) 单位：分
    - 收盘价(4字节整数) 单位：分
    - 成交额(4字节浮点数)
    - 成交量(4字节整数)
    - 保留(4字节)
    """
    records = []

    try:
        with open(file_path, 'rb') as f:
            while True:
                data = f.read(32)
                if len(data) < 32:
                    break

                # 解析数据
                date_int, open_p, high_p, low_p, close_p = struct.unpack('iiiii', data[:20])
                amount, volume = struct.unpack('fi', data[20:28])

                # 转换日期
                date_str = str(date_int)
                try:
                    date = datetime.strptime(date_str, '%Y%m%d')
                except:
                    continue

                records.append({
                    'date': date,
                    'open': open_p / 100.0,
                    'high': high_p / 100.0,
                    'low': low_p / 100.0,
                    'close': close_p / 100.0,
                    'volume': volume,
                    'money': amount
                })
    except Exception as e:
        logging.error(f"解析日线文件失败 {file_path}: {e}")
        return pd.DataFrame()

    return pd.DataFrame(records)


def parse_tdx_lc1_file(file_path):
    """
    解析通达信1分钟/5分钟数据文件 (.lc1/.lc5)

    文件格式：每条记录32字节
    - 日期(2字节) 从1900年开始的天数
    - 时间(2字节) 分钟数
    - 开盘价(4字节浮点数)
    - 最高价(4字节浮点数)
    - 最低价(4字节浮点数)
    - 收盘价(4字节浮点数)
    - 成交额(4字节浮点数)
    - 成交量(4字节整数)
    - 保留(4字节)
    """
    records = []

    try:
        with open(file_path, 'rb') as f:
            while True:
                data = f.read(32)
                if len(data) < 32:
                    break

                # 解析数据
                date_raw, time_raw = struct.unpack('HH', data[:4])
                open_p, high_p, low_p, close_p = struct.unpack('ffff', data[4:20])
                amount, volume = struct.unpack('fi', data[20:28])

                # 转换日期时间
                year = int(date_raw / 2048) + 2004
                month = int((date_raw % 2048) / 100)
                day = date_raw % 2048 % 100
                hour = int(time_raw / 60)
                minute = time_raw % 60

                try:
                    dt = datetime(year, month, day, hour, minute)
                except:
                    continue

                records.append({
                    'date': dt,
                    'open': round(open_p, 2),
                    'high': round(high_p, 2),
                    'low': round(low_p, 2),
                    'close': round(close_p, 2),
                    'volume': volume,
                    'money': round(amount, 2)
                })
    except Exception as e:
        logging.error(f"解析分钟文件失败 {file_path}: {e}")
        return pd.DataFrame()

    return pd.DataFrame(records)


def get_tdx_file_path(tdx_root, stock_code, data_type='day'):
    """
    获取通达信数据文件路径

    Args:
        tdx_root: 通达信安装根目录
        stock_code: 股票代码
        data_type: 数据类型 day/lc1/lc5

    Returns:
        文件路径或None
    """
    # 判断市场
    if stock_code.startswith(('6', '5', '9')):
        market = 'sh'
    else:
        market = 'sz'

    # 构建路径
    if data_type == 'day':
        subdir = 'lday'
        ext = '.day'
    elif data_type == 'lc1':
        subdir = 'fzline'
        ext = '.lc1'
    elif data_type == 'lc5':
        subdir = 'fzline'
        ext = '.lc5'
    else:
        return None

    file_path = os.path.join(tdx_root, 'vipdoc', market, subdir, f'{market}{stock_code}{ext}')

    if os.path.exists(file_path):
        return file_path
    return None


@tdx_bp.route('/tdx')
@tdx_bp.route('/tdx_download_page')
def tdx_download_page():
    """通达信数据管理页面"""
    # 查找通达信安装路径
    tdx_path = find_tdx_path()
    return render_template('data/tdx_download.html', tdx_path=tdx_path)


@tdx_bp.route('/tdx/check_path', methods=['POST'])
def check_tdx_path():
    """检查通达信路径是否有效"""
    data = request.get_json()
    path = data.get('path', '')

    if not path:
        path = find_tdx_path()
        if path:
            return jsonify({'valid': True, 'path': path, 'message': '自动检测到通达信路径'})
        return jsonify({'valid': False, 'message': '未找到通达信安装目录'})

    # 验证路径
    vipdoc = os.path.join(path, 'vipdoc')
    if os.path.exists(vipdoc):
        return jsonify({'valid': True, 'path': path, 'message': '路径有效'})
    return jsonify({'valid': False, 'message': '无效的通达信路径（未找到vipdoc目录）'})


@tdx_bp.route('/tdx/read_local', methods=['POST'])
def read_local_tdx_data():
    """读取本地通达信数据"""
    data = request.get_json()
    tdx_path = data.get('tdx_path', '')
    stock_code = data.get('stock_code', '')
    data_type = data.get('data_type', 'day')  # day/lc1/lc5

    if not tdx_path:
        tdx_path = find_tdx_path()

    if not tdx_path:
        return jsonify({'success': False, 'message': '未找到通达信安装目录'})

    if not stock_code:
        return jsonify({'success': False, 'message': '请输入股票代码'})

    # 获取文件路径
    file_path = get_tdx_file_path(tdx_path, stock_code, data_type)
    if not file_path:
        return jsonify({
            'success': False,
            'message': f'未找到 {stock_code} 的 {data_type} 数据文件'
        })

    # 解析数据
    if data_type == 'day':
        df = parse_tdx_day_file(file_path)
    else:
        df = parse_tdx_lc1_file(file_path)

    if df.empty:
        return jsonify({'success': False, 'message': '数据文件为空或解析失败'})

    # 转换为JSON格式
    df['date'] = df['date'].dt.strftime('%Y-%m-%d %H:%M:%S')
    records = df.to_dict(orient='records')

    return jsonify({
        'success': True,
        'message': f'成功读取 {len(records)} 条记录',
        'data': records[-100:],  # 只返回最近100条
        'total': len(records),
        'file_path': file_path,
        'first_date': records[0]['date'] if records else None,
        'last_date': records[-1]['date'] if records else None
    })


@tdx_bp.route('/tdx/download_pytdx', methods=['POST'])
def download_via_pytdx():
    """使用pytdx下载数据"""
    from App.codes.downloads.DlPytdx import PytdxDownloader, PytdxBoardDownloader

    data = request.get_json()
    stock_code = data.get('stock_code', '')
    days = data.get('days', 5)
    code_type = data.get('code_type', 'stock')  # stock/board

    if not stock_code:
        return jsonify({'success': False, 'message': '请输入代码'})

    try:
        days = int(days)
        days = max(1, min(days, 30))  # 限制1-30天
    except:
        days = 5

    try:
        if code_type == 'board':
            # 板块数据
            with PytdxBoardDownloader() as downloader:
                df, end_date = downloader.get_1m_data(stock_code, days)
        else:
            # 股票数据
            with PytdxDownloader() as downloader:
                df, end_date = downloader.get_1m_data(stock_code, days)

        if df.empty:
            return jsonify({
                'success': False,
                'message': f'未获取到 {stock_code} 的数据（可能是非交易时间或代码无效）'
            })

        # 转换为JSON格式
        df['date'] = df['date'].dt.strftime('%Y-%m-%d %H:%M:%S')
        records = df.to_dict(orient='records')

        return jsonify({
            'success': True,
            'message': f'成功获取 {len(records)} 条记录',
            'data': records[-100:],  # 只返回最近100条预览
            'total': len(records),
            'end_date': str(end_date) if end_date else None,
            'first_date': records[0]['date'] if records else None,
            'last_date': records[-1]['date'] if records else None
        })

    except Exception as e:
        logging.error(f"pytdx下载失败: {e}")
        return jsonify({'success': False, 'message': f'下载失败: {str(e)}'})


@tdx_bp.route('/tdx/save_to_csv', methods=['POST'])
def save_tdx_to_csv():
    """将数据保存到CSV文件"""
    from App.codes.RnnDataFile.save_download import save_1m_to_csv
    from App.codes.downloads.DlPytdx import PytdxDownloader, PytdxBoardDownloader

    data = request.get_json()
    stock_code = data.get('stock_code', '')
    days = data.get('days', 5)
    code_type = data.get('code_type', 'stock')

    if not stock_code:
        return jsonify({'success': False, 'message': '请输入代码'})

    try:
        days = int(days)
        days = max(1, min(days, 30))
    except:
        days = 5

    try:
        if code_type == 'board':
            with PytdxBoardDownloader() as downloader:
                df, end_date = downloader.get_1m_data(stock_code, days)
        else:
            with PytdxDownloader() as downloader:
                df, end_date = downloader.get_1m_data(stock_code, days)

        if df.empty:
            return jsonify({
                'success': False,
                'message': f'未获取到 {stock_code} 的数据'
            })

        # 保存到CSV
        save_1m_to_csv(df, stock_code)

        return jsonify({
            'success': True,
            'message': f'成功下载并保存 {stock_code} 的 {len(df)} 条记录',
            'total': len(df),
            'end_date': str(end_date) if end_date else None
        })

    except Exception as e:
        logging.error(f"保存CSV失败: {e}")
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'})


@tdx_bp.route('/tdx/batch_download', methods=['POST'])
def batch_download_pytdx():
    """批量下载多只股票数据"""
    from App.codes.downloads.DlPytdx import PytdxDownloader
    from App.codes.RnnDataFile.save_download import save_1m_to_csv
    import time

    data = request.get_json()
    stock_codes = data.get('stock_codes', '')
    days = data.get('days', 5)

    if not stock_codes:
        return jsonify({'success': False, 'message': '请输入股票代码'})

    # 解析股票代码列表
    codes = [c.strip() for c in stock_codes.replace('\n', ',').replace(' ', ',').split(',') if c.strip()]

    if not codes:
        return jsonify({'success': False, 'message': '未识别到有效的股票代码'})

    try:
        days = int(days)
        days = max(1, min(days, 30))
    except:
        days = 5

    results = []
    success_count = 0
    fail_count = 0

    with PytdxDownloader() as downloader:
        for code in codes[:20]:  # 限制最多20只
            try:
                df, end_date = downloader.get_1m_data(code, days)

                if not df.empty:
                    save_1m_to_csv(df, code)
                    results.append({
                        'code': code,
                        'success': True,
                        'records': len(df),
                        'end_date': str(end_date) if end_date else None
                    })
                    success_count += 1
                else:
                    results.append({
                        'code': code,
                        'success': False,
                        'message': '无数据'
                    })
                    fail_count += 1

                # 延迟避免请求过快
                time.sleep(0.5)

            except Exception as e:
                results.append({
                    'code': code,
                    'success': False,
                    'message': str(e)
                })
                fail_count += 1

    return jsonify({
        'success': True,
        'message': f'批量下载完成: 成功 {success_count}, 失败 {fail_count}',
        'results': results,
        'success_count': success_count,
        'fail_count': fail_count
    })
