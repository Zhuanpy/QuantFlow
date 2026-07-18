"""
一次性回填 data_download_records（1m 下载账本），把板块成分股(4000+)全部纳入表单。

思路（用户确认方案 A）：让 data_download_records 成为「全部个股 1m 下载到哪天」的唯一账本，
日常收盘下载 + 板块修复都读/写它。本脚本把「板块最新名单里、有 StockInfo、但尚未在表单」的
个股补进表单，end_date 取其日K表(data_stock_daily)最大日期(=该股 1m 最新交易日)。

用法：
    python scripts/Others/backfill_download_records.py            # dry-run，只统计不写库
    python scripts/Others/backfill_download_records.py --commit   # 实际写库
"""
import sys
from pathlib import Path
from datetime import datetime, date

# 允许从仓库根目录导入 App
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text, bindparam


def main(commit: bool):
    from App import create_app, db
    app = create_app()
    with app.app_context():
        from App.models.data.basic_info import StockInfo
        from App.models.data.Stock1m import DownloadRecord

        eng = db.engines['quanttradingsystem']
        with eng.connect() as conn:
            # 1) 板块最新名单里的非 BK 成分股（去重）
            codes = [r[0] for r in conn.execute(text(
                '''
                SELECT DISTINCT ie.stock_code FROM industry_eastmoney ie
                JOIN (SELECT board_code, MAX(date) d FROM industry_eastmoney GROUP BY board_code) m
                  ON ie.board_code = m.board_code AND ie.date = m.d
                WHERE ie.stock_code NOT LIKE 'BK%'
                '''
            )).fetchall()]
            # 2) 市场最新交易日 + 每股日K最大日期（作为 1m 最新日期）
            mkt = conn.execute(text(
                "SELECT MAX(date) FROM data_stock_daily WHERE stock_code NOT LIKE 'BK%'")).scalar()
            daily_max = {}
            for i in range(0, len(codes), 900):
                ch = codes[i:i + 900]
                for r in conn.execute(text(
                        'SELECT stock_code, MAX(date) FROM data_stock_daily WHERE stock_code IN :c '
                        'GROUP BY stock_code').bindparams(bindparam('c', expanding=True)),
                        {'c': ch}).fetchall():
                    daily_max[r[0]] = r[1]

        # 3) StockInfo 映射 & 已在表单集合
        info = {r.code: r.id for r in
                StockInfo.query.with_entities(StockInfo.id, StockInfo.code).all()}
        have_info = [c for c in codes if c in info]
        no_info = [c for c in codes if c not in info]
        ids = [info[c] for c in have_info]
        existing_ids = set()
        for i in range(0, len(ids), 900):
            ch = ids[i:i + 900]
            existing_ids |= {r[0] for r in db.session.execute(text(
                'SELECT stock_code_id FROM data_download_records WHERE stock_code_id IN :ids'
            ).bindparams(bindparam('ids', expanding=True)), {'ids': ch}).fetchall()}
        missing = [c for c in have_info if info[c] not in existing_ids]

        print(f'板块成分股(去重): {len(codes)}')
        print(f'  缺 StockInfo(跳过, 多为B股): {len(no_info)} {no_info[:6]}')
        print(f'  已在表单: {len(have_info) - len(missing)}   需新增: {len(missing)}')
        print(f'  市场最新交易日: {mkt}')

        # 4) 组装新行
        now = datetime.utcnow()
        far_past = date(2018, 1, 1)
        n_success = n_pending = n_nodata = 0
        new_rows = []
        for c in missing:
            end_d = daily_max.get(c)                 # 该股 1m/日K 最新日期
            if end_d is None:
                status = 'pending'; n_nodata += 1     # 从未下载：end_date 留 NULL（诚实，
                rec_d = far_past                       #   日常 end<=today 天然跳过，靠修复 include_new 全量补）
                new_rows.append(dict(stock_code_id=info[c], download_status=status,
                                     download_progress=0.0, start_date=None, end_date=None,
                                     record_date=rec_d, total_records=0, downloaded_records=0,
                                     last_download_time=now, created_at=now, updated_at=now))
                continue
            elif mkt is not None and end_d >= mkt:
                status = 'success'; n_success += 1    # 已最新
                rec_d = end_d
            else:
                status = 'success'; n_pending += 1    # 有数据但落后：登记真值，
                rec_d = end_d                         #   大缺口靠板块修复补，日常下载按新交易日重置补
            new_rows.append(dict(stock_code_id=info[c], download_status=status,
                                 download_progress=0.0,
                                 start_date=(end_d or far_past), end_date=(end_d or far_past),
                                 record_date=rec_d, total_records=0, downloaded_records=0,
                                 last_download_time=now, created_at=now, updated_at=now))

        print(f'  拟新增明细: 已最新 success={n_success}  落后(仍标success待重置) ={n_pending}  '
              f'无数据 pending={n_nodata}')

        if not commit:
            print('\n[dry-run] 未写库。加 --commit 实际执行。')
            return

        # 5) 批量插入
        for i in range(0, len(new_rows), 500):
            db.session.bulk_insert_mappings(DownloadRecord, new_rows[i:i + 500])
            db.session.commit()
        print(f'\n[OK] 已写入 {len(new_rows)} 条到 data_download_records。')
        total = db.session.execute(text('SELECT COUNT(*) FROM data_download_records')).scalar()
        print(f'  表单现有总记录数: {total}')


if __name__ == '__main__':
    main(commit=('--commit' in sys.argv))
