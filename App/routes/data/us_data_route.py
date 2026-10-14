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


# ================================================================ 美股档案页（长期跟踪）
# 方案 A：SEC 披露监控（us_sec_watch 后台线程）；方案 B：日K + 事件时间线 + 季度 KPI。

def _sym(symbol):
    return (symbol or 'SPCX').strip().upper().replace('US.', '')


def _ok(**kw):
    return jsonify({'success': True, **kw})


def _err(e, code=200):
    return jsonify({'success': False, 'message': str(e)}), code


@us_data_bp.route('/us/profile')
@us_data_bp.route('/us/profile/<symbol>')
def us_profile_page(symbol='SPCX'):
    from App.services.us_sec_watch import ensure_started
    from App.models.data.UsWatch import EVENT_CATEGORIES
    try:
        ensure_started(current_app._get_current_object())
    except Exception as e:
        logging.warning(f'[sec] 兜底自启失败: {e}')
    return render_template('data/us_profile.html', symbol=_sym(symbol), categories=EVENT_CATEGORIES)


@us_data_bp.route('/api/us/<symbol>/daily', methods=['GET'])
def api_us_daily(symbol):
    try:
        from App.services.us_profile_service import daily_payload
        return _ok(**daily_payload(_sym(symbol)))
    except Exception as e:
        logging.exception('[us_profile] 日K失败')
        return _err(e)


@us_data_bp.route('/api/us/<symbol>/events', methods=['GET'])
def api_us_events(symbol):
    try:
        from App.services.us_profile_service import list_events
        return _ok(items=list_events(_sym(symbol)))
    except Exception as e:
        logging.exception('[us_profile] 事件列表失败')
        return _err(e)


@us_data_bp.route('/api/us/<symbol>/events', methods=['POST'])
def api_us_event_save(symbol):
    try:
        from App.services.us_profile_service import save_event
        return _ok(item=save_event(_sym(symbol), request.get_json(silent=True) or {}))
    except Exception as e:
        return _err(e)


@us_data_bp.route('/api/us/events/<int:eid>', methods=['DELETE'])
def api_us_event_delete(eid):
    try:
        from App.services.us_profile_service import delete_event
        delete_event(eid)
        return _ok()
    except Exception as e:
        return _err(e)


@us_data_bp.route('/api/us/<symbol>/kpis', methods=['GET'])
def api_us_kpis(symbol):
    try:
        from App.services.us_profile_service import list_kpis
        return _ok(**list_kpis(_sym(symbol)))
    except Exception as e:
        logging.exception('[us_profile] KPI 列表失败')
        return _err(e)


@us_data_bp.route('/api/us/<symbol>/kpis', methods=['POST'])
def api_us_kpi_save(symbol):
    try:
        from App.services.us_profile_service import save_kpi
        return _ok(item=save_kpi(_sym(symbol), request.get_json(silent=True) or {}))
    except Exception as e:
        return _err(e)


@us_data_bp.route('/api/us/kpis/<int:kid>', methods=['DELETE'])
def api_us_kpi_delete(kid):
    try:
        from App.services.us_profile_service import delete_kpi
        delete_kpi(kid)
        return _ok()
    except Exception as e:
        return _err(e)


@us_data_bp.route('/api/us/<symbol>/filings', methods=['GET'])
def api_us_filings(symbol):
    try:
        from App.services.us_profile_service import list_filings
        return _ok(items=list_filings(_sym(symbol)))
    except Exception as e:
        logging.exception('[us_profile] 披露列表失败')
        return _err(e)


@us_data_bp.route('/api/us/sec_watch/status', methods=['GET'])
def api_sec_watch_status():
    from App.services.us_sec_watch import get_status
    from App.services.notifier import channels_configured
    return _ok(data=get_status(), channels=channels_configured())


@us_data_bp.route('/api/us/sec_watch/run_once', methods=['POST'])
def api_sec_watch_run_once():
    """立即同步一次（同步执行，几秒）。"""
    try:
        from App.services.us_sec_watch import sync_all
        return _ok(result=sync_all(current_app._get_current_object()))
    except Exception as e:
        logging.exception('[sec] 手动同步失败')
        return _err(e)


@us_data_bp.route('/api/us/sec_watch/<action>', methods=['POST'])
def api_sec_watch_toggle(action):
    from App.services.us_sec_watch import start_watch, stop_watch
    if action == 'start':
        ok, msg = start_watch(current_app._get_current_object())
    elif action == 'stop':
        ok, msg = stop_watch()
    else:
        return _err('未知操作', 404)
    return jsonify({'success': ok, 'message': msg})
