# -*- coding: utf-8 -*-
"""
15分钟数据查看器路由
股票列表从 data_download_records 数据库读取（快速）
具体15m数据从 data/15m/ 文件读取
"""
from flask import Blueprint, render_template, jsonify, request
from App.models.data.basic_info import StockInfo
from App.models.data.Stock1m import DownloadRecord
from App.utils.path_manager import get_path_manager
from App.exts import db
import pandas as pd
import os
import logging

logger = logging.getLogger(__name__)

viewer_15m_bp = Blueprint('viewer_15m', __name__)


def _get_15m_dir() -> str:
    """获取15m数据目录"""
    pm = get_path_manager()
    return str(pm.data_base / '15m')


def _find_15m_file(data_dir: str, stock_code: str) -> str:
    """查找指定股票的15m数据文件，parquet优先"""
    for ext in ['.parquet', '.csv']:
        candidate = os.path.join(data_dir, f'{stock_code}{ext}')
        if os.path.exists(candidate):
            return candidate
    return None


def _read_15m_file(fpath: str) -> pd.DataFrame:
    """读取15m数据文件，支持csv和parquet"""
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
    """获取15m数据总览（从数据库统计）"""
    try:
        DLR = DownloadRecord
        SI = StockInfo

        # 总股票数（有下载记录的）
        stock_count = DLR.query.count()

        # 成功下载的数量
        success_count = DLR.query.filter(DLR.download_status == 'success').count()

        # 日期范围
        date_range = db.session.query(
            db.func.min(DLR.start_date),
            db.func.max(DLR.end_date),
        ).filter(
            DLR.download_status == 'success'
        ).first()

        min_date = date_range[0].strftime('%Y-%m-%d') if date_range and date_range[0] else '-'
        max_date = date_range[1].strftime('%Y-%m-%d') if date_range and date_range[1] else '-'

        # 总记录数（已下载的）
        total_records = db.session.query(
            db.func.sum(DLR.downloaded_records)
        ).filter(DLR.download_status == 'success').scalar() or 0

        avg_records = round(total_records / success_count) if success_count > 0 else 0

        return jsonify({
            'stock_count': stock_count,
            'success_count': success_count,
            'total_records': total_records,
            'min_date': min_date,
            'max_date': max_date,
            'avg_records': avg_records,
        }), 200

    except Exception as e:
        logger.error(f"获取15m数据总览失败: {e}")
        return jsonify({'error': str(e)}), 500


@viewer_15m_bp.route('/api/viewer_15m/stocks', methods=['GET'])
def get_stocks():
    """获取股票列表（从数据库查询，不再遍历文件）"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '', type=str)
        sort_by = request.args.get('sort_by', 'records', type=str)
        per_page = min(per_page, 200)

        DLR = DownloadRecord
        SI = StockInfo

        # 联表查询：download_records JOIN stock_info
        query = db.session.query(
            SI.code,
            SI.name,
            DLR.download_status,
            DLR.end_date,
            DLR.downloaded_records,
            DLR.last_download_time,
        ).join(
            SI, DLR.stock_code_id == SI.id
        )

        # 搜索过滤
        if search:
            query = query.filter(
                db.or_(
                    SI.code.like(f'%{search}%'),
                    SI.name.like(f'%{search}%'),
                )
            )

        # 排序
        if sort_by == 'code':
            query = query.order_by(SI.code)
        elif sort_by == 'date':
            query = query.order_by(DLR.end_date.desc())
        else:  # records
            query = query.order_by(DLR.downloaded_records.desc())

        total = query.count()
        total_pages = (total + per_page - 1) // per_page
        rows = query.offset((page - 1) * per_page).limit(per_page).all()

        stocks = []
        for row in rows:
            stocks.append({
                'code': row.code,
                'name': row.name or '-',
                'status': row.download_status or 'pending',
                'end_date': row.end_date.strftime('%Y-%m-%d') if row.end_date else '-',
                'records': row.downloaded_records or 0,
                'time': row.last_download_time.strftime('%m-%d %H:%M') if row.last_download_time else '-',
            })

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
    """获取指定股票的15m数据（从文件读取）"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 100, type=int)
        start_date = request.args.get('start_date', None)
        end_date = request.args.get('end_date', None)
        per_page = min(per_page, 500)

        data_dir = _get_15m_dir()
        fpath = _find_15m_file(data_dir, stock_code)

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
