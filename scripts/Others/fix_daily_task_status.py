# -*- coding: utf-8 -*-
"""
修复 DailyTaskStatus 虚假标记

问题：之前的下载流水线会把 today 强加到标记列表中，
即使 API 返回的数据并不包含当天日期，也会被标记为 ✓。

本脚本：
1. 对指定日期，检查每只股票是否真的有 1m 数据
2. 没有实际数据的标记重置为 False
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import date
from App import create_app
from App.exts import db
from App.models.data.DailyTaskStatus import DailyTaskStatus
from App.utils.path_manager import get_path_manager
import pandas as pd

app = create_app()


def fix_date(target_date: date):
    """修复指定日期的虚假标记"""
    pm = get_path_manager()

    with app.app_context():
        # 获取该日期所有标记记录
        records = DailyTaskStatus.query.filter(
            DailyTaskStatus.date == target_date
        ).all()

        print(f"日期: {target_date}, 共 {len(records)} 条任务记录")
        if not records:
            print("无记录，跳过")
            return

        fixed_1m = 0
        fixed_15m = 0
        fixed_macd = 0
        fixed_daily = 0

        for task in records:
            code = task.stock_code

            # 检查 1m 数据是否真的包含该日期
            has_1m = False
            year = str(target_date.year)
            month = target_date.month
            quarter = f"Q{(month - 1) // 3 + 1}"
            for ext in ['.csv', '.parquet']:
                fpath = os.path.join(str(pm.data_base), 'quarters', year, quarter, f"{code}{ext}")
                if os.path.exists(fpath):
                    try:
                        if ext == '.csv':
                            df = pd.read_csv(fpath, usecols=['date'], parse_dates=['date'])
                        else:
                            df = pd.read_parquet(fpath, columns=['date'])
                            df['date'] = pd.to_datetime(df['date'])
                        dates_in_file = set(df['date'].dt.date.unique())
                        if target_date in dates_in_file:
                            has_1m = True
                    except Exception:
                        pass
                    break

            # 检查 15m 数据
            has_15m = False
            for ext in ['.csv', '.parquet']:
                fpath_15m = os.path.join(str(pm.data_base), '15m', f"{code}{ext}")
                if os.path.exists(fpath_15m):
                    try:
                        if ext == '.csv':
                            df15 = pd.read_csv(fpath_15m, usecols=['date'], parse_dates=['date'])
                        else:
                            df15 = pd.read_parquet(fpath_15m, columns=['date'])
                            df15['date'] = pd.to_datetime(df15['date'])
                        dates_15m = set(df15['date'].dt.date.unique())
                        if target_date in dates_15m:
                            has_15m = True
                    except Exception:
                        pass
                    break

            # 检查日K
            from App.models.data.StockDaily import StockDaily
            has_daily = StockDaily.query.filter(
                StockDaily.stock_code == code,
                StockDaily.date == target_date
            ).first() is not None

            # 修正虚假标记
            changed = False
            if task.is_1m_downloaded and not has_1m:
                task.is_1m_downloaded = False
                fixed_1m += 1
                changed = True
            if task.is_15m_generated and not has_15m:
                task.is_15m_generated = False
                fixed_15m += 1
                changed = True
            if task.is_macd_calculated and not has_15m:
                task.is_macd_calculated = False
                fixed_macd += 1
                changed = True
            if task.is_daily_processed and not has_daily:
                task.is_daily_processed = False
                fixed_daily += 1
                changed = True

        db.session.commit()
        print(f"修复完成:")
        print(f"  1m下载标记修正: {fixed_1m}")
        print(f"  15m生成标记修正: {fixed_15m}")
        print(f"  MACD标记修正: {fixed_macd}")
        print(f"  日K标记修正: {fixed_daily}")


if __name__ == '__main__':
    # 默认修复今天
    target = date.today()
    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])

    print(f"=== 修复 DailyTaskStatus 虚假标记 ===")
    fix_date(target)
    print("完成")
