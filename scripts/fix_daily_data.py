#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
修复日K数据脚本 — 使用 pytdx 批量补充缺失的日线数据

用法:
    python scripts/fix_daily_data.py              # 补充所有缺失数据（默认365天）
    python scripts/fix_daily_data.py --days 30     # 只补最近30天
    python scripts/fix_daily_data.py --code 000001 # 只修复指定股票
    python scripts/fix_daily_data.py --dry-run     # 预览不执行
"""

import sys
import os
import time
import argparse
import logging
from datetime import date, datetime

# 项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='修复日K数据（pytdx）')
    parser.add_argument('--days', type=int, default=365, help='下载天数（默认365）')
    parser.add_argument('--code', type=str, default=None, help='指定股票/板块代码')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不实际下载')
    args = parser.parse_args()

    from main import create_app
    app = create_app()

    with app.app_context():
        from App.exts import db
        from sqlalchemy import text
        from App.models.data.StockDaily import StockDaily, save_daily_stock_data_to_sql
        from App.models.data.DailyTaskStatus import DailyTaskStatus
        from App.codes.downloads.DlPytdx import download_daily_pytdx
        import pandas as pd

        today = date.today()

        # ===== 1. 收集需要修复的股票 =====
        if args.code:
            # 指定股票
            tasks = [{'code': args.code, 'last_date': None, 'gap': args.days, 'reason': '手动指定'}]
        else:
            tasks = []

            # 1a. 有日K数据但落后的股票
            rows = db.session.execute(text('''
                SELECT stock_code, MAX(date) as last_date
                FROM data_stock_daily
                GROUP BY stock_code
                HAVING MAX(date) < :today
                ORDER BY MAX(date) ASC
            '''), {'today': str(today)}).fetchall()

            for row in rows:
                code = row[0]
                last_date = row[1] if isinstance(row[1], date) else datetime.strptime(str(row[1]), '%Y-%m-%d').date()
                gap = (today - last_date).days
                if gap >= 1:
                    tasks.append({
                        'code': code,
                        'last_date': str(last_date),
                        'gap': gap,
                        'reason': f'落后{gap}天'
                    })

            # 1b. 有分钟数据但完全没有日K的
            missing_rows = db.session.execute(text('''
                SELECT s.code FROM data_download_records r
                JOIN data_stock_info s ON r.stock_code_id = s.id
                WHERE r.download_status = 'success' AND r.end_date != '2050-01-01'
                AND s.code NOT IN (SELECT DISTINCT stock_code FROM data_stock_daily)
                ORDER BY s.code
            ''')).fetchall()

            for row in missing_rows:
                tasks.append({
                    'code': row[0],
                    'last_date': None,
                    'gap': args.days,
                    'reason': '无日K数据'
                })

        if not tasks:
            logger.info('所有日K数据已是最新，无需修复')
            return

        # ===== 2. 统计 =====
        boards = sum(1 for t in tasks if t['code'].startswith('BK'))
        stocks = len(tasks) - boards
        logger.info('=' * 60)
        logger.info(f'日K数据修复任务: 共 {len(tasks)} 只 (股票 {stocks}, 板块 {boards})')
        logger.info(f'下载天数: {args.days}')
        logger.info('=' * 60)

        if args.dry_run:
            logger.info('[预览模式] 以下股票需要修复:')
            for t in tasks:
                logger.info(f"  {t['code']:>8s}  最后日期: {t['last_date'] or '无'}  {t['reason']}")
            logger.info(f'\n共 {len(tasks)} 只，使用 --days {args.days} 下载')
            return

        # ===== 3. 批量下载 =====
        success_count = 0
        fail_count = 0
        total_records = 0

        for i, task in enumerate(tasks):
            code = task['code']
            gap = min(task['gap'] + 10, args.days)  # 多下载10天确保覆盖

            logger.info(f"[{i+1}/{len(tasks)}] {code} ({task['reason']}) 下载 {gap} 天日K...")

            try:
                df, end_date = download_daily_pytdx(code, days=gap)

                if df.empty:
                    logger.warning(f"  {code} 返回空数据，跳过")
                    fail_count += 1
                    continue

                save_daily_stock_data_to_sql(code, df)

                # 标记任务状态
                df['date'] = pd.to_datetime(df['date'])
                for d in df['date'].dt.date.unique():
                    DailyTaskStatus.mark_task(code, d, 'is_daily_processed')

                success_count += 1
                total_records += len(df)
                logger.info(f"  {code} 成功: {len(df)} 条, 最新={end_date}")

            except Exception as e:
                logger.error(f"  {code} 失败: {e}")
                fail_count += 1

            # 每10只 commit 一次，避免事务太大
            if (i + 1) % 10 == 0:
                db.session.commit()
                time.sleep(0.5)

        db.session.commit()

        # ===== 4. 汇总 =====
        logger.info('=' * 60)
        logger.info(f'修复完成!')
        logger.info(f'  成功: {success_count} 只')
        logger.info(f'  失败: {fail_count} 只')
        logger.info(f'  总记录: {total_records} 条')
        logger.info('=' * 60)


if __name__ == '__main__':
    main()
