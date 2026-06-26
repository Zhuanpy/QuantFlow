#!/usr/bin/env python3
"""
strategy_issues.solution TEXT → MEDIUMTEXT

原因：富文本里粘贴 base64 截图很容易突破 TEXT 的 64KB 上限。
MEDIUMTEXT 上限 ~16MB，单条放几十张截图够用。

幂等：已经是 MEDIUMTEXT/LONGTEXT 则跳过。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db


TABLE = 'strategy_issues'
COLUMN = 'solution'
BIND = 'quanttradingsystem'


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines[BIND]
        insp = inspect(engine)
        cols = {c['name']: c for c in insp.get_columns(TABLE)}
        if COLUMN not in cols:
            print(f'!!  列 {TABLE}.{COLUMN} 不存在，先建表')
            return

        cur_type = str(cols[COLUMN]['type']).upper()
        print(f'当前类型：{TABLE}.{COLUMN} = {cur_type}')

        if 'MEDIUMTEXT' in cur_type or 'LONGTEXT' in cur_type:
            print('OK  已经是 MEDIUMTEXT/LONGTEXT，跳过')
            return

        with engine.begin() as conn:
            sql = (
                f"ALTER TABLE {TABLE} "
                f"MODIFY COLUMN `{COLUMN}` MEDIUMTEXT NULL "
                f"COMMENT '详细描述 / 实现说明 / 备注（富文本 HTML，含 base64 图片）'"
            )
            conn.execute(text(sql))
            print(f'OK  {TABLE}.{COLUMN} 已升级为 MEDIUMTEXT')


if __name__ == '__main__':
    main()
