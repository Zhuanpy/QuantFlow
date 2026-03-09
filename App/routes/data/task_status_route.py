"""
每日任务状态路由
展示和管理每日数据处理任务的完成情况
"""
from flask import Blueprint, render_template, jsonify, request
from App.models.data.DailyTaskStatus import DailyTaskStatus
from App.exts import db
from datetime import date, datetime, timedelta
import logging

logger = logging.getLogger(__name__)

task_status_bp = Blueprint('task_status', __name__)


@task_status_bp.route('/task_status')
def task_status_page():
    """任务状态看板页面"""
    return render_template('data/task_status.html')


@task_status_bp.route('/api/task_status/list', methods=['GET'])
def get_task_status_list():
    """
    获取任务状态列表

    查询参数:
        stock_code: 股票代码（模糊搜索）
        date: 指定日期 (YYYY-MM-DD)
        start_date: 开始日期
        end_date: 结束日期
        status: 筛选状态 (all/complete/incomplete)
        page: 页码
        per_page: 每页条数
    """
    try:
        stock_code = request.args.get('stock_code', '', type=str)
        query_date = request.args.get('date', '', type=str)
        start_date = request.args.get('start_date', '', type=str)
        end_date = request.args.get('end_date', '', type=str)
        status = request.args.get('status', 'all', type=str)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = min(per_page, 200)

        query = DailyTaskStatus.query

        # 股票代码筛选
        if stock_code:
            query = query.filter(DailyTaskStatus.stock_code.like(f'%{stock_code}%'))

        # 日期筛选
        if query_date:
            query = query.filter(DailyTaskStatus.date == query_date)
        else:
            if start_date:
                query = query.filter(DailyTaskStatus.date >= start_date)
            if end_date:
                query = query.filter(DailyTaskStatus.date <= end_date)

        # 完成状态筛选
        if status == 'complete':
            query = query.filter(
                DailyTaskStatus.is_1m_downloaded == True,
                DailyTaskStatus.is_1m_cleaned == True,
                DailyTaskStatus.is_15m_generated == True,
                DailyTaskStatus.is_macd_calculated == True,
                DailyTaskStatus.is_volume_processed == True,
                DailyTaskStatus.is_daily_processed == True,
            )
        elif status == 'incomplete':
            query = query.filter(
                db.or_(
                    DailyTaskStatus.is_1m_downloaded == False,
                    DailyTaskStatus.is_1m_cleaned == False,
                    DailyTaskStatus.is_15m_generated == False,
                    DailyTaskStatus.is_macd_calculated == False,
                    DailyTaskStatus.is_volume_processed == False,
                    DailyTaskStatus.is_daily_processed == False,
                )
            )

        # 排序：日期倒序，股票代码正序
        query = query.order_by(DailyTaskStatus.date.desc(), DailyTaskStatus.stock_code)

        # 分页
        total = query.count()
        records = query.offset((page - 1) * per_page).limit(per_page).all()

        data = []
        for r in records:
            row = r.to_dict()
            row['date'] = r.date.strftime('%Y-%m-%d') if r.date else ''
            row['signal_start_time'] = r.signal_start_time.strftime('%Y-%m-%d %H:%M') if r.signal_start_time else ''
            row['updated_at'] = r.updated_at.strftime('%Y-%m-%d %H:%M') if r.updated_at else ''
            row['core_rate'] = round(r.task_completion_rate * 100)
            row['all_rate'] = round(r.all_task_completion_rate * 100)
            data.append(row)

        return jsonify({
            'data': data,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })

    except Exception as e:
        logger.error(f"获取任务状态列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@task_status_bp.route('/api/task_status/summary', methods=['GET'])
def get_task_summary():
    """
    获取任务统计概览

    查询参数:
        date: 指定日期，默认今天
    """
    try:
        query_date = request.args.get('date', date.today().strftime('%Y-%m-%d'))

        total = DailyTaskStatus.query.filter_by(date=query_date).count()

        if total == 0:
            return jsonify({
                'date': query_date,
                'total': 0,
                'tasks': {}
            })

        task_fields = [
            ('is_1m_downloaded', '1分钟下载'),
            ('is_1m_cleaned', '1分钟清洗'),
            ('is_15m_generated', '15分钟生成'),
            ('is_macd_calculated', 'MACD计算'),
            ('is_volume_processed', '成交量处理'),
            ('is_daily_processed', '日K处理'),
            ('is_feature_generated', '特征生成'),
            ('is_rnn_predicted', 'RNN预测'),
            ('is_board_downloaded', '板块下载'),
            ('is_fund_analyzed', '基金分析'),
        ]

        tasks = {}
        for field, label in task_fields:
            done = DailyTaskStatus.query.filter(
                DailyTaskStatus.date == query_date,
                getattr(DailyTaskStatus, field) == True
            ).count()
            tasks[field] = {
                'label': label,
                'done': done,
                'total': total,
                'rate': round(done / total * 100) if total > 0 else 0
            }

        # 核心任务全部完成的数量
        all_core_done = DailyTaskStatus.query.filter(
            DailyTaskStatus.date == query_date,
            DailyTaskStatus.is_1m_downloaded == True,
            DailyTaskStatus.is_1m_cleaned == True,
            DailyTaskStatus.is_15m_generated == True,
            DailyTaskStatus.is_macd_calculated == True,
            DailyTaskStatus.is_volume_processed == True,
            DailyTaskStatus.is_daily_processed == True,
        ).count()

        return jsonify({
            'date': query_date,
            'total': total,
            'all_core_done': all_core_done,
            'core_rate': round(all_core_done / total * 100) if total > 0 else 0,
            'tasks': tasks
        })

    except Exception as e:
        logger.error(f"获取任务统计失败: {e}")
        return jsonify({'error': str(e)}), 500


@task_status_bp.route('/api/task_status/dates', methods=['GET'])
def get_available_dates():
    """获取有记录的日期列表"""
    try:
        dates = db.session.query(DailyTaskStatus.date).distinct().order_by(
            DailyTaskStatus.date.desc()
        ).limit(60).all()

        return jsonify({
            'dates': [d.date.strftime('%Y-%m-%d') for d in dates]
        })

    except Exception as e:
        logger.error(f"获取日期列表失败: {e}")
        return jsonify({'error': str(e)}), 500
