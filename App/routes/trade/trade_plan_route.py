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


@trade_plan_bp.route('/trade/watch')
def watch_page():
    """盯盘页：持仓实时 + 底部放量提醒 + 自选池适配度（从交易计划页拆出）。"""
    try:
        from flask import current_app
        from App.services.holdings_1m_autofetch import ensure_started
        ensure_started(current_app._get_current_object())
    except Exception as e:
        logger.warning(f'盯盘页兜底自启失败: {e}')
    return render_template('trade/watch.html')


@trade_plan_bp.route('/trade/statistics')
def trade_statistics_page():
    """历史交易统计独立页面。

    复用 /api/trade/records/statistics 接口，按"持仓 0→正→0"周期切分展示
    胜率/盈亏比/ROI、按年份汇总、已完成交易明细。
    可选 query 参数 mode=simulate|real 作为初始模式。
    """
    trade_mode = (request.args.get('mode') or '').strip()
    return render_template('trade/trade_statistics.html', trade_mode=trade_mode)


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


@trade_plan_bp.route('/api/trade/records/<int:record_id>/reason', methods=['PATCH'])
def update_trade_record_reason(record_id):
    """更新单条成交记录的操作理由（trade_reason）。

    Body (JSON): {"trade_reason": "..."}  空字符串视为清空（存 NULL）。
    """
    try:
        from App.models.trade.trade_records import TradeRecord
        rec = TradeRecord.query.get(record_id)
        if not rec:
            return jsonify({'success': False, 'message': '成交记录不存在'}), 404

        data = request.get_json(silent=True) or {}
        reason = (data.get('trade_reason') or '').strip()
        rec.trade_reason = reason or None
        rec.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'data': {'id': rec.id, 'trade_reason': rec.trade_reason}})
    except Exception as e:
        db.session.rollback()
        logger.exception('更新操作理由失败')
        return jsonify({'success': False, 'message': str(e)}), 500


# ============ API 接口 ============

def _latest_closes(codes):
    """批量取各代码 data_stock_daily 最新收盘价，作为「现价」。返回 {code: close}。"""
    codes = [c for c in set(codes) if c]
    if not codes:
        return {}
    sub = (db.session.query(StockDaily.stock_code,
                            db.func.max(StockDaily.date).label('md'))
           .filter(StockDaily.stock_code.in_(codes))
           .group_by(StockDaily.stock_code).subquery())
    rows = (db.session.query(StockDaily.stock_code, StockDaily.close)
            .join(sub, db.and_(StockDaily.stock_code == sub.c.stock_code,
                               StockDaily.date == sub.c.md)).all())
    return {c: float(cl) for c, cl in rows if cl is not None}


def _plan_dict_with_pnl(plan, close_map):
    """plan.to_dict() + 现价/手续费/浮动盈亏（持仓中才算浮盈）。"""
    d = plan.to_dict()
    d['commission'] = float(plan.commission or 0)
    d['tax'] = float(plan.tax or 0)
    d['fee_total'] = round(d['commission'] + d['tax'], 2)
    cur = close_map.get(plan.stock_code)
    if cur is None and d.get('current_price'):
        cur = d['current_price']
    d['current_price'] = cur
    entry = d.get('entry_price')
    qty = plan.quantity or 0
    if plan.status == TradePlan.STATUS_ACTIVE and entry and cur and qty:
        gross = ((cur - entry) * qty if plan.direction != TradePlan.DIRECTION_SHORT
                 else (entry - cur) * qty)
        net = gross - d['fee_total']            # 扣手续费+税
        d['market_value'] = round(cur * qty, 2)
        d['cost_amount'] = round(entry * qty, 2)
        d['unrealized_pnl'] = round(net, 2)
        d['unrealized_pnl_pct'] = round(net / (entry * qty) * 100, 2) if entry * qty else None
    return d


@trade_plan_bp.route('/api/trade/plans', methods=['GET'])
def get_trade_plans():
    """获取交易计划列表（持仓中的附带现价/手续费/浮动盈亏）"""
    try:
        trade_mode = request.args.get('mode', '')
        status = request.args.get('status', '')
        limit = int(request.args.get('limit', 50))

        query = TradePlan.query

        # MANUAL-<code> 不是持仓，而是 15m 详情页「止损/止盈」功能的存储容器
        # （/api/<code>/sl_tp 创建，写死 status=active，且不被成交推断 wipe/更新）。
        # 它不代表真实仓位，真实仓位来自 INFER-<code> 周期，所以这里排除掉，
        # 否则只要设过止损止盈就会在持仓列表里留下一行永不消失的幽灵「持仓中」。
        query = query.filter(~TradePlan.plan_id.like('MANUAL-%'))

        if trade_mode:
            query = query.filter(TradePlan.trade_mode == trade_mode)
        if status:
            query = query.filter(TradePlan.status == status)

        plans = query.order_by(TradePlan.created_at.desc()).limit(limit).all()
        close_map = _latest_closes([p.stock_code for p in plans])

        return jsonify({
            'success': True,
            'total': len(plans),
            'plans': [_plan_dict_with_pnl(p, close_map) for p in plans]
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
        close_map = _latest_closes([p.stock_code for p in plans])

        return jsonify({
            'success': True,
            'total': len(plans),
            'plans': [_plan_dict_with_pnl(p, close_map) for p in plans]
        })
    except Exception as e:
        logger.error(f"获取活跃计划失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@trade_plan_bp.route('/api/trade/monitor', methods=['GET'])
def get_monitor_holdings():
    """盯盘面板：持仓中(active)股票 + 实时现价/涨跌/浮动盈亏 + 盯盘状态。

    - 行来源：active 交易计划（排除 MANUAL-<code> 幽灵持仓），可按 ?mode= 过滤。
    - 现价：优先东财实时行情（push2），失败回退 data_stock_daily 收盘价。
    - 盯盘状态：来自持仓 1m 自动拉取后台线程（holdings_1m_autofetch）——
      线程运行 + 交易时段 + 该股在当前持仓(trade_records)里才算「盯盘中」。

    该接口是「盯盘」独立面板的数据源，字段可按需扩展（后续加更多列只需在此补字段）。
    """
    try:
        from datetime import datetime as _dt
        from App.services.stock_quote_service import fetch_stock_quote

        trade_mode = (request.args.get('mode') or '').strip()
        plans = TradePlan.get_active_plans(trade_mode if trade_mode else None)
        # 排除 MANUAL-<code>（止损止盈容器，非真实仓位）
        plans = [p for p in plans if not (p.plan_id or '').startswith('MANUAL-')]

        # 实时行情（持仓通常很少，逐只拉；失败的留空回退当日收盘）
        rt_price, rt_change, rt_extra = {}, {}, {}
        for p in plans:
            try:
                q = fetch_stock_quote(p.stock_code)
            except Exception:
                q = None
            if q and q.get('price'):        # 排除 None / 0（盘前或异常返回 0 价）
                rt_price[p.stock_code] = q['price']
                rt_change[p.stock_code] = q.get('change_pct')
                rt_extra[p.stock_code] = {
                    'turnover': q.get('turnover'),          # 换手率 %
                    'volume_ratio': q.get('volume_ratio'),  # 量比
                    'ts': q.get('ts'),                      # 行情时间戳（unix 秒）
                }

        missing = [p.stock_code for p in plans if p.stock_code not in rt_price]
        close_map = _latest_closes(missing) if missing else {}
        price_map = {**close_map, **rt_price}   # 实时优先

        # 盯盘线程状态 + 当前实际持仓集合
        try:
            from App.services.holdings_1m_autofetch import get_status as _af_status
            af = _af_status()
        except Exception:
            af = {'running': False, 'in_session_now': False}
        running = bool(af.get('running'))
        in_session = bool(af.get('in_session_now'))
        try:
            from App.services.realtime_data_service import get_current_holdings
            holding_codes = {h['stock_code'] for h in get_current_holdings()}
        except Exception:
            holding_codes = None   # 交叉校验不可用时退化为全局状态

        def _row_monitor(code):
            in_hold = True if holding_codes is None else (code in holding_codes)
            monitoring = running and in_session and in_hold
            if monitoring:
                label = '盯盘中'
            elif not running:
                label = '未启动'
            elif not in_session:
                label = '休市待盘'
            elif not in_hold:
                label = '无成交数据'
            else:
                label = '未盯盘'
            return monitoring, label

        # 全局状态标签（页头徽标用）
        global_label = '盯盘中' if (running and in_session) else ('休市待盘' if running else '未启动')

        try:
            from App.services.bottom_volume_signal import get_signal as _bv_get
        except Exception:
            _bv_get = None

        rows = []
        for p in plans:
            d = _plan_dict_with_pnl(p, price_map)
            d['change_pct'] = rt_change.get(p.stock_code)
            d['price_source'] = ('realtime' if p.stock_code in rt_price
                                 else ('daily' if p.stock_code in close_map else None))
            d['monitoring'], d['monitor_label'] = _row_monitor(p.stock_code)
            # 距止损 / 距止盈（相对现价的百分比；正=现价仍在止损上方 / 止盈下方）
            cur = d.get('current_price')
            sl = d.get('stop_loss_price')
            tp = d.get('take_profit_price')
            d['dist_to_sl_pct'] = round((cur - sl) / cur * 100, 2) if (cur and sl) else None
            d['dist_to_tp_pct'] = round((tp - cur) / cur * 100, 2) if (cur and tp) else None
            q_extra = rt_extra.get(p.stock_code) or {}
            d['turnover'] = q_extra.get('turnover')
            d['volume_ratio'] = q_extra.get('volume_ratio')
            # 最新时间：实时行情时间戳 → HH:MM:SS；回退当日收盘则无
            ts = q_extra.get('ts')
            try:
                d['quote_time'] = _dt.fromtimestamp(int(ts)).strftime('%H:%M:%S') if ts else None
            except Exception:
                d['quote_time'] = None
            # 底部放量信号（由后台扫描写入，按 code 取用；无则空）
            sig = _bv_get(p.stock_code) if _bv_get else None
            d['rvol'] = sig.get('rvol') if sig else None
            d['amp_dn_pct'] = sig.get('amp_dn_pct') if sig else None
            d['bottom_vol'] = bool(sig.get('is_signal')) if sig else False
            d['signal_level'] = sig.get('level') if sig else None
            rows.append(d)

        return jsonify({
            'success': True,
            'as_of': _dt.now().strftime('%H:%M:%S'),
            'monitor_label': global_label,
            'autofetch': {
                'running': running,
                'in_session_now': in_session,
                'interval_sec': af.get('interval_sec'),
                'last_run': af.get('last_run'),
                'last_run_reason': af.get('last_run_reason'),
                'next_run': af.get('next_run'),
                # 最近一轮下载成败（供页面显示「下载状态：正常/失败」）
                'last_codes': af.get('last_codes'),
                'last_ok': af.get('last_ok'),
                'last_fail': af.get('last_fail'),
                'last_error': af.get('last_error'),
            },
            'rows': rows,
        })
    except Exception as e:
        logger.exception('盯盘面板加载失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/monitor/watch_details', methods=['GET'])
def get_monitor_watch_details():
    """盯盘明细：为盯盘面板每只持仓股生成三条盯盘判断（5m三均线 / 板块15m同步 / 放量）。

    行来源与 /api/trade/monitor 一致（active 计划，排除 MANUAL-<code>）。计算较重
    （逐只拉 5m + 板块 15m 打分，均带缓存），故前端在主表渲染后单独异步拉取本接口填充明细行。
    """
    try:
        from datetime import datetime as _dt
        from App.services.watch_detail_signal import evaluate_all

        trade_mode = (request.args.get('mode') or '').strip()
        plans = TradePlan.get_active_plans(trade_mode if trade_mode else None)
        plans = [p for p in plans if not (p.plan_id or '').startswith('MANUAL-')]

        details = {}
        for p in plans:
            try:
                details[p.stock_code] = evaluate_all(p.stock_code, p.stock_name)
            except Exception as e:
                details[p.stock_code] = {'code': p.stock_code, 'error': str(e)}

        return jsonify({
            'success': True,
            'as_of': _dt.now().strftime('%H:%M:%S'),
            'details': details,
        })
    except Exception as e:
        logger.exception('盯盘明细加载失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/signals/bottom_volume', methods=['GET'])
def get_bottom_volume_signals():
    """底部放量信号列表（持仓+关注，后台每 5 分钟扫描的最近结果）。

    返回 signals（全部评估结果，含未命中）、recent_alerts（新命中告警，带 fired_at）、
    push_channels（已配置的推送渠道）。前端据此渲染提醒区 + 触发弹窗/声音。
    """
    try:
        from App.services.bottom_volume_signal import get_signals
        data = get_signals()
        try:
            from App.services.notifier import channels_configured
            data['push_channels'] = channels_configured()
        except Exception:
            data['push_channels'] = []
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.exception('底部放量信号加载失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/<code>/volume_15m', methods=['GET'])
def get_volume_15m(code):
    """某股最近 N 根 15m 成交量 + 逐根 RVOL（当前量/前20根均量），供量能图。

    ?bars=40（20~160）。数据取 历史+当日实时 15m 合并（refresh=0 读缓存，快）。
    每根标注 hot（RVOL≥2 放量）/ faint（低于其滚动均量=缩量）。
    """
    try:
        import pandas as pd
        from App.services.realtime_data_service import merge_history_and_today_15m
        from App.services.bottom_volume_signal import RVOL_MIN, RVOL_N

        try:
            bars = int(request.args.get('bars', 40))
        except ValueError:
            bars = 40
        bars = max(20, min(bars, 160))

        res = merge_history_and_today_15m(code, refresh=False)
        df = res.get('df')
        if df is None or df.empty:
            return jsonify({'success': False, 'message': f'{code} 无 15m 数据'}), 404

        df = df.sort_values('date').reset_index(drop=True)
        vol = pd.to_numeric(df['volume'], errors='coerce')
        roll = vol.rolling(RVOL_N, min_periods=5).mean().shift(1)   # 前 N 根均量（不含当前）
        rvol = vol / roll

        sub = df.tail(bars)
        rows = []
        for i in sub.index:
            v = vol.iloc[i]
            rv = rvol.iloc[i]
            rmean = roll.iloc[i]
            hot = bool(pd.notna(rv) and rv >= RVOL_MIN)
            faint = bool((not hot) and pd.notna(rmean) and pd.notna(v) and v < rmean)
            rows.append({
                't': pd.to_datetime(sub['date'][i]).strftime('%m-%d %H:%M'),
                'volume': int(v) if pd.notna(v) else 0,
                'rvol': round(float(rv), 2) if pd.notna(rv) else None,
                'hot': hot, 'faint': faint,
            })

        base_avg = round(float(roll.iloc[-1]), 0) if pd.notna(roll.iloc[-1]) else None
        last_rvol = round(float(rvol.iloc[-1]), 2) if pd.notna(rvol.iloc[-1]) else None
        # 末根若在形成中，给出现量→预估全量（供前端画预估虚柱、标预估 RVOL）
        from App.services.volume_projection import current_bar_projection
        projection = current_bar_projection(
            df['date'].iloc[-1],
            float(vol.iloc[-1]) if pd.notna(vol.iloc[-1]) else None,
            base_avg)
        return jsonify({
            'success': True,
            'data': {
                'code': code,
                'bars': rows,
                'base_avg': base_avg,
                'last_rvol': last_rvol,
                'last_time': rows[-1]['t'] if rows else None,
                'rvol_min': RVOL_MIN,
                'projection': projection,
            },
        })
    except Exception as e:
        logger.exception(f'量能图数据失败 {code}')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/<code>/bottom_volume_history', methods=['GET'])
def get_bottom_volume_history(code):
    """历史底部放量扫描：?days=240&rvol=1.8&min_drop=0.05&stabilize=0。"""
    try:
        from App.services.bottom_volume_backtest import scan_history

        def _f(name, default):
            try:
                return float(request.args.get(name, default))
            except (TypeError, ValueError):
                return default

        days = int(_f('days', 240))
        rvol_min = _f('rvol', 1.8)
        min_drop = _f('min_drop', 0.05)
        stabilize = (request.args.get('stabilize') or '0').strip() in ('1', 'true', 'yes')

        data = scan_history(code, days=days, rvol_min=rvol_min,
                            min_drop=min_drop, require_stabilize=stabilize)
        if not data.get('ok'):
            return jsonify({'success': False, 'message': data.get('message', '无数据')}), 404
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.exception(f'历史底部放量扫描失败 {code}')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/watchlist/bottom_volume_scan', methods=['GET'])
def scan_watchlist_bottom_volume():
    """批量扫 持仓+关注 的底部放量适配度：?days=240&rvol=1.8&stabilize=0。"""
    try:
        from flask import current_app
        from App.services.bottom_volume_backtest import scan_watchlist

        def _f(name, default):
            try:
                return float(request.args.get(name, default))
            except (TypeError, ValueError):
                return default

        days = int(_f('days', 240))
        rvol_min = _f('rvol', 1.8)
        min_drop = _f('min_drop', 0.05)
        stabilize = (request.args.get('stabilize') or '0').strip() in ('1', 'true', 'yes')

        data = scan_watchlist(current_app._get_current_object(), days=days,
                              rvol_min=rvol_min, min_drop=min_drop, require_stabilize=stabilize)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.exception('自选池底部放量扫描失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@trade_plan_bp.route('/api/trade/signals/scan_now', methods=['POST'])
def scan_bottom_volume_now():
    """立即同步扫一轮底部放量（手动触发/自测），返回本轮命中数。"""
    try:
        from flask import current_app
        from App.services.bottom_volume_signal import scan_focus
        results, hits = scan_focus(current_app._get_current_object())
        return jsonify({
            'success': True,
            'data': {
                'scanned': len(results),
                'hits': len(hits),
                'hit_codes': [h['code'] for h in hits],
            },
        })
    except Exception as e:
        logger.exception('底部放量手动扫描失败')
        return jsonify({'success': False, 'message': str(e)}), 500
