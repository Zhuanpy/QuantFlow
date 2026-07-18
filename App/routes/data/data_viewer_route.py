"""
数据查看器路由
用于展示已下载的1分钟数据
"""
from flask import Blueprint, render_template, jsonify, request
from App.models.data.Stock1m import RecordStockMinute as dlr
from App.models.data.basic_info import StockCodes
from App.exts import db
from datetime import date, datetime, timedelta
import logging
import pandas as pd
import os
from pathlib import Path

data_viewer_bp = Blueprint('data_viewer', __name__)


def get_data_base_path():
    """获取数据基础路径（新路径 data/quarters/）"""
    try:
        from config import Config
        project_root = Path(Config.get_project_root())
    except:
        project_root = Path(__file__).parent.parent.parent.parent
    return project_root / 'data' / 'quarters'


def get_data_base_path_legacy():
    """路径统一后旧路径已并入新路径；保留此函数兼容调用方，返回同一 data/quarters/"""
    return get_data_base_path()


def get_1m_stock_data_from_csv(stock_code: str, year: int = None, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    从CSV文件读取1分钟股票数据

    Args:
        stock_code: 股票代码
        year: 年份
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)

    Returns:
        DataFrame: 股票数据
    """
    if year is None:
        year = datetime.now().year

    all_data = []
    seen_quarters = set()  # 避免同一季度重复读取

    # 搜索新路径 (data/quarters/) 和旧路径 (data/data/quarters/)
    for data_base in [get_data_base_path(), get_data_base_path_legacy()]:
        # 新格式: quarters/{year}/{quarter}/
        year_dir = data_base / str(year)
        if year_dir.exists():
            for quarter_dir in sorted(year_dir.iterdir()):
                if quarter_dir.is_dir() and quarter_dir.name.startswith('Q'):
                    q_key = f"{year}-{quarter_dir.name}"
                    if q_key in seen_quarters:
                        continue
                    parquet_file = quarter_dir / f"{stock_code}.parquet"
                    csv_file = quarter_dir / f"{stock_code}.csv"
                    if parquet_file.exists():
                        try:
                            df = pd.read_parquet(parquet_file)
                            all_data.append(df)
                            seen_quarters.add(q_key)
                        except Exception as e:
                            logging.warning(f"读取文件失败 {parquet_file}: {e}")
                    elif csv_file.exists():
                        try:
                            df = pd.read_csv(csv_file)
                            all_data.append(df)
                            seen_quarters.add(q_key)
                        except Exception as e:
                            logging.warning(f"读取文件失败 {csv_file}: {e}")

        # 旧格式: quarters/2024Q1/
        for quarter in ['Q1', 'Q2', 'Q3', 'Q4']:
            q_key = f"{year}-{quarter}"
            if q_key in seen_quarters:
                continue
            old_format_dir = data_base / f"{year}{quarter}"
            if old_format_dir.exists():
                parquet_file = old_format_dir / f"{stock_code}.parquet"
                csv_file = old_format_dir / f"{stock_code}.csv"
                if parquet_file.exists():
                    try:
                        df = pd.read_parquet(parquet_file)
                        all_data.append(df)
                        seen_quarters.add(q_key)
                    except Exception as e:
                        logging.warning(f"读取文件失败 {parquet_file}: {e}")
                elif csv_file.exists():
                    try:
                        df = pd.read_csv(csv_file)
                        all_data.append(df)
                        seen_quarters.add(q_key)
                    except Exception as e:
                        logging.warning(f"读取文件失败 {csv_file}: {e}")

    if not all_data:
        return pd.DataFrame()

    # 合并所有数据
    df = pd.concat(all_data, ignore_index=True)

    # 确保date列存在且格式正确
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        df = df.drop_duplicates(subset=['date'], keep='last')

        # 日期范围过滤
        if start_date:
            start_dt = pd.to_datetime(start_date)
            df = df[df['date'] >= start_dt]
        if end_date:
            end_dt = pd.to_datetime(end_date) + timedelta(days=1)
            df = df[df['date'] < end_dt]

    return df


def get_available_years_for_stock(stock_code: str) -> list:
    """获取股票可用的年份列表"""
    years = set()

    for data_base in [get_data_base_path(), get_data_base_path_legacy()]:
        if not data_base.exists():
            continue

        for item in data_base.iterdir():
            if item.is_dir():
                # 新格式目录: 2025, 2026 等
                if item.name.isdigit():
                    year = int(item.name)
                    for quarter_dir in item.iterdir():
                        if quarter_dir.is_dir():
                            parquet_file = quarter_dir / f"{stock_code}.parquet"
                            csv_file = quarter_dir / f"{stock_code}.csv"
                            if parquet_file.exists() or csv_file.exists():
                                years.add(year)
                                break
                # 旧格式目录: 2024Q1 等
                elif len(item.name) == 6 and item.name[:4].isdigit() and item.name[4] == 'Q':
                    year = int(item.name[:4])
                    parquet_file = item / f"{stock_code}.parquet"
                    csv_file = item / f"{stock_code}.csv"
                    if parquet_file.exists() or csv_file.exists():
                        years.add(year)

    return sorted(years, reverse=True)


def get_stock_code_by_id(stock_code_id):
    """根据ID获取股票代码"""
    try:
        stock = StockCodes.query.filter_by(id=stock_code_id).first()
        return stock.code if stock else None
    except:
        return None


def get_stock_name_by_id(stock_code_id):
    """根据ID获取股票名称"""
    try:
        stock = StockCodes.query.filter_by(id=stock_code_id).first()
        return stock.name if stock else None
    except:
        return None


@data_viewer_bp.route('/data_viewer')
def data_viewer_page():
    """数据查看器页面"""
    return render_template('data/data_viewer.html')


@data_viewer_bp.route('/api/data_viewer/stocks', methods=['GET'])
def get_stocks_list():
    """获取已下载数据的股票列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '', type=str)
        per_page = min(per_page, 100)

        # 查询有数据的股票
        query = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.downloaded_records > 0
        )

        # 如果有搜索条件，需要关联查询
        if search:
            # 获取匹配的股票ID
            matching_stocks = StockCodes.query.filter(
                db.or_(
                    StockCodes.code.like(f'%{search}%'),
                    StockCodes.name.like(f'%{search}%')
                )
            ).with_entities(StockCodes.id).all()
            matching_ids = [s.id for s in matching_stocks]
            if matching_ids:
                query = query.filter(dlr.stock_code_id.in_(matching_ids))
            else:
                return jsonify({
                    "stocks": [],
                    "total": 0,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": 0
                }), 200

        query = query.order_by(dlr.end_date.desc(), dlr.downloaded_records.desc())

        total_count = query.count()
        records = query.offset((page - 1) * per_page).limit(per_page).all()

        stocks_list = []
        for record in records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            stock_name = get_stock_name_by_id(record.stock_code_id)
            if stock_code:
                stocks_list.append({
                    'code': stock_code,
                    'name': stock_name or '-',
                    'records': record.downloaded_records or 0,
                    'start_date': record.start_date.strftime('%Y-%m-%d') if record.start_date else '-',
                    'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else '-',
                    'last_update': record.last_download_time.strftime('%Y-%m-%d %H:%M') if record.last_download_time else '-'
                })

        return jsonify({
            "stocks": stocks_list,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page
        }), 200

    except Exception as e:
        logging.error(f"获取股票列表失败: {e}")
        return jsonify({"error": str(e)}), 500


@data_viewer_bp.route('/api/data_viewer/stock/<stock_code>', methods=['GET'])
def get_stock_data(stock_code):
    """获取指定股票的1分钟数据"""
    try:
        # 获取参数
        year = request.args.get('year', datetime.now().year, type=int)
        start_date = request.args.get('start_date', None)
        end_date = request.args.get('end_date', None)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 100, type=int)
        per_page = min(per_page, 500)

        # 从CSV文件获取数据
        df = get_1m_stock_data_from_csv(stock_code, year, start_date, end_date)

        if df.empty:
            return jsonify({
                "data": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "summary": {
                    "total_records": 0,
                    "date_range": "-",
                    "trading_days": 0
                }
            }), 200

        # 数据统计
        total_records = len(df)
        if 'date' in df.columns and len(df) > 0:
            df['date'] = pd.to_datetime(df['date'])
            min_date = df['date'].min()
            max_date = df['date'].max()
            trading_days = df['date'].dt.date.nunique()
            date_range = f"{min_date.strftime('%Y-%m-%d')} ~ {max_date.strftime('%Y-%m-%d')}"
        else:
            date_range = "-"
            trading_days = 0

        # 分页
        total_pages = (total_records + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        df_page = df.iloc[start_idx:end_idx]

        # 转换为列表
        data_list = []
        for _, row in df_page.iterrows():
            data_list.append({
                'date': row['date'].strftime('%Y-%m-%d %H:%M:%S') if pd.notna(row['date']) else '-',
                'open': round(row['open'], 2) if pd.notna(row['open']) else 0,
                'high': round(row['high'], 2) if pd.notna(row['high']) else 0,
                'low': round(row['low'], 2) if pd.notna(row['low']) else 0,
                'close': round(row['close'], 2) if pd.notna(row['close']) else 0,
                'volume': int(row['volume']) if pd.notna(row['volume']) else 0,
                'money': int(row['money']) if pd.notna(row['money']) else 0
            })

        return jsonify({
            "data": data_list,
            "total": total_records,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "summary": {
                "total_records": total_records,
                "date_range": date_range,
                "trading_days": trading_days
            }
        }), 200

    except Exception as e:
        logging.error(f"获取股票数据失败: {e}")
        return jsonify({"error": str(e)}), 500


@data_viewer_bp.route('/api/data_viewer/stock/<stock_code>/summary', methods=['GET'])
def get_stock_summary(stock_code):
    """获取股票数据概要信息"""
    try:
        year = request.args.get('year', datetime.now().year, type=int)

        # 从CSV文件获取数据
        df = get_1m_stock_data_from_csv(stock_code, year)

        if df.empty:
            return jsonify({
                "code": stock_code,
                "year": year,
                "has_data": False,
                "total_records": 0,
                "date_range": "-",
                "trading_days": 0,
                "price_range": "-",
                "avg_volume": 0
            }), 200

        df['date'] = pd.to_datetime(df['date'])

        summary = {
            "code": stock_code,
            "year": year,
            "has_data": True,
            "total_records": len(df),
            "date_range": f"{df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')}",
            "trading_days": df['date'].dt.date.nunique(),
            "price_range": f"{df['low'].min():.2f} ~ {df['high'].max():.2f}",
            "avg_volume": int(df['volume'].mean()),
            "latest_close": round(df.iloc[-1]['close'], 2) if len(df) > 0 else 0,
            "latest_date": df.iloc[-1]['date'].strftime('%Y-%m-%d %H:%M') if len(df) > 0 else '-'
        }

        return jsonify(summary), 200

    except Exception as e:
        logging.error(f"获取股票概要信息失败: {e}")
        return jsonify({"error": str(e)}), 500


@data_viewer_bp.route('/api/data_viewer/years', methods=['GET'])
def get_available_years():
    """获取可用的年份列表"""
    try:
        stock_code = request.args.get('stock_code', None)

        if stock_code:
            # 返回该股票有数据的年份
            years = get_available_years_for_stock(stock_code)
            if years:
                return jsonify({"years": years}), 200

        # 默认返回有数据的年份
        years = set()

        for data_base in [get_data_base_path(), get_data_base_path_legacy()]:
            if not data_base.exists():
                continue
            for item in data_base.iterdir():
                if item.is_dir():
                    if item.name.isdigit():
                        years.add(int(item.name))
                    elif len(item.name) == 6 and item.name[:4].isdigit() and item.name[4] == 'Q':
                        years.add(int(item.name[:4]))

        if years:
            return jsonify({"years": sorted(years, reverse=True)}), 200

        # 如果没有找到任何年份，返回最近5年
        current_year = datetime.now().year
        years = list(range(current_year, current_year - 5, -1))
        return jsonify({"years": years}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_viewer_bp.route('/api/data_viewer/data_quality', methods=['GET'])
def get_data_quality():
    """获取数据质量统计"""
    try:
        today = date.today()

        # 统计各种状态
        total = dlr.query.filter(
            dlr.end_date != date(2050, 1, 1)
        ).count()

        with_data = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.downloaded_records > 0
        ).count()

        up_to_date = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date >= today - timedelta(days=1),
            dlr.downloaded_records > 0
        ).count()

        outdated = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date < today - timedelta(days=1),
            dlr.downloaded_records > 0
        ).count()

        no_data = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.downloaded_records == 0
        ).count()

        # 计算平均记录数
        avg_records_result = db.session.query(
            db.func.avg(dlr.downloaded_records)
        ).filter(
            dlr.download_status == 'success',
            dlr.downloaded_records > 0
        ).scalar()
        avg_records = int(avg_records_result) if avg_records_result else 0

        return jsonify({
            "total_stocks": total,
            "with_data": with_data,
            "up_to_date": up_to_date,
            "outdated": outdated,
            "no_data": no_data,
            "avg_records": avg_records,
            "data_coverage": round(with_data / total * 100, 1) if total > 0 else 0,
            "freshness": round(up_to_date / with_data * 100, 1) if with_data > 0 else 0
        }), 200

    except Exception as e:
        logging.error(f"获取数据质量统计失败: {e}")
        return jsonify({"error": str(e)}), 500


@data_viewer_bp.route('/api/data_viewer/stock/<stock_code>/chart_data', methods=['GET'])
def get_chart_data(stock_code):
    """
    获取K线图数据，支持单天和多天模式。

    参数:
        year: 年份
        date: 结束日期（YYYY-MM-DD），默认最新交易日
        days: 显示天数（1/3/5/10/20），默认5
    """
    try:
        year = request.args.get('year', datetime.now().year, type=int)
        target_date = request.args.get('date', None)
        days = request.args.get('days', 5, type=int)
        days = max(1, min(days, 60))

        df = get_1m_stock_data_from_csv(stock_code, year)

        if df.empty:
            return jsonify({"data": [], "trading_dates": [], "current_date": None, "days": days}), 200

        df['date'] = pd.to_datetime(df['date'])
        trading_dates = sorted(df['date'].dt.date.unique())
        trading_dates_str = [d.strftime('%Y-%m-%d') for d in trading_dates]

        # 确定结束日期
        if target_date:
            end_date = pd.to_datetime(target_date).date()
        else:
            end_date = trading_dates[-1]

        # 找到 end_date 在交易日列表中的位置，向前取 days 天
        end_idx = len(trading_dates) - 1
        for i, d in enumerate(trading_dates):
            if d >= end_date:
                end_idx = i
                break
        start_idx = max(0, end_idx - days + 1)
        selected_dates = set(trading_dates[start_idx:end_idx + 1])

        # 筛选数据
        chart_df = df[df['date'].dt.date.isin(selected_dates)].copy()

        data_list = []
        for _, row in chart_df.iterrows():
            data_list.append({
                'date': row['date'].strftime('%Y-%m-%d %H:%M'),
                'open': round(float(row['open']), 2) if pd.notna(row['open']) else 0,
                'high': round(float(row['high']), 2) if pd.notna(row['high']) else 0,
                'low': round(float(row['low']), 2) if pd.notna(row['low']) else 0,
                'close': round(float(row['close']), 2) if pd.notna(row['close']) else 0,
                'volume': int(row['volume']) if pd.notna(row['volume']) else 0,
            })

        return jsonify({
            "data": data_list,
            "trading_dates": trading_dates_str,
            "current_date": end_date.strftime('%Y-%m-%d'),
            "days": days,
            "date_range": {
                "start": trading_dates[start_idx].strftime('%Y-%m-%d'),
                "end": trading_dates[end_idx].strftime('%Y-%m-%d'),
                "count": len(selected_dates),
            }
        }), 200

    except Exception as e:
        logging.error(f"获取K线图数据失败: {e}")
        return jsonify({"error": str(e)}), 500


@data_viewer_bp.route('/api/data_viewer/open_folder', methods=['POST'])
def open_data_folder():
    """打开数据文件夹"""
    try:
        import subprocess
        import platform

        stock_code = request.args.get('stock_code', None)
        year = request.args.get('year', str(datetime.now().year))

        data_base = get_data_base_path()

        # 确定要打开的文件夹
        folder_to_open = str(data_base)
        if stock_code and year:
            # 搜索新路径和旧路径
            for base in [get_data_base_path(), get_data_base_path_legacy()]:
                year_dir = base / year
                if year_dir.exists():
                    for quarter_dir in sorted(year_dir.iterdir(), reverse=True):
                        if quarter_dir.is_dir():
                            parquet_file = quarter_dir / f"{stock_code}.parquet"
                            csv_file = quarter_dir / f"{stock_code}.csv"
                            if parquet_file.exists() or csv_file.exists():
                                folder_to_open = str(quarter_dir)
                                break
                    else:
                        continue
                    break

        # 确保文件夹存在
        if not os.path.exists(folder_to_open):
            os.makedirs(folder_to_open, exist_ok=True)

        # 根据操作系统打开文件夹
        system = platform.system()

        if system == "Windows":
            subprocess.run(['explorer', folder_to_open], capture_output=True)
        elif system == "Darwin":  # macOS
            subprocess.run(['open', folder_to_open], check=True)
        elif system == "Linux":
            subprocess.run(['xdg-open', folder_to_open], check=True)
        else:
            return jsonify({"success": False, "message": f"不支持的操作系统: {system}"}), 400

        logging.info(f"成功打开数据文件夹: {folder_to_open}")
        return jsonify({"success": True, "message": "数据文件夹已打开", "path": folder_to_open}), 200

    except Exception as e:
        logging.error(f"打开数据文件夹失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
