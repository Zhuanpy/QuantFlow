"""
独立进程跑「增量补齐·全部板块」，不挂在 Flask dev server 上——避免 debug 自动重载
杀掉后台修复线程（Web 触发的修复反复中断就是这个原因）。

逻辑与 /board_data/api/repair_all 完全一致：
  取各板块最新名单的非 BK 成分股 → 跳过已到市场最新日的 → 逐板块归集(去重) →
  调 start_repair 后台线程增量补齐(本地zip + TDX缺口 + 重生成15m + 补日K + 回写账本end_date) →
  然后自动重算趋势/分布。本进程阻塞轮询直到全部完成才退出。

用法： python scripts/Others/repair_all_boards_standalone.py [--no-new] [--no-trend]
  --no-new    不下载「从未下载」的股票(默认下载, include_new=True)
  --no-trend  修复后不接力重算趋势/分布(默认重算)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def build_candidates(db, include_new):
    from sqlalchemy import text, bindparam
    from config import Config
    eng = db.engines['quanttradingsystem']
    with eng.connect() as conn:
        board_rows = conn.execute(text(
            """
            SELECT ie.board_code, ie.board_name, ie.stock_code FROM industry_eastmoney ie
            JOIN (SELECT board_code, MAX(date) d FROM industry_eastmoney GROUP BY board_code) m
              ON ie.board_code = m.board_code AND ie.date = m.d
            WHERE ie.stock_code NOT LIKE 'BK%'
            ORDER BY ie.board_code, ie.stock_code
            """)).fetchall()
        codes = list({r[2] for r in board_rows})
        mkt = conn.execute(text(
            "SELECT MAX(date) FROM data_stock_daily WHERE stock_code NOT LIKE 'BK%'")).scalar()
        latest = ({r[0]: r[1] for r in conn.execute(text(
            'SELECT stock_code, MAX(date) FROM data_stock_daily WHERE stock_code IN :codes '
            'GROUP BY stock_code').bindparams(bindparam('codes', expanding=True)),
            {'codes': codes}).fetchall()} if codes else {})
    have15 = {p.stem for p in (Path(Config.get_project_root()) / 'data' / '15m').glob('*.parquet')}

    def has_data(c):
        return c in have15 or latest.get(c) is not None

    def is_current(c):
        return mkt is not None and latest.get(c) == mkt

    assigned, ordered, code2board = set(), [], {}
    for bcode, bname, code in board_rows:
        if code in assigned or is_current(code) or not (has_data(code) or include_new):
            continue
        assigned.add(code)
        ordered.append(code)
        code2board[code] = f'{bname or bcode}'
    return ordered, code2board, len(set(code2board.values())), mkt


def main():
    include_new = '--no-new' not in sys.argv
    then_trend = '--no-trend' not in sys.argv
    from App import create_app, db
    app = create_app()
    with app.app_context():
        ordered, code2board, board_total, mkt = build_candidates(db, include_new)
        print(f'[standalone] 市场最新日 {mkt}；待修复 {len(ordered)} 只，跨 {board_total} 板块；'
              f'include_new={include_new} then_trend={then_trend}', flush=True)
        if not ordered:
            print('[standalone] 无待修复股票，退出。', flush=True)
            return

        from App.services.minute_data_repair import (
            start_repair, get_status, get_trend_status, DEFAULT_ZIP_DIR)
        ok, msg = start_repair(app, '全部板块(standalone)', ordered, zip_dir=DEFAULT_ZIP_DIR,
                               tdx_days=90, then_trend=then_trend,
                               board_map=code2board, board_total=board_total)
        print(f'[standalone] start_repair: ok={ok} msg={msg}', flush=True)
        if not ok:
            return

        # 阻塞轮询：修复阶段
        while True:
            time.sleep(20)
            s = get_status()
            print(f'[repair] 板块 {s["board_idx"]}/{s["board_total"]}「{s["cur_board"]}」 '
                  f'总 {s["done"]}/{s["total"]} 成功{s["ok"]}/失败{s["fail"]} 当前={s["cur"]} '
                  f'| 1m新增 zip{s["zip_added"]}+tdx{s["tdx_added"]} 补日K{s["daily_added"]}', flush=True)
            if not s['running']:
                print(f'[standalone] 修复阶段结束：成功{s["ok"]} 失败{s["fail"]} '
                      f'跳过{s["skipped"]} 停止={s["stopped"]} 错误={s["error"]}', flush=True)
                break

        # 阻塞轮询：趋势/分布重算阶段（then_trend 时由 _run 自动接力）
        if then_trend:
            time.sleep(3)
            while True:
                ts = get_trend_status()
                if not ts['running'] and ts['done'] == 0 and ts['total'] == 0:
                    # 还没启动或已结束且无数据；再确认一次
                    time.sleep(5)
                    ts = get_trend_status()
                print(f'[trend] {ts.get("phase")} {ts["done"]}/{ts["total"]} '
                      f'打分{ts["ts_ok"]}/{ts["ts_fail"]} 快照{ts["snap_ok"]}/{ts["snap_fail"]} '
                      f'当前={ts["cur"]}', flush=True)
                if not ts['running']:
                    break
                time.sleep(20)
        print('[standalone] 全部完成。', flush=True)


if __name__ == '__main__':
    main()
