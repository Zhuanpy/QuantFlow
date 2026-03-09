"""
交易计划管理路由
提供模拟交易和实盘交易的管理功能
"""
from flask import Blueprint, render_template, request, jsonify
from App.exts import db
from App.models.trade import TradePlan
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
