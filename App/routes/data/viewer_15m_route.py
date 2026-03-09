# -*- coding: utf-8 -*-
"""
15分钟数据查看器路由
读取 data/15m/ 目录下的 CSV 文件展示15分钟K线 + MACD信号数据
"""
from flask import Blueprint, render_template, jsonify, request
from App.models.data.basic_info import StockInfo
from App.utils.path_manager import get_path_manager
import pandas as pd
import os
import logging

logger = logging.getLogger(__name__)

viewer_15m_bp = Blueprint('viewer_15m', __name__)

def _get_15m_dir() -> str:
    """获取15m数据目录"""
    pm = get_path_manager()
    return str(pm.data_base / '15m')


def _list_15m_files(data_dir: str) -> list:
    """列出15m目录下所有数据文件（csv和parquet），同一股票优先csv"""
    if not os.path.isdir(data_dir):
        return []
    files = {}
    for f in os.listdir(data_dir):
        if f.endswith('.csv') or f.endswith('.parquet'):
            code = f.rsplit('.', 1)[0]
            ext = f.rsplit('.', 1)[1]
            # csv 优先（因为 parquet 可能无法读取）
            if code not in files or ext == 'csv':
                files[code] = f
    return list(files.values())


def _read_15m_file(fpath: str) -> pd.DataFrame:
    """读取15m数据文件，支持csv和parquet，parquet失败时返回空DataFrame"""
    if fpath.endswith('.csv'):
        return pd.read_csv(fpath, parse_dates=['date'])
    else:
        try:
            return pd.read_parquet(fpath)
        except ImportError:
            logger.warning(f"pyarrow未安装，无法读取parquet文件: {fpath}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"读取parquet失败: {fpath}, {e}")
            return pd.DataFrame()


@viewer_15m_bp.route('/viewer_15m')
def viewer_15m_page():
    """15分钟数据查看器页面"""
    return render_template('data/viewer_15m.html')


@viewer_15m_bp.route('/api/viewer_15m/overview', methods=['GET'])
def get_overview():
    """获取15m数据总览"""
    try:
        data_dir = _get_15m_dir()
        files = _list_15m_files(data_dir)
        if not files:
            return jsonify({'stock_count': 0, 'total_records': 0,
                            'min_date': '-', 'max_date': '-',
                            'avg_records': 0}), 200

        stock_count = len(files)

        # 采样几个文件获取日期范围和平均记录数
        total_records = 0
        global_min = None
        global_max = None
        sample_files = files[:50] if len(files) > 50 else files

        for fname in sample_files:
            fpath = os.path.join(data_dir, fname)
            try:
                df = _read_15m_file(fpath)
                if df.empty or 'date' not in df.columns:
                    continue
                df['date'] = pd.to_datetime(df['date'])
                total_records += len(df)
                fmin, fmax = df['date'].min(), df['date'].max()
                if global_min is None or fmin < global_min:
                    global_min = fmin
                if global_max is None or fmax > global_max:
                    global_max = fmax
            except Exception:
                continue

        # 估算总记录数
        if len(sample_files) < len(files) and len(sample_files) > 0:
            avg_per_file = total_records / len(sample_files)
            total_records = int(avg_per_file * len(files))

        avg_records = round(total_records / stock_count) if stock_count > 0 else 0

        return jsonify({
            'stock_count': stock_count,
            'total_records': total_records,
            'min_date': global_min.strftime('%Y-%m-%d') if global_min else '-',
            'max_date': global_max.strftime('%Y-%m-%d %H:%M') if global_max else '-',
            'avg_records': avg_records,
        }), 200

    except Exception as e:
        logger.error(f"获取15m数据总览失败: {e}")
        return jsonify({'error': str(e)}), 500


@viewer_15m_bp.route('/api/viewer_15m/stocks', methods=['GET'])
def get_stocks():
    """获取所有有15m数据的股票列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '', type=str)
        sort_by = request.args.get('sort_by', 'records', type=str)
        per_page = min(per_page, 200)

        data_dir = _get_15m_dir()
        files = _list_15m_files(data_dir)
        if not files:
            return jsonify({'stocks': [], 'total': 0, 'page': 1,
                            'per_page': per_page, 'total_pages': 0}), 200

        # 构建股票列表
        stock_list = []
        for fname in files:
            code = fname.rsplit('.', 1)[0]
            fpath = os.path.join(data_dir, fname)
            try:
                fsize = os.path.getsize(fpath)
                df = _read_15m_file(fpath)
                if not df.empty and 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'])
                    records = len(df)
                    start_date = df['date'].min().strftime('%Y-%m-%d')
                    end_date = df['date'].max().strftime('%m-%d %H:%M')
                else:
                    records = 0
                    start_date = '-'
                    end_date = '-'
            except Exception:
                records = 0
                start_date = '-'
                end_date = '-'
                fsize = 0

            # 获取股票名称
            info = StockInfo.query.filter_by(code=code).first()
            name = info.name if info else '-'

            stock_list.append({
                'code': code,
                'name': name,
                'records': records,
                'start_date': start_date,
                'end_date': end_date,
                'size_kb': round(fsize / 1024, 1),
            })

        # 搜索过滤
        if search:
            search_lower = search.lower()
            stock_list = [s for s in stock_list
                          if search_lower in s['code'].lower() or search_lower in s['name'].lower()]

        # 排序
        if sort_by == 'code':
            stock_list.sort(key=lambda x: x['code'])
        elif sort_by == 'date':
            stock_list.sort(key=lambda x: x['end_date'], reverse=True)
        else:
            stock_list.sort(key=lambda x: x['records'], reverse=True)

        total = len(stock_list)
        total_pages = (total + per_page - 1) // per_page
        start = (page - 1) * per_page
        stocks = stock_list[start:start + per_page]

        return jsonify({
            'stocks': stocks,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
        }), 200

    except Exception as e:
        logger.error(f"获取15m股票列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@viewer_15m_bp.route('/api/viewer_15m/stock/<stock_code>', methods=['GET'])
def get_stock_15m_data(stock_code):
    """获取指定股票的15m数据"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 100, type=int)
        start_date = request.args.get('start_date', None)
        end_date = request.args.get('end_date', None)
        per_page = min(per_page, 500)

        data_dir = _get_15m_dir()
        # 优先找csv，再找parquet
        fpath = None
        for ext in ['.csv', '.parquet']:
            candidate = os.path.join(data_dir, f'{stock_code}{ext}')
            if os.path.exists(candidate):
                fpath = candidate
                break

        if not fpath:
            return jsonify({'error': f'未找到 {stock_code} 的15m数据文件'}), 404

        df = _read_15m_file(fpath)
        if df.empty:
            return jsonify({'error': f'无法读取 {stock_code} 的15m数据（可能需要安装pyarrow）'}), 500
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date', ascending=False).reset_index(drop=True)

        # 日期过滤
        if start_date:
            df = df[df['date'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['date'] <= pd.to_datetime(end_date)]

        total = len(df)
        total_pages = (total + per_page - 1) // per_page

        # 分页
        start = (page - 1) * per_page
        page_df = df.iloc[start:start + per_page]

        def _r(val, n=2):
            try:
                if pd.isna(val):
                    return None
                return round(float(val), n)
            except (TypeError, ValueError):
                return None

        data = []
        for _, row in page_df.iterrows():
            data.append({
                'date': row['date'].strftime('%Y-%m-%d %H:%M') if pd.notna(row['date']) else '-',
                'open': _r(row.get('open')),
                'close': _r(row.get('close')),
                'high': _r(row.get('high')),
                'low': _r(row.get('low')),
                'volume': int(row.get('volume', 0)) if pd.notna(row.get('volume')) else 0,
                'money': _r(row.get('money', 0), 0),
                # MACD
                'dif': _r(row.get('Dif'), 4),
                'dea': _r(row.get('Dea'), 4),
                'macd': _r(row.get('MACD'), 4),
                # Signal
                'signal': int(row.get('Signal', 0)) if pd.notna(row.get('Signal')) else 0,
                'signal_choice': row.get('SignalChoice', '') if pd.notna(row.get('SignalChoice', '')) else '',
                'signal_start': str(row.get('SignalStartIndex', '')) if pd.notna(row.get('SignalStartIndex', '')) else '',
                # Boll
                'boll_mid': _r(row.get('BollMid')),
                'boll_up': _r(row.get('BollUp')),
                'boll_dn': _r(row.get('BollDn')),
                'stop_loss': _r(row.get('StopLoss')),
                # Cycle
                'end_price': _r(row.get('EndPrice')),
                'start_price': _r(row.get('StartPrice')),
                'cyc_amp_bar': _r(row.get('CycleAmplitudePerBar'), 4),
                'cyc_amp_max': _r(row.get('CycleAmplitudeMax'), 4),
                'cyc_len_bar': int(row.get('CycleLengthPerBar', 0)) if pd.notna(row.get('CycleLengthPerBar')) else 0,
                'cyc_len_max': int(row.get('CycleLengthMax', 0)) if pd.notna(row.get('CycleLengthMax')) else 0,
                # Volume
                'vol_max1': int(row.get('Daily1mVolMax1', 0)) if pd.notna(row.get('Daily1mVolMax1')) else 0,
                'vol_max5': int(row.get('Daily1mVolMax5', 0)) if pd.notna(row.get('Daily1mVolMax5')) else 0,
            })

        # 股票名称
        info = StockInfo.query.filter_by(code=stock_code).first()
        stock_name = info.name if info else '-'

        return jsonify({
            'stock_code': stock_code,
            'stock_name': stock_name,
            'data': data,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
        }), 200

    except Exception as e:
        logger.error(f"获取 {stock_code} 15m数据失败: {e}")
        return jsonify({'error': str(e)}), 500
