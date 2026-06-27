from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file
from App.models.data.basic_info import StockClassification, StockCodes
from App.models.data.Stock1m import RecordStockMinute
from App.models.strategy.StockPool import StockPool
from App.exts import db
from datetime import datetime
from sqlalchemy import text, or_
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
    search = (request.args.get('search') or '').strip()
    in_download = (request.args.get('in_download') or '').strip()   # '' | yes | no
    in_pool = (request.args.get('in_pool') or '').strip()           # '' | yes | no

    # 两个"我的"集合（都在 quanttradingsystem 库，集合很小）：
    #   下载/收盘数据 = data_download_records 里出现过的 stock_code_id
    #   股票池        = strategy_stock_pool 里 is_active 的 code → pool_type
    dl_ids = {r[0] for r in db.session.query(RecordStockMinute.stock_code_id).distinct().all()
              if r[0] is not None}
    pool_map = {r.stock_code: r.pool_type
                for r in StockPool.query.filter(StockPool.is_active == True).all()}
    pool_codes = set(pool_map.keys())

    q = StockCodes.query
    if search:
        like = f'%{search}%'
        q = q.filter(or_(StockCodes.code.like(like), StockCodes.name.like(like)))
    if in_download == 'yes':
        q = q.filter(StockCodes.id.in_(dl_ids or {-1}))
    elif in_download == 'no':
        # NULL id 的行 NOT IN 会得 NULL（被排除），显式并入"不在"，避免漏掉这些行
        q = q.filter(or_(StockCodes.id.is_(None), ~StockCodes.id.in_(dl_ids or {-1})))
    if in_pool == 'yes':
        q = q.filter(StockCodes.code.in_(pool_codes or {'__none__'}))
    elif in_pool == 'no':
        q = q.filter(~StockCodes.code.in_(pool_codes or {'__none__'}))

    pagination = q.order_by(StockCodes.id.desc()).paginate(page=page, per_page=20, error_out=False)
    stocks = pagination.items
    # 给当页每行打上"是否在收盘数据 / 是否在股票池"标记（瞬态属性，模板直接读）
    for s in stocks:
        s.in_download = s.id in dl_ids
        s.in_pool = s.code in pool_codes
        s.pool_type = pool_map.get(s.code)

    return render_template('data/stock_market_data.html', pagination=pagination, stocks=stocks,
                           filters={'search': search, 'in_download': in_download, 'in_pool': in_pool})


# ---------------- data_stock_info（全市场代码表）CRUD ----------------
# 配合 stock_market_data.html 的「添加 / 编辑 / 删除 / 查看」按钮。
# data_bp 未设 url_prefix，故路径直接就是前端调用的 /data/stock[/<id>]。

def _stock_info_to_dict(s):
    """GET 用：字段放在顶层（前端 editStock/viewStock 直接读 data.name 等）。"""
    return {
        'success': True,
        'id': s.id,
        'name': s.name,
        'code': s.code,
        'es_code': s.EsCode,
        'market_code': s.MarketCode,
        'txd_market': s.TxdMarket,
        'hs_market': s.HsMarket,
        'created_at': s.created_at.strftime('%Y-%m-%d %H:%M:%S') if s.created_at else None,
    }


def _stock_info_payload():
    """从 FormData(优先) 或 JSON 取字段并 strip。映射前端 snake_case → 模型字段名。"""
    src = request.form if request.form else (request.get_json(silent=True) or {})

    def g(k):
        v = src.get(k)
        return v.strip() if isinstance(v, str) else v

    return {
        'name': g('name'), 'code': g('code'),
        'EsCode': g('es_code'), 'MarketCode': g('market_code'),
        'TxdMarket': g('txd_market'), 'HsMarket': g('hs_market'),
    }


@data_bp.route('/data/stock', methods=['POST'])
def add_stock_info():
    """新增一条全市场股票/板块代码记录。

    注意：data_stock_info 表无自增主键（id 仅为普通索引，由数据导入流程外部分配），
    因此这里必须手动分配 id = max(id)+1，且不在 commit 后再访问实例属性（会触发
    按 PK 重查而报 ObjectDeletedError）。
    """
    try:
        from sqlalchemy import func
        p = _stock_info_payload()
        if not p['name'] or not p['code']:
            return jsonify({'success': False, 'message': '股票名称和代码不能为空'}), 400
        new_id = int((db.session.query(func.max(StockCodes.id)).scalar() or 0)) + 1
        stock = StockCodes(id=new_id, name=p['name'], code=p['code'], EsCode=p['EsCode'],
                           MarketCode=p['MarketCode'], TxdMarket=p['TxdMarket'],
                           HsMarket=p['HsMarket'])
        db.session.add(stock)
        db.session.commit()
        return jsonify({'success': True, 'message': '添加成功', 'id': new_id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'添加失败: {e}'}), 500


@data_bp.route('/data/stock/<int:id>', methods=['GET'])
def get_stock_info(id):
    """取单条（编辑/查看回填用）。"""
    stock = StockCodes.query.get(id)
    if not stock:
        return jsonify({'success': False, 'message': '未找到该股票'}), 404
    return jsonify(_stock_info_to_dict(stock))


@data_bp.route('/data/stock/<int:id>', methods=['PUT'])
def update_stock_info(id):
    """更新单条。"""
    try:
        stock = StockCodes.query.get(id)
        if not stock:
            return jsonify({'success': False, 'message': '未找到该股票'}), 404
        p = _stock_info_payload()
        if not p['name'] or not p['code']:
            return jsonify({'success': False, 'message': '股票名称和代码不能为空'}), 400
        stock.name = p['name']
        stock.code = p['code']
        stock.EsCode = p['EsCode']
        stock.MarketCode = p['MarketCode']
        stock.TxdMarket = p['TxdMarket']
        stock.HsMarket = p['HsMarket']
        db.session.commit()
        return jsonify({'success': True, 'message': '更新成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'更新失败: {e}'}), 500


@data_bp.route('/data/stock/<int:id>', methods=['DELETE'])
def delete_stock_info(id):
    """删除单条。注意：data_download_records / data_1m_* 通过 stock_code_id 外键
    (ondelete=CASCADE) 关联本表，删代码可能连带其下载记录/1m 数据，请谨慎。"""
    try:
        stock = StockCodes.query.get(id)
        if not stock:
            return jsonify({'success': False, 'message': '未找到该股票'}), 404
        # 友好拦截：若仍有下载记录引用，提示先处理，避免误删连带数据
        ref = RecordStockMinute.query.filter_by(stock_code_id=id).count()
        if ref:
            return jsonify({'success': False,
                            'message': f'该代码下还有 {ref} 条下载记录，删除会连带清除其数据；'
                                       f'请先在「股票分钟数据记录」处理后再删。'}), 409
        db.session.delete(stock)
        db.session.commit()
        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败: {e}'}), 500

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
        FROM data_download_records r
        LEFT JOIN data_stock_info s ON r.stock_code_id = s.id
        WHERE {where_sql}
    """)
    total_result = db.session.execute(count_query, params).fetchone()
    total = total_result[0] if total_result else 0

    # 获取分页数据
    paginated_query = text(f"""
        SELECT r.*, s.name as stock_name, s.code as stock_code
        FROM data_download_records r
        LEFT JOIN data_stock_info s ON r.stock_code_id = s.id
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

    # 全表统计（不受分页和搜索影响）
    stats_query = text("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN download_status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN download_status = 'processing' THEN 1 ELSE 0 END) as processing,
            SUM(CASE WHEN download_status = 'success' THEN 1 ELSE 0 END) as success,
            SUM(CASE WHEN download_status = 'failed' THEN 1 ELSE 0 END) as failed,
            ROUND(AVG(download_progress), 1) as avg_progress
        FROM data_download_records
        WHERE end_date != '2050-01-01' AND record_date != '2050-01-01'
    """)
    stats_result = db.session.execute(stats_query).fetchone()
    stats = {
        'total': stats_result[0] or 0,
        'pending': stats_result[1] or 0,
        'processing': stats_result[2] or 0,
        'success': stats_result[3] or 0,
        'failed': stats_result[4] or 0,
        'avg_progress': stats_result[5] or 0,
    }

    return render_template('data/record_stock_minute.html', pagination=pagination, records=records, request=request, stats=stats)

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
        FROM data_download_records r
        LEFT JOIN data_stock_info s ON r.stock_code_id = s.id
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