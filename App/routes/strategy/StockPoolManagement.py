"""
股票池管理路由
提供股票池的CRUD操作和管理功能
"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from App.exts import db
from App.models.strategy.StockPool import StockPool
from App.models.data.Stock1m import RecordStockMinute
from App.models.data.basic_info import StockCodes
from sqlalchemy import text, and_, or_
from datetime import datetime, date
import logging

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
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        pool_type = request.args.get('pool_type', '')
        industry = request.args.get('industry', '')
        search = request.args.get('search', '')
        is_active = request.args.get('is_active', 'true')
        is_training_ready = request.args.get('is_training_ready', '')
        
        # 构建查询
        query = StockPool.query
        
        # 应用筛选条件
        if pool_type:
            query = query.filter(StockPool.pool_type == pool_type)
        if industry:
            query = query.filter(StockPool.industry == industry)
        if is_active.lower() == 'true':
            query = query.filter(StockPool.is_active == True, StockPool.is_excluded == False)
        elif is_active.lower() == 'false':
            query = query.filter(or_(StockPool.is_active == False, StockPool.is_excluded == True))
        if is_training_ready.lower() == 'true':
            query = query.filter(StockPool.is_training_ready == True)
        elif is_training_ready.lower() == 'false':
            query = query.filter(StockPool.is_training_ready == False)
        if search:
            query = query.filter(or_(
                StockPool.stock_code.like(f'%{search}%'),
                StockPool.stock_name.like(f'%{search}%')
            ))
        
        # 排序
        query = query.order_by(
            StockPool.pool_priority.desc(),
            StockPool.data_quality_score.desc(),
            StockPool.stock_code
        )
        
        # 分页
        pagination = query.paginate(
            page=page, 
            per_page=page_size, 
            error_out=False
        )
        
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


@stock_pool_bp.route('/api/create_from_records', methods=['POST'])
def create_from_records():
    """从record_stock_minute记录创建股票池"""
    try:
        data = request.get_json()
        pool_type = data.get('pool_type', 'general')
        min_quality_score = data.get('min_quality_score', 0.0)
        min_completeness = data.get('min_completeness', 0.0)
        
        # 获取符合条件的下载记录
        records_query = db.session.query(RecordStockMinute).filter(
            RecordStockMinute.download_status == 'success'
        )
        
        if min_quality_score > 0:
            # 这里可以根据实际需求添加质量筛选逻辑
            pass
            
        records = records_query.all()
        
        created_count = 0
        skipped_count = 0
        
        for record in records:
            # 检查是否已存在
            existing = StockPool.query.filter_by(record_id=record.id).first()
            if existing:
                skipped_count += 1
                continue
                
            # 获取股票基本信息
            stock_info = db.session.query(StockCodes).filter_by(id=record.stock_code_id).first()
            stock_code = stock_info.code if stock_info else f"UNKNOWN_{record.stock_code_id}"
            stock_name = stock_info.name if stock_info else None
            
            # 创建股票池条目
            StockPool.create_from_record(
                record_id=record.id,
                stock_code=stock_code,
                stock_name=stock_name,
                pool_type=pool_type,
                last_data_update=record.end_date,
                data_quality_score=80.0,  # 默认质量评分
                data_completeness=90.0   # 默认完整性评分
            )
            created_count += 1
        
        return jsonify({
            'success': True,
            'message': f'成功创建 {created_count} 个股票池条目，跳过 {skipped_count} 个已存在的条目',
            'created_count': created_count,
            'skipped_count': skipped_count
        })
        
    except Exception as e:
        logger.error(f"从记录创建股票池失败: {e}")
        return jsonify({
            'success': False,
            'message': f'创建股票池失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/update/<int:stock_id>', methods=['PUT'])
def update_stock(stock_id):
    """更新股票池条目"""
    try:
        data = request.get_json()
        stock = StockPool.query.get_or_404(stock_id)
        
        # 更新字段
        updatable_fields = [
            'pool_type', 'pool_priority', 'data_quality_score', 'data_completeness',
            'is_training_ready', 'training_status', 'market_cap', 'pe_ratio', 'pb_ratio',
            'industry', 'board', 'is_active', 'is_excluded', 'exclusion_reason'
        ]
        
        for field in updatable_fields:
            if field in data:
                setattr(stock, field, data[field])
        
        # 更新日期字段
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
        logger.error(f"更新股票池条目失败: {e}")
        return jsonify({
            'success': False,
            'message': f'更新失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/delete/<int:stock_id>', methods=['DELETE'])
def delete_stock(stock_id):
    """删除股票池条目"""
    try:
        stock = StockPool.query.get_or_404(stock_id)
        db.session.delete(stock)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '删除成功'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"删除股票池条目失败: {e}")
        return jsonify({
            'success': False,
            'message': f'删除失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/batch_update', methods=['POST'])
def batch_update():
    """批量更新股票池条目"""
    try:
        data = request.get_json()
        stock_ids = data.get('stock_ids', [])
        updates = data.get('updates', {})
        
        if not stock_ids:
            return jsonify({
                'success': False,
                'message': '请选择要更新的股票'
            }), 400
        
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
        logger.error(f"批量更新股票池失败: {e}")
        return jsonify({
            'success': False,
            'message': f'批量更新失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/training_ready')
def get_training_ready_stocks():
    """获取训练就绪的股票列表"""
    try:
        limit = request.args.get('limit', type=int)
        stocks = StockPool.get_training_ready_stocks(limit=limit)
        
        return jsonify({
            'success': True,
            'data': [stock.to_dict() for stock in stocks]
        })
        
    except Exception as e:
        logger.error(f"获取训练就绪股票失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取训练就绪股票失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/update_quality', methods=['POST'])
def update_data_quality():
    """更新数据质量信息"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')
        quality_score = data.get('quality_score', 0.0)
        completeness = data.get('completeness', 0.0)
        last_update = data.get('last_update')
        
        if not stock_code:
            return jsonify({
                'success': False,
                'message': '股票代码不能为空'
            }), 400
        
        last_update_date = None
        if last_update:
            last_update_date = datetime.strptime(last_update, '%Y-%m-%d').date()
        
        success = StockPool.update_data_quality(
            stock_code=stock_code,
            quality_score=quality_score,
            completeness=completeness,
            last_update=last_update_date
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': '数据质量信息更新成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': '未找到指定的股票'
            }), 404
            
    except Exception as e:
        logger.error(f"更新数据质量信息失败: {e}")
        return jsonify({
            'success': False,
            'message': f'更新失败: {str(e)}'
        }), 500


@stock_pool_bp.route('/api/export')
def export_stocks():
    """导出股票池数据"""
    try:
        # 获取查询参数
        pool_type = request.args.get('pool_type', '')
        industry = request.args.get('industry', '')
        is_active = request.args.get('is_active', 'true')
        
        # 构建查询
        query = StockPool.query
        if pool_type:
            query = query.filter(StockPool.pool_type == pool_type)
        if industry:
            query = query.filter(StockPool.industry == industry)
        if is_active.lower() == 'true':
            query = query.filter(StockPool.is_active == True, StockPool.is_excluded == False)
        
        stocks = query.all()
        
        # 转换为CSV格式
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 写入表头
        writer.writerow([
            '股票代码', '股票名称', '股票池类型', '优先级', '数据质量评分',
            '数据完整性', '最后数据更新', '训练就绪', '训练状态', '市值',
            '市盈率', '市净率', '行业', '板块', '激活状态', '排除状态'
        ])
        
        # 写入数据
        for stock in stocks:
            writer.writerow([
                stock.stock_code,
                stock.stock_name or '',
                stock.pool_type,
                stock.pool_priority,
                stock.data_quality_score,
                stock.data_completeness,
                stock.last_data_update.isoformat() if stock.last_data_update else '',
                '是' if stock.is_training_ready else '否',
                stock.training_status,
                stock.market_cap or '',
                stock.pe_ratio or '',
                stock.pb_ratio or '',
                stock.industry or '',
                stock.board or '',
                '是' if stock.is_active else '否',
                '是' if stock.is_excluded else '否'
            ])
        
        output.seek(0)
        
        from flask import Response
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=stock_pool.csv'}
        )
        
    except Exception as e:
        logger.error(f"导出股票池数据失败: {e}")
        return jsonify({
            'success': False,
            'message': f'导出失败: {str(e)}'
        }), 500



