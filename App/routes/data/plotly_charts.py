# -*- coding: utf-8 -*-
"""
15分钟数据的Plotly交互式图表生成模块
"""
import pandas as pd
import logging

logger = logging.getLogger(__name__)

try:
    import plotly
    import plotly.graph_objs as go
    from plotly.subplots import make_subplots
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False
    logger.warning("Plotly未安装，将无法生成交互式图表")


def create_interactive_charts(stock_code, df_data):
    """
    创建交互式Plotly图表配置
    
    Args:
        stock_code: 股票代码
        df_data: DataFrame，包含15分钟数据
    
    Returns:
        dict: 包含多个图表HTML的字典
    """
    if not HAS_PLOTLY:
        return {'error': 'Plotly未安装，无法生成交互式图表'}
    
    try:
        # 确保date列是datetime类型
        df = df_data.copy()
        df['date'] = pd.to_datetime(df['date'])
        
        # 如果数据量过大，进行采样以提高性能
        original_length = len(df)
        if original_length > 2000:
            # 每N行取一个，保持时间顺序
            sample_rate = original_length // 2000
            df = df.iloc[::sample_rate].copy()
            logger.info(f"数据采样: 从 {original_length} 条采样到 {len(df)} 条")
        
        charts = {}
        
        # 1. 价格折线图 + 趋势区间 + 交易信号
        if 'close' in df.columns:
            fig_price = go.Figure()
            
            # 添加趋势背景区间（Signal列：1=上涨，-1=下跌）
            if 'Signal' in df.columns:
                try:
                    # 过滤有效的Signal值
                    df_with_signal = df[df['Signal'].notna()].copy()
                    
                    # 转换Signal为数值类型（处理可能的字符串）
                    df_with_signal['Signal'] = pd.to_numeric(df_with_signal['Signal'], errors='coerce')
                    
                    # 过滤有效的数值（1或-1）
                    df_with_signal = df_with_signal[df_with_signal['Signal'].notna()].copy()
                    df_with_signal = df_with_signal[df_with_signal['Signal'].isin([1, -1, 1.0, -1.0])].copy()
                    df_with_signal = df_with_signal.reset_index(drop=True)
                    
                    logger.info(f"Signal数据: 总数={len(df_with_signal)}, 唯一值={df_with_signal['Signal'].unique()}, 数据类型={df_with_signal['Signal'].dtype}")
                    
                    if len(df_with_signal) > 1:
                        # 添加一个辅助列标记Signal变化
                        df_with_signal['signal_change'] = df_with_signal['Signal'].diff().fillna(0) != 0
                        
                        # 遍历每个趋势段
                        current_signal = None
                        segment_start_date = None
                        
                        for i in range(len(df_with_signal)):
                            row_signal = df_with_signal.iloc[i]['Signal']
                            row_date = df_with_signal.iloc[i]['date']
                            row_change = df_with_signal.iloc[i]['signal_change']
                            
                            # 确保row_signal是数值
                            try:
                                row_signal = float(row_signal)
                            except:
                                continue
                            
                            if current_signal is None or row_change:
                                # 保存上一段
                                if current_signal is not None and segment_start_date is not None:
                                    segment_end_date = row_date
                                    color = 'rgba(255, 200, 200, 0.3)' if current_signal > 0 else 'rgba(200, 200, 255, 0.3)'
                                    
                                    # 确保日期有效
                                    if pd.notna(segment_start_date) and pd.notna(segment_end_date):
                                        fig_price.add_vrect(
                                            x0=segment_start_date, 
                                            x1=segment_end_date,
                                            fillcolor=color,
                                            layer="below",
                                            line_width=0
                                        )
                                        
                                        # 添加SignalId标注（如果存在）
                                        if 'SignalId' in df_with_signal.columns:
                                            # 找到这个区间的SignalId
                                            segment_mask = (df_with_signal['date'] >= segment_start_date) & (df_with_signal['date'] < segment_end_date)
                                            segment_data = df_with_signal[segment_mask]
                                            if len(segment_data) > 0:
                                                signal_id = segment_data.iloc[0]['SignalId']
                                                if pd.notna(signal_id):
                                                    # 在区间中间位置添加文本
                                                    mid_date = segment_start_date + (segment_end_date - segment_start_date) / 2
                                                    # 获取这个时间段的最高价格，在其上方显示SignalId
                                                    segment_prices = segment_data['close']
                                                    if len(segment_prices) > 0:
                                                        max_price = segment_prices.max()
                                                        
                                                        # 构建标注文本：SignalId + 趋势方向 + 3个指标
                                                        trend_label = "📈上涨" if current_signal > 0 else "📉下跌"
                                                        annotation_text = f"ID: {signal_id} ({trend_label})"
                                                        
                                                        # 添加CycleLengthMax
                                                        if 'CycleLengthMax' in segment_data.columns:
                                                            cycle_length = segment_data.iloc[0]['CycleLengthMax']
                                                            if pd.notna(cycle_length):
                                                                annotation_text += f"<br>周期长度: {cycle_length}"
                                                        
                                                        # 添加CycleAmplitudeMax
                                                        if 'CycleAmplitudeMax' in segment_data.columns:
                                                            cycle_amp = segment_data.iloc[0]['CycleAmplitudeMax']
                                                            if pd.notna(cycle_amp):
                                                                annotation_text += f"<br>振幅: {cycle_amp:.2f}"
                                                        
                                                        # 添加Cycle1mVolMax5
                                                        if 'Cycle1mVolMax5' in segment_data.columns:
                                                            cycle_vol = segment_data.iloc[0]['Cycle1mVolMax5']
                                                            if pd.notna(cycle_vol):
                                                                annotation_text += f"<br>成交量: {cycle_vol:.0f}"
                                                        
                                                        fig_price.add_annotation(
                                                            x=mid_date,
                                                            y=max_price,
                                                            text=annotation_text,
                                                            showarrow=False,
                                                            yshift=10,
                                                            font=dict(size=9, color='#333'),
                                                            bgcolor='rgba(255, 255, 255, 0.85)',
                                                            bordercolor='gray',
                                                            borderwidth=1,
                                                            borderpad=3,
                                                            align='left'
                                                        )
                                
                                # 开始新段
                                current_signal = row_signal
                                segment_start_date = row_date
                        
                        # 处理最后一段
                        if current_signal is not None and segment_start_date is not None:
                            segment_end_date = df_with_signal.iloc[-1]['date']
                            color = 'rgba(255, 200, 200, 0.3)' if current_signal > 0 else 'rgba(200, 200, 255, 0.3)'
                            
                            # 确保日期有效
                            if pd.notna(segment_start_date) and pd.notna(segment_end_date):
                                fig_price.add_vrect(
                                    x0=segment_start_date, 
                                    x1=segment_end_date,
                                    fillcolor=color,
                                    layer="below",
                                    line_width=0
                                )
                                
                                # 添加SignalId标注（如果存在）
                                if 'SignalId' in df_with_signal.columns:
                                    # 找到这个区间的SignalId
                                    segment_mask = (df_with_signal['date'] >= segment_start_date) & (df_with_signal['date'] <= segment_end_date)
                                    segment_data = df_with_signal[segment_mask]
                                    if len(segment_data) > 0:
                                        signal_id = segment_data.iloc[0]['SignalId']
                                        if pd.notna(signal_id):
                                            # 在区间中间位置添加文本
                                            mid_date = segment_start_date + (segment_end_date - segment_start_date) / 2
                                            # 获取这个时间段的最高价格，在其上方显示SignalId
                                            segment_prices = segment_data['close']
                                            if len(segment_prices) > 0:
                                                max_price = segment_prices.max()
                                                
                                                # 构建标注文本：SignalId + 趋势方向 + 3个指标
                                                trend_label = "📈上涨" if current_signal > 0 else "📉下跌"
                                                annotation_text = f"ID: {signal_id} ({trend_label})"
                                                
                                                # 添加CycleLengthMax
                                                if 'CycleLengthMax' in segment_data.columns:
                                                    cycle_length = segment_data.iloc[0]['CycleLengthMax']
                                                    if pd.notna(cycle_length):
                                                        annotation_text += f"<br>周期长度: {cycle_length}"
                                                
                                                # 添加CycleAmplitudeMax
                                                if 'CycleAmplitudeMax' in segment_data.columns:
                                                    cycle_amp = segment_data.iloc[0]['CycleAmplitudeMax']
                                                    if pd.notna(cycle_amp):
                                                        annotation_text += f"<br>振幅: {cycle_amp:.2f}"
                                                
                                                # 添加Cycle1mVolMax5
                                                if 'Cycle1mVolMax5' in segment_data.columns:
                                                    cycle_vol = segment_data.iloc[0]['Cycle1mVolMax5']
                                                    if pd.notna(cycle_vol):
                                                        annotation_text += f"<br>成交量: {cycle_vol:.0f}"
                                                
                                                fig_price.add_annotation(
                                                    x=mid_date,
                                                    y=max_price,
                                                    text=annotation_text,
                                                    showarrow=False,
                                                    yshift=10,
                                                    font=dict(size=9, color='#333'),
                                                    bgcolor='rgba(255, 255, 255, 0.85)',
                                                    bordercolor='gray',
                                                    borderwidth=1,
                                                    borderpad=3,
                                                    align='left'
                                                )
                        
                        # 添加图例（使用虚拟trace，需要一个有效的点但不显示）
                        first_date = df['date'].iloc[0] if len(df) > 0 else pd.Timestamp.now()
                        dummy_y = df['close'].mean() if 'close' in df.columns else 0
                        
                        fig_price.add_trace(go.Scatter(
                            x=[first_date], y=[dummy_y],
                            mode='markers',
                            marker=dict(size=10, color='rgba(255, 200, 200, 0.6)', symbol='square'),
                            showlegend=True,
                            name='上涨区间',
                            hoverinfo='skip',
                            opacity=0  # 不显示，只用于图例
                        ))
                        fig_price.add_trace(go.Scatter(
                            x=[first_date], y=[dummy_y],
                            mode='markers',
                            marker=dict(size=10, color='rgba(200, 200, 255, 0.6)', symbol='square'),
                            showlegend=True,
                            name='下跌区间',
                            hoverinfo='skip',
                            opacity=0  # 不显示，只用于图例
                        ))
                except Exception as e:
                    logger.error(f"添加趋势背景区间失败: {str(e)}")
                    # 继续绘制其他内容，不影响整体图表
            
            # 收盘价折线图
            fig_price.add_trace(go.Scatter(
                x=df['date'],
                y=df['close'],
                mode='lines',
                name='收盘价',
                line=dict(color='blue', width=2),
                hovertemplate='日期: %{x}<br>收盘价: %{y:.2f}<extra></extra>'
            ))
            
            # 添加买卖信号标记
            if 'SignalChoice' in df.columns:
                buy_signals = df[df['SignalChoice'] == 'up']
                sell_signals = df[df['SignalChoice'] == 'down']
                
                if not buy_signals.empty:
                    fig_price.add_trace(go.Scatter(
                        x=buy_signals['date'],
                        y=buy_signals['close'],
                        mode='markers',
                        name='买入信号',
                        marker=dict(symbol='triangle-up', size=12, color='red'),
                        hovertemplate='买入<br>日期: %{x}<br>价格: %{y:.2f}<extra></extra>'
                    ))
                
                if not sell_signals.empty:
                    fig_price.add_trace(go.Scatter(
                        x=sell_signals['date'],
                        y=sell_signals['close'],
                        mode='markers',
                        name='卖出信号',
                        marker=dict(symbol='triangle-down', size=12, color='green'),
                        hovertemplate='卖出<br>日期: %{x}<br>价格: %{y:.2f}<extra></extra>'
                    ))
            
            fig_price.update_layout(
                title=f'{stock_code} - 价格走势与交易信号',
                xaxis_title='时间',
                yaxis_title='价格',
                hovermode='x unified',
                height=500,
                template='plotly_white',
                xaxis_rangeslider_visible=False
            )
            
            # 前端已加载Plotly.js，这里不需要包含
            charts['price'] = fig_price.to_html(
                full_html=False, 
                include_plotlyjs=False,
                div_id='price-chart'
            )
        
        # 2. MACD指标图
        if all(col in df.columns for col in ['DIF', 'DEA', 'MACD']):
            fig_macd = make_subplots(rows=2, cols=1, row_heights=[0.7, 0.3], vertical_spacing=0.03,
                                      subplot_titles=('MACD指标', 'MACD柱状图'))
            
            # DIF和DEA线
            fig_macd.add_trace(go.Scatter(
                x=df['date'], y=df['DIF'],
                mode='lines', name='DIF', line=dict(color='blue', width=2)
            ), row=1, col=1)
            
            fig_macd.add_trace(go.Scatter(
                x=df['date'], y=df['DEA'],
                mode='lines', name='DEA', line=dict(color='orange', width=2)
            ), row=1, col=1)
            
            # MACD柱状图
            colors = ['red' if val >= 0 else 'green' for val in df['MACD']]
            fig_macd.add_trace(go.Bar(
                x=df['date'], y=df['MACD'],
                name='MACD', marker_color=colors,
                hovertemplate='MACD: %{y:.4f}<extra></extra>'
            ), row=2, col=1)
            
            fig_macd.update_xaxes(title_text='时间', row=2, col=1)
            fig_macd.update_yaxes(title_text='DIF/DEA值', row=1, col=1)
            fig_macd.update_yaxes(title_text='MACD值', row=2, col=1)
            
            fig_macd.update_layout(
                title=f'{stock_code} - MACD指标分析',
                hovermode='x unified',
                height=600,
                template='plotly_white',
                showlegend=True
            )
            
            charts['macd'] = fig_macd.to_html(
                full_html=False, 
                include_plotlyjs=False,
                div_id='macd-chart'
            )
        
        # 3. 布林带指标图 - 已移除
        
        # 4. 成交量图
        if 'volume' in df.columns:
            fig_vol = go.Figure()
            
            # 成交量柱状图
            colors = ['red' if df.loc[i, 'close'] >= df.loc[i, 'open'] else 'green' 
                      for i in df.index if 'close' in df.columns and 'open' in df.columns]
            
            fig_vol.add_trace(go.Bar(
                x=df['date'], y=df['volume'],
                name='成交量', marker_color=colors if colors else 'blue',
                hovertemplate='成交量: %{y:,.0f}<extra></extra>'
            ))
            
            # 如果有Daily1mVolMax字段，添加为线条
            vol_indicators = ['Daily1mVolMax1', 'Daily1mVolMax5', 'Daily1mVolMax15']
            for vol_ind in vol_indicators:
                if vol_ind in df.columns:
                    fig_vol.add_trace(go.Scatter(
                        x=df['date'], y=df[vol_ind],
                        mode='lines', name=vol_ind,
                        line=dict(width=2)
                    ))
            
            fig_vol.update_layout(
                title=f'{stock_code} - 成交量分析',
                xaxis_title='时间',
                yaxis_title='成交量',
                hovermode='x unified',
                height=400,
                template='plotly_white'
            )
            
            charts['volume'] = fig_vol.to_html(
                full_html=False, 
                include_plotlyjs=False,
                div_id='volume-chart'
            )
        
        # 5. EMA指标图
        if all(col in df.columns for col in ['EmaShort', 'EmaMid', 'EmaLong', 'close']):
            fig_ema = go.Figure()
            
            # 收盘价
            fig_ema.add_trace(go.Scatter(
                x=df['date'], y=df['close'],
                mode='lines', name='收盘价',
                line=dict(color='blue', width=2)
            ))
            
            # EMA线
            fig_ema.add_trace(go.Scatter(
                x=df['date'], y=df['EmaShort'],
                mode='lines', name='EMA短期',
                line=dict(color='red', width=1.5)
            ))
            
            fig_ema.add_trace(go.Scatter(
                x=df['date'], y=df['EmaMid'],
                mode='lines', name='EMA中期',
                line=dict(color='orange', width=1.5)
            ))
            
            fig_ema.add_trace(go.Scatter(
                x=df['date'], y=df['EmaLong'],
                mode='lines', name='EMA长期',
                line=dict(color='green', width=1.5)
            ))
            
            fig_ema.update_layout(
                title=f'{stock_code} - EMA均线分析',
                xaxis_title='时间',
                yaxis_title='价格',
                hovermode='x unified',
                height=500,
                template='plotly_white'
            )
            
            charts['ema'] = fig_ema.to_html(
                full_html=False, 
                include_plotlyjs=False,
                div_id='ema-chart'
            )
        
        if not charts:
            return {'error': '无法生成图表：数据中缺少必要的字段'}
        
        logger.info(f"成功生成 {len(charts)} 个交互式图表")
        return charts
        
    except Exception as e:
        logger.error(f"生成交互式图表失败: {str(e)}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
        return {'error': str(e)}

