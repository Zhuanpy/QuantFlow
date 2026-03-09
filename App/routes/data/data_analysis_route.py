"""
1分钟数据完整性分析路由
分析已下载数据的完整性、质量和统计信息
"""
from flask import Blueprint, render_template, request, jsonify
import os
import pandas as pd
from datetime import datetime, timedelta, date
import logging
from config import Config
import glob

# 创建蓝图
data_analysis_bp = Blueprint('data_analysis_bp', __name__)

# A股交易时间常量
MINUTES_PER_DAY = 240  # 每天约240分钟（上午120 + 下午120）
MIN_RECORDS_THRESHOLD = 200  # 低于此数量视为数据不完整


def get_trading_days(start_date, end_date):
    """
    获取指定范围内的交易日列表（简化版，排除周末）
    实际应用中应该使用交易日历
    """
    trading_days = []
    current = start_date
    while current <= end_date:
        # 排除周末
        if current.weekday() < 5:
            trading_days.append(current)
        current += timedelta(days=1)
    return trading_days


def analyze_single_file(file_path):
    """
    分析单个CSV文件的数据质量

    Returns:
        dict: 分析结果
    """
    result = {
        'file_path': file_path,
        'file_name': os.path.basename(file_path),
        'stock_code': os.path.basename(file_path).replace('.csv', ''),
        'exists': False,
        'total_records': 0,
        'date_range': None,
        'first_date': None,
        'last_date': None,
        'trading_days': 0,
        'daily_stats': [],
        'issues': [],
        'quality_score': 0,
        'file_size': 0
    }

    if not os.path.exists(file_path):
        result['issues'].append('文件不存在')
        return result

    result['exists'] = True
    result['file_size'] = os.path.getsize(file_path)

    try:
        df = pd.read_csv(file_path, encoding='utf-8-sig')

        if df.empty:
            result['issues'].append('文件为空')
            return result

        # 转换日期
        df['date'] = pd.to_datetime(df['date'])
        df['trade_date'] = df['date'].dt.date

        result['total_records'] = len(df)
        result['first_date'] = df['date'].min().strftime('%Y-%m-%d %H:%M')
        result['last_date'] = df['date'].max().strftime('%Y-%m-%d %H:%M')
        result['date_range'] = f"{result['first_date']} ~ {result['last_date']}"

        # 按天统计
        daily_counts = df.groupby('trade_date').agg({
            'date': 'count',
            'open': ['min', 'max'],
            'close': ['first', 'last'],
            'volume': 'sum'
        }).reset_index()

        daily_counts.columns = ['date', 'count', 'open_min', 'open_max', 'close_first', 'close_last', 'volume_sum']

        result['trading_days'] = len(daily_counts)

        # 分析每天的数据
        incomplete_days = []
        zero_price_days = []

        for _, row in daily_counts.iterrows():
            day_info = {
                'date': str(row['date']),
                'count': int(row['count']),
                'status': 'ok' if row['count'] >= MIN_RECORDS_THRESHOLD else 'incomplete'
            }
            result['daily_stats'].append(day_info)

            if row['count'] < MIN_RECORDS_THRESHOLD:
                incomplete_days.append(f"{row['date']} ({row['count']}条)")

            if row['open_min'] == 0 or row['close_first'] == 0:
                zero_price_days.append(str(row['date']))

        # 检查问题
        if incomplete_days:
            result['issues'].append(f"数据不完整的交易日: {', '.join(incomplete_days[:5])}" +
                                   (f" 等{len(incomplete_days)}天" if len(incomplete_days) > 5 else ""))

        if zero_price_days:
            result['issues'].append(f"存在价格为0的交易日: {', '.join(zero_price_days[:3])}" +
                                   (f" 等{len(zero_price_days)}天" if len(zero_price_days) > 3 else ""))

        # 检查是否有重复数据
        duplicates = df.duplicated(subset=['date']).sum()
        if duplicates > 0:
            result['issues'].append(f"存在 {duplicates} 条重复记录")

        # 检查数据连续性
        if result['trading_days'] > 0:
            first_day = daily_counts['date'].min()
            last_day = daily_counts['date'].max()
            expected_days = get_trading_days(first_day, last_day)
            actual_days = set(daily_counts['date'].tolist())
            missing_days = [str(d) for d in expected_days if d not in actual_days]

            if missing_days:
                result['issues'].append(f"缺失交易日: {', '.join(missing_days[:5])}" +
                                       (f" 等{len(missing_days)}天" if len(missing_days) > 5 else ""))

        # 计算质量评分 (0-100)
        score = 100
        if incomplete_days:
            score -= min(30, len(incomplete_days) * 5)
        if zero_price_days:
            score -= min(20, len(zero_price_days) * 10)
        if duplicates > 0:
            score -= 10
        if 'missing_days' in dir() and missing_days:
            score -= min(20, len(missing_days) * 3)

        result['quality_score'] = max(0, score)

    except Exception as e:
        result['issues'].append(f"分析出错: {str(e)}")
        logging.error(f"分析文件失败 {file_path}: {e}")

    return result


def get_all_data_files():
    """获取所有1分钟数据文件"""
    data_root = os.path.join(Config.get_project_root(), 'data', 'data', 'quarters')
    files = []

    # 遍历所有年份和季度
    for year_dir in glob.glob(os.path.join(data_root, '*')):
        if os.path.isdir(year_dir):
            year = os.path.basename(year_dir)
            for quarter_dir in glob.glob(os.path.join(year_dir, 'Q*')):
                if os.path.isdir(quarter_dir):
                    quarter = os.path.basename(quarter_dir)
                    m1_dir = os.path.join(quarter_dir, '1m')
                    if os.path.isdir(m1_dir):
                        for csv_file in glob.glob(os.path.join(m1_dir, '*.csv')):
                            files.append({
                                'path': csv_file,
                                'year': year,
                                'quarter': quarter,
                                'stock_code': os.path.basename(csv_file).replace('.csv', '')
                            })

    return files


@data_analysis_bp.route('/data_analysis')
def data_analysis_page():
    """数据完整性分析页面"""
    return render_template('data/data_analysis.html')


@data_analysis_bp.route('/api/data_analysis/summary', methods=['GET'])
def get_data_summary():
    """获取数据概览统计"""
    try:
        files = get_all_data_files()

        if not files:
            return jsonify({
                'success': True,
                'total_files': 0,
                'total_size': 0,
                'by_quarter': {},
                'message': '未找到任何数据文件'
            })

        # 按季度统计
        by_quarter = {}
        total_size = 0

        for f in files:
            key = f"{f['year']}-{f['quarter']}"
            if key not in by_quarter:
                by_quarter[key] = {'count': 0, 'size': 0}
            by_quarter[key]['count'] += 1

            size = os.path.getsize(f['path'])
            by_quarter[key]['size'] += size
            total_size += size

        return jsonify({
            'success': True,
            'total_files': len(files),
            'total_size': total_size,
            'total_size_mb': round(total_size / 1024 / 1024, 2),
            'by_quarter': by_quarter
        })

    except Exception as e:
        logging.error(f"获取数据概览失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@data_analysis_bp.route('/api/data_analysis/analyze', methods=['POST'])
def analyze_data():
    """分析指定股票或全部数据"""
    try:
        data = request.get_json() or {}
        stock_code = data.get('stock_code', '').strip()
        analyze_all = data.get('analyze_all', False)
        limit = data.get('limit', 50)  # 限制分析数量

        files = get_all_data_files()

        if not files:
            return jsonify({
                'success': False,
                'message': '未找到任何数据文件'
            })

        # 筛选要分析的文件
        if stock_code:
            files = [f for f in files if f['stock_code'] == stock_code]
            if not files:
                return jsonify({
                    'success': False,
                    'message': f'未找到股票 {stock_code} 的数据文件'
                })
        elif not analyze_all:
            files = files[:limit]

        # 分析文件
        results = []
        quality_scores = []
        issues_count = 0

        for f in files:
            result = analyze_single_file(f['path'])
            result['year'] = f['year']
            result['quarter'] = f['quarter']
            results.append(result)

            if result['quality_score'] > 0:
                quality_scores.append(result['quality_score'])
            if result['issues']:
                issues_count += 1

        # 统计汇总
        avg_score = sum(quality_scores) / len(quality_scores) if quality_scores else 0

        return jsonify({
            'success': True,
            'total_analyzed': len(results),
            'avg_quality_score': round(avg_score, 1),
            'files_with_issues': issues_count,
            'results': results
        })

    except Exception as e:
        logging.error(f"分析数据失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@data_analysis_bp.route('/api/data_analysis/stock_list', methods=['GET'])
def get_stock_list():
    """获取所有已下载的股票列表"""
    try:
        files = get_all_data_files()

        # 按股票代码分组
        stocks = {}
        for f in files:
            code = f['stock_code']
            if code not in stocks:
                stocks[code] = {
                    'code': code,
                    'files': [],
                    'total_size': 0
                }
            stocks[code]['files'].append(f"{f['year']}-{f['quarter']}")
            stocks[code]['total_size'] += os.path.getsize(f['path'])

        # 转换为列表并排序
        stock_list = sorted(stocks.values(), key=lambda x: x['code'])

        return jsonify({
            'success': True,
            'total': len(stock_list),
            'stocks': stock_list
        })

    except Exception as e:
        logging.error(f"获取股票列表失败: {e}")
        return jsonify({'success': False, 'message': str(e)})


@data_analysis_bp.route('/api/data_analysis/detail/<stock_code>', methods=['GET'])
def get_stock_detail(stock_code):
    """获取单只股票的详细分析"""
    try:
        files = get_all_data_files()
        files = [f for f in files if f['stock_code'] == stock_code]

        if not files:
            return jsonify({
                'success': False,
                'message': f'未找到股票 {stock_code} 的数据文件'
            })

        # 分析所有相关文件
        all_data = []
        file_results = []

        for f in files:
            result = analyze_single_file(f['path'])
            result['year'] = f['year']
            result['quarter'] = f['quarter']
            file_results.append(result)

            # 读取数据用于汇总
            try:
                df = pd.read_csv(f['path'], encoding='utf-8-sig')
                df['date'] = pd.to_datetime(df['date'])
                all_data.append(df)
            except:
                pass

        # 合并所有数据进行整体分析
        overall_stats = {}
        if all_data:
            combined = pd.concat(all_data, ignore_index=True)
            combined = combined.drop_duplicates(subset=['date']).sort_values('date')
            combined['trade_date'] = combined['date'].dt.date

            daily_counts = combined.groupby('trade_date').size()

            overall_stats = {
                'total_records': len(combined),
                'total_days': len(daily_counts),
                'first_date': combined['date'].min().strftime('%Y-%m-%d'),
                'last_date': combined['date'].max().strftime('%Y-%m-%d'),
                'avg_records_per_day': round(daily_counts.mean(), 1),
                'min_records_day': int(daily_counts.min()),
                'max_records_day': int(daily_counts.max()),
                'incomplete_days': int((daily_counts < MIN_RECORDS_THRESHOLD).sum()),
                'daily_distribution': daily_counts.to_dict()
            }

        return jsonify({
            'success': True,
            'stock_code': stock_code,
            'file_count': len(files),
            'file_results': file_results,
            'overall_stats': overall_stats
        })

    except Exception as e:
        logging.error(f"获取股票详情失败: {e}")
        return jsonify({'success': False, 'message': str(e)})
