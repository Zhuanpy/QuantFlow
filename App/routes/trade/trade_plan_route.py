"""
交易计划管理路由
提供模拟交易和实盘交易的管理功能
"""
from flask import Blueprint, render_template, request, jsonify
from App.exts import db
from App.models.trade import TradePlan
from App.models.data.basic_info import StockInfo
from App.models.data.StockDaily import StockDaily
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# 创建蓝图
trade_plan_bp = Blueprint('trade_plan', __name__)


@trade_plan_bp.route('/trade')
def trade_page():
    """交易管理主页"""
    return render_template('trade/trade_plan.html')


@trade_plan_bp.route('/trade/simulate')
def simulate_trade_page():
    """模拟交易页面"""
    return render_template('trade/trade_plan.html', trade_mode='simulate')


@trade_plan_bp.route('/trade/real')
def real_trade_page():
    """实盘交易页面"""
    return render_template('trade/trade_plan.html', trade_mode='real')


# ============ 交易记录导入 ============

@trade_plan_bp.route('/api/trade/records/import', methods=['POST'])
def import_trade_records():
    """导入成交记录到 trade_records 表，支持两种来源：

    1) 文件（同花顺历史导出，含「成交日期」列）
       Multipart form: file=<.csv/.tsv/.txt/.xls/.xlsx>, dry_run=true|false
       可选 default_date=YYYY-MM-DD（文件无日期列时补）

    2) 粘贴文本（当日成交，只有「成交时间」无日期）
       form: text=<粘贴的表格文本>, default_date=YYYY-MM-DD（默认今天）, dry_run
    """
    try:
        from App.services.trade_import_service import import_from_ths, import_from_text

        dry_run = (request.form.get('dry_run', 'false').lower() == 'true')
        default_date = (request.form.get('default_date') or '').strip() or None

        # 优先文件；没有文件再看粘贴文本
        f = request.files.get('file')
        if f and f.filename:
            stats = import_from_ths(f, dry_run=dry_run, default_date=default_date)
            return jsonify({'success': True, 'data': stats, 'dry_run': dry_run})

        text = (request.form.get('text') or '').strip()
        if text:
            stats = import_from_text(text, dry_run=dry_run, default_date=default_date)
            return jsonify({'success': True, 'data': stats, 'dry_run': dry_run})

        return jsonify({'success': False, 'message': '请上传文件或粘贴成交文本'}), 400
    except Exception as e:
        logger.exception('交易记录导入失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/records/statistics', methods=['GET'])
def trade_records_statistics():
    """从 trade_records 按周期聚合后的统计：胜率、盈亏比、ROI 等。

    Query 参数：
        mode: '' (全部) | 'simulate' | 'real'，与前端 currentMode 对齐。
              为兼容旧调用，也接受 trade_mode；都不传时返回全部。
    """
    try:
        from App.services.trade_import_service import calculate_trade_statistics
        trade_mode = (request.args.get('mode')
                      or request.args.get('trade_mode')
                      or '').strip()
        stats = calculate_trade_statistics(trade_mode=trade_mode)
        return jsonify({'success': True, 'data': stats})
    except Exception as e:
        logger.exception('计算交易统计失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/plans/infer_from_records', methods=['POST'])
def infer_plans_from_records_route():
    """从 trade_records 推断 trade_plans（持仓/已平仓）。

    Body (JSON, 都可选):
        stock_code: 只推断指定股票
        trade_mode: 推断出的 plan 标记为哪种模式（默认 'real'）
        dry_run: true 则只解析返回会做什么，不写库
    """
    try:
        from App.services.trade_import_service import infer_plans_from_records
        body = request.get_json(silent=True) or {}
        stock_code = (body.get('stock_code') or '').strip() or None
        trade_mode = body.get('trade_mode', 'real')
        dry_run = bool(body.get('dry_run', False))

        result = infer_plans_from_records(
            trade_mode=trade_mode, stock_code=stock_code, dry_run=dry_run
        )
        return jsonify({
            'success': True,
            'dry_run': dry_run,
            'data': {
                'active_count': len(result['active']),
                'completed_count': len(result['completed']),
                'short_anomaly_count': len(result['short_anomaly']),
                'inserted_or_updated': result['inserted_or_updated'],
                'active': result['active'],
                'completed': result['completed'],
                'short_anomaly': result['short_anomaly'],
            }
        })
    except Exception as e:
        logger.exception('推断交易计划失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/records', methods=['GET'])
def list_trade_records():
    """查询 trade_records，供前端展示导入结果。

    Query 参数（都可选）:
        page, page_size
        mode: 'simulate' | 'real' （与 trade_plans 的 mode 参数对齐）
        stock_code: 股票代码模糊匹配
        stock_name: 股票名称模糊匹配
        trade_type: 'buy' | 'sell'
        market: 交易市场（深圳A股/上海A股）
        start_date, end_date: 成交时间区间 YYYY-MM-DD
        sort_by: time_desc / time_asc / amount_desc / amount_asc
    """
    try:
        from App.models.trade.trade_records import TradeRecord
        page = request.args.get('page', 1, type=int)
        page_size = min(max(request.args.get('page_size', 10, type=int), 1), 200)
        trade_mode = (request.args.get('mode') or '').strip()
        stock_code = (request.args.get('stock_code') or '').strip()
        stock_name = (request.args.get('stock_name') or '').strip()
        trade_type = (request.args.get('trade_type') or '').strip()
        market = (request.args.get('market') or '').strip()
        start_date_s = (request.args.get('start_date') or '').strip()
        end_date_s = (request.args.get('end_date') or '').strip()
        sort_by = (request.args.get('sort_by') or 'time_desc').strip()

        q = TradeRecord.query
        if trade_mode:
            q = q.filter(TradeRecord.trade_mode == trade_mode)
        if stock_code:
            q = q.filter(TradeRecord.stock_code.like(f'%{stock_code}%'))
        if stock_name:
            q = q.filter(TradeRecord.stock_name.like(f'%{stock_name}%'))
        if trade_type:
            q = q.filter(TradeRecord.trade_type == trade_type)
        if market:
            q = q.filter(TradeRecord.market == market)
        if start_date_s:
            try:
                start_dt = datetime.strptime(start_date_s, '%Y-%m-%d')
                q = q.filter(TradeRecord.execute_time >= start_dt)
            except ValueError:
                pass
        if end_date_s:
            try:
                end_dt = datetime.strptime(end_date_s, '%Y-%m-%d') + timedelta(days=1)
                q = q.filter(TradeRecord.execute_time < end_dt)
            except ValueError:
                pass

        sort_map = {
            'time_desc':   TradeRecord.execute_time.desc(),
            'time_asc':    TradeRecord.execute_time.asc(),
            'amount_desc': TradeRecord.total_amount.desc(),
            'amount_asc':  TradeRecord.total_amount.asc(),
        }
        q = q.order_by(sort_map.get(sort_by, TradeRecord.execute_time.desc()))

        # 聚合统计（在分页前算）：总买金额 / 总卖金额 / 净流
        agg_rows = q.with_entities(
            TradeRecord.trade_type,
            db.func.sum(TradeRecord.total_amount),
            db.func.sum(TradeRecord.net_amount),
            db.func.count(TradeRecord.id),
        ).group_by(TradeRecord.trade_type).all()
        summary = {'total_count': 0, 'buy_count': 0, 'sell_count': 0,
                   'buy_amount': 0.0, 'sell_amount': 0.0, 'net_cash_flow': 0.0}
        for tt, total_amt, net_amt, cnt in agg_rows:
            summary['total_count'] += int(cnt or 0)
            if tt == TradeRecord.TRADE_TYPE_BUY:
                summary['buy_count'] = int(cnt or 0)
                summary['buy_amount'] = float(total_amt or 0)
            elif tt == TradeRecord.TRADE_TYPE_SELL:
                summary['sell_count'] = int(cnt or 0)
                summary['sell_amount'] = float(total_amt or 0)
            summary['net_cash_flow'] += float(net_amt or 0)

        pagination = q.paginate(page=page, per_page=page_size, error_out=False)
        items = [r.to_dict() for r in pagination.items]
        return jsonify({
            'success': True,
            'data': {
                'items': items,
                'summary': summary,
                'pagination': {
                    'page': pagination.page, 'pages': pagination.pages,
                    'per_page': pagination.per_page, 'total': pagination.total,
                    'has_prev': pagination.has_prev, 'has_next': pagination.has_next,
                }
            }
        })
    except Exception as e:
        logger.exception('查询交易记录失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ============ API 接口 ============

@trade_plan_bp.route('/api/trade/plans', methods=['GET'])
def get_trade_plans():
    """获取交易计划列表"""
    try:
        trade_mode = request.args.get('mode', '')
        status = request.args.get('status', '')
        limit = int(request.args.get('limit', 50))

        query = TradePlan.query

        if trade_mode:
            query = query.filter(TradePlan.trade_mode == trade_mode)
        if status:
            query = query.filter(TradePlan.status == status)

        plans = query.order_by(TradePlan.created_at.desc()).limit(limit).all()

        return jsonify({
            'success': True,
            'total': len(plans),
            'plans': [p.to_dict() for p in plans]
        })
    except Exception as e:
        logger.error(f"获取交易计划失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/plans', methods=['POST'])
def create_trade_plan():
    """创建交易计划"""
    try:
        data = request.get_json()

        # 验证必要字段
        required = ['stock_code', 'stock_name', 'entry_price', 'quantity']
        for field in required:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'缺少必要字段: {field}'})

        plan = TradePlan(
            plan_id=TradePlan.create_plan_id(),
            stock_code=data['stock_code'],
            stock_name=data['stock_name'],
            trade_mode=data.get('trade_mode', TradePlan.MODE_SIMULATE),
            direction=data.get('direction', TradePlan.DIRECTION_LONG),
            entry_price=data['entry_price'],
            target_price=data.get('target_price'),
            stop_loss_price=data.get('stop_loss_price'),
            take_profit_price=data.get('take_profit_price'),
            quantity=data['quantity'],
            position_ratio=data.get('position_ratio'),
            plan_time=datetime.fromisoformat(data['plan_time']) if data.get('plan_time') else datetime.utcnow(),
            expire_time=datetime.fromisoformat(data['expire_time']) if data.get('expire_time') else None,
            entry_reason=data.get('entry_reason'),
            strategy_name=data.get('strategy_name'),
            notes=data.get('notes'),
            status=TradePlan.STATUS_PLANNING
        )

        # 计算风险指标
        plan.calculate_risk_metrics()

        db.session.add(plan)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': '交易计划创建成功',
            'plan': plan.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"创建交易计划失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/plans/<int:plan_id>', methods=['GET'])
def get_trade_plan(plan_id):
    """获取单个交易计划详情"""
    try:
        plan = TradePlan.query.get(plan_id)
        if not plan:
            return jsonify({'success': False, 'message': '交易计划不存在'})

        return jsonify({
            'success': True,
            'plan': plan.to_dict()
        })
    except Exception as e:
        logger.error(f"获取交易计划详情失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/plans/<int:plan_id>', methods=['PUT'])
def update_trade_plan(plan_id):
    """更新交易计划"""
    try:
        plan = TradePlan.query.get(plan_id)
        if not plan:
            return jsonify({'success': False, 'message': '交易计划不存在'})

        data = request.get_json()

        # 更新可编辑字段
        editable_fields = [
            'entry_price', 'target_price', 'stop_loss_price', 'take_profit_price',
            'quantity', 'position_ratio', 'entry_reason', 'strategy_name', 'notes'
        ]

        for field in editable_fields:
            if field in data:
                setattr(plan, field, data[field])

        # 处理时间字段
        if data.get('plan_time'):
            plan.plan_time = datetime.fromisoformat(data['plan_time'])
        if data.get('expire_time'):
            plan.expire_time = datetime.fromisoformat(data['expire_time'])

        # 重新计算风险指标
        plan.calculate_risk_metrics()
        plan.updated_at = datetime.utcnow()

        db.session.commit()

        return jsonify({
            'success': True,
            'message': '交易计划更新成功',
            'plan': plan.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"更新交易计划失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/plans/<int:plan_id>/enter', methods=['POST'])
def enter_trade(plan_id):
    """执行入场"""
    try:
        plan = TradePlan.query.get(plan_id)
        if not plan:
            return jsonify({'success': False, 'message': '交易计划不存在'})

        data = request.get_json() or {}

        success = plan.enter_trade(
            actual_price=data.get('actual_price'),
            actual_quantity=data.get('actual_quantity'),
            entry_time=datetime.utcnow()
        )

        if success:
            return jsonify({
                'success': True,
                'message': '入场成功',
                'plan': plan.to_dict()
            })
        else:
            return jsonify({'success': False, 'message': '入场失败'})
    except Exception as e:
        logger.error(f"入场失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/plans/<int:plan_id>/exit', methods=['POST'])
def exit_trade(plan_id):
    """执行出场"""
    try:
        plan = TradePlan.query.get(plan_id)
        if not plan:
            return jsonify({'success': False, 'message': '交易计划不存在'})

        data = request.get_json()
        if not data.get('exit_price'):
            return jsonify({'success': False, 'message': '请输入出场价格'})

        success = plan.exit_trade(
            exit_price=data['exit_price'],
            exit_reason=data.get('exit_reason'),
            exit_time=datetime.utcnow()
        )

        if success:
            return jsonify({
                'success': True,
                'message': '出场成功',
                'plan': plan.to_dict()
            })
        else:
            return jsonify({'success': False, 'message': '出场失败'})
    except Exception as e:
        logger.error(f"出场失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/plans/<int:plan_id>/cancel', methods=['POST'])
def cancel_trade_plan(plan_id):
    """取消交易计划"""
    try:
        plan = TradePlan.query.get(plan_id)
        if not plan:
            return jsonify({'success': False, 'message': '交易计划不存在'})

        data = request.get_json() or {}
        success = plan.cancel(reason=data.get('reason'))

        if success:
            return jsonify({
                'success': True,
                'message': '交易计划已取消'
            })
        else:
            return jsonify({'success': False, 'message': '取消失败'})
    except Exception as e:
        logger.error(f"取消失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/plans/<int:plan_id>', methods=['DELETE'])
def delete_trade_plan(plan_id):
    """删除交易计划"""
    try:
        plan = TradePlan.query.get(plan_id)
        if not plan:
            return jsonify({'success': False, 'message': '交易计划不存在'})

        db.session.delete(plan)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': '交易计划已删除'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"删除失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/stocks/search', methods=['GET'])
def search_stocks():
    """搜索已有市场数据的股票（支持代码或名称模糊搜索）"""
    try:
        keyword = request.args.get('keyword', '').strip()
        if not keyword:
            return jsonify({'success': True, 'stocks': []})

        # 有日线数据的股票代码子查询
        daily_codes = db.session.query(
            StockDaily.stock_code.distinct()
        ).subquery()

        query = StockInfo.query.filter(
            StockInfo.code.in_(db.session.query(daily_codes))
        ).filter(
            db.or_(
                StockInfo.code.like(f'%{keyword}%'),
                StockInfo.name.like(f'%{keyword}%')
            )
        )
        stocks = query.order_by(StockInfo.code).limit(20).all()

        return jsonify({
            'success': True,
            'stocks': [
                {
                    'stock_code': s.code,
                    'stock_name': s.name,
                    'market': s.MarketCode or '',
                    'industry': ''
                }
                for s in stocks
            ]
        })
    except Exception as e:
        logger.error(f"搜索股票失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/statistics', methods=['GET'])
def get_trade_statistics():
    """获取交易统计数据"""
    try:
        trade_mode = request.args.get('mode', '')
        days = int(request.args.get('days', 30))

        start_date = datetime.utcnow() - timedelta(days=days)

        stats = TradePlan.get_statistics(
            trade_mode=trade_mode if trade_mode else None,
            start_date=start_date
        )

        # 获取各模式的统计
        simulate_stats = TradePlan.get_statistics(trade_mode=TradePlan.MODE_SIMULATE, start_date=start_date)
        real_stats = TradePlan.get_statistics(trade_mode=TradePlan.MODE_REAL, start_date=start_date)

        # 获取活跃计划数
        active_simulate = len(TradePlan.get_active_plans(TradePlan.MODE_SIMULATE))
        active_real = len(TradePlan.get_active_plans(TradePlan.MODE_REAL))

        return jsonify({
            'success': True,
            'overall': stats,
            'simulate': {**simulate_stats, 'active_count': active_simulate},
            'real': {**real_stats, 'active_count': active_real},
            'period_days': days
        })
    except Exception as e:
        logger.error(f"获取统计数据失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/active', methods=['GET'])
def get_active_plans():
    """获取活跃的交易计划（持仓中）"""
    try:
        trade_mode = request.args.get('mode', '')

        plans = TradePlan.get_active_plans(trade_mode if trade_mode else None)

        return jsonify({
            'success': True,
            'total': len(plans),
            'plans': [p.to_dict() for p in plans]
        })
    except Exception as e:
        logger.error(f"获取活跃计划失败: {e}")
        return jsonify({'success': False, 'message': str(e)})
