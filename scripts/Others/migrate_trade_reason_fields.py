#!/usr/bin/env python3
"""
trade_records「操作理由」拆字段迁移

1) 新增 reason_title(VARCHAR200) / reason_tags(VARCHAR200) / reason_insight(TEXT)
2) trade_reason: TEXT → MEDIUMTEXT（富文本里贴 base64 截图会突破 TEXT 的 64KB）
3) 回填：老记录的纯文本 trade_reason 取首行(<=200字) 填进 reason_title，
   这样列表里立刻有一句话摘要可看，正文原样保留在 trade_reason 里。

为什么要这个脚本：本项目没有迁移框架，模型的 ensure_table()/create_all 都是
checkfirst=True —— 表已存在就整个跳过，加不了新列。所以必须手工 ALTER 一次。

幂等：列已存在则跳过；已是 MEDIUMTEXT 则跳过；只回填 reason_title 为空的行。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # scripts/Others
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 项目根

from sqlalchemy import inspect, text

from App import create_app
from App.exts import db

TABLE = 'trade_records'
BIND = 'quanttradingsystem'

NEW_COLUMNS = [
    ('reason_title', "VARCHAR(200) NULL COMMENT '操作理由一句话摘要(列表单行显示)'"),
    ('reason_tags', "VARCHAR(200) NULL COMMENT '标签，逗号分隔(如 低吸,板块轮动)'"),
    ('reason_insight', "TEXT NULL COMMENT '感悟：这笔交易给我的灵感/教训'"),
]


def _first_line(s: str, limit: int = 200) -> str:
    """取正文首行做摘要。老数据是纯文本，但也可能有人贴过 HTML，顺手剥掉标签。"""
    import re
    if not s:
        return ''
    txt = re.sub(r'<[^>]+>', ' ', s)          # 剥 HTML 标签
    txt = txt.replace('&nbsp;', ' ').strip()
    line = next((l.strip() for l in txt.splitlines() if l.strip()), '')
    return line[:limit]


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines[BIND]
        insp = inspect(engine)
        cols = {c['name']: c for c in insp.get_columns(TABLE)}

        # 1) 新列
        for name, ddl in NEW_COLUMNS:
            if name in cols:
                print(f'--  列 {name} 已存在，跳过')
                continue
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {TABLE} ADD COLUMN `{name}` {ddl}'))
            print(f'OK  新增列 {name}')

        # 2) trade_reason → MEDIUMTEXT
        cur_type = str(cols.get('trade_reason', {}).get('type', '')).upper()
        if not cur_type:
            print('!!  列 trade_reason 不存在，跳过类型升级')
        elif 'MEDIUMTEXT' in cur_type or 'LONGTEXT' in cur_type:
            print(f'--  trade_reason 已是 {cur_type}，跳过')
        else:
            with engine.begin() as conn:
                conn.execute(text(
                    f"ALTER TABLE {TABLE} MODIFY COLUMN `trade_reason` MEDIUMTEXT NULL "
                    f"COMMENT '操作理由详细正文(富文本HTML)'"
                ))
            print(f'OK  trade_reason {cur_type} → MEDIUMTEXT')

        # 3) 回填 reason_title（只补空的，可重复跑）
        with engine.begin() as conn:
            rows = conn.execute(text(
                f"SELECT id, trade_reason FROM {TABLE} "
                f"WHERE trade_reason IS NOT NULL AND TRIM(trade_reason) <> '' "
                f"AND (reason_title IS NULL OR reason_title = '')"
            )).fetchall()

            filled = 0
            for rid, reason in rows:
                title = _first_line(reason)
                if not title:
                    continue
                conn.execute(text(f"UPDATE {TABLE} SET reason_title = :t WHERE id = :i"),
                             {'t': title, 'i': rid})
                filled += 1

        print(f'OK  回填 reason_title：{filled} 条（共 {len(rows)} 条有正文待补）')
        print('完成。')


if __name__ == '__main__':
    main()
