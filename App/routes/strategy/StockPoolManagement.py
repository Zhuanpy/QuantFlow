"""
股票池管理路由
提供股票池的CRUD操作和管理功能
"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, Response
from App.exts import db
from App.models.strategy.StockPool import StockPool
from App.models.data.Stock1m import RecordStockMinute
from App.models.data.basic_info import StockCodes
from sqlalchemy import text, and_, or_
from datetime import datetime, date
import logging
import csv
import io

logger = logging.getLogger(__name__)

# 创建蓝图
stock_pool_bp = Blueprint('stock_pool', __name__, url_prefix='/stock_pool')


@stock_pool_bp.route('/')
def stock_pool_page():
    """股票池管理页面"""
    return render_template('strategy/stock_pool_management.html')


@stock_pool_bp.route('/api/statistics')
def get_pool_statistics():
    """获取股票池统计信息"""
    try:
        stats = StockPool.get_pool_statistics()
        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"获取股票池统计信息失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取统计信息失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/stocks')
def get_stocks():
    """获取股票池列表"""
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        pool_type = request.args.get('pool_type', '')
        industry = request.args.get('industry', '')
        search = request.args.get('search', '')
        is_active = request.args.get('is_active', 'true')
        is_training_ready = request.args.get('is_training_ready', '')
        sort_by = request.args.get('sort_by', 'priority')

        query = StockPool.query

        if pool_type:
            query = query.filter(StockPool.pool_type == pool_type)
        if industry:
            query = query.filter(StockPool.industry == industry)
        if is_active.lower() == 'true':
            query = query.filter(StockPool.is_active == True, StockPool.is_excluded == False)
        elif is_active.lower() == 'false':
            query = query.filter(or_(StockPool.is_active == False, StockPool.is_excluded == True))
        if is_training_ready == 'true':
            query = query.filter(StockPool.is_training_ready == True)
        elif is_training_ready == 'false':
            query = query.filter(StockPool.is_training_ready == False)
        if search:
            query = query.filter(or_(
                StockPool.stock_code.like(f'%{search}%'),
                StockPool.stock_name.like(f'%{search}%'),
                StockPool.tags.like(f'%{search}%')
            ))

        # 排序
        if sort_by == 'score_desc':
            query = query.order_by(StockPool.score.desc())
        elif sort_by == 'score_asc':
            query = query.order_by(StockPool.score.asc())
        elif sort_by == 'updated':
            query = query.order_by(StockPool.updated_at.desc())
        elif sort_by == 'name':
            query = query.order_by(StockPool.stock_code)
        else:
            query = query.order_by(
                StockPool.pool_priority.desc(),
                StockPool.score.desc(),
                StockPool.stock_code
            )

        pagination = query.paginate(page=page, per_page=page_size, error_out=False)
        stocks = [stock.to_dict() for stock in pagination.items]

        return jsonify({
            'success': True,
            'data': {
                'stocks': stocks,
                'pagination': {
                    'page': pagination.page,
                    'pages': pagination.pages,
                    'per_page': pagination.per_page,
                    'total': pagination.total,
                    'has_prev': pagination.has_prev,
                    'has_next': pagination.has_next,
                    'prev_num': pagination.prev_num,
                    'next_num': pagination.next_num
                }
            }
        })

    except Exception as e:
        logger.error(f"获取股票池列表失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取股票列表失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/add', methods=['POST'])
def add_stock():
    """手动添加股票到股票池"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code', '').strip()
        if not stock_code:
            return jsonify({'success': False, 'message': '股票代码不能为空'}), 400

        # 检查是否已存在
        existing = StockPool.query.filter_by(stock_code=stock_code, is_active=True).first()
        if existing:
            return jsonify({'success': False, 'message': f'{stock_code} 已存在于 {existing.pool_type} 池中'}), 400

        stock = StockPool.create_manual(
            stock_code=stock_code,
            stock_name=data.get('stock_name', ''),
            pool_type=data.get('pool_type', 'watching'),
            pool_priority=data.get('pool_priority', 3),
            score=data.get('score', 0.0),
            notes=data.get('notes', ''),
            tags=data.get('tags', ''),
            industry=data.get('industry', ''),
            target_price=data.get('target_price'),
            stop_loss_price=data.get('stop_loss_price'),
        )

        return jsonify({
            'success': True,
            'message': f'成功添加 {stock_code}',
            'data': stock.to_dict()
        })

    except Exception as e:
        logger.error(f"添加股票失败: {e}")
        return jsonify({'success': False, 'message': f'添加失败: {str(e)}'}), 500


@stock_pool_bp.route('/api/create_from_records', methods=['POST'])
def create_from_records():
    """从下载记录批量创建股票池"""
    try:
        data = request.get_json()
        pool_type = data.get('pool_type', 'candidate')

        records = db.session.query(RecordStockMinute).filter(
            RecordStockMinute.download_status == 'success'
        ).all()

        created_count = 0
        skipped_count = 0

        for record in records:
            existing = StockPool.query.filter_by(record_id=record.id).first()
            if existing:
                skipped_count += 1
                continue

            stock_info = db.session.query(StockCodes).filter_by(id=record.stock_code_id).first()
            stock_code = stock_info.code if stock_info else f"UNKNOWN_{record.stock_code_id}"
            stock_name = stock_info.name if stock_info else None

            StockPool.create_from_record(
                record_id=record.id,
                stock_code=stock_code,
                stock_name=stock_name,
                pool_type=pool_type,
                last_data_update=record.end_date,
                data_quality_score=80.0,
                data_completeness=90.0
            )
            created_count += 1

        return jsonify({
            'success': True,
            'message': f'成功创建 {created_count} 个，跳过 {skipped_count} 个已存在',
            'created_count': created_count,
            'skipped_count': skipped_count
        })

    except Exception as e:
        logger.error(f"从记录创建股票池失败: {e}")
        return jsonify({'success': False, 'message': f'创建失败: {str(e)}'}), 500


@stock_pool_bp.route('/api/update/<int:stock_id>', methods=['PUT'])
def update_stock(stock_id):
    """更新股票池条目"""
    try:
        data = request.get_json()
        stock = StockPool.query.get_or_404(stock_id)

        updatable_fields = [
            'pool_type', 'pool_priority', 'score', 'data_quality_score', 'data_completeness',
            'is_training_ready', 'training_status', 'market_cap', 'pe_ratio', 'pb_ratio',
            'industry', 'board', 'is_active', 'is_excluded', 'exclusion_reason',
            'target_price', 'stop_loss_price', 'current_price', 'position_ratio',
            'notes', 'tags', 'stock_name'
        ]

        for field in updatable_fields:
            if field in data:
                setattr(stock, field, data[field])

        if 'score' in data:
            old_score = stock.score or 0
            stock.score_trend = data['score'] - old_score
            stock.score_updated_at = datetime.utcnow()

        if 'last_data_update' in data and data['last_data_update']:
            stock.last_data_update = datetime.strptime(data['last_data_update'], '%Y-%m-%d').date()
        if 'last_training_date' in data and data['last_training_date']:
            stock.last_training_date = datetime.strptime(data['last_training_date'], '%Y-%m-%d').date()

        stock.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            'success': True,
            'message': '更新成功',
            'data': stock.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"更新失败: {e}")
        return jsonify({'success': False, 'message': f'更新失败: {str(e)}'}), 500


@stock_pool_bp.route('/api/move/<int:stock_id>', methods=['PUT'])
def move_stock(stock_id):
    """快速移动股票到指定池"""
    try:
        data = request.get_json()
        pool_type = data.get('pool_type')
        if not pool_type:
            return jsonify({'success': False, 'message': '目标池类型不能为空'}), 400

        success = StockPool.move_to_pool(stock_id, pool_type)
        if success:
            return jsonify({'success': True, 'message': f'已移动到{pool_type}池'})
        return jsonify({'success': False, 'message': '未找到该股票'}), 404

    except Exception as e:
        logger.error(f"移动失败: {e}")
        return jsonify({'success': False, 'message': f'移动失败: {str(e)}'}), 500


@stock_pool_bp.route('/api/update_score/<int:stock_id>', methods=['PUT'])
def update_score(stock_id):
    """快速更新评分"""
    try:
        data = request.get_json()
        score = data.get('score', 0.0)
        success = StockPool.update_score(stock_id, score)
        if success:
            return jsonify({'success': True, 'message': '评分更新成功'})
        return jsonify({'success': False, 'message': '未找到该股票'}), 404

    except Exception as e:
        logger.error(f"更新评分失败: {e}")
        return jsonify({'success': False, 'message': f'更新评分失败: {str(e)}'}), 500


@stock_pool_bp.route('/api/delete/<int:stock_id>', methods=['DELETE'])
def delete_stock(stock_id):
    """删除股票池条目"""
    try:
        stock = StockPool.query.get_or_404(stock_id)
        db.session.delete(stock)
        db.session.commit()
        return jsonify({'success': True, 'message': '删除成功'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"删除失败: {e}")
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@stock_pool_bp.route('/api/batch_update', methods=['POST'])
def batch_update():
    """批量更新"""
    try:
        data = request.get_json()
        stock_ids = data.get('stock_ids', [])
        updates = data.get('updates', {})

        if not stock_ids:
            return jsonify({'success': False, 'message': '请选择要更新的股票'}), 400

        updated_count = 0
        for stock_id in stock_ids:
            stock = StockPool.query.get(stock_id)
            if stock:
                for field, value in updates.items():
                    if hasattr(stock, field):
                        setattr(stock, field, value)
                stock.updated_at = datetime.utcnow()
                updated_count += 1

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'成功更新 {updated_count} 个股票',
            'updated_count': updated_count
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"批量更新失败: {e}")
        return jsonify({'success': False, 'message': f'批量更新失败: {str(e)}'}), 500


@stock_pool_bp.route('/api/training_ready')
def get_training_ready_stocks():
    """获取训练就绪的股票"""
    try:
        limit = request.args.get('limit', type=int)
        stocks = StockPool.get_training_ready_stocks(limit=limit)
        return jsonify({
            'success': True,
            'data': [stock.to_dict() for stock in stocks]
        })
    except Exception as e:
        logger.error(f"获取训练就绪股票失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_pool_bp.route('/api/update_quality', methods=['POST'])
def update_data_quality():
    """更新数据质量"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')
        if not stock_code:
            return jsonify({'success': False, 'message': '股票代码不能为空'}), 400

        last_update_date = None
        if data.get('last_update'):
            last_update_date = datetime.strptime(data['last_update'], '%Y-%m-%d').date()

        success = StockPool.update_data_quality(
            stock_code=stock_code,
            quality_score=data.get('quality_score', 0.0),
            completeness=data.get('completeness', 0.0),
            last_update=last_update_date
        )

        if success:
            return jsonify({'success': True, 'message': '更新成功'})
        return jsonify({'success': False, 'message': '未找到该股票'}), 404

    except Exception as e:
        logger.error(f"更新数据质量失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_pool_bp.route('/api/export')
def export_stocks():
    """导出股票池数据"""
    try:
        pool_type = request.args.get('pool_type', '')
        is_active = request.args.get('is_active', 'true')

        query = StockPool.query
        if pool_type:
            query = query.filter(StockPool.pool_type == pool_type)
        if is_active.lower() == 'true':
            query = query.filter(StockPool.is_active == True, StockPool.is_excluded == False)

        stocks = query.order_by(StockPool.score.desc()).all()

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            '股票代码', '股票名称', '池类型', '优先级', '综合评分',
            '数据质量', '完整性', '行业', '板块', '目标价',
            '止损价', '备注', '标签', '创建时间'
        ])

        for stock in stocks:
            writer.writerow([
                stock.stock_code,
                stock.stock_name or '',
                stock.pool_type,
                stock.pool_priority,
                stock.score,
                stock.data_quality_score,
                stock.data_completeness,
                stock.industry or '',
                stock.board or '',
                stock.target_price or '',
                stock.stop_loss_price or '',
                stock.notes or '',
                stock.tags or '',
                stock.created_at.strftime('%Y-%m-%d %H:%M') if stock.created_at else ''
            ])

        output.seek(0)

        return Response(
            output.getvalue().encode('utf-8-sig'),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=stock_pool_{datetime.now().strftime("%Y%m%d")}.csv'}
        )

    except Exception as e:
        logger.error(f"导出失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
