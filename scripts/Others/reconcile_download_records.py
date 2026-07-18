"""
下载记录对账：以磁盘 parquet 为真值，把 data_download_records 的
start_date / end_date / record_date / download_status 校准到磁盘实际。

背景：修复/下载更新了磁盘 1m parquet，但下载记录的 end_date 常没同步（回写缺口/被重置覆盖），
导致记录 end_date（如 05-22）与磁盘真值（如 07-10）漂移，"落后"判断失真。本脚本先对账再谈补数据。

规则（仅对非BK、非忽略、且磁盘有 parquet 的记录）：
  start_date = parquet 最早日期
  end_date   = record_date = parquet 最新日期
  status     = 'success' 若 end_date >= 市场最新交易日，否则 'pending'
磁盘无 parquet 的记录跳过（保持原样，属从未下载）。

用法： python scripts/Others/reconcile_download_records.py [--commit]
"""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(commit: bool):
    from App import create_app, db
    from sqlalchemy import text
    app = create_app()
    with app.app_context():
        from App.services.minute_data_repair import _code_date_range
        from config import Config
        dq = Path(Config.get_project_root()) / 'data' / 'quarters'
        qdirs = sorted([d for y in dq.iterdir() if y.is_dir()
                        for d in y.iterdir() if d.is_dir()],
                       key=lambda d: (d.parent.name, d.name), reverse=True) if dq.exists() else []
        mkt = db.session.execute(text(
            "SELECT MAX(date) FROM data_stock_daily WHERE stock_code NOT LIKE 'BK%'")).scalar()
        print(f'市场最新交易日: {mkt}   季度目录数: {len(qdirs)}')

        rows = db.session.execute(text(
            """
            SELECT dr.id, si.code, dr.start_date, dr.end_date, dr.download_status
            FROM data_download_records dr JOIN data_stock_info si ON si.id = dr.stock_code_id
            WHERE si.code NOT LIKE 'BK%'
              AND dr.end_date != '2050-01-01' AND dr.record_date != '2050-01-01'
            """)).fetchall()
        print(f'待对账记录(非BK): {len(rows)}')

        updates = []
        n_nodisk = 0
        seen = set()
        for rid, code, sstart, send, sstatus in rows:
            if code in seen:      # 同 code 多条 StockInfo → 只处理一次的磁盘扫描，但每条记录都更新
                pass
            earliest, latest = _code_date_range(code, qdirs)
            if latest is None:
                n_nodisk += 1
                continue
            d_start = earliest.date() if earliest is not None else None
            d_end = latest.date()
            status = 'success' if (mkt is not None and d_end >= mkt) else 'pending'
            if str(send) != str(d_end) or str(sstart) != str(d_start) or sstatus != status:
                updates.append({'id': rid, 's': d_start, 'e': d_end, 'st': status})

        print(f'需更新: {len(updates)}   磁盘无数据(跳过): {n_nodisk}')
        if not commit:
            for u in updates[:10]:
                print('   样例:', u)
            print('\n[dry-run] 未写库。加 --commit 执行。')
            return

        now = datetime.utcnow()
        for i in range(0, len(updates), 500):
            for u in updates[i:i + 500]:
                db.session.execute(text(
                    'UPDATE data_download_records SET start_date=:s, end_date=:e, '
                    'record_date=:e, download_status=:st, updated_at=:now WHERE id=:id'),
                    {'s': u['s'], 'e': u['e'], 'st': u['st'], 'now': now, 'id': u['id']})
            db.session.commit()
            print(f'   已提交 {min(i + 500, len(updates))}/{len(updates)}')
        print('[OK] 对账完成。')


if __name__ == '__main__':
    main(commit=('--commit' in sys.argv))
