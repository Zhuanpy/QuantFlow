# -*- coding: utf-8 -*-
"""美股 1m 历史数据下载页（数据源：富途 OpenAPI）。

与 A 股下载/修复页分开是刻意的：数据源、交易日历、作息、额度模型全都不同，
挤进同一个页面做条件分支会很快失控。这里也不用 data_download_records 账本，
本地 parquet 就是唯一真相源，状态每次现扫现算。
"""
import logging

from flask import Blueprint, jsonify, render_template, request, current_app

us_data_bp = Blueprint('us_data', __name__)


@us_data_bp.route('/us_data_page')
def us_data_page():
    return render_template('data/us_data.html')


@us_data_bp.route('/api/us_data/quota', methods=['GET'])
def api_quota():
    """历史K线额度。页面常驻显示——这是与 A 股页面最本质的区别：
    pytdx 点错重来就行，富途每碰一只新票就永久消耗 1/300。"""
    try:
        from App.services.us_data_futu import get_quota
        return jsonify({'success': True, 'quota': get_quota()})
    except Exception as e:
        logging.error(f'[us] 查询额度失败: {e}')
        return jsonify({'success': False, 'message': str(e)}), 200


@us_data_bp.route('/api/us_data/check', methods=['POST'])
def api_check():
    """体检：本地有没有数据、缺哪些交易日、哪些日子根数不足、是否已在额度内。
    body{codes:[...]}"""
    try:
        from App.services.us_data_futu import check_local
        data = request.get_json(silent=True) or {}
        codes = data.get('codes') or []
        if not codes:
            return jsonify({'success': False, 'message': '请先输入股票代码'}), 200
        return jsonify({'success': True, 'items': check_local(codes)})
    except Exception as e:
        logging.error(f'[us] 体检失败: {e}')
        return jsonify({'success': False, 'message': str(e)}), 200


@us_data_bp.route('/api/us_data/download', methods=['POST'])
def api_download():
    """启动后台下载。body{codes:[...], years?}
    默认顶格回溯 8 年——额度按「只」计，与区间长短无关，分批拉只会白白多花额度。"""
    try:
        from App.services.us_data_futu import start_download, MAX_YEARS
        data = request.get_json(silent=True) or {}
        codes = data.get('codes') or []
        years = int(data.get('years') or MAX_YEARS)
        years = max(1, min(MAX_YEARS, years))
        app = current_app._get_current_object()
        ok, msg = start_download(app, codes, years)
        return jsonify({'success': ok, 'message': msg}), (200 if ok else 409)
    except Exception as e:
        logging.error(f'[us] 启动下载失败: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500


@us_data_bp.route('/api/us_data/status', methods=['GET'])
def api_status():
    from App.services.us_data_futu import get_status
    return jsonify({'success': True, 'data': get_status()})


@us_data_bp.route('/api/us_data/stop', methods=['POST'])
def api_stop():
    """停止：当前这只处理完即停（翻页循环里检查停止标志）。"""
    from App.services.us_data_futu import stop_download
    stop_download()
    return jsonify({'success': True, 'message': '已请求停止，当前标的处理完即停'})
