# -*- coding: utf-8 -*-
"""
板块数据诊断脚本（只读，不修改任何数据）

检查所有板块（BK 开头）在 data_stock_daily 中的日K新鲜度，
以及 data_daily_task_status 中最近的任务流水线完成情况。

用法（PyCharm 右键 Run，或命令行）：
    python scripts/diagnose_all_boards.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from App import create_app
from App.exts import db
from sqlalchemy import text


def main():
    app = create_app()
    with app.app_context():
        eng = db.engines['quanttradingsystem']
        today = date.today()

        print('=' * 78)
        print('板块数据诊断报告')
        print('=' * 78)

        # ---- 检查 1：各板块日K新鲜度 ----
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT stock_code, MAX(date) AS last_date "
                "FROM data_stock_daily "
                "WHERE stock_code LIKE 'BK%' "
                "GROUP BY stock_code ORDER BY stock_code"
            )).fetchall()

        if not rows:
            print('\n[!] data_stock_daily 里没有任何 BK 板块行。')
            print('    板块日K可能仍存在旧表 datadaily.<code> 里（见 _load_board_daily 回退逻辑）。')
            return

        print(f'\n检查1：日K新鲜度  共 {len(rows)} 个板块')
        print('-' * 78)
        print(f'{"板块":<10}{"最新日期":<14}{"落后天数":<10}{"状态"}')
        print('-' * 78)

        stale = []
        for code, last_date in rows:
            if last_date is None:
                print(f'{code:<10}{"无数据":<14}{"-":<10}MISSING')
                stale.append(code)
                continue
            behind = (today - last_date).days
            if behind <= 1:
                status = 'OK'
            elif behind <= 3:
                status = 'STALE'
                stale.append(code)
            else:
                status = 'OLD'
                stale.append(code)
            print(f'{code:<10}{str(last_date):<14}{behind:<10}{status}')

        # ---- 检查 2：最近的任务流水线状态 ----
        print(f'\n检查2：data_daily_task_status 最近 30 条 BK 记录')
        print('-' * 78)
        with eng.connect() as conn:
            trows = conn.execute(text(
                "SELECT stock_code, date, is_1m_downloaded, is_15m_generated, is_daily_processed "
                "FROM data_daily_task_status "
                "WHERE stock_code LIKE 'BK%' "
                "ORDER BY date DESC, stock_code LIMIT 30"
            )).fetchall()

        if not trows:
            print('  （无 BK 任务记录）')
        else:
            print(f'{"板块":<10}{"日期":<14}{"1m":<5}{"15m":<6}{"日K":<6}{"完整?"}')
            print('-' * 78)
            for code, d, m1, m15, daily in trows:
                y = lambda b: 'Y' if b else 'N'
                full = 'OK' if (m1 and m15 and daily) else 'INCOMPLETE'
                print(f'{code:<10}{str(d):<14}{y(m1):<5}{y(m15):<6}{y(daily):<6}{full}')

        # ---- 汇总 ----
        print('\n' + '=' * 78)
        print(f'汇总：{len(rows)} 个板块，其中 {len(stale)} 个数据不新鲜（落后>1天或缺失）')
        if stale:
            print('不新鲜的板块：' + ', '.join(stale[:40]) + (' ...' if len(stale) > 40 else ''))
        print('=' * 78)


if __name__ == '__main__':
    main()
