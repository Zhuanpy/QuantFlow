# -*- coding: utf-8 -*-
"""
诊断并修复指定日期的15m数据缺失

流程：
1. 遍历所有应下载的股票，检查1m和15m数据是否包含目标日期
2. 分类：
   - A类：1m数据缺失 → 重新下载1m，然后处理15m
   - B类：1m存在但15m缺失 → 仅重新处理15m
   - C类：都存在 → 跳过
3. 执行修复并更新 DailyTaskStatus
"""
import sys
import os
import time
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import date
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

from App import create_app
from App.exts import db

app = create_app()


def get_all_stock_codes():
    """获取所有需要下载的股票代码列表"""
    from App.models.data.Stock1m import DownloadRecord
    from App.models.data.basic_info import StockCodes

    records = DownloadRecord.query.filter(
        DownloadRecord.end_date != date(2050, 1, 1),
        DownloadRecord.record_date != date(2050, 1, 1)
    ).all()

    codes = []
    for rec in records:
        stock = StockCodes.query.filter_by(id=rec.stock_code_id).first()
        if stock:
            codes.append(stock.code)
    return codes


def check_1m_has_date(stock_code: str, target_date: date) -> bool:
    """检查1m数据文件是否包含目标日期"""
    from App.utils.file_utils import get_stock_data_path
    from App.codes.RnnDataFile.save_download import get_quarter_from_month

    year = str(target_date.year)
    quarter = get_quarter_from_month(target_date.month)
    file_path = get_stock_data_path(stock_code, data_type='1m', year=year, quarter=quarter, create=False)

    # 检查 parquet 和 csv
    for fpath in [file_path, file_path.replace('.parquet', '.csv')]:
        if os.path.exists(fpath):
            try:
                if fpath.endswith('.parquet'):
                    df = pd.read_parquet(fpath, columns=['date'])
                else:
                    df = pd.read_csv(fpath, usecols=['date'], parse_dates=['date'])
                df['date'] = pd.to_datetime(df['date'])
                dates = set(df['date'].dt.date.unique())
                return target_date in dates
            except Exception as e:
                logger.warning(f"{stock_code} 读取1m文件失败: {e}")
                return False
    return False


def check_15m_has_date(stock_code: str, target_date: date) -> bool:
    """检查15m数据文件是否包含目标日期"""
    from App.utils.file_utils import get_stock_data_path

    file_path = get_stock_data_path(stock_code, data_type='15m_normal')
    # 检查 parquet 和 csv
    for fpath in [file_path, file_path.replace('.parquet', '.csv')]:
        if os.path.exists(fpath):
            try:
                if fpath.endswith('.parquet'):
                    df = pd.read_parquet(fpath, columns=['date'])
                else:
                    df = pd.read_csv(fpath, usecols=['date'], parse_dates=['date'])
                df['date'] = pd.to_datetime(df['date'])
                dates = set(df['date'].dt.date.unique())
                return target_date in dates
            except Exception as e:
                logger.warning(f"{stock_code} 读取15m文件失败: {e}")
                return False
    return False


def download_1m(stock_code: str, days: int = 3):
    """下载1m数据并保存"""
    from App.codes.downloads.DlStockData import download_1m_by_type, StockType
    from App.codes.RnnDataFile.save_download import save_1m_to_csv

    is_board = stock_code.startswith('BK')
    stock_type = StockType.BOARD_1M if is_board else StockType.STOCK_1M

    data, ending = download_1m_by_type(stock_code, days, stock_type)
    if data is None or data.empty:
        logger.warning(f"{stock_code} 下载1m数据为空")
        return None

    save_1m_to_csv(data, stock_code)
    logger.info(f"{stock_code} 1m数据下载保存成功，{len(data)} 条")
    return data


def process_15m(stock_code: str, target_date: date):
    """处理15m数据（重采样 + MACD）"""
    from App.codes.utils.Normal import ResampleData
    from App.codes.Signals.StatisticsMacd import SignalMethod
    from App.utils.file_utils import get_stock_data_path
    from App.codes.RnnDataFile.save_download import get_quarter_from_month
    from App.models.data.DailyTaskStatus import DailyTaskStatus

    year = str(target_date.year)
    quarter = get_quarter_from_month(target_date.month)

    # 读取当前季度1m数据
    cur_1m_path = get_stock_data_path(stock_code, data_type='1m', year=year, quarter=quarter, create=False)
    df_1m_full = None
    for p in [cur_1m_path, cur_1m_path.replace('.parquet', '.csv')]:
        if os.path.exists(p):
            df_1m_full = pd.read_parquet(p) if p.endswith('.parquet') else pd.read_csv(p)
            df_1m_full['date'] = pd.to_datetime(df_1m_full['date'])
            break

    if df_1m_full is None or df_1m_full.empty:
        logger.warning(f"{stock_code} 无当前季度1m数据，跳过15m处理")
        return False

    # 读取上一季度1m数据（用于MACD历史计算）
    q_num = int(quarter[1])
    if q_num == 1:
        prev_year, prev_quarter = str(int(year) - 1), 'Q4'
    else:
        prev_year, prev_quarter = year, f'Q{q_num - 1}'

    prev_1m_path = get_stock_data_path(stock_code, data_type='1m', year=prev_year, quarter=prev_quarter, create=False)
    for p in [prev_1m_path, prev_1m_path.replace('.parquet', '.csv')]:
        if os.path.exists(p):
            try:
                df_prev = pd.read_parquet(p) if p.endswith('.parquet') else pd.read_csv(p)
                df_prev['date'] = pd.to_datetime(df_prev['date'])
                df_1m_full = pd.concat([df_prev, df_1m_full]).sort_values('date').reset_index(drop=True)
                df_1m_full = df_1m_full.drop_duplicates(subset=['date'], keep='last')
            except Exception as e:
                logger.warning(f"{stock_code} 读取上一季度1m失败: {e}")
            break

    cur_quarter_start = df_1m_full[df_1m_full['date'].dt.date >= date(int(year), (q_num - 1) * 3 + 1, 1)]['date'].min()

    # 重采样 1m -> 15m
    data_15m_full = ResampleData.resample_1m_data(df_1m_full, '15m')
    if data_15m_full.empty:
        logger.warning(f"{stock_code} 15m重采样结果为空")
        return False

    # 计算 MACD 信号
    macd_success = False
    try:
        data_15m_full = SignalMethod.signal_by_MACD_3ema(data_15m_full, df_1m_full)
        macd_success = True
    except Exception as e:
        logger.warning(f"{stock_code} MACD计算失败: {e}")

    # 只保留当前季度
    if cur_quarter_start is not None and not pd.isna(cur_quarter_start):
        data_15m = data_15m_full[data_15m_full['date'] >= cur_quarter_start].copy()
    else:
        data_15m = data_15m_full.copy()

    if 'SignalTimes' in data_15m.columns:
        data_15m = data_15m.drop(columns=['SignalTimes'])

    # 保存15m数据（追加合并模式）
    file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')

    # 检查已有15m文件
    existing_15m_path = file_path_15m
    if file_path_15m.endswith('.parquet') and not os.path.exists(file_path_15m):
        csv_fallback = file_path_15m.replace('.parquet', '.csv')
        if os.path.exists(csv_fallback):
            existing_15m_path = csv_fallback

    if os.path.exists(existing_15m_path):
        try:
            if existing_15m_path.endswith('.parquet'):
                existing_15m = pd.read_parquet(existing_15m_path)
            else:
                existing_15m = pd.read_csv(existing_15m_path, parse_dates=['date'])
            existing_15m['date'] = pd.to_datetime(existing_15m['date'])
            if 'SignalTimes' in existing_15m.columns:
                existing_15m = existing_15m.drop(columns=['SignalTimes'])
            data_15m['date'] = pd.to_datetime(data_15m['date'])
            data_15m = pd.concat([existing_15m, data_15m], ignore_index=True)
            data_15m = data_15m.drop_duplicates(subset=['date'], keep='last')
            data_15m = data_15m.sort_values('date').reset_index(drop=True)
        except Exception as e:
            logger.warning(f"{stock_code} 合并旧15m失败: {e}")

    # 保存
    try:
        if file_path_15m.endswith('.parquet'):
            try:
                data_15m.to_parquet(file_path_15m, index=False, engine='pyarrow')
            except ImportError:
                csv_path = file_path_15m.replace('.parquet', '.csv')
                data_15m.to_csv(csv_path, index=False)
        else:
            data_15m.to_csv(file_path_15m, index=False)
    except Exception as e:
        logger.error(f"{stock_code} 15m保存失败: {e}")
        return False

    # 标记 DailyTaskStatus（只标记实际存在的日期）
    try:
        trade_dates = data_15m['date'].dt.date.unique()
        for d in trade_dates:
            DailyTaskStatus.mark_task(stock_code, d, 'is_15m_generated')
            if macd_success:
                DailyTaskStatus.mark_task(stock_code, d, 'is_macd_calculated')
        db.session.commit()
    except Exception as e:
        logger.error(f"{stock_code} DailyTaskStatus标记失败: {e}")

    logger.info(f"{stock_code} 15m处理完成，{len(data_15m)} 条，MACD={'成功' if macd_success else '失败'}")
    return True


def diagnose_and_repair(target_date: date, dry_run: bool = False):
    """诊断并修复指定日期的数据缺失"""
    with app.app_context():
        print(f"\n{'='*60}")
        print(f"诊断日期: {target_date}")
        print(f"模式: {'仅诊断(dry_run)' if dry_run else '诊断+修复'}")
        print(f"{'='*60}\n")

        # 获取所有股票代码
        all_codes = get_all_stock_codes()
        print(f"总股票数: {len(all_codes)}")

        # 分类
        missing_1m = []   # A类：1m缺失
        missing_15m = []  # B类：有1m但缺15m
        complete = []     # C类：都有

        for i, code in enumerate(all_codes):
            if (i + 1) % 50 == 0:
                print(f"  检查进度: {i+1}/{len(all_codes)}...")

            has_1m = check_1m_has_date(code, target_date)
            has_15m = check_15m_has_date(code, target_date)

            if not has_1m:
                missing_1m.append(code)
            elif not has_15m:
                missing_15m.append(code)
            else:
                complete.append(code)

        # 诊断报告
        print(f"\n{'='*60}")
        print(f"诊断结果:")
        print(f"  完整(C): {len(complete)} 只")
        print(f"  缺1m数据(A): {len(missing_1m)} 只")
        print(f"  有1m缺15m(B): {len(missing_15m)} 只")
        print(f"{'='*60}")

        if missing_1m:
            print(f"\n缺1m数据的股票(前20):")
            for code in missing_1m[:20]:
                print(f"  {code}")
            if len(missing_1m) > 20:
                print(f"  ... 共 {len(missing_1m)} 只")

        if missing_15m:
            print(f"\n有1m缺15m的股票(前20):")
            for code in missing_15m[:20]:
                print(f"  {code}")
            if len(missing_15m) > 20:
                print(f"  ... 共 {len(missing_15m)} 只")

        if dry_run:
            print("\n[dry_run模式] 不执行修复")
            return

        # ===== 修复 A 类：下载1m + 处理15m =====
        if missing_1m:
            print(f"\n{'='*60}")
            print(f"开始修复 A类（下载1m + 处理15m）: {len(missing_1m)} 只")
            print(f"{'='*60}")

            a_download_ok = 0
            a_download_fail = 0
            a_15m_ok = 0
            a_15m_fail = 0

            for i, code in enumerate(missing_1m):
                print(f"  [{i+1}/{len(missing_1m)}] {code} 下载1m...", end=' ', flush=True)
                try:
                    data = download_1m(code, days=3)
                    if data is not None and not data.empty:
                        # 标记1m下载状态
                        from App.models.data.DailyTaskStatus import DailyTaskStatus
                        data['date'] = pd.to_datetime(data['date'])
                        for d in data['date'].dt.date.unique():
                            DailyTaskStatus.mark_task(code, d, 'is_1m_downloaded')
                        db.session.commit()

                        a_download_ok += 1
                        print("OK", end=' ', flush=True)

                        # 检查新数据是否包含目标日期
                        if target_date in set(data['date'].dt.date.unique()):
                            print("→ 处理15m...", end=' ', flush=True)
                            if process_15m(code, target_date):
                                a_15m_ok += 1
                                print("OK")
                            else:
                                a_15m_fail += 1
                                print("失败")
                        else:
                            print(f"(数据不含{target_date}，可能API未更新)")
                    else:
                        a_download_fail += 1
                        print("下载失败(空数据)")
                except Exception as e:
                    a_download_fail += 1
                    print(f"异常: {e}")

                # 避免API限流
                time.sleep(0.3)

            print(f"\nA类修复结果: 下载成功={a_download_ok} 失败={a_download_fail}, 15m处理成功={a_15m_ok} 失败={a_15m_fail}")

        # ===== 修复 B 类：仅处理15m =====
        if missing_15m:
            print(f"\n{'='*60}")
            print(f"开始修复 B类（仅处理15m）: {len(missing_15m)} 只")
            print(f"{'='*60}")

            b_ok = 0
            b_fail = 0

            for i, code in enumerate(missing_15m):
                print(f"  [{i+1}/{len(missing_15m)}] {code} 处理15m...", end=' ', flush=True)
                try:
                    if process_15m(code, target_date):
                        b_ok += 1
                        print("OK")
                    else:
                        b_fail += 1
                        print("失败")
                except Exception as e:
                    b_fail += 1
                    print(f"异常: {e}")

            print(f"\nB类修复结果: 成功={b_ok} 失败={b_fail}")

        # 总结
        print(f"\n{'='*60}")
        print(f"修复完成")
        print(f"{'='*60}")


if __name__ == '__main__':
    target = date.today()
    dry = False

    args = sys.argv[1:]
    for arg in args:
        if arg == '--dry-run':
            dry = True
        elif arg.startswith('20'):
            target = date.fromisoformat(arg)

    diagnose_and_repair(target, dry_run=dry)
