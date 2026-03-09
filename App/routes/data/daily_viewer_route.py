"""
日线数据查看器路由
展示 data_stock_daily + data_daily_task_status 合并数据
"""
from flask import Blueprint, render_template, jsonify, request
from App.models.data.StockDaily import StockDaily, save_daily_stock_data_to_sql
from App.models.data.DailyTaskStatus import DailyTaskStatus
from App.models.data.basic_info import StockInfo
from App.exts import db
from datetime import date, datetime
import logging

logger = logging.getLogger(__name__)

daily_viewer_bp = Blueprint('daily_viewer', __name__)


@daily_viewer_bp.route('/daily_viewer')
def daily_viewer_page():
    """日线数据查看器页面"""
    return render_template('data/daily_viewer.html')


@daily_viewer_bp.route('/api/daily_viewer/overview', methods=['GET'])
def get_overview():
    """获取日线数据总览统计"""
    try:
        # 有多少只股票
        stock_count = db.session.query(
            db.func.count(db.distinct(StockDaily.stock_code))
        ).scalar() or 0

        # 总记录数
        total_records = db.session.query(db.func.count(StockDaily.stock_code)).scalar() or 0

        # 日期范围
        date_range = db.session.query(
            db.func.min(StockDaily.date),
            db.func.max(StockDaily.date)
        ).first()
        min_date = date_range[0].strftime('%Y-%m-%d') if date_range[0] else '-'
        max_date = date_range[1].strftime('%Y-%m-%d') if date_range[1] else '-'

        # 最近更新的股票数（最近3个交易日有数据的）
        recent_date = db.session.query(db.func.max(StockDaily.date)).scalar()
        recent_count = 0
        if recent_date:
            recent_count = db.session.query(
                db.func.count(db.distinct(StockDaily.stock_code))
            ).filter(StockDaily.date == recent_date).scalar() or 0

        # 平均每只股票的记录数
        avg_records = round(total_records / stock_count) if stock_count > 0 else 0

        return jsonify({
            'stock_count': stock_count,
            'total_records': total_records,
            'min_date': min_date,
            'max_date': max_date,
            'recent_count': recent_count,
            'recent_date': recent_date.strftime('%Y-%m-%d') if recent_date else '-',
            'avg_records': avg_records,
        }), 200

    except Exception as e:
        logger.error(f"获取日线数据总览失败: {e}")
        return jsonify({'error': str(e)}), 500


@daily_viewer_bp.route('/api/daily_viewer/stocks', methods=['GET'])
def get_stocks():
    """获取所有维护的股票列表（含每只股票的记录数和日期范围）"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '', type=str)
        sort_by = request.args.get('sort_by', 'records', type=str)  # records / code / date
        per_page = min(per_page, 200)

        # 子查询：按 stock_code 汇总
        subq = db.session.query(
            StockDaily.stock_code,
            db.func.count(StockDaily.date).label('record_count'),
            db.func.min(StockDaily.date).label('start_date'),
            db.func.max(StockDaily.date).label('end_date'),
        ).group_by(StockDaily.stock_code).subquery()

        # 关联 stock_info 拿名称
        query = db.session.query(
            subq.c.stock_code,
            subq.c.record_count,
            subq.c.start_date,
            subq.c.end_date,
            StockInfo.name.label('stock_name'),
        ).outerjoin(StockInfo, StockInfo.code == subq.c.stock_code)

        # 搜索
        if search:
            query = query.filter(
                db.or_(
                    subq.c.stock_code.like(f'%{search}%'),
                    StockInfo.name.like(f'%{search}%'),
                )
            )

        # 排序
        if sort_by == 'code':
            query = query.order_by(subq.c.stock_code)
        elif sort_by == 'date':
            query = query.order_by(subq.c.end_date.desc())
        else:  # records
            query = query.order_by(subq.c.record_count.desc())

        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()

        stocks = []
        for row in rows:
            stocks.append({
                'code': row.stock_code,
                'name': row.stock_name or '-',
                'records': row.record_count,
                'start_date': row.start_date.strftime('%Y-%m-%d') if row.start_date else '-',
                'end_date': row.end_date.strftime('%Y-%m-%d') if row.end_date else '-',
            })

        return jsonify({
            'stocks': stocks,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        }), 200

    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@daily_viewer_bp.route('/api/daily_viewer/stock/<stock_code>', methods=['GET'])
def get_stock_daily_data(stock_code):
    """获取指定股票的日线数据（合并 StockDaily + DailyTaskStatus）"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 100, type=int)
        start_date = request.args.get('start_date', None)
        end_date = request.args.get('end_date', None)
        per_page = min(per_page, 500)

        T = DailyTaskStatus

        # LEFT JOIN: StockDaily 左连接 DailyTaskStatus
        query = db.session.query(
            StockDaily, T
        ).outerjoin(
            T,
            db.and_(
                StockDaily.stock_code == T.stock_code,
                StockDaily.date == T.date
            )
        ).filter(StockDaily.stock_code == stock_code)

        if start_date:
            query = query.filter(StockDaily.date >= start_date)
        if end_date:
            query = query.filter(StockDaily.date <= end_date)

        total = query.count()
        rows = query.order_by(StockDaily.date.desc()).offset(
            (page - 1) * per_page
        ).limit(per_page).all()

        def _r(val, n=2):
            """安全四舍五入"""
            return round(val, n) if val else 0

        # 空 task 模板
        _empty_task = {
            # 任务状态
            't_1m_dl': False, 't_1m_cl': False, 't_15m': False,
            't_macd': False, 't_vol': False, 't_daily': False,
            't_feat': False, 't_rnn': False, 't_board': False, 't_fund': False,
            # 趋势
            'signal': None, 'signal_name': '', 'signal_id': '',
            'signal_start': '', 're_trend': 0,
            # 周期
            'cyc_amp_bar': 0, 'cyc_amp_max': 0, 'cyc_amp': 0,
            'cyc_vol1': 0, 'cyc_vol5': 0, 'cyc_len': 0,
            # 预测
            'pred_cyc_len': 0, 'pred_cyc_chg': 0, 'pred_cyc_price': 0,
            'pred_bar_chg': 0, 'pred_bar_price': 0, 'pred_bar_vol': 0,
            # 真实
            'real_cyc_len': 0, 'real_cyc_chg': 0, 'real_bar_chg': 0, 'real_bar_vol': 0,
            # 评分
            'sc_rnn': 0, 'sc_boll': 0, 'sc_money': 0, 'sc_hot': 0,
            'sc_fund': 0, 'trend_prob': 0, 'trend_score': 0,
            'trade_action': 0,
            'has_task': False,
        }

        data = []
        for daily, task in rows:
            row_data = {
                # StockDaily 行情
                'date': daily.date.strftime('%Y-%m-%d') if daily.date else '-',
                'open': _r(daily.open), 'close': _r(daily.close),
                'high': _r(daily.high), 'low': _r(daily.low),
                'volume': daily.volume or 0, 'money': daily.money or 0,
                'fund_count': daily.fund_holdings_count or 0,
                'fund_ratio': _r(daily.fund_holdings_ratio),
            }
            if task:
                row_data.update({
                    # 任务状态
                    't_1m_dl': task.is_1m_downloaded, 't_1m_cl': task.is_1m_cleaned,
                    't_15m': task.is_15m_generated, 't_macd': task.is_macd_calculated,
                    't_vol': task.is_volume_processed, 't_daily': task.is_daily_processed,
                    't_feat': task.is_feature_generated, 't_rnn': task.is_rnn_predicted,
                    't_board': task.is_board_downloaded, 't_fund': task.is_fund_analyzed,
                    # 趋势
                    'signal': task.signal, 'signal_name': task.signal_name or '',
                    'signal_id': task.signal_id or '',
                    'signal_start': task.signal_start_time.strftime('%m-%d %H:%M') if task.signal_start_time else '',
                    're_trend': task.re_trend or 0,
                    # 周期
                    'cyc_amp_bar': _r(task.cycle_amplitude_per_bar, 4),
                    'cyc_amp_max': _r(task.cycle_amplitude_max, 4),
                    'cyc_amp': _r(task.cycle_amplitude, 4),
                    'cyc_vol1': task.cycle_1m_vol_max1 or 0,
                    'cyc_vol5': task.cycle_1m_vol_max5 or 0,
                    'cyc_len': task.cycle_length_per_bar or 0,
                    # 预测
                    'pred_cyc_len': task.predict_cycle_length or 0,
                    'pred_cyc_chg': _r(task.predict_cycle_change),
                    'pred_cyc_price': _r(task.predict_cycle_price),
                    'pred_bar_chg': _r(task.predict_bar_change),
                    'pred_bar_price': _r(task.predict_bar_price),
                    'pred_bar_vol': task.predict_bar_volume or 0,
                    # 真实
                    'real_cyc_len': task.real_cycle_length or 0,
                    'real_cyc_chg': _r(task.real_cycle_change),
                    'real_bar_chg': _r(task.real_bar_change),
                    'real_bar_vol': task.real_bar_volume or 0,
                    # 评分
                    'sc_rnn': _r(task.score_rnn_model),
                    'sc_boll': _r(task.score_board_boll),
                    'sc_money': _r(task.score_board_money),
                    'sc_hot': _r(task.score_board_hot),
                    'sc_fund': _r(task.score_funds_awkward),
                    'trend_prob': _r(task.trend_probability),
                    'trend_score': _r(task.trend_score),
                    'trade_action': task.trade_action or 0,
                    'has_task': True,
                })
            else:
                row_data.update(_empty_task)
            data.append(row_data)

        # 股票名称
        stock_info = StockInfo.query.filter_by(code=stock_code).first()
        stock_name = stock_info.name if stock_info else '-'

        return jsonify({
            'stock_code': stock_code,
            'stock_name': stock_name,
            'data': data,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        }), 200

    except Exception as e:
        logger.error(f"获取股票 {stock_code} 日线数据失败: {e}")
        return jsonify({'error': str(e)}), 500


@daily_viewer_bp.route('/api/daily_viewer/download_daily', methods=['POST'])
def download_daily_data():
    """
    下载并补充日线数据到数据库

    请求参数:
        stock_code: 股票代码
        days: 下载天数（默认 365）
    """
    try:
        data = request.get_json() or {}
        stock_code = data.get('stock_code', '').strip()
        days = data.get('days', 365)

        if not stock_code:
            return jsonify({'error': '请提供股票代码'}), 400

        # 查询当前已有数据范围
        existing = db.session.query(
            db.func.count(StockDaily.date),
            db.func.min(StockDaily.date),
            db.func.max(StockDaily.date),
        ).filter(StockDaily.stock_code == stock_code).first()
        old_count = existing[0] or 0
        old_min = existing[1]
        old_max = existing[2]

        # 使用 AKShare 下载日线数据
        from App.codes.downloads.DlAkshare import AkshareDownloader
        downloader = AkshareDownloader()

        if not downloader.akshare_available:
            return jsonify({'error': 'AKShare 未安装，请运行: pip install akshare'}), 500

        df, end_date = downloader.get_daily_data(stock_code, days=days)

        if df.empty:
            return jsonify({'error': f'未获取到 {stock_code} 的日线数据，请检查代码是否正确'}), 404

        # 保存到数据库
        success = save_daily_stock_data_to_sql(stock_code, df)
        if not success:
            return jsonify({'error': '保存数据到数据库失败'}), 500

        # 查询保存后的数据范围
        updated = db.session.query(
            db.func.count(StockDaily.date),
            db.func.min(StockDaily.date),
            db.func.max(StockDaily.date),
        ).filter(StockDaily.stock_code == stock_code).first()
        new_count = updated[0] or 0
        new_min = updated[1]
        new_max = updated[2]

        return jsonify({
            'message': f'下载完成',
            'stock_code': stock_code,
            'downloaded': len(df),
            'old_count': old_count,
            'new_count': new_count,
            'added': new_count - old_count,
            'date_range': {
                'start': new_min.strftime('%Y-%m-%d') if new_min else '-',
                'end': new_max.strftime('%Y-%m-%d') if new_max else '-',
            },
            'old_range': {
                'start': old_min.strftime('%Y-%m-%d') if old_min else '-',
                'end': old_max.strftime('%Y-%m-%d') if old_max else '-',
            },
        }), 200

    except Exception as e:
        logger.error(f"下载日线数据失败: {e}")
        return jsonify({'error': str(e)}), 500
