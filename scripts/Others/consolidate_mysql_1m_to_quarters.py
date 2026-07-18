"""
把 MySQL 里遗留的「按年 1m 库」data1m<year>（每只股票一张 data_1m_<code> 表）
核对/补齐进本地 data/quarters，确认该年每张表的每一根 1m 都已在 parquet 后，
DROP 掉整个 data1m<year> 库，消除与本地重复。

背景：app 早已不用这些 per-year 库（SQLALCHEMY_BINDS 未配置 data1m{year}，读写都失败回退本地）。

安全：
  · 默认 dry-run 只统计；--commit 才合并 + DROP。
  · 只有该年【全部表】的数据都确认已在 parquet，才 DROP 该库；有任一表未覆盖则保留整库不删。

用法:
  python scripts/Others/consolidate_mysql_1m_to_quarters.py --years 2025,2026
  python scripts/Others/consolidate_mysql_1m_to_quarters.py --years 2025,2026 --commit
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from scripts.Others.import_minute_zips import _write_quarter_parquet, _M1_COLS


def get_cfg():
    from App import create_app
    app = create_app()
    with app.app_context():
        from config import Config
        return Config.get_db_config()


def _to_m1(df):
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df['year'] = df['date'].dt.year
    df['month'] = df['date'].dt.month
    df['day'] = df['date'].dt.day
    df['hour'] = df['date'].dt.hour
    df['minute'] = df['date'].dt.minute
    for c in ('open', 'close', 'high', 'low', 'volume', 'money'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df[_M1_COLS]


def _missing(part, out):
    want = set(part['date'])
    if not out.exists():
        return want
    have = set(pd.to_datetime(pd.read_parquet(out, columns=['date'])['date']))
    return want - have


def process_table(conn, db, tn, year, dq, commit):
    code = tn[len('data_1m_'):]
    df = pd.read_sql(f'SELECT date,open,close,high,low,volume,money FROM `{db}`.`{tn}`', conn)
    if df.empty:
        return 0, True
    m = _to_m1(df)
    g = m.assign(_q=((m['date'].dt.month - 1) // 3 + 1))
    added = 0
    for q, part in g.groupby('_q'):
        part = part.drop(columns=['_q'])
        out = dq / str(year) / f'Q{int(q)}' / f'{code}.parquet'
        miss = _missing(part, out)
        if not miss:
            continue
        added += len(miss)
        _write_quarter_parquet(part[_M1_COLS], out, dry_run=not commit)
    # 覆盖判定
    if commit:
        covered = True
        for q, part in g.groupby('_q'):
            out = dq / str(year) / f'Q{int(q)}' / f'{code}.parquet'
            if _missing(part.drop(columns=['_q']), out):
                covered = False
                break
    else:
        covered = (added == 0)
    return added, covered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='2025,2026')
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()
    years = [int(y) for y in args.years.split(',') if y.strip()]

    import pymysql
    cfg = get_cfg()
    dq = Path(__file__).resolve().parents[2] / 'data' / 'quarters'
    conn = pymysql.connect(host=cfg['host'], user=cfg['user'],
                           password=cfg['password'], charset='utf8mb4')
    cur = conn.cursor()

    for year in years:
        db = f'data1m{year}'
        cur.execute(f"SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='{db}'")
        if not cur.fetchone()[0]:
            print(f'[{db}] 库不存在，跳过')
            continue
        cur.execute(f"SELECT table_name FROM information_schema.tables "
                    f"WHERE table_schema='{db}' AND table_name LIKE 'data_1m_%' ORDER BY table_name")
        tabs = [r[0] for r in cur.fetchall()]
        print(f'\n===== {db}: {len(tabs)} 张表  模式={"COMMIT" if args.commit else "dry-run"} =====')

        total_added = 0
        uncovered = []
        for i, tn in enumerate(tabs, 1):
            try:
                added, covered = process_table(conn, db, tn, year, dq, args.commit)
                total_added += added
                if not covered:
                    uncovered.append(tn)
            except Exception as e:
                uncovered.append(f'{tn} (出错:{str(e)[:40]})')
            if i % 100 == 0:
                print(f'   ...{i}/{len(tabs)}  累计补 {total_added} 根  未覆盖 {len(uncovered)}', flush=True)

        print(f'  {db}: 累计补进 parquet {total_added} 根；未覆盖表 {len(uncovered)}')
        for u in uncovered[:10]:
            print('    未覆盖:', u)

        if not args.commit:
            print(f'  [dry-run] 若齐全将 DROP DATABASE {db}。')
            continue
        if uncovered:
            print(f'  ⚠ 有 {len(uncovered)} 张表数据未确认进 parquet，保留 {db} 不删。')
            continue
        cur.execute(f'DROP DATABASE `{db}`')
        conn.commit()
        print(f'  [OK] {db} 全部数据已在 parquet，已 DROP DATABASE。')

    conn.close()


if __name__ == '__main__':
    main()
