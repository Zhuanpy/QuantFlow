from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file
from App.models.data.basic_info import StockClassification, StockCodes
from App.models.data.Stock1m import RecordStockMinute
from App.exts import db
from datetime import datetime
from sqlalchemy import text
import csv
import io
import os

# 数据管理蓝图
data_bp = Blueprint('data_bp', __name__)

@data_bp.route('/stock_classification')
def stock_classification():
    page = request.args.get('page', 1, type=int)
    pagination = StockClassification.query.order_by(StockClassification.id.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('data/stock_classification.html', pagination=pagination, classifications=pagination.items)

@data_bp.route('/stock_market_data')
def stock_market_data():
    page = request.args.get('page', 1, type=int)
    pagination = StockCodes.query.order_by(StockCodes.id.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('data/stock_market_data.html', pagination=pagination, stocks=pagination.items)

# RecordStockMinute 管理路由
@data_bp.route('/record_stock_minute')
def record_stock_minute():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()

    where_clauses = []
    params = {}

    if search:
        where_clauses.append("(s.code LIKE :search OR s.name LIKE :search OR r.stock_code_id LIKE :search)")
        params['search'] = f"%{search}%"

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    per_page = 20
    offset = (page - 1) * per_page

    # 获取总记录数
    count_query = text(f"""
        SELECT COUNT(*) as total
        FROM record_stock_minute r
        LEFT JOIN stock_market_data s ON r.stock_code_id = s.id
        WHERE {where_sql}
    """)
    total_result = db.session.execute(count_query, params).fetchone()
    total = total_result[0] if total_result else 0

    # 获取分页数据
    paginated_query = text(f"""
        SELECT r.*, s.name as stock_name, s.code as stock_code
        FROM record_stock_minute r
        LEFT JOIN stock_market_data s ON r.stock_code_id = s.id
        WHERE {where_sql}
        ORDER BY r.id DESC
        LIMIT {per_page} OFFSET {offset}
    """)
    records = db.session.execute(paginated_query, params).fetchall()

    # 创建分页对象
    class Pagination:
        def __init__(self, page, per_page, total):
            self.page = page
            self.per_page = per_page
            self.total = total
            self.pages = (total + per_page - 1) // per_page
            self.has_prev = page > 1
            self.has_next = page < self.pages
            self.prev_num = page - 1 if self.has_prev else None
            self.next_num = page + 1 if self.has_next else None

    pagination = Pagination(page, per_page, total)

    return render_template('data/record_stock_minute.html', pagination=pagination, records=records, request=request)

@data_bp.route('/record_stock_minute/add', methods=['POST'])
def add_record_stock_minute():
    try:
        data = request.form
        record = RecordStockMinute(
            stock_code_id=data.get('stock_code_id'),
            download_status=data.get('download_status', 'pending'),
            download_progress=float(data.get('download_progress', 0.0)),
            total_records=int(data.get('total_records', 0)),
            downloaded_records=int(data.get('downloaded_records', 0)),
            start_date=datetime.strptime(data.get('start_date'), '%Y-%m-%d').date() if data.get('start_date') else None,
            end_date=datetime.strptime(data.get('end_date'), '%Y-%m-%d').date() if data.get('end_date') else None,
            record_date=datetime.strptime(data.get('record_date'), '%Y-%m-%d').date() if data.get('record_date') else None,
            error_message=data.get('error_message'),
            last_download_time=datetime.strptime(data.get('last_download_time'), '%Y-%m-%dT%H:%M') if data.get('last_download_time') else None
        )
        db.session.add(record)
        db.session.commit()
        flash('记录添加成功！', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'添加失败：{str(e)}', 'error')
    
    return redirect(url_for('data_bp.record_stock_minute'))

@data_bp.route('/record_stock_minute/edit/<int:id>', methods=['POST'])
def edit_record_stock_minute(id):
    try:
        record = RecordStockMinute.query.get_or_404(id)
        data = request.form
        
        record.stock_code_id = data.get('stock_code_id')
        record.download_status = data.get('download_status')
        record.download_progress = float(data.get('download_progress', 0.0))
        record.total_records = int(data.get('total_records', 0))
        record.downloaded_records = int(data.get('downloaded_records', 0))
        record.error_message = data.get('error_message')
        
        if data.get('start_date'):
            record.start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
        if data.get('end_date'):
            record.end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
        if data.get('record_date'):
            record.record_date = datetime.strptime(data.get('record_date'), '%Y-%m-%d').date()
        if data.get('last_download_time'):
            record.last_download_time = datetime.strptime(data.get('last_download_time'), '%Y-%m-%dT%H:%M')
        
        db.session.commit()
        flash('记录更新成功！', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'更新失败：{str(e)}', 'error')
    
    return redirect(url_for('data_bp.record_stock_minute'))

@data_bp.route('/record_stock_minute/delete/<int:id>', methods=['POST'])
def delete_record_stock_minute(id):
    try:
        record = RecordStockMinute.query.get_or_404(id)
        db.session.delete(record)
        db.session.commit()
        flash('记录删除成功！', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除失败：{str(e)}', 'error')
    
    return redirect(url_for('data_bp.record_stock_minute'))

@data_bp.route('/record_stock_minute/get/<int:id>')
def get_record_stock_minute(id):
    # 使用JOIN查询获取完整信息
    query = text("""
        SELECT r.*, s.name as stock_name, s.code as stock_code
        FROM record_stock_minute r
        LEFT JOIN stock_market_data s ON r.stock_code_id = s.id
        WHERE r.id = :id
    """)
    
    result = db.session.execute(query, {'id': id}).fetchone()
    
    if not result:
        return jsonify({'error': '记录不存在'}), 404
    
    return jsonify({
        'id': result.id,
        'stock_code_id': result.stock_code_id,
        'stock_name': result.stock_name,
        'stock_code': result.stock_code,
        'download_status': result.download_status,
        'download_progress': result.download_progress,
        'total_records': result.total_records,
        'downloaded_records': result.downloaded_records,
        'start_date': result.start_date.strftime('%Y-%m-%d') if result.start_date else None,
        'end_date': result.end_date.strftime('%Y-%m-%d') if result.end_date else None,
        'record_date': result.record_date.strftime('%Y-%m-%d') if result.record_date else None,
        'last_download_time': result.last_download_time.strftime('%Y-%m-%d %H:%M:%S') if result.last_download_time else None,
        'created_at': result.created_at.strftime('%Y-%m-%d %H:%M:%S') if result.created_at else None,
        'updated_at': result.updated_at.strftime('%Y-%m-%d %H:%M:%S') if result.updated_at else None,
        'error_message': result.error_message
    })

@data_bp.route('/record_stock_minute/export')
def export_record_stock_minute():
    try:
        # 获取筛选参数
        search = request.args.get('search', '')
        download_status = request.args.get('download_status', '')
        progress_filter = request.args.get('progress_filter', '')
        date_filter = request.args.get('date_filter', '')
        start_date_from = request.args.get('start_date_from', '')
        start_date_to = request.args.get('start_date_to', '')
        end_date_from = request.args.get('end_date_from', '')
        end_date_to = request.args.get('end_date_to', '')
        total_records_min = request.args.get('total_records_min', '')
        total_records_max = request.args.get('total_records_max', '')
        downloaded_records_min = request.args.get('downloaded_records_min', '')
        downloaded_records_max = request.args.get('downloaded_records_max', '')
        error_filter = request.args.get('error_filter', '')
        sort_by = request.args.get('sort_by', 'id_desc')
        selected_ids = request.args.get('selected_ids', '')
        
        # 构建查询条件
        where_conditions = []
        params = {}
        
        # 如果指定了选中的ID，优先使用
        if selected_ids:
            id_list = selected_ids.split(',')
            placeholders = ','.join([':id_' + str(i) for i in range(len(id_list))])
            where_conditions.append(f"r.id IN ({placeholders})")
            for i, id_val in enumerate(id_list):
                params[f'id_{i}'] = int(id_val)
        else:
            # 其他筛选条件
            if search:
                where_conditions.append("(s.code LIKE :search OR s.name LIKE :search OR r.stock_code_id LIKE :search)")
                params['search'] = f'%{search}%'
            
            if download_status:
                where_conditions.append("r.download_status = :download_status")
                params['download_status'] = download_status
            
            if start_date_from:
                where_conditions.append("r.start_date >= :start_date_from")
                params['start_date_from'] = start_date_from
            
            if start_date_to:
                where_conditions.append("r.start_date <= :start_date_to")
                params['start_date_to'] = start_date_to
            
            if end_date_from:
                where_conditions.append("r.end_date >= :end_date_from")
                params['end_date_from'] = end_date_from
            
            if end_date_to:
                where_conditions.append("r.end_date <= :end_date_to")
                params['end_date_to'] = end_date_to
            
            if total_records_min:
                where_conditions.append("r.total_records >= :total_records_min")
                params['total_records_min'] = int(total_records_min)
            
            if total_records_max:
                where_conditions.append("r.total_records <= :total_records_max")
                params['total_records_max'] = int(total_records_max)
            
            if downloaded_records_min:
                where_conditions.append("r.downloaded_records >= :downloaded_records_min")
                params['downloaded_records_min'] = int(downloaded_records_min)
            
            if downloaded_records_max:
                where_conditions.append("r.downloaded_records <= :downloaded_records_max")
                params['downloaded_records_max'] = int(downloaded_records_max)
            
            if error_filter:
                where_conditions.append("r.error_message LIKE :error_filter")
                params['error_filter'] = f'%{error_filter}%'
        
        # 构建排序
        order_by = "r.id DESC"
        if sort_by == 'id_asc':
            order_by = "r.id ASC"
        elif sort_by == 'stock_code_id':
            order_by = "r.stock_code_id ASC"
        elif sort_by == 'download_status':
            order_by = "r.download_status ASC"
        elif sort_by == 'download_progress':
            order_by = "r.download_progress DESC"
        elif sort_by == 'start_date':
            order_by = "r.start_date ASC"
        elif sort_by == 'end_date':
            order_by = "r.end_date ASC"
        elif sort_by == 'created_at':
            order_by = "r.created_at DESC"
        
        # 构建完整查询
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        query = text(f"""
            SELECT r.*, s.name as stock_name, s.code as stock_code
            FROM record_stock_minute r
            LEFT JOIN stock_market_data s ON r.stock_code_id = s.id
            WHERE {where_clause}
            ORDER BY {order_by}
        """)
        
        records = db.session.execute(query, params).fetchall()
        
        # 创建CSV数据
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 写入表头
        headers = ['ID', '股票代码ID', '股票代码', '股票名称', '下载状态', '下载进度', '开始日期', '结束日期', '记录日期', '总记录数', '已下载记录数', '最后下载时间', '创建时间', '更新时间', '错误信息']
        writer.writerow(headers)
        
        # 写入数据
        for record in records:
            row = [
                record.id,
                record.stock_code_id,
                record.stock_code or '',
                record.stock_name or '',
                record.download_status,
                f"{record.download_progress}%",
                record.start_date.strftime('%Y-%m-%d') if record.start_date else '',
                record.end_date.strftime('%Y-%m-%d') if record.end_date else '',
                record.record_date.strftime('%Y-%m-%d') if record.record_date else '',
                record.total_records,
                record.downloaded_records,
                record.last_download_time.strftime('%Y-%m-%d %H:%M:%S') if record.last_download_time else '',
                record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '',
                record.updated_at.strftime('%Y-%m-%d %H:%M:%S') if record.updated_at else '',
                record.error_message or ''
            ]
            writer.writerow(row)
        
        output.seek(0)
        
        # 创建文件名
        if selected_ids:
            filename = f"record_stock_minute_selected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        else:
            filename = f"record_stock_minute_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        flash(f'导出失败：{str(e)}', 'error')
        return redirect(url_for('data_bp.record_stock_minute')) 