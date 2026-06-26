#!/usr/bin/env python3
"""清理板块趋势打分表中遗留的 MTF 明细 JSON 备注。

历史版本曾把 `[v1-mtf] {...}` / `[v1-mtf] <error>` 写进 notes（手工备注列），
导致 /board_trend/ 备注列被调试数据占满。本脚本把这类自动写入的 notes 置空，
仅保留真正的手工备注（不以 `[v1-mtf]` 开头）。

幂等：可重复执行；--dry-run 仅统计不写库。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App import create_app
from App.exts import db
from App.models.evaluation.BoardTrendScore import BoardTrendScore


def main():
    dry_run = '--dry-run' in sys.argv
    app = create_app()
    with app.app_context():
        rows = (BoardTrendScore.query
                .filter(BoardTrendScore.notes.like('[v1-mtf]%'))
                .all())
        print(f'匹配到 {len(rows)} 条遗留 MTF 备注'
              + ('（dry-run，不写库）' if dry_run else ''))
        if not dry_run:
            for r in rows:
                r.notes = None
            db.session.commit()
            print(f'已清空 {len(rows)} 条 notes')


if __name__ == '__main__':
    main()
