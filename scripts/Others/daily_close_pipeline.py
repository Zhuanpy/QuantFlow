#!/usr/bin/env python3
"""收盘后一键流水线：下载行情(日K+1m+15m) → 计算个股分布快照。

为什么要这个脚本
----------------
/stock_stats/ 页面读的是 stock_dist_snapshot 表，而这张表此前只能靠手动点
/screener/ 的「计算快照」按钮写入，且快照又是从 data/15m parquet 算出来的、
15m 本身也无调度。于是任何一环没人手动跑，页面就会"卡"在旧日期。

本脚本把这条链按**依赖顺序**串起来，专供「每天收盘后」调度（Windows 任务计划
程序 / cron），保证：必须先把 data/15m 更新到最新交易日，再算快照——否则快照
会基于旧 15m、页面继续卡住。

顺序：
    STEP 1  download_file()             下载日K + 1分钟 + 转 15m（= UI「下载」按钮同款批处理）
    STEP 2  compute_dist_snapshots      扫 data/15m 重算分布快照（原始口径，可选叠加去噪口径）

用法：
    python scripts/Others/daily_close_pipeline.py                 # 完整流水线（下载 + 快照）
    python scripts/Others/daily_close_pipeline.py --days 5        # 下载天数(1-5)
    python scripts/Others/daily_close_pipeline.py --force         # 强制重下(忽略当天已成功)
    python scripts/Others/daily_close_pipeline.py --skip-download # 跳过下载，只重算快照
    python scripts/Others/daily_close_pipeline.py --merge-below 5 # 额外再算一遍「合并<5%」去噪口径
    python scripts/Others/daily_close_pipeline.py --skip-non-trading  # 非交易日直接退出(调度兜底)

退出码：0=全部成功；2=下载步骤异常但快照已跑；3=快照步骤失败；4=非交易日跳过。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger('daily_close_pipeline')


def _is_trading_day(app) -> bool:
    """今天是否交易日（复用下载路由里的交易日历）。失败时保守返回 True，不阻断流水线。"""
    try:
        from App.routes.data.download_data_route import get_latest_trading_date
        _, is_today_trading = get_latest_trading_date()
        return bool(is_today_trading)
    except Exception as e:
        logger.warning(f'[trading-day] 判断交易日失败，按交易日继续: {e}')
        return True


def run_download(days: int, force: bool) -> bool:
    """STEP 1：调用 UI 同款批处理 download_file()（同步，跑完才返回）。

    download_file 用模块级全局变量读取 days/force，并在内部自建 app_context，
    这里在外层 app_context 内直接调用即可。返回 True 表示状态为「已完成」。
    """
    import App.routes.data.download_data_route as dl
    dl.download_max_days = max(1, min(5, int(days)))
    dl.download_force = bool(force)
    dl.stop_download = False
    logger.info(f'[STEP1] 开始下载行情：days={dl.download_max_days} force={dl.download_force}')
    t0 = time.time()
    dl.download_file()
    status = dl.download_status
    logger.info(f'[STEP1] 下载结束：status="{status}"  elapsed={time.time()-t0:.0f}s')
    # download_file 完成时把 download_status 置为「已完成」或「数据已是最新...」/「无需下载」
    ok_markers = ('已完成', '已是最新', '无需下载', '没有需要下载')
    return any(m in (status or '') for m in ok_markers)


def main():
    parser = argparse.ArgumentParser(description='收盘后一键流水线：下载 → 转15m → 算分布快照')
    parser.add_argument('--days', type=int, default=5, help='下载天数(1-5)，默认 5')
    parser.add_argument('--force', action='store_true', help='强制重下，忽略当天已成功记录')
    parser.add_argument('--skip-download', action='store_true', help='跳过下载，只重算快照')
    parser.add_argument('--skip-non-trading', action='store_true',
                        help='非交易日直接退出(退出码4)，给每日调度做兜底')
    parser.add_argument('--merge-below', type=float, default=0.0,
                        help='额外再算一遍「合并<X%%小周期」去噪口径(原始口径恒会算)')
    args = parser.parse_args()

    from App import create_app
    app = create_app()
    download_ok = True

    with app.app_context():
        if args.skip_non_trading and not _is_trading_day(app):
            logger.info('[skip] 今天非交易日，按 --skip-non-trading 退出')
            sys.exit(4)

        # ---- STEP 1：下载行情（日K + 1m + 15m）----
        # 板块(BKxxxx)已随 download_file() 主下载队列一起跑（与股票池并列），STEP1 即覆盖；
        # --skip-download 保持纯「只重算快照」语义，不触发任何下载。
        if args.skip_download:
            logger.info('[STEP1] 按 --skip-download 跳过下载，直接重算快照')
        else:
            try:
                download_ok = run_download(args.days, args.force)
            except Exception:
                download_ok = False
                logger.exception('[STEP1] 下载流程抛出异常（仍继续算快照，但快照可能基于旧 15m）')

        # ---- STEP 2：算分布快照（扫最新 data/15m）----
        from scripts.Others.compute_dist_snapshots import run_batch
        today = date.today()
        try:
            logger.info('[STEP2] 重算分布快照（原始口径 merge_below=0）')
            s0 = run_batch(target_date=today, merge_below=0.0)
            if args.merge_below and args.merge_below > 0:
                logger.info(f'[STEP2] 重算分布快照（去噪口径 merge_below={args.merge_below}）')
                run_batch(target_date=today, merge_below=args.merge_below)
        except Exception:
            logger.exception('[STEP2] 快照计算失败')
            sys.exit(3)

    # ---- 收尾退出码 ----
    if s0.get('ok', 0) == 0 and s0.get('total', 0) > 0:
        logger.error('[done] 快照 ok=0，全部失败/无数据，请检查 data/15m 是否为空或已损坏')
        sys.exit(3)
    if not download_ok:
        logger.warning('[done] 下载步骤异常，但快照已基于现有 15m 重算完成（退出码 2）')
        sys.exit(2)
    logger.info(f'[done] 流水线完成：快照 ok={s0.get("ok")} fail={s0.get("fail")} '
                f'empty={s0.get("empty")} date={s0.get("date")}')


if __name__ == '__main__':
    main()
