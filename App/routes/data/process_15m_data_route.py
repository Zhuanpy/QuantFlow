import os
import pandas as pd
import logging
from flask import Blueprint, request, jsonify, render_template, send_file
from App.utils.file_utils import get_stock_data_path

# 延迟导入，避免启动时失败
try:
    from App.codes.utils.Normal import ResampleData
except ImportError:
    ResampleData = None

try:
    from App.codes.Signals.StatisticsMacd import SignalMethod
except ImportError:
    SignalMethod = None

try:
    from App.codes.parsers.MacdParser import *
except ImportError:
    pass

# 创建蓝图
process_data_bp = Blueprint('process_data', __name__, url_prefix='/process_data')

logger = logging.getLogger(__name__)

# 延迟导入plotly_charts，避免启动时的导入错误
def get_create_interactive_charts():
    """延迟导入图表创建函数"""
    try:
        from .plotly_charts import create_interactive_charts
        return create_interactive_charts
    except ImportError as e:
        logger.error(f"无法导入plotly_charts: {str(e)}")
        return None

def create_complete_standardized_data(stock_code, df_15m):
    """
    创建完整的标准化数据，包含所有技术指标和信号字段
    """

    # 复制数据并重置索引
    df_complete = df_15m.copy()
    df_complete = df_complete.reset_index(drop=True)
    
    # 确保date列存在且格式正确
    if 'date' not in df_complete.columns:
        logger.error("数据中缺少date列")
        return df_complete
    
    # 2. 定义需要标准化的字段（包括技术指标）
    cols_to_standardize = [
        # 基础OHLCV字段
        'open', 'high', 'low', 'close', 'volume', 'money',
        # MACD相关字段
        'EmaShort', 'EmaMid', 'EmaLong', 'DIF', 'DIFSm', 'DIFMl', 'DEA', 'MACD',
        # 布林带相关字段
        'BollMid', 'BollStd', 'BollUp', 'BollDn', 'StopLoss'
    ]
    
    # 3. 计算极值并进行Z-score标准化
    for col in cols_to_standardize:
        if col in df_complete.columns:
            data_col = df_complete[col].dropna()
            if len(data_col) > 0:
                mean_val = data_col.mean()
                std_val = data_col.std()
                
                if std_val > 0:
                    # 处理极端值（超过3个标准差的值）
                    upper_limit = mean_val + 3 * std_val
                    lower_limit = mean_val - 3 * std_val
                    
                    # 将极端值限制在合理范围内
                    df_complete.loc[df_complete[col] > upper_limit, col] = upper_limit
                    df_complete.loc[df_complete[col] < lower_limit, col] = lower_limit
                    
                    # Z-score标准化
                    df_complete[col] = (df_complete[col] - mean_val) / std_val
    
    # 4. 保留信号相关字段（不标准化）
    signal_columns = [Signal, SignalId, SignalChoice, SignalStartIndex]
    for col in signal_columns:
        if col not in df_complete.columns:
            df_complete[col] = None
    
    logger.info(f"创建完整标准化数据: {len(df_complete)} 条记录, {len(df_complete.columns)} 个字段")
    logger.info(f"包含字段: {list(df_complete.columns)}")
    
    return df_complete


def create_plotly_charts(stock_code, df_data):
    """
    旧的图表创建函数，已废弃，使用plotly_charts.py中的create_interactive_charts替代
    
    Args:
        stock_code: 股票代码
        df_data: 标准化数据DataFrame
    
    Returns:
        dict: 错误信息
    """
    logger.warning("create_plotly_charts函数已废弃，请使用plotly_charts.create_interactive_charts")
    return {'error': '此函数已废弃，请使用新的交互式图表功能'}

def load_extreme_values_cache():
    """加载极值缓存"""
    cache_file = "./cache/extreme_values.json"
    if os.path.exists(cache_file):
        try:
            import json
            with open(cache_file, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

@process_data_bp.route('/15m_data')
def process_15m_data_page():
    """15分钟数据处理页面"""
    return render_template('data/process_15m_data.html')

@process_data_bp.route('/api/process_15m_data', methods=['POST'])
def api_process_15m_data():
    """处理15分钟数据的API"""
    # 检查必要的模块是否已导入
    if ResampleData is None or SignalMethod is None:
        return jsonify({
            'success': False,
            'message': '数据处理模块未加载，请检查 App.codes 模块是否正确安装'
        }), 503
    
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')
        year = str(data.get('year'))  # 确保year是字符串类型
        quarter = data.get('quarter')
        processing_type = data.get('processing_type', 'resample')
        # 统一使用追加模式，去重以最新数据为准
        overwrite_mode = 'append'
        
        logger.info(f"开始处理15分钟数据: {stock_code}, {year}-{quarter}, 类型: {processing_type}")
        
        # 验证输入
        if not all([stock_code, year, quarter]):
            return jsonify({
                'success': False,
                'message': '请填写完整的股票代码、年份和季度信息'
            }), 400
        
        # 步骤1: 读取1分钟数据（包括当前季度和前一个季度）
        # 计算前一个季度
        prev_year = int(year)
        prev_quarter = f"Q{int(quarter[1]) - 1}"
        if prev_quarter == "Q0":
            prev_quarter = "Q4"
            prev_year = prev_year - 1
        
        # 读取当前季度的1分钟数据
        file_path_1m = get_stock_data_path(stock_code, data_type='1m', year=year, quarter=quarter)
        if not os.path.exists(file_path_1m):
            return jsonify({
                'success': False,
                'message': f'未找到1分钟数据文件: {file_path_1m}'
            }), 404
        
        # 读取1分钟数据（当前季度 + 前一季度）
        try:
            df_1m_current = pd.read_csv(file_path_1m, parse_dates=['date'])
            logger.info(f"成功读取当前季度1分钟数据: {len(df_1m_current)} 条记录")
            
            # 尝试读取前一个季度的数据（用于MACD计算的历史数据）
            file_path_1m_prev = get_stock_data_path(stock_code, data_type='1m', year=str(prev_year), quarter=prev_quarter)
            if os.path.exists(file_path_1m_prev):
                df_1m_prev = pd.read_csv(file_path_1m_prev, parse_dates=['date'])
                logger.info(f"成功读取前一季度1分钟数据: {len(df_1m_prev)} 条记录")
                # 合并前一季度和当前季度的数据
                df_1m_full = pd.concat([df_1m_prev, df_1m_current]).sort_values('date').reset_index(drop=True)
                logger.info(f"合并后的1分钟数据: {len(df_1m_full)} 条记录")
            else:
                logger.warning(f"未找到前一季度数据: {file_path_1m_prev}，将仅使用当前季度数据")
                df_1m_full = df_1m_current
            
            # 记录当前季度的起始日期，用于后续过滤
            current_quarter_start_date = df_1m_current['date'].min()
            logger.info(f"当前季度起始日期: {current_quarter_start_date}")
            
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'读取1分钟数据失败: {str(e)}'
            }), 500
        
        # 步骤2: 使用完整数据（含前一季度）重采样为15分钟数据
        try:
            df_15m_full = ResampleData.resample_1m_data(df_1m_full, '15m')
            if df_15m_full.empty:
                return jsonify({
                    'success': False,
                    'message': '15分钟数据重采样结果为空'
                }), 500
            
            logger.info(f"成功重采样为15分钟数据（含前一季度）: {len(df_15m_full)} 条记录")
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'15分钟数据重采样失败: {str(e)}'
            }), 500
        
        # 步骤3: 信号计算（使用完整数据，包含前一季度，确保MACD计算准确）
        df_15m_full = SignalMethod.signal_by_MACD_3ema(df_15m_full, df_1m_full)
        logger.info(f"信号计算完成（含前一季度）: {len(df_15m_full)} 条记录")
        
        # 步骤4: 过滤掉前一季度的数据，只保留当前季度
        df_15m = df_15m_full[df_15m_full['date'] >= current_quarter_start_date].copy()
        logger.info(f"过滤后的当前季度15分钟数据: {len(df_15m)} 条记录")
        
        # 清理旧的列名：删除 SignalTimes 列（已改名为 SignalId）
        if 'SignalTimes' in df_15m.columns:
            df_15m = df_15m.drop(columns=['SignalTimes'])
            logger.info("已删除旧的 SignalTimes 列")
        
        # 统计信号数量
        signals_up = 0
        signals_down = 0
        if SignalChoice in df_15m.columns:
            signal_values = df_15m[SignalChoice].dropna()
            if len(signal_values) > 0:
                signals_up = len(signal_values[signal_values == up])
                signals_down = len(signal_values[signal_values == down])
                logger.info(f"信号统计: 上涨 {signals_up} 个, 下跌 {signals_down} 个")
        
        # 步骤5: 保存带信号的15分钟普通数据
        file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')
        try:
            # 统一使用追加模式，去重以最新数据为准
            if os.path.exists(file_path_15m):
                # 读取现有数据并合并
                existing_data = pd.read_csv(file_path_15m, parse_dates=['date'])
                # 清理旧数据中的 SignalTimes 列
                if 'SignalTimes' in existing_data.columns:
                    existing_data = existing_data.drop(columns=['SignalTimes'])
                    logger.info("已从旧数据中删除 SignalTimes 列")
                combined_data = pd.concat([existing_data, df_15m]).drop_duplicates(subset=['date'], keep='last')
                combined_data = combined_data.sort_values('date')
                df_15m = combined_data
                logger.info(f"合并现有15分钟数据，保留最新记录")
            
            df_15m.to_csv(file_path_15m, index=False)
            logger.info(f"成功保存15分钟普通数据（含信号）: {file_path_15m}")
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'保存15分钟普通数据失败: {str(e)}'
            }), 500
        
        result_data = {
            'stock_code': stock_code,
            'year': year,
            'quarter': quarter,
            'processing_type': processing_type,
            'file_paths': [
                {
                    'type': '1分钟数据',
                    'path': file_path_1m
                },
                {
                    'type': '15分钟普通数据（含信号）',
                    'path': file_path_15m
                }
            ],
            'statistics': {
                'records_1m_current': len(df_1m_current),
                'records_1m_with_prev': len(df_1m_full),
                'records_15m': len(df_15m),
                'signals_up': signals_up,
                'signals_down': signals_down
            }
        }
        
        # 步骤6: 完整标准化处理（仅在standardized模式下）
        if processing_type == 'standardized':
            try:
                # 基于已包含信号的15分钟普通数据创建标准化数据
                df_standardized = create_complete_standardized_data(stock_code, df_15m)
                
                # 保存完整标准化数据
                file_path_standardized = get_stock_data_path(stock_code, data_type='15m_standardized')
                
                # 统一使用追加模式，去重以最新数据为准
                if os.path.exists(file_path_standardized):
                    # 读取现有数据并合并
                    existing_data = pd.read_csv(file_path_standardized, parse_dates=['date'])
                    # 清理旧数据中的 SignalTimes 列
                    if 'SignalTimes' in existing_data.columns:
                        existing_data = existing_data.drop(columns=['SignalTimes'])
                        logger.info("已从旧标准化数据中删除 SignalTimes 列")
                    combined_data = pd.concat([existing_data, df_standardized]).drop_duplicates(subset=['date'], keep='last')
                    combined_data = combined_data.sort_values('date')
                    df_standardized = combined_data
                    logger.info(f"合并现有15分钟标准化数据，保留最新记录")
                
                df_standardized.to_csv(file_path_standardized, index=False)
                logger.info(f"成功保存15分钟完整标准化数据（用于模型训练）: {file_path_standardized}")
                
                result_data['file_paths'].append({
                    'type': '15分钟标准化数据（用于模型训练）',
                    'path': file_path_standardized
                })
                
                # 提示：可使用交互式图表查看数据
                logger.info(f"标准化数据已保存，可通过交互式图表查看: {stock_code}")
                
            except Exception as e:
                logger.error(f"完整标准化处理失败: {str(e)}")
                import traceback
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                return jsonify({
                    'success': False,
                    'message': f'完整标准化处理失败: {str(e)}'
                }), 500
        
        # 步骤7: 完成处理
        result_data['message'] = f'成功处理 {stock_code} 的15分钟数据'
        result_data['success'] = True
        
        logger.info(f"15分钟数据处理完成: {stock_code}")
        return jsonify(result_data)
        
    except Exception as e:
        logger.error(f"处理15分钟数据时发生错误: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'处理过程中发生错误: {str(e)}'
        }), 500

@process_data_bp.route('/api/create_visualization', methods=['POST'])
def api_create_visualization():
    """创建15分钟标准化数据的交互式图表API（使用Plotly）"""
    try:
        # 先检查Plotly是否可用
        try:
            from .plotly_charts import HAS_PLOTLY
            if not HAS_PLOTLY:
                return jsonify({
                    'success': False,
                    'message': 'Plotly未安装。请运行: pip install plotly'
                }), 500
        except Exception as e:
            logger.error(f"检查Plotly时出错: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'Plotly模块加载失败: {str(e)}'
            }), 500
        
        data = request.get_json()
        stock_code = data.get('stock_code')
        data_type = data.get('data_type', '15m_standardized')
        year = data.get('year')  # 新增：获取年份
        quarter = data.get('quarter')  # 新增：获取季度
        
        logger.info(f"开始创建交互式图表: {stock_code}, 数据类型: {data_type}, 时间范围: {year}-{quarter}")
        
        # 验证输入
        if not stock_code:
            return jsonify({
                'success': False,
                'message': '请提供股票代码'
            }), 400
        
        # 检查数据文件是否存在
        file_path = get_stock_data_path(stock_code, data_type=data_type)
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': f'未找到数据文件: {file_path}'
            }), 404
        
        # 读取数据
        try:
            df = pd.read_csv(file_path, parse_dates=['date'])
            logger.info(f"成功读取完整数据: {len(df)} 条记录")
            
            # 根据年份和季度过滤数据
            if year and quarter:
                # 将year转换为整数
                year_int = int(year)
                quarter_num = int(quarter[1])  # 从 "Q1" 提取 1
                
                # 计算季度的起止日期
                quarter_start_month = (quarter_num - 1) * 3 + 1
                quarter_end_month = quarter_num * 3
                
                start_date = pd.Timestamp(year=year_int, month=quarter_start_month, day=1)
                if quarter_end_month == 12:
                    end_date = pd.Timestamp(year=year_int, month=12, day=31, hour=23, minute=59, second=59)
                else:
                    end_date = pd.Timestamp(year=year_int, month=quarter_end_month + 1, day=1) - pd.Timedelta(seconds=1)
                
                # 过滤数据
                df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
                logger.info(f"根据 {year}-{quarter} 过滤后的数据: {len(df)} 条记录 (时间范围: {start_date} 到 {end_date})")
                
                if len(df) == 0:
                    return jsonify({
                        'success': False,
                        'message': f'所选时间范围 {year}-{quarter} 内没有数据'
                    }), 404
            
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'读取数据失败: {str(e)}'
            }), 500
        
        # 获取图表创建函数
        create_func = get_create_interactive_charts()
        if create_func is None:
            return jsonify({
                'success': False,
                'message': 'Plotly模块加载失败，请确保已安装: pip install plotly'
            }), 500
        
        # 创建交互式图表
        charts = create_func(stock_code, df)
        
        if 'error' in charts:
            return jsonify({
                'success': False,
                'message': charts['error']
            }), 500
        
        return jsonify({
            'success': True,
            'charts': charts,
            'message': f'成功生成 {len(charts)} 个交互式图表'
        })
            
    except Exception as e:
        logger.error(f"创建交互式图表时发生错误: {str(e)}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'message': f'创建图表过程中发生错误: {str(e)}'
        }), 500

@process_data_bp.route('/api/test_plotly')
def test_plotly():
    """测试Plotly是否正常工作"""
    try:
        import plotly.graph_objs as go
        
        # 创建一个简单的测试图表
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[1, 2, 3, 4], y=[10, 11, 12, 13], mode='lines+markers', name='测试'))
        fig.update_layout(title='Plotly测试图表', xaxis_title='X轴', yaxis_title='Y轴')
        
        # 返回HTML
        html = fig.to_html(full_html=True, include_plotlyjs='cdn')
        return html
    except Exception as e:
        return f"<html><body><h1>错误</h1><p>{str(e)}</p></body></html>"

@process_data_bp.route('/api/check_data_fields/<stock_code>')
def check_data_fields(stock_code):
    """检查15分钟标准化数据包含哪些字段"""
    try:
        file_path = get_stock_data_path(stock_code, data_type='15m_standardized')
        if not os.path.exists(file_path):
            return jsonify({
                'error': f'文件不存在: {file_path}'
            })
        
        df = pd.read_csv(file_path, nrows=5)
        
        return jsonify({
            'file_path': file_path,
            'total_rows': len(df),
            'columns': list(df.columns),
            'sample_data': df.head(2).to_dict('records'),
            'has_price': all(col in df.columns for col in ['open', 'high', 'low', 'close']),
            'has_macd': all(col in df.columns for col in ['DIF', 'DEA', 'MACD']),
            'has_boll': all(col in df.columns for col in ['BollUp', 'BollMid', 'BollDn']),
            'has_volume': 'volume' in df.columns,
            'has_ema': all(col in df.columns for col in ['EmaShort', 'EmaMid', 'EmaLong'])
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@process_data_bp.route('/api/view_chart/<filename>')
def view_chart(filename):
    """查看生成的图表"""
    try:
        # 构建图表文件路径
        chart_dir = os.path.join(os.path.dirname(get_stock_data_path('dummy', data_type='15m_standardized')), 'charts')
        chart_path = os.path.join(chart_dir, filename)
        
        if os.path.exists(chart_path):
            return send_file(chart_path, mimetype='image/png')
        else:
            return jsonify({'error': '图表文件不存在'}), 404
            
    except Exception as e:
        logger.error(f"查看图表时发生错误: {str(e)}")
        return jsonify({'error': str(e)}), 500

@process_data_bp.route('/api/check_15m_data', methods=['POST'])
def api_check_15m_data():
    """检查15分钟数据的API"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')
        year = str(data.get('year'))  # 确保year是字符串类型
        quarter = data.get('quarter')
        
        logger.info(f"检查15分钟数据: {stock_code}, {year}-{quarter}")
        
        # 验证输入
        if not all([stock_code, year, quarter]):
            return jsonify({
                'success': False,
                'message': '请填写完整的股票代码、年份和季度信息'
            }), 400
        
        # 检查1分钟数据（需要year和quarter）
        file_path_1m = get_stock_data_path(stock_code, data_type='1m', year=year, quarter=quarter)
        has_1m_data = os.path.exists(file_path_1m)
        
        # 检查15分钟原始数据（统一文件，不需要year和quarter）
        file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')
        has_15m_data = os.path.exists(file_path_15m)
        
        # 检查15分钟标准化数据（统一文件，不需要year和quarter）
        file_path_standardized = get_stock_data_path(stock_code, data_type='15m_standardized')
        has_standardized_data = os.path.exists(file_path_standardized)
        
        # 信号数据现在包含在标准化数据中，不需要单独检查
        has_signal_data = has_standardized_data  # 标准化数据包含信号
        
        # 统计信息
        stats = {}
        if has_1m_data:
            try:
                df_1m = pd.read_csv(file_path_1m, parse_dates=['date'])
                stats['records_1m'] = int(len(df_1m))  # 转换为Python int
            except:
                stats['records_1m'] = '读取失败'
        
        if has_15m_data:
            try:
                df_15m = pd.read_csv(file_path_15m, parse_dates=['date'])
                stats['records_15m'] = int(len(df_15m))  # 转换为Python int
            except:
                stats['records_15m'] = '读取失败'
        
        if has_standardized_data:
            try:
                df_std = pd.read_csv(file_path_standardized, parse_dates=['date'])
                stats['records_standardized'] = int(len(df_std))  # 转换为Python int
                # 检查是否包含信号数据
                if Signal in df_std.columns:
                    signal_count = df_std[Signal].dropna().count()
                    stats['records_signals'] = int(signal_count)  # 转换为Python int
                else:
                    stats['records_signals'] = 0
            except:
                stats['records_standardized'] = '读取失败'
                stats['records_signals'] = '读取失败'
        
        # 构建检查结果消息
        message_parts = []
        message_parts.append(f"股票代码: {stock_code}")
        message_parts.append(f"时间范围: {year}-{quarter}")
        message_parts.append("")
        message_parts.append("数据文件状态:")
        message_parts.append(f"  1分钟数据: {'✅ 存在' if has_1m_data else '❌ 不存在'} ({file_path_1m})")
        message_parts.append(f"  15分钟原始数据: {'✅ 存在' if has_15m_data else '❌ 不存在'} ({file_path_15m})")
        message_parts.append(f"  15分钟标准化数据: {'✅ 存在' if has_standardized_data else '❌ 不存在'} ({file_path_standardized})")
        message_parts.append(f"  信号数据: {'✅ 存在' if has_signal_data else '❌ 不存在'} (包含在标准化数据中)")
        
        if stats:
            message_parts.append("")
            message_parts.append("数据统计:")
            for key, value in stats.items():
                message_parts.append(f"  {key}: {value}")
        
        return jsonify({
            'success': True,
            'message': '\n'.join(message_parts),
            'data_status': {
                'has_1m_data': has_1m_data,
                'has_15m_data': has_15m_data,
                'has_standardized_data': has_standardized_data,
                'has_signal_data': has_signal_data
            },
            'statistics': stats
        })
        
    except Exception as e:
        logger.error(f"检查15分钟数据时发生错误: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'检查数据时发生错误: {str(e)}'
        }), 500

@process_data_bp.route('/api/get_interactive_charts', methods=['POST'])
def api_get_interactive_charts():
    """获取15分钟数据的交互式图表"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')
        data_type = data.get('data_type', '15m_standardized')  # 默认使用标准化数据
        
        logger.info(f"请求生成交互式图表: {stock_code}, 数据类型: {data_type}")
        
        # 验证输入
        if not stock_code:
            return jsonify({
                'success': False,
                'message': '请提供股票代码'
            }), 400
        
        # 检查数据文件是否存在
        file_path = get_stock_data_path(stock_code, data_type=data_type)
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': f'未找到数据文件: {file_path}'
            }), 404
        
        # 读取数据
        try:
            df = pd.read_csv(file_path, parse_dates=['date'])
            logger.info(f"成功读取数据: {len(df)} 条记录")
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'读取数据失败: {str(e)}'
            }), 500
        
        # 获取图表创建函数
        create_func = get_create_interactive_charts()
        if create_func is None:
            return jsonify({
                'success': False,
                'message': 'Plotly模块加载失败，请确保已安装: pip install plotly'
            }), 500
        
        # 生成交互式图表
        charts = create_func(stock_code, df)
        
        if 'error' in charts:
            return jsonify({
                'success': False,
                'message': charts['error']
            }), 500
        
        return jsonify({
            'success': True,
            'charts': charts,
            'message': f'成功生成 {len(charts)} 个交互式图表'
        })
        
    except Exception as e:
        logger.error(f"获取交互式图表时发生错误: {str(e)}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'message': f'生成图表过程中发生错误: {str(e)}'
        }), 500
