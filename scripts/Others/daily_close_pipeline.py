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
    STEP 3  sync_sector_flow            板块(行业+概念)每日热度快照 → mkt_sector_flow_daily
    STEP 4  board_pool 维护                L1 候选池进出 + 新入池板块拉成分
    STEP 5  board_trend 评分               给 L1 池内板块跑 v1-mtf 趋势评分
    STEP 6  board_heat_ts                 L1 池内板块的时序热度分（增量）

STEP 3 说明：这是热点追踪 L0 层，东财只给**当日**快照，今天没抓就永久缺这一天
（概念板块尤其补不回来）。所以它放在最后、独立成败：前面步骤失败也照跑，
它自己失败也不拖垮整条流水线，只把退出码抬到 5。

用法：
    python scripts/Others/daily_close_pipeline.py                 # 完整流水线（下载 + 快照 + 热度）
    python scripts/Others/daily_close_pipeline.py --days 5        # 下载天数(1-5)
    python scripts/Others/daily_close_pipeline.py --force         # 强制重下(忽略当天已成功)
    python scripts/Others/daily_close_pipeline.py --skip-download # 跳过下载，只重算快照
    python scripts/Others/daily_close_pipeline.py --merge-below 5 # 额外再算一遍「合并<5%」去噪口径
    python scripts/Others/daily_close_pipeline.py --skip-non-trading  # 非交易日直接退出(调度兜底)
    python scripts/Others/daily_close_pipeline.py --only-flow     # 只跑 STEP3 板块热度快照
    python scripts/Others/daily_close_pipeline.py --skip-flow     # 不跑 STEP3
    python scripts/Others/daily_close_pipeline.py --only-pool     # 只跑 STEP4+5 候选池维护与评分
    python scripts/Others/daily_close_pipeline.py --skip-pool     # 不跑 STEP4+5

STEP4/5 说明：候选池的进出规则读的就是 STEP3 落的热度快照，所以必须排在它后面。
新入池板块会自动拉成分股；**合成指数不在流水线里自动跑**（读几百只成分股的日K/15m，
很慢且失败面大），需要在 /board_pool 页面按需点，或单独跑脚本。没有日K/合成指数的
板块在 STEP5 会被跳过，不算失败。

退出码：0=全部成功；2=下载步骤异常但快照已跑；3=快照步骤失败；4=非交易日跳过；
        5=板块热度快照失败(其余步骤已完成)。
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


def run_sector_flow(app) -> int:
    """STEP 3：抓行业+概念板块的当日热度快照 → mkt_sector_flow_daily（热点追踪 L0）。

    这里是**主线程**，所以允许 akshare 兜底（东财 push2 时常 502/限流；akshare 的
    py_mini_racer(V8) 只能主线程初始化，Web 请求线程那条路径是禁用它的）。

    返回 0=成功，非 0=失败（由调用方决定退出码）。
    """
    from App.services.sector_flow_service import sync_sector_flow
    logger.info('[STEP3] 抓取板块热度快照（行业+概念，全量翻页）')
    t0 = time.time()
    try:
        res = sync_sector_flow(app, allow_akshare=True)
    except Exception:
        logger.exception('[STEP3] 板块热度快照失败')
        return 1
    per, src = res.get('per_type', {}), res.get('sources', {})
    logger.info(f'[STEP3] 完成：date={res.get("date")} intraday={res.get("is_intraday")} '
                f'{per} source={src} elapsed={time.time()-t0:.0f}s')
    if res.get('errors'):
        for e in res['errors']:
            logger.error(f'[STEP3] {e}')
        return 1
    if not per:
        logger.error('[STEP3] 一条都没落库')
        return 1
    return 0


def run_board_pool() -> int:
    """STEP 4：候选池维护 —— 按热度快照跑进出池规则，并给新入池板块拉成分股。

    出池只降层级（tracking_tier 回 0），L0 的每日热度记录一行不删。
    返回 0=正常（这一步失败不该拖垮流水线，异常只记日志）。
    """
    logger.info('[STEP4] 候选池维护（进出池规则 + 新入池拉成分）')
    try:
        from App.services import board_pool_service as pool
        res = pool.apply_changes()
        if not res.get('ok'):
            logger.warning(f'[STEP4] 未执行：{res.get("message")}')
            return 0
        added = res.get('added', [])
        logger.info(f'[STEP4] mode={res.get("mode")} 入池 {len(added)} 个，'
                    f'出池 {len(res.get("dropped", []))} 个，'
                    f'闸门拦截 {len(res.get("blocked", []))} 个，'
                    f'超限额顺延 {len(res.get("deferred", []))} 个')
        if added:
            m = pool.sync_members(added, only_missing=False)
            logger.info(f'[STEP4] 新入池成分同步：成功 {m.get("synced")}，'
                        f'过宽退回 {len(m.get("too_broad", []))}，失败 {len(m.get("failed", []))}')
    except Exception:
        logger.exception('[STEP4] 候选池维护失败（不影响其余步骤）')
    return 0


def run_pool_scoring() -> int:
    """STEP 5：给 L1 池内**有日K/合成指数**的板块跑 v1-mtf 趋势评分。

    概念板块的日K来自合成指数（BKxxxxS），需要先在 /board_pool 页点"合成指数"；
    没有的会被跳过，不算失败——不能因为合成没跑就让整条流水线报错。
    """
    from datetime import date as _date
    try:
        from App.models.evaluation.Board import Board, TIER_L1
        from App.services.board_trend_score_service import compute_and_persist
        codes = [b.board_code for b in Board.list_by_tier(TIER_L1) if b.has_daily_data]
        if not codes:
            logger.info('[STEP5] 池内没有可评分板块（都缺日K/合成指数），跳过')
            return 0
        logger.info(f'[STEP5] L1 趋势评分：{len(codes)} 个板块')
        t0 = time.time()
        res = compute_and_persist(codes, _date.today())
        logger.info(f'[STEP5] 完成：ok={res.get("ok")} fail={res.get("fail")} '
                    f'elapsed={time.time()-t0:.0f}s')
        for e in (res.get('errors') or [])[:10]:
            logger.warning(f'[STEP5] {e}')
    except Exception:
        logger.exception('[STEP5] L1 评分失败（不影响其余步骤）')
    return 0


def run_cycle_forecast() -> int:
    """STEP 7：周期预估的状态刷新 + 复盘回填。

    - 状态：段翻转→作废、触达目标→已触达、过了预估结束时间→过期
      （触达只看下预估之后的 bar —— 段内更早的历史极值不算数）
    - 复盘：段走完后回填实际极值/实际长度/误差。**没触达的也要回填** ——
      只统计成功的样本会把分位系统性调偏，而这批数据是日后校准
      「多周期自动定 P」那张硬编码表的唯一依据。
    """
    try:
        from App.services.cycle_forecast_service import refresh_all
        res = refresh_all()
        logger.info(f'[STEP7] 周期预估：检查 {res.get("checked")} 条 / '
                    f'{res.get("stocks")} 只股票，状态变更 {res.get("status_changed")}，'
                    f'回填复盘 {res.get("reviewed")}')
    except Exception:
        logger.exception('[STEP7] 周期预估刷新失败（不影响其余步骤）')
    return 0


def run_heat_ts() -> int:
    """STEP 6：L1 池内板块的时序热度分（增量）。

    与 STEP3 的横截面热度是**两个口径**：这个只跟板块自己的历史比，不需要全市场快照，
    所以概念板块也能有长历史。纯本地计算，很快（实测 101 个板块 4 秒）。
    """
    try:
        from App.services.board_heat_ts_service import compute_pool
        t0 = time.time()
        res = compute_pool(incremental=True)
        logger.info(f'[STEP6] 时序热度：{res.get("ok")}/{res.get("targets")} 个板块，'
                    f'新增 {res.get("rows")} 行，elapsed={time.time()-t0:.0f}s')
        for e in (res.get('failed') or [])[:5]:
            logger.warning(f'[STEP6] {e}')
    except Exception:
        logger.exception('[STEP6] 时序热度计算失败（不影响其余步骤）')
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='收盘后一键流水线：下载 → 转15m → 分布快照 → 板块热度 → 候选池 → L1评分')
    parser.add_argument('--days', type=int, default=5, help='下载天数(1-5)，默认 5')
    parser.add_argument('--force', action='store_true', help='强制重下，忽略当天已成功记录')
    parser.add_argument('--skip-download', action='store_true', help='跳过下载，只重算快照')
    parser.add_argument('--skip-non-trading', action='store_true',
                        help='非交易日直接退出(退出码4)，给每日调度做兜底')
    parser.add_argument('--merge-below', type=float, default=0.0,
                        help='额外再算一遍「合并<X%%小周期」去噪口径(原始口径恒会算)')
    parser.add_argument('--skip-flow', action='store_true',
                        help='跳过 STEP3 板块热度快照')
    parser.add_argument('--only-flow', action='store_true',
                        help='只跑 STEP3 板块热度快照（不下载、不算分布快照）')
    parser.add_argument('--skip-pool', action='store_true',
                        help='跳过 STEP4/5 候选池维护与 L1 评分')
    parser.add_argument('--only-pool', action='store_true',
                        help='只跑 STEP4/5 候选池维护与 L1 评分')
    args = parser.parse_args()

    from App import create_app
    app = create_app()
    download_ok = True

    with app.app_context():
        if args.skip_non_trading and not _is_trading_day(app):
            logger.info('[skip] 今天非交易日，按 --skip-non-trading 退出')
            sys.exit(4)

        # ---- --only-flow：只跑板块热度快照 ----
        if args.only_flow:
            code = run_sector_flow(app)
            sys.exit(code)

        # ---- --only-pool：只跑候选池维护 + L1 评分 ----
        if args.only_pool:
            run_board_pool()
            run_pool_scoring()
            run_heat_ts()
            run_cycle_forecast()
            sys.exit(0)

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

        # ---- STEP 3：板块热度快照（热点追踪 L0）----
        flow_code = 0 if args.skip_flow else run_sector_flow(app)
        if args.skip_flow:
            logger.info('[STEP3] 按 --skip-flow 跳过板块热度快照')

        # ---- STEP 4/5：候选池维护 + L1 评分（读 STEP3 的快照，必须排它后面）----
        if args.skip_pool:
            logger.info('[STEP4/5] 按 --skip-pool 跳过候选池维护与评分')
        else:
            run_board_pool()
            run_pool_scoring()
            run_heat_ts()

        # ---- STEP 7：周期预估状态刷新与复盘回填 ----
        # 只依赖 15m parquet，与候选池无关，所以 --skip-pool 时照跑
        run_cycle_forecast()

    # ---- 收尾退出码 ----
    if s0.get('ok', 0) == 0 and s0.get('total', 0) > 0:
        logger.error('[done] 快照 ok=0，全部失败/无数据，请检查 data/15m 是否为空或已损坏')
        sys.exit(3)
    if not download_ok:
        logger.warning('[done] 下载步骤异常，但快照已基于现有 15m 重算完成（退出码 2）')
        sys.exit(2)
    if flow_code:
        logger.warning('[done] 板块热度快照失败，其余步骤已完成（退出码 5）')
        sys.exit(5)
    logger.info(f'[done] 流水线完成：快照 ok={s0.get("ok")} fail={s0.get("fail")} '
                f'empty={s0.get("empty")} date={s0.get("date")}')


if __name__ == '__main__':
    main()
