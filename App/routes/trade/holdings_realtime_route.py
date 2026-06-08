# -*- coding: utf-8 -*-
"""
持仓股票实时 15m 趋势页
- 列表页：/trade/holdings/realtime
- 详情页：/trade/holdings/realtime/<code>
- API：当前持仓列表 / 15m K（历史+当日实时聚合）/ 强制刷新
"""
import logging

import pandas as pd
from flask import Blueprint, render_template, jsonify, request

from App.services.realtime_data_service import (
    get_focus_stocks,
    merge_history_and_today_15m,
    fetch_today_1m,
)

logger = logging.getLogger(__name__)

holdings_realtime_bp = Blueprint('holdings_realtime', __name__)


# ============ 页面 ============

@holdings_realtime_bp.route('/trade/holdings/realtime')
def holdings_realtime_list_page():
    """当前持仓列表页"""
    return render_template('trade/holdings_realtime_list.html')


@holdings_realtime_bp.route('/trade/holdings/realtime/<code>')
def holdings_realtime_detail_page(code):
    """单只持仓的实时 15m 详情页"""
    return render_template('trade/holdings_realtime_detail.html', stock_code=code)


# ============ API ============

@holdings_realtime_bp.route('/api/trade/holdings/realtime/list')
def api_holdings_list():
    """关注列表：已持仓 (kind=holding) + 等待入场 (kind=watching)"""
    try:
        items = get_focus_stocks()
        holding_count = sum(1 for x in items if x.get('kind') == 'holding')
        watching_count = sum(1 for x in items if x.get('kind') == 'watching')
        board_count = sum(1 for x in items if x.get('kind') == 'board')
        return jsonify({
            'success': True,
            'data': items,
            'count': len(items),
            'holding_count': holding_count,
            'watching_count': watching_count,
            'board_count': board_count,
        })
    except Exception as e:
        logger.exception('关注列表 API 异常')
        return jsonify({'success': False, 'message': str(e)}), 500


@holdings_realtime_bp.route('/api/trade/holdings/realtime/<code>/chart_15m')
def api_chart_15m(code):
    """历史 15m + 当日实时聚合 15m

    query:
      live=0  仅读临时缓存，不重新拉 pytdx（默认 1 = 现拉）
      bars=N  返回末尾 N 根（默认 192 ≈ 10 个交易日）
    """
    try:
        from App.services.board_trend_score_service import _ema

        live = (request.args.get('live') or '1').strip() not in ('0', 'false', 'no')
        try:
            bars = int(request.args.get('bars', 192))
        except ValueError:
            bars = 192
        bars = max(48, min(bars, 5000))

        result = merge_history_and_today_15m(code, refresh=live)
        df = result['df']
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'message': f'未找到 {code} 的 15m 数据（历史与当日均为空）',
            }), 404

        # 指标（与 stock_trend api_stock_chart_15m 一致）
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        close = df['close']
        df['ma20'] = close.rolling(20, min_periods=1).mean()
        df['ma60'] = close.rolling(60, min_periods=1).mean()
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        df['dif'] = ema12 - ema26
        df['dea'] = _ema(df['dif'], 9)
        df['bar'] = (df['dif'] - df['dea']) * 2

        df = df.tail(bars).reset_index(drop=True)

        def _r(v, n=4):
            try:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return None
                return round(float(v), n)
            except Exception:
                return None

        rows = []
        for _, r in df.iterrows():
            rows.append({
                'date': r['date'].strftime('%Y-%m-%d %H:%M'),
                'open': _r(r['open']),
                'close': _r(r['close']),
                'high': _r(r['high']),
                'low': _r(r['low']),
                'volume': int(r['volume']) if pd.notna(r.get('volume')) else 0,
                'ma20': _r(r['ma20']),
                'ma60': _r(r['ma60']),
                'dif': _r(r['dif']),
                'dea': _r(r['dea']),
                'bar': _r(r['bar']),
            })

        return jsonify({
            'success': True,
            'data': {
                'stock_code': code,
                'count': len(rows),
                'rows': rows,
                'intraday_rows': result['intraday_rows'],
                'has_intraday': result['has_intraday'],
                'fetched_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            },
        })
    except Exception as e:
        logger.exception(f'15m 图表 API 异常 {code}: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500


@holdings_realtime_bp.route('/api/trade/holdings/realtime/<code>/refresh', methods=['POST'])
def api_refresh(code):
    """强制重拉当日 1m 并落盘"""
    try:
        df = fetch_today_1m(code, save=True)
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'message': f'{code} 当日 1m 拉取为空',
            }), 404
        return jsonify({
            'success': True,
            'data': {
                'stock_code': code,
                'rows': len(df),
                'last_time': pd.to_datetime(df['date']).max().strftime('%Y-%m-%d %H:%M'),
            },
        })
    except Exception as e:
        logger.exception(f'刷新 API 异常 {code}: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500
