"""
基金数据分析路由
提供基金持仓数据的分析功能
"""
import pandas as pd
import os
import logging
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, jsonify, request
from collections import Counter
import json

from App.models.data.FundsAwkward import get_funds_holdings_from_csv, list_available_dates

# 创建蓝图
fund_analysis_bp = Blueprint('fund_analysis_bp', __name__)

logger = logging.getLogger(__name__)


@fund_analysis_bp.route('/')
@fund_analysis_bp.route('/analysis')
def fund_analysis_index():
    """基金数据分析主页"""
    return render_template('data/fund_analysis.html')


@fund_analysis_bp.route('/api/get_available_dates', methods=['GET'])
def get_available_dates():
    """获取可用的数据日期"""
    try:
        dates = list_available_dates()
        return jsonify({
            'success': True,
            'dates': dates
        })
    except Exception as e:
        logger.error(f"获取可用日期失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@fund_analysis_bp.route('/api/hot_stocks', methods=['GET'])
def get_hot_stocks():
    """获取热门股票分析（被最多基金持有的股票）"""
    try:
        # 获取日期参数
        analysis_date = request.args.get('date')
        if not analysis_date:
            analysis_date = date.today().strftime('%Y%m%d')
        
        # 转换为日期对象
        try:
            date_obj = datetime.strptime(analysis_date, '%Y%m%d').date()
        except ValueError:
            return jsonify({
                'success': False,
                'error': '日期格式错误，请使用YYYYMMDD格式'
            }), 400
        
        # 获取基金持仓数据（限制为前500只基金）
        df = get_funds_holdings_from_csv(date_obj, limit_funds=500)
        
        if df.empty:
            return jsonify({
                'success': False,
                'error': f'没有找到 {analysis_date} 的数据'
            }), 404
        
        # 统计每只股票被多少基金持有
        stock_fund_count = df.groupby(['stock_code', 'stock_name']).agg({
            'fund_code': 'count',
            'holdings_ratio': ['mean', 'sum', 'max']
        }).round(2)
        
        # 重命名列
        stock_fund_count.columns = ['fund_count', 'avg_ratio', 'total_ratio', 'max_ratio']
        stock_fund_count = stock_fund_count.reset_index()
        
        # 按基金数量排序
        stock_fund_count = stock_fund_count.sort_values('fund_count', ascending=False)
        
        # 取前20名
        top_stocks = stock_fund_count.head(20).to_dict('records')
        
        return jsonify({
            'success': True,
            'date': analysis_date,
            'total_stocks': len(stock_fund_count),
            'total_funds': df['fund_code'].nunique(),
            'top_stocks': top_stocks
        })
        
    except Exception as e:
        logger.error(f"获取热门股票分析失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@fund_analysis_bp.route('/api/fund_distribution', methods=['GET'])
def get_fund_distribution():
    """获取基金分布分析（基金持仓股票数量分布）"""
    try:
        # 获取日期参数
        analysis_date = request.args.get('date')
        if not analysis_date:
            analysis_date = date.today().strftime('%Y%m%d')
        
        # 转换为日期对象
        try:
            date_obj = datetime.strptime(analysis_date, '%Y%m%d').date()
        except ValueError:
            return jsonify({
                'success': False,
                'error': '日期格式错误，请使用YYYYMMDD格式'
            }), 400
        
        # 获取基金持仓数据（限制为前500只基金）
        df = get_funds_holdings_from_csv(date_obj, limit_funds=500)
        
        if df.empty:
            return jsonify({
                'success': False,
                'error': f'没有找到 {analysis_date} 的数据'
            }), 404
        
        # 统计每个基金持有的股票数量
        fund_stock_count = df.groupby(['fund_code', 'fund_name']).size().reset_index(name='stock_count')
        
        # 计算分布统计
        distribution_stats = fund_stock_count['stock_count'].describe().to_dict()
        
        # 按股票数量分组统计基金数量
        distribution_groups = fund_stock_count.groupby(
            pd.cut(fund_stock_count['stock_count'], 
                   bins=[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 100],
                   labels=['1-5', '6-10', '11-15', '16-20', '21-25', 
                          '26-30', '31-35', '36-40', '41-45', '46-50', '50+'])
        ).size().to_dict()
        
        # 获取持仓股票最多的基金
        top_funds = fund_stock_count.sort_values('stock_count', ascending=False).head(10).to_dict('records')
        
        return jsonify({
            'success': True,
            'date': analysis_date,
            'distribution_stats': distribution_stats,
            'distribution_groups': distribution_groups,
            'top_funds': top_funds
        })
        
    except Exception as e:
        logger.error(f"获取基金分布分析失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@fund_analysis_bp.route('/api/industry_analysis', methods=['GET'])
def get_industry_analysis():
    """获取行业分析（基于股票代码前缀的行业分布）"""
    try:
        # 获取日期参数
        analysis_date = request.args.get('date')
        if not analysis_date:
            analysis_date = date.today().strftime('%Y%m%d')
        
        # 转换为日期对象
        try:
            date_obj = datetime.strptime(analysis_date, '%Y%m%d').date()
        except ValueError:
            return jsonify({
                'success': False,
                'error': '日期格式错误，请使用YYYYMMDD格式'
            }), 400
        
        # 获取基金持仓数据（限制为前500只基金）
        df = get_funds_holdings_from_csv(date_obj, limit_funds=500)
        
        if df.empty:
            return jsonify({
                'success': False,
                'error': f'没有找到 {analysis_date} 的数据'
            }), 404
        
        # 根据股票代码前缀判断市场
        def get_market(stock_code):
            if stock_code.startswith('6'):
                return '沪市主板'
            elif stock_code.startswith('0'):
                return '深市主板'
            elif stock_code.startswith('3'):
                return '创业板'
            elif stock_code.startswith('8'):
                return '北交所'
            elif stock_code.startswith('BK'):
                return '板块指数'
            else:
                return '其他'
        
        # 添加市场分类
        df['market'] = df['stock_code'].apply(get_market)
        
        # 按市场统计
        market_stats = df.groupby('market').agg({
            'stock_code': 'nunique',  # 股票数量
            'fund_code': 'nunique',   # 基金数量
            'holdings_ratio': ['mean', 'sum']  # 平均和总持仓比例
        }).round(2)
        
        # 重命名列
        market_stats.columns = ['stock_count', 'fund_count', 'avg_ratio', 'total_ratio']
        market_stats = market_stats.reset_index()
        
        # 按总持仓比例排序
        market_stats = market_stats.sort_values('total_ratio', ascending=False)
        
        # 获取每个市场的热门股票
        market_hot_stocks = {}
        for market in market_stats['market']:
            market_data = df[df['market'] == market]
            market_stocks = market_data.groupby(['stock_code', 'stock_name']).agg({
                'fund_code': 'count',
                'holdings_ratio': 'mean'
            }).round(2)
            market_stocks.columns = ['fund_count', 'avg_ratio']
            market_stocks = market_stocks.sort_values('fund_count', ascending=False)
            market_hot_stocks[market] = market_stocks.head(5).to_dict('records')
        
        return jsonify({
            'success': True,
            'date': analysis_date,
            'market_stats': market_stats.to_dict('records'),
            'market_hot_stocks': market_hot_stocks
        })
        
    except Exception as e:
        logger.error(f"获取行业分析失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@fund_analysis_bp.route('/api/fund_detail/<fund_code>', methods=['GET'])
def get_fund_detail(fund_code):
    """获取特定基金的详细持仓分析"""
    try:
        # 获取日期参数
        analysis_date = request.args.get('date')
        if not analysis_date:
            analysis_date = date.today().strftime('%Y%m%d')
        
        # 转换为日期对象
        try:
            date_obj = datetime.strptime(analysis_date, '%Y%m%d').date()
        except ValueError:
            return jsonify({
                'success': False,
                'error': '日期格式错误，请使用YYYYMMDD格式'
            }), 400
        
        # 获取基金持仓数据（限制为前500只基金）
        df = get_funds_holdings_from_csv(date_obj, limit_funds=500)
        
        if df.empty:
            return jsonify({
                'success': False,
                'error': f'没有找到 {analysis_date} 的数据'
            }), 404
        
        # 筛选特定基金的数据
        fund_data = df[df['fund_code'] == fund_code]
        
        if fund_data.empty:
            return jsonify({
                'success': False,
                'error': f'没有找到基金代码 {fund_code} 的数据'
            }), 404
        
        # 获取基金基本信息
        fund_info = {
            'fund_name': fund_data.iloc[0]['fund_name'],
            'fund_code': fund_code,
            'stock_count': len(fund_data),
            'total_ratio': fund_data['holdings_ratio'].sum(),
            'avg_ratio': fund_data['holdings_ratio'].mean(),
            'max_ratio': fund_data['holdings_ratio'].max()
        }
        
        # 按持仓比例排序
        holdings = fund_data.sort_values('holdings_ratio', ascending=False).to_dict('records')
        
        # 计算持仓集中度（前10大持仓占比）
        top10_ratio = fund_data.nlargest(10, 'holdings_ratio')['holdings_ratio'].sum()
        
        return jsonify({
            'success': True,
            'date': analysis_date,
            'fund_info': fund_info,
            'top10_ratio': round(top10_ratio, 2),
            'holdings': holdings
        })
        
    except Exception as e:
        logger.error(f"获取基金详情失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@fund_analysis_bp.route('/api/stock_detail/<stock_code>', methods=['GET'])
def get_stock_detail(stock_code):
    """获取特定股票的基金持仓分析"""
    try:
        # 获取日期参数
        analysis_date = request.args.get('date')
        if not analysis_date:
            analysis_date = date.today().strftime('%Y%m%d')
        
        # 转换为日期对象
        try:
            date_obj = datetime.strptime(analysis_date, '%Y%m%d').date()
        except ValueError:
            return jsonify({
                'success': False,
                'error': '日期格式错误，请使用YYYYMMDD格式'
            }), 400
        
        # 获取基金持仓数据（限制为前500只基金）
        df = get_funds_holdings_from_csv(date_obj, limit_funds=500)
        
        if df.empty:
            return jsonify({
                'success': False,
                'error': f'没有找到 {analysis_date} 的数据'
            }), 404
        
        # 筛选特定股票的数据
        stock_data = df[df['stock_code'] == stock_code]
        
        if stock_data.empty:
            return jsonify({
                'success': False,
                'error': f'没有找到股票代码 {stock_code} 的数据'
            }), 404
        
        # 获取股票基本信息
        stock_info = {
            'stock_name': stock_data.iloc[0]['stock_name'],
            'stock_code': stock_code,
            'fund_count': len(stock_data),
            'total_ratio': stock_data['holdings_ratio'].sum(),
            'avg_ratio': stock_data['holdings_ratio'].mean(),
            'max_ratio': stock_data['holdings_ratio'].max(),
            'min_ratio': stock_data['holdings_ratio'].min()
        }
        
        # 按持仓比例排序
        holdings = stock_data.sort_values('holdings_ratio', ascending=False).to_dict('records')
        
        return jsonify({
            'success': True,
            'date': analysis_date,
            'stock_info': stock_info,
            'holdings': holdings
        })
        
    except Exception as e:
        logger.error(f"获取股票详情失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@fund_analysis_bp.route('/api/trend_analysis', methods=['GET'])
def get_trend_analysis():
    """获取趋势分析（多日期数据对比）"""
    try:
        # 获取日期范围参数
        days = int(request.args.get('days', 7))  # 默认7天
        
        # 获取最近几天的数据
        dates = []
        for i in range(days):
            check_date = date.today() - timedelta(days=i)
            date_str = check_date.strftime('%Y%m%d')
            dates.append((check_date, date_str))
        
        trend_data = []
        
        for check_date, date_str in dates:
            try:
                df = get_funds_holdings_from_csv(check_date)
                if not df.empty:
                    # 统计基本信息
                    stats = {
                        'date': date_str,
                        'total_funds': df['fund_code'].nunique(),
                        'total_stocks': df['stock_code'].nunique(),
                        'total_records': len(df),
                        'avg_holdings_per_fund': round(len(df) / df['fund_code'].nunique(), 2) if df['fund_code'].nunique() > 0 else 0
                    }
                    
                    # 获取热门股票（被最多基金持有）
                    top_stocks = df.groupby('stock_code').size().sort_values(ascending=False).head(5)
                    stats['top_stocks'] = {
                        stock_code: count for stock_code, count in top_stocks.items()
                    }
                    
                    trend_data.append(stats)
            except Exception as e:
                logger.warning(f"获取 {date_str} 数据失败: {e}")
                continue
        
        # 按日期排序
        trend_data.sort(key=lambda x: x['date'])
        
        return jsonify({
            'success': True,
            'days': days,
            'trend_data': trend_data
        })
        
    except Exception as e:
        logger.error(f"获取趋势分析失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@fund_analysis_bp.route('/api/save_fund_holdings_to_daily', methods=['POST'])
def save_fund_holdings_to_daily():
    """将基金持仓分析结果保存到daily_stock_data表"""
    try:
        from App.models.data.StockDaily import update_fund_holdings_data
        
        # 获取请求参数
        data = request.get_json()
        analysis_date = data.get('date') if data else None
        
        # 更新基金持仓数据
        success = update_fund_holdings_data(analysis_date)
        
        if success:
            return jsonify({
                'success': True,
                'message': '基金持仓数据已成功保存到daily_stock_data表'
            })
        else:
            return jsonify({
                'success': False,
                'error': '保存基金持仓数据失败'
            }), 500
            
    except Exception as e:
        logger.error(f"保存基金持仓数据到daily_stock_data表失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

