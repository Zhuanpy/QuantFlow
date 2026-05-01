# -*- coding: utf-8 -*-
"""
个股详情页路由

页面：
    GET  /stock/<code>                          详情页

API：
    GET  /stock/api/<code>/overview             基础信息（名称/分类/最近价/基金数）
    GET  /stock/api/<code>/15m?start=&end=      15m K 线数据
    GET  /stock/api/<code>/1m?date=YYYY-MM-DD   单日 1m 数据
    GET  /stock/api/<code>/1m/dates             有 1m 数据的所有日期列表
    GET  /stock/api/<code>/params?month=        标准化参数（指定月份）
    GET  /stock/api/<code>/params/months        有标准化参数的所有月份列表
    GET  /stock/api/<code>/metrics?month=       指标摘要
    GET  /stock/api/<code>/predictions?limit=   预测记录
    GET  /stock/api/<code>/fund_holdings?days=  基金持仓时序
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Blueprint, jsonify, render_template, request

from App.exts import db
from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.models.data.basic_info import StockInfo, StockClassification
from App.models.data.StockDaily import StockDaily
from App.models.strategy.RnnRunningRecords import RnnRunningRecord

logger = logging.getLogger(__name__)

stock_detail_bp = Blueprint(
    'stock_detail_bp', __name__, url_prefix='/stock'
)


# ==================== 页面 ====================
@stock_detail_bp.route('/<code>')
def stock_detail_page(code: str):
    return render_template('data/stock_view.html', stock_code=code)


# ==================== 工具函数 ====================
def _project_root() -> Path:
    from config import Config
    return Path(Config.get_project_root())


def _safe_jsonable(v):
    """将 numpy/pandas 类型转换为 Python 原生 JSON 可序列化类型"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, 'item'):
        return v.item()
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


# ==================== API ====================
@stock_detail_bp.route('/api/<code>/overview', methods=['GET'])
def api_overview(code):
    """股票基础信息 + 最新价 + 基金持仓数"""
    info = StockInfo.query.filter_by(code=code).first()
    classification = StockClassification.query.filter_by(code=code).first()
    latest_daily = (StockDaily.query
                    .filter_by(stock_code=code)
                    .order_by(StockDaily.date.desc()).first())

    return jsonify({
        'success': True,
        'data': {
            'code': code,
            'name': info.name if info else None,
            'classification': classification.classification if classification else None,
            'es_code': info.EsCode if info else None,
            'market_code': info.MarketCode if info else None,
            'latest_close': latest_daily.close if latest_daily else None,
            'latest_date': latest_daily.date.isoformat() if latest_daily and latest_daily.date else None,
            'fund_holdings_count': latest_daily.fund_holdings_count if latest_daily else None,
            'fund_holdings_ratio': latest_daily.fund_holdings_ratio if latest_daily else None,
        }
    })


@stock_detail_bp.route('/api/<code>/15m', methods=['GET'])
def api_15m(code):
    """15m K 线数据，可按时间范围过滤"""
    start = request.args.get('start')
    end = request.args.get('end')
    try:
        limit = max(50, min(int(request.args.get('limit', 2000)), 30000))
    except ValueError:
        limit = 2000

    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} 的 15m 数据'}), 404

    df['date'] = pd.to_datetime(df['date'])
    if start:
        df = df[df['date'] >= pd.to_datetime(start)]
    if end:
        df = df[df['date'] <= pd.to_datetime(end)]

    # 取最后 limit 行（避免一次返回几万条）
    df = df.tail(limit)
    records = []
    for _, row in df.iterrows():
        records.append({
            'date': row['date'].strftime('%Y-%m-%d %H:%M:%S'),
            'open': float(row['open']),
            'close': float(row['close']),
            'high': float(row['high']),
            'low': float(row['low']),
            'volume': int(row['volume']) if pd.notna(row.get('volume')) else 0,
        })
    return jsonify({
        'success': True,
        'count': len(records),
        'first_date': records[0]['date'] if records else None,
        'last_date': records[-1]['date'] if records else None,
        'data': records,
    })


def _list_1m_dates(code: str):
    """扫描 data/data/quarters 和 data/quarters，返回该股票所有可用 1m 日期"""
    root = _project_root()
    candidates = [
        root / 'data' / 'data' / 'quarters',
        root / 'data' / 'quarters',
    ]
    dates = set()
    for base in candidates:
        if not base.exists():
            continue
        # parquet 一个文件 = 一个季度，得读出来才知道日期
        for parquet in base.rglob(f'{code}.parquet'):
            try:
                df = pd.read_parquet(parquet, columns=['date'])
                df['date'] = pd.to_datetime(df['date'])
                for d in df['date'].dt.date.unique():
                    dates.add(d.isoformat())
            except Exception as e:
                logger.warning(f'读 {parquet} 失败: {e}')
    return sorted(dates, reverse=True)


@stock_detail_bp.route('/api/<code>/1m/dates', methods=['GET'])
def api_1m_dates(code):
    dates = _list_1m_dates(code)
    return jsonify({
        'success': True,
        'count': len(dates),
        'dates': dates[:200],     # 返回最近 200 天，避免一次太多
        'earliest': dates[-1] if dates else None,
        'latest': dates[0] if dates else None,
    })


@stock_detail_bp.route('/api/<code>/1m', methods=['GET'])
def api_1m(code):
    """单日的 1m 数据"""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'success': False, 'message': '需要 date 参数 (YYYY-MM-DD)'}), 400

    try:
        target = pd.to_datetime(date_str).date()
    except Exception:
        return jsonify({'success': False, 'message': f'date 格式错误: {date_str}'}), 400

    # 找包含该日期的季度 parquet
    quarter = (target.month - 1) // 3 + 1
    year = target.year
    root = _project_root()
    candidates = [
        root / 'data' / 'data' / 'quarters' / str(year) / f'Q{quarter}' / f'{code}.parquet',
        root / 'data' / 'quarters' / str(year) / f'Q{quarter}' / f'{code}.parquet',
        root / 'data' / 'data' / 'quarters' / str(year) / f'Q{quarter}' / f'{code}.csv',
    ]
    df = None
    for path in candidates:
        if path.exists():
            try:
                df = pd.read_parquet(path) if path.suffix == '.parquet' else pd.read_csv(path)
                break
            except Exception:
                continue

    if df is None or df.empty:
        return jsonify({'success': False, 'message': f'未找到 {code} {date_str} 的 1m 数据'}), 404

    df['date'] = pd.to_datetime(df['date'])
    df = df[df['date'].dt.date == target]
    if df.empty:
        return jsonify({'success': False, 'message': f'当日无数据: {date_str}'}), 404

    records = []
    for _, row in df.iterrows():
        records.append({
            'time': row['date'].strftime('%H:%M'),
            'open': float(row['open']),
            'close': float(row['close']),
            'high': float(row['high']),
            'low': float(row['low']),
            'volume': int(row['volume']) if pd.notna(row.get('volume')) else 0,
        })
    return jsonify({
        'success': True,
        'date': date_str,
        'count': len(records),
        'data': records,
    })


def _list_param_months(code: str):
    """扫描 data/RnnData/<month>/json/<code>.json，返回有该股票参数的月份列表"""
    root = _project_root() / 'data' / 'RnnData'
    months = []
    if not root.exists():
        return months
    for sub in root.iterdir():
        if not sub.is_dir() or sub.name == 'CommonFile':
            continue
        if (sub / 'json' / f'{code}.json').exists():
            months.append(sub.name)
    months.sort(reverse=True)
    return months


@stock_detail_bp.route('/api/<code>/params/months', methods=['GET'])
def api_param_months(code):
    months = _list_param_months(code)
    return jsonify({'success': True, 'count': len(months), 'months': months})


@stock_detail_bp.route('/api/<code>/params', methods=['GET'])
def api_params(code):
    """指定月份的标准化参数 + 所有非字典字段一并返回"""
    month = request.args.get('month')
    if not month:
        # 默认最新
        ms = _list_param_months(code)
        if not ms:
            return jsonify({'success': False, 'message': f'未找到 {code} 任何月份的标准化参数'}), 404
        month = ms[0]

    json_path = StockDataPath.json_data_path(month, code)
    if not os.path.exists(json_path):
        return jsonify({'success': False, 'message': f'未找到参数文件: {json_path}'}), 404

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 拆分：归一化字段（含 num_max/num_min） vs 标量字段
    normalized = {}
    scalars = {}
    for k, v in data.items():
        if isinstance(v, dict) and 'num_max' in v:
            normalized[k] = v
        else:
            scalars[k] = v

    return jsonify({
        'success': True,
        'month': month,
        'normalized_fields_count': len(normalized),
        'normalized_fields': normalized,
        'other_fields': scalars,
    })


@stock_detail_bp.route('/api/<code>/metrics', methods=['GET'])
def api_metrics(code):
    """关键指标摘要：从最近月份参数 + 15m 行情 综合"""
    # 1) 最近月份参数
    months = _list_param_months(code)
    params = {}
    if months:
        try:
            with open(StockDataPath.json_data_path(months[0], code), 'r', encoding='utf-8') as f:
                params = json.load(f)
        except Exception:
            params = {}

    # 2) 15m 数据计算近期统计
    df = StockData15m.load_15m(code)
    if df.empty:
        return jsonify({'success': True, 'message': '无 15m 数据', 'params_month': months[0] if months else None, 'metrics': {}})

    df['date'] = pd.to_datetime(df['date'])
    df['amplitude'] = (df['close'] - df['close'].shift(1)) / df['close'].shift(1)

    # 近 30 / 90 个交易日（约）
    recent_30d = df[df['date'] >= pd.Timestamp.now() - pd.Timedelta(days=45)]
    recent_90d = df[df['date'] >= pd.Timestamp.now() - pd.Timedelta(days=130)]

    def _pick(p, key, sub):
        try:
            return p[key][sub]
        except (KeyError, TypeError):
            return None

    metrics = {
        # 来自 JSON 参数（标准化 max/min，反映模型见过的范围）
        'volume_param_max': _pick(params, 'volume', 'num_max'),
        'volume_param_min': _pick(params, 'volume', 'num_min'),
        'cycle_length_max_param': _pick(params, 'CycleLengthMax', 'num_max'),
        'cycle_amplitude_max_param': _pick(params, 'CycleAmplitudeMax', 'num_max'),
        'cycle_amplitude_min_param': _pick(params, 'CycleAmplitudeMax', 'num_min'),
        'daily_vol_ema_param': params.get('DailyVolEma'),
        # 来自实时 15m 计算
        'volume_max_recent_30d': float(recent_30d['volume'].max()) if not recent_30d.empty else None,
        'volume_mean_recent_30d': float(recent_30d['volume'].mean()) if not recent_30d.empty else None,
        'amplitude_max_recent_90d': float(recent_90d['amplitude'].max()) if not recent_90d.empty else None,
        'amplitude_min_recent_90d': float(recent_90d['amplitude'].min()) if not recent_90d.empty else None,
        'close_latest': float(df['close'].iloc[-1]),
        'close_max_recent_90d': float(recent_90d['close'].max()) if not recent_90d.empty else None,
        'close_min_recent_90d': float(recent_90d['close'].min()) if not recent_90d.empty else None,
        'rows_15m': len(df),
        'first_date_15m': df['date'].min().strftime('%Y-%m-%d'),
        'last_date_15m': df['date'].max().strftime('%Y-%m-%d'),
    }
    # 清理 None / NaN
    metrics = {k: _safe_jsonable(v) for k, v in metrics.items()}

    return jsonify({
        'success': True,
        'params_month': months[0] if months else None,
        'metrics': metrics,
    })


@stock_detail_bp.route('/api/<code>/predictions', methods=['GET'])
def api_predictions(code):
    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 500))
    except ValueError:
        limit = 50

    rows = (RnnRunningRecord.query
            .filter_by(code=code)
            .order_by(RnnRunningRecord.id.desc())
            .limit(limit).all())
    return jsonify({
        'success': True,
        'count': len(rows),
        'data': [r.to_dict() for r in rows],
    })


@stock_detail_bp.route('/api/<code>/fund_holdings', methods=['GET'])
def api_fund_holdings(code):
    """基金持仓数随时间的变化（用 StockDaily 表的 fund_holdings_count 字段）"""
    try:
        days = max(1, min(int(request.args.get('days', 365)), 3650))
    except ValueError:
        days = 365

    cutoff = (datetime.now().date() - timedelta(days=days))
    rows = (StockDaily.query
            .filter(StockDaily.stock_code == code,
                    StockDaily.date >= cutoff,
                    StockDaily.fund_holdings_count > 0)
            .order_by(StockDaily.date.asc())
            .all())

    series = [{
        'date': r.date.isoformat(),
        'fund_count': r.fund_holdings_count,
        'ratio_sum': r.fund_holdings_ratio,
        'ratio_avg': r.fund_holdings_avg_ratio,
        'ratio_max': r.fund_holdings_max_ratio,
        'close': r.close,
    } for r in rows]

    return jsonify({
        'success': True,
        'count': len(series),
        'data': series,
    })
