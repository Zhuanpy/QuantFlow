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

        # 批量查最新日 K 的 close + date，注入到响应里（始终最新，无需手动同步）
        if stocks:
            from App.models.data.StockDaily import StockDaily
            codes = [s['stock_code'] for s in stocks]
            # 子查询：每个 stock_code 的最大 date
            from sqlalchemy import func
            sub = (db.session.query(StockDaily.stock_code,
                                    func.max(StockDaily.date).label('max_date'))
                   .filter(StockDaily.stock_code.in_(codes))
                   .group_by(StockDaily.stock_code).subquery())
            latest_rows = (db.session.query(StockDaily)
                           .join(sub, and_(StockDaily.stock_code == sub.c.stock_code,
                                           StockDaily.date == sub.c.max_date))
                           .all())
            latest_map = {r.stock_code: {'close': r.close, 'date': r.date.isoformat()}
                          for r in latest_rows}
            for s in stocks:
                lk = latest_map.get(s['stock_code'])
                if lk:
                    s['latest_close'] = lk['close']
                    s['latest_close_date'] = lk['date']
                else:
                    s['latest_close'] = None
                    s['latest_close_date'] = None

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


@stock_pool_bp.route('/api/filter_scan', methods=['POST'])
def api_filter_scan():
    """
    扫描某池里"应被剔除"的股票，返回候选列表，由前端确认后再调 batch_update 归档。
    body: {
      pool_type: 'candidate' (必填),
      filters: {
        st: true,                    剔除名称含 ST 的
        min_money_60d: 50000000,     近 60 日日均成交额阈值
        min_market_cap: 5000000000   市值最小阈值（仅当字段有值时生效）
      }
    }
    """
    try:
        from App.models.data.StockDaily import StockDaily
        from datetime import timedelta

        data = request.get_json(silent=True) or {}
        pool_type = data.get('pool_type')
        filters = data.get('filters', {})
        if pool_type not in ('candidate', 'watching', 'trading', 'archived'):
            return jsonify({'success': False, 'message': '需要有效的 pool_type'}), 400

        stocks = StockPool.query.filter(
            StockPool.pool_type == pool_type,
            StockPool.is_active == True,
            StockPool.is_excluded == False,
        ).all()

        cutoff = date.today() - timedelta(days=90)
        to_remove = []
        for s in stocks:
            reasons = []

            # 1) ST 名称识别
            if filters.get('st'):
                name = (s.stock_name or '').upper()
                if 'ST' in name or '*ST' in name:
                    reasons.append('ST 类股票')

            # 2) 流动性：近 60 日日均成交额
            min_money = filters.get('min_money_60d')
            if min_money:
                rows = (StockDaily.query
                        .filter(StockDaily.stock_code == s.stock_code,
                                StockDaily.date >= cutoff)
                        .order_by(StockDaily.date.desc())
                        .limit(60).all())
                if rows:
                    avg_money = sum((r.money or 0) for r in rows) / len(rows)
                    if avg_money < float(min_money):
                        reasons.append(f'近 {len(rows)} 日日均成交额 {avg_money:,.0f} < 阈值 {float(min_money):,.0f}')
                else:
                    reasons.append('无近 90 日日 K 数据')

            # 3) 市值（仅当 StockPool.market_cap 有值时才能判）
            min_cap = filters.get('min_market_cap')
            if min_cap and s.market_cap is not None:
                if s.market_cap < float(min_cap):
                    reasons.append(f'市值 {s.market_cap:,.0f} < 阈值 {float(min_cap):,.0f}')

            if reasons:
                to_remove.append({
                    'id': s.id,
                    'stock_code': s.stock_code,
                    'stock_name': s.stock_name,
                    'reasons': reasons,
                })

        return jsonify({
            'success': True,
            'pool_type': pool_type,
            'scanned': len(stocks),
            'to_remove_count': len(to_remove),
            'to_remove': to_remove,
        })
    except Exception as e:
        logger.exception('filter_scan 失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_pool_bp.route('/api/apply_rules', methods=['POST'])
def api_apply_rules():
    """
    应用一组自动化规则，返回每条规则触发的股票列表。
    body: {
      candidate_archive_days: 30,    candidate 在池中超过 N 天未晋升 → archived
      degenerate_demote: {           watching 模型综合 DEGENERATE → candidate
          enabled: true,
          month: '2026-04',          检查健康度用的月份
          sample_size: 30
      }
    }
    """
    try:
        from datetime import timedelta
        data = request.get_json(silent=True) or {}

        results = {'archived_old_candidates': [], 'demoted_degenerate': []}

        # 规则 1：candidate 超 N 天未晋升
        days = int(data.get('candidate_archive_days', 30) or 30)
        if days > 0:
            cutoff = datetime.utcnow() - timedelta(days=days)
            old_candidates = StockPool.query.filter(
                StockPool.pool_type == 'candidate',
                StockPool.is_active == True,
                StockPool.is_excluded == False,
                StockPool.created_at <= cutoff,
            ).all()
            for s in old_candidates:
                old_pool = s.pool_type
                s.pool_type = 'archived'
                s.exclusion_reason = (s.exclusion_reason or '') + f' 候选超 {days} 天未晋升自动归档'
                s.updated_at = datetime.utcnow()
                results['archived_old_candidates'].append({
                    'id': s.id, 'stock_code': s.stock_code, 'stock_name': s.stock_name,
                    'created_at': s.created_at.isoformat() if s.created_at else None,
                    'from': old_pool,
                })

        # 规则 2：watching 模型 DEGENERATE 降级
        deg_cfg = data.get('degenerate_demote') or {}
        if deg_cfg.get('enabled') and deg_cfg.get('month'):
            month = deg_cfg['month']
            sample_size = int(deg_cfg.get('sample_size', 30))
            from App.routes.strategy.RnnStrategies import api_model_health  # 复用
            from App.codes.RnnDataFile.stock_path import StockDataPath
            import os, json as _json, numpy as _np
            from App.codes.RnnModel.model_predictor import _get_cached_model
            from App.codes.parsers.RnnParser import ModelName

            watching = StockPool.query.filter(
                StockPool.pool_type == 'watching',
                StockPool.is_active == True,
                StockPool.is_excluded == False,
            ).all()

            for s in watching:
                # 直接复用 model_health 内部逻辑（精简版）
                rank = []
                for name in ModelName:
                    x_path = StockDataPath.train_data_path(month, f'{name}_{s.stock_code}_x.npy')
                    y_path = StockDataPath.train_data_path(month, f'{name}_{s.stock_code}_y.npy')
                    m_path = StockDataPath.model_path(month, f'{name}_{s.stock_code}.h5')
                    if not (os.path.exists(x_path) and os.path.exists(y_path) and os.path.exists(m_path)):
                        continue
                    try:
                        x = _np.load(x_path)[:sample_size]
                        y = _np.load(y_path)[:sample_size].flatten()
                        model = _get_cached_model(month, f'{name}_{s.stock_code}.h5')
                        pred = model.predict(x, verbose=0).flatten()
                        ratio = (float(_np.std(pred)) / float(_np.std(y))) if _np.std(y) > 1e-9 else 0
                        rank.append('HEALTHY' if ratio >= 0.30 else ('WEAK' if ratio >= 0.05 else 'DEGENERATE'))
                    except Exception:
                        continue
                if rank and all(r == 'DEGENERATE' for r in rank):
                    # 综合 DEGENERATE → 降级
                    s.pool_type = 'candidate'
                    s.notes = (s.notes or '') + f' 模型({month}) 全 DEGENERATE 自动降级'
                    s.updated_at = datetime.utcnow()
                    results['demoted_degenerate'].append({
                        'id': s.id, 'stock_code': s.stock_code, 'stock_name': s.stock_name,
                        'health_per_model': rank,
                    })

        db.session.commit()
        return jsonify({
            'success': True,
            'archived_count': len(results['archived_old_candidates']),
            'demoted_count': len(results['demoted_degenerate']),
            'details': results,
        })
    except Exception as e:
        db.session.rollback()
        logger.exception('apply_rules 失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_pool_bp.route('/api/scan_market', methods=['POST'])
def api_scan_market():
    """
    全市场评分扫描（v1）。
    body: {
      top_n: 100,                  返回 top N
      exclude_st: true,            剔除 ST
      exclude_chinext: true,       剔除创业板（300/301）
      exclude_kechuang: false,     剔除科创板（688）
      exclude_beijiao: false,      剔除北交所（8/4）
      weights: {...} (可选，覆盖默认权重)
    }
    """
    try:
        from App.services.market_score_service import compute_market_scores
        data = request.get_json(silent=True) or {}
        results = compute_market_scores(
            weights=data.get('weights'),
            exclude_st=data.get('exclude_st', True),
            exclude_chinext=data.get('exclude_chinext', True),
            exclude_kechuang=data.get('exclude_kechuang', False),
            exclude_beijiao=data.get('exclude_beijiao', False),
            top_n=int(data.get('top_n', 100)),
        )
        return jsonify({
            'success': True,
            'count': len(results),
            'data': results,
        })
    except Exception as e:
        logger.exception('scan_market 失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_pool_bp.route('/api/batch_add_to_candidate', methods=['POST'])
def api_batch_add_to_candidate():
    """
    把扫描结果（前 N 名）批量加入候选池。已存在的跳过。
    body: { stocks: [{stock_code, stock_name, total_score}, ...] }
    """
    try:
        data = request.get_json(silent=True) or {}
        items = data.get('stocks', [])
        if not items:
            return jsonify({'success': False, 'message': '没有股票'}), 400
        added, skipped = [], []
        for it in items:
            code = (it.get('stock_code') or '').strip()
            if not code:
                continue
            existing = StockPool.query.filter_by(stock_code=code).first()
            if existing:
                skipped.append({'code': code, 'pool': existing.pool_type})
                continue
            StockPool.create_manual(
                stock_code=code,
                stock_name=it.get('stock_name', ''),
                pool_type='candidate',
                pool_priority=3,
                score=float(it.get('total_score', 0)),
                tags='市场扫描',
                notes=f'来自全市场评分扫描，初始分 {it.get("total_score", 0)}',
            )
            added.append(code)
        return jsonify({
            'success': True,
            'added_count': len(added),
            'skipped_count': len(skipped),
            'added': added,
            'skipped': skipped,
        })
    except Exception as e:
        logger.exception('batch_add_to_candidate 失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@stock_pool_bp.route('/api/scan_health', methods=['POST'])
def api_scan_health():
    """
    扫描指定池的所有股票模型健康度，返回每只股票的综合状态。
    body: { pool_type: 'watching', month: '2026-04', sample_size: 30 }
    """
    try:
        import os, numpy as _np
        from App.codes.RnnDataFile.stock_path import StockDataPath
        from App.codes.RnnModel.model_predictor import _get_cached_model
        from App.codes.parsers.RnnParser import ModelName

        data = request.get_json(silent=True) or {}
        pool_type = data.get('pool_type', 'watching')
        month = data.get('month')
        sample_size = int(data.get('sample_size', 30))
        if not month:
            return jsonify({'success': False, 'message': '需要 month'}), 400

        stocks = StockPool.query.filter(
            StockPool.pool_type == pool_type,
            StockPool.is_active == True,
            StockPool.is_excluded == False,
        ).all()

        results = []
        rank_order = {'HEALTHY': 3, 'WEAK': 2, 'DEGENERATE': 1, 'UNTRAINED': 0}
        for s in stocks:
            ranks = []
            for name in ModelName:
                x_path = StockDataPath.train_data_path(month, f'{name}_{s.stock_code}_x.npy')
                y_path = StockDataPath.train_data_path(month, f'{name}_{s.stock_code}_y.npy')
                m_path = StockDataPath.model_path(month, f'{name}_{s.stock_code}.h5')
                if not (os.path.exists(x_path) and os.path.exists(y_path) and os.path.exists(m_path)):
                    ranks.append('UNTRAINED')
                    continue
                try:
                    x = _np.load(x_path)[:sample_size]
                    y = _np.load(y_path)[:sample_size].flatten()
                    model = _get_cached_model(month, f'{name}_{s.stock_code}.h5')
                    pred = model.predict(x, verbose=0).flatten()
                    ratio = (float(_np.std(pred)) / float(_np.std(y))) if _np.std(y) > 1e-9 else 0
                    if ratio >= 0.30:
                        ranks.append('HEALTHY')
                    elif ratio >= 0.05:
                        ranks.append('WEAK')
                    else:
                        ranks.append('DEGENERATE')
                except Exception:
                    ranks.append('UNTRAINED')
            overall = min(ranks, key=lambda r: rank_order.get(r, 0)) if ranks else 'UNTRAINED'
            healthy_n = sum(1 for r in ranks if r == 'HEALTHY')
            results.append({
                'id': s.id,
                'stock_code': s.stock_code,
                'stock_name': s.stock_name,
                'overall': overall,
                'per_model': ranks,
                'healthy_count': healthy_n,
                'total_models': len(ranks),
            })

        return jsonify({
            'success': True,
            'pool_type': pool_type,
            'month': month,
            'count': len(results),
            'data': results,
        })
    except Exception as e:
        logger.exception('scan_health 失败')
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
