from App.codes.downloads.DlStockData import RMDownloadData, StockType, download_1m_by_type
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from App.codes.utils.Normal import ResampleData
from flask import render_template, current_app, jsonify, Blueprint, copy_current_request_context, request, flash
from App.models.data.Stock1m import RecordStockMinute as dlr
from App.models.data.basic_info import StockCodes
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.exts import db
import pandas as pd
from datetime import date, datetime, timedelta
import logging
import time

from App.codes.RnnDataFile.save_download import save_1m_to_csv, complete_download_process, batch_complete_download_process

# 创建蓝图
download_data_bp = Blueprint('download_data_bp', __name__)

# 交易日历缓存
_trading_dates_cache = None
_trading_dates_cache_date = None
_trading_dates_lock = threading.Lock()

# 下载状态和进度的存储
download_status = "未开始"
download_progress = 0
download_thread = None
stop_download = False  # 用于控制下载停止
download_max_days = 5  # 最大下载天数，默认5天
download_force = False  # 是否强制重新下载（忽略当天已下载成功的记录）
download_lock = threading.Lock()  # 用于保护全局变量的锁
DOWNLOAD_WORKERS = 3  # 并发下载线程数

# 流水线状态跟踪
pipeline_current_stock = ""       # 当前处理的股票代码
pipeline_current_step = 0         # 当前步骤 0=空闲, 1=日K, 2=1分钟, 3=15分钟
pipeline_step_name = ""           # 当前步骤名称
pipeline_stock_index = 0          # 当前第几只股票
pipeline_total_stocks = 0         # 总股票数
pipeline_completed_count = 0      # 已完成的股票数（并发用）

# 股票代码缓存
stock_code_cache = {}
# 股票名称缓存（按 stock_code_id）
stock_name_cache = {}

# 最近完成的股票15m数据预览缓存
last_completed_preview = {
    'stock_code': '',
    'stock_name': '',
    'data_15m': [],       # 最近N条15m数据
    'data_daily': [],     # 当次下载的日K摘要
    'macd_success': False,
    'total_15m': 0,
    'updated_at': '',
}


def get_trading_dates():
    """
    获取A股交易日历，优先使用akshare，失败则回退到周末判断。
    结果按天缓存，避免重复请求。

    Returns:
        set[date] or None: 交易日期集合。None表示无法获取（回退到周末判断）。
    """
    global _trading_dates_cache, _trading_dates_cache_date
    today = date.today()

    if _trading_dates_cache is not None and _trading_dates_cache_date == today:
        return _trading_dates_cache

    # akshare 的日历用 py_mini_racer(V8) 执行 JS，V8/PartitionAlloc 在非主线程或二次初始化
    # 会直接 abort（[FATAL] partition_address_space.cc: IsConfigurablePoolInitialized）。
    # 因此只在「主线程」里真正拉取；worker（Flask 请求线程/后台线程）一律复用已有缓存，
    # 拿不到就返回上次缓存/None（回退周末判断），宁可稍旧也绝不在请求线程里初始化 V8。
    # 日历一次拉取即含当年全部交易日，跨零点也无需 worker 再拉。启动时会主线程预热。
    if threading.current_thread() is not threading.main_thread():
        return _trading_dates_cache

    with _trading_dates_lock:
        # 双检：等锁期间可能已被别的主线程路径填好
        if _trading_dates_cache is not None and _trading_dates_cache_date == today:
            return _trading_dates_cache
        try:
            import akshare as ak
            df = ak.tool_trade_date_hist_sina()
            # 列名可能是 'trade_date'
            col = df.columns[0]
            dates = set(pd.to_datetime(df[col]).dt.date)
            _trading_dates_cache = dates
            _trading_dates_cache_date = today
            logging.info(f"成功获取交易日历，共 {len(dates)} 个交易日")
            return dates
        except Exception as e:
            logging.warning(f"获取交易日历失败: {e}，将回退到周末判断")
            _trading_dates_cache = None
            _trading_dates_cache_date = today
            return None


def get_latest_trading_date(ref_date=None):
    """
    获取 ref_date 当天或之前最近的一个交易日。
    如果当前时间在15:00之前，认为今天的数据尚未完整，返回上一个交易日。

    Returns:
        date: 最近的交易日
        bool: 今天是否是交易日
    """
    if ref_date is None:
        ref_date = date.today()

    from datetime import time as dt_time
    now = datetime.now()
    # 15:00之前认为今天数据不完整，用前一天
    if ref_date == date.today() and now.time() < dt_time(15, 0):
        ref_date = ref_date - timedelta(days=1)

    trading_dates = get_trading_dates()

    if trading_dates is not None:
        # 使用交易日历精确判断
        d = ref_date
        while d not in trading_dates and d > ref_date - timedelta(days=30):
            d = d - timedelta(days=1)
        is_today_trading = date.today() in trading_dates
        return d, is_today_trading
    else:
        # 回退：跳过周末
        d = ref_date
        while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
            d = d - timedelta(days=1)
        is_today_trading = date.today().weekday() < 5
        return d, is_today_trading


def _mark_recent_attempts_failed(stock_code: str, n_days: int, error_msg: str):
    """把当前 stock_code 最近 n 个交易日打上"尝试失败"印记。

    意图：每次 STEP1/2/3 失败时调用，DailyTaskStatus 表记录该股票哪些交易日的下载失败过、
    失败次数、最近失败原因。is_*_downloaded 等成功标志保持 False，等下次成功才翻 True。

    n_days 通常等于 download_max_days（默认 5）——下载流水线意图覆盖的最近 n 个交易日。
    """
    try:
        from App.models.data.DailyTaskStatus import DailyTaskStatus
        all_dates = get_trading_dates() or []
        today = date.today()
        recent = sorted([d for d in all_dates if d <= today])[-n_days:]
        for d in recent:
            DailyTaskStatus.mark_attempt_failed(stock_code, d, error_msg)
    except Exception as e:
        logging.warning(f"_mark_recent_attempts_failed({stock_code}) 自身异常: {e}")


def get_stock_code_by_id(stock_code_id):
    """
    根据股票代码ID获取股票代码
    
    Args:
        stock_code_id: 股票代码ID
        
    Returns:
        str: 股票代码，如果未找到返回None
    """
    if stock_code_id in stock_code_cache:
        return stock_code_cache[stock_code_id]
    
    try:
        stock = StockCodes.query.get(stock_code_id)
        if stock:
            stock_code_cache[stock_code_id] = stock.code
            return stock.code
        else:
            logging.warning(f"未找到股票代码ID {stock_code_id} 对应的股票信息")
            return None
    except Exception as e:
        logging.error(f"获取股票代码时发生错误: {e}")
        return None


def get_stock_name_by_id(stock_code_id):
    """根据股票代码ID获取股票/板块名称（带缓存），未找到返回 None。"""
    if stock_code_id in stock_name_cache:
        return stock_name_cache[stock_code_id]

    try:
        stock = StockCodes.query.get(stock_code_id)
        if stock:
            stock_name_cache[stock_code_id] = stock.name
            return stock.name
        return None
    except Exception as e:
        logging.error(f"获取股票名称时发生错误: {e}")
        return None


def _recompute_dist_snapshots_after_download():
    """下载完成后按最新 data/15m 重算个股分布快照（原始口径 merge_below=0）。

    Why：股票池页 /stock_pool/ 与筛选页 /screener/ 的「15m 趋势 / 预估目标价」都读
    stock_dist_snapshot 表，而这张表只有重算才更新；个股详情页 /stock/ 却是实时算 15m。
    不在下载后重算快照，池里的趋势就会卡在旧快照、与详情页不一致。这里把"重算快照"
    挂到下载收尾，让池/筛选页的趋势随每天收盘下载即时刷新。

    失败只记日志、绝不影响下载主流程。假定调用方已在 app_context 内（download_file 是）。
    """
    global download_status
    try:
        from scripts.Others.compute_dist_snapshots import run_batch
        with download_lock:
            download_status = "计算趋势快照..."
        s = run_batch(target_date=date.today(), merge_below=0.0)
        logging.info(f"[snapshot] 下载后重算分布快照完成：ok={s.get('ok')} fail={s.get('fail')} "
                     f"empty={s.get('empty')} total={s.get('total')}")
    except Exception as e:
        logging.warning(f"[snapshot] 下载后重算分布快照失败（不影响下载主流程）: {e}")


def _active_pool_stock_ids():
    """当前股票池(is_active) 对应的 data_stock_info.id 列表。
    分层维护：日常收盘下载只维护股票池；其余板块成分股(在账本里但不在池)靠周/月「增量补齐」。
    池为空则返回 []（日常下载不处理任何股票）。"""
    from App.models.strategy.StockPool import StockPool
    from App.models.data.basic_info import StockInfo
    codes = [p.stock_code for p in
             StockPool.query.filter(StockPool.is_active == True)
             .with_entities(StockPool.stock_code).all()]
    if not codes:
        return []
    return [r.id for r in StockInfo.query.filter(StockInfo.code.in_(codes))
            .with_entities(StockInfo.id).all()]


def _board_stock_ids():
    """所有板块(BKxxxx) 对应的 data_stock_info.id 列表。
    板块日K/1m/15m 已并入日常收盘下载（与股票池并列一起进下载队列），不再单靠脚本补。"""
    from App.models.data.basic_info import StockInfo
    return [r.id for r in StockInfo.query.filter(StockInfo.code.like('BK%'))
            .with_entities(StockInfo.id).all()]


def _apply_scope(query, scope):
    """按页面口径收窄 RecordStockMinute 查询。

    scope='pool'：盘后下载页(mode=download)口径，看「股票池 + 板块」——日常收盘下载
    现在同时跑池内个股和板块，列表/统计跟着这个口径，才能既不把全市场几千条一起显示、
    又能让「板块」筛选/统计有数据（板块从不在池里，只按池收窄会恒为空）。
    其余：数据修复页(mode=repair)口径，全市场都要看。
    """
    if scope != 'pool':
        return query
    return query.filter(dlr.stock_code_id.in_(_active_pool_stock_ids() + _board_stock_ids()))


def download_file():
    # 声明使用全局变量，记录下载状态、进度、停止下载标志和最大下载天数
    global download_status, download_progress, stop_download, download_max_days, download_force
    global pipeline_current_stock, pipeline_current_step, pipeline_step_name
    global pipeline_stock_index, pipeline_total_stocks
    global last_completed_preview

    logging.info(f"download_file() 启动，download_max_days = {download_max_days}")

    # 使用下载锁，初始化下载状态和进度
    with download_lock:
        download_status = "进行中"  # 下载状态为进行中
        download_progress = 0  # 进度初始化为 0
        stop_download = False  # 重置停止下载的标志

    """启动下载任务"""
    today = date.today()  # 获取今天的日期
    current = datetime.now().date()  # 获取当前日期（不含时间部分）

    # 使用应用上下文以便于访问数据库和其他应用资源
    with current_app.app_context():

        # 分层维护：日常收盘下载处理「当前股票池 + 板块」，其余板块成分股靠周/月增量补齐。
        # 板块(BKxxxx)与个股并列进下载队列，走同一套 日K+1m+15m 管线，队列里可见、可筛选。
        pool_ids = _active_pool_stock_ids()
        board_ids = _board_stock_ids()
        download_ids = pool_ids + board_ids
        logging.info(f"日常下载范围=股票池 {len(pool_ids)} 只 + 板块 {len(board_ids)} 个 = {len(download_ids)} 项")

        # 重置失败的股票为待下载状态
        logging.info("开始重置失败的股票为待下载状态...")

        # 将所有失败的股票重置为pending状态（股票池 + 板块）
        failed_reset_count = dlr.query.filter(
            dlr.stock_code_id.in_(download_ids),
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).update({
            'download_status': 'pending',
            'download_progress': 0.0,
            'error_message': None,  # 清除错误信息
            'updated_at': datetime.utcnow()
        }, synchronize_session=False)

        # 获取最近交易日，判断是否需要下载
        latest_trading_date, is_today_trading = get_latest_trading_date()
        logging.info(f"最近交易日: {latest_trading_date}, 今天是否交易日: {is_today_trading}")

        # 检查是否需要重置success记录（判断口径限定股票池）。
        # 注意：这里必须「逐只数还有多少只落后」，不能像以前那样取 record_date 最大的
        # 那一行当全池代表——数据修复(mode=repair)跑的是全市场、包含池内个股，只要它把
        # 任意一只补到最新交易日，代表行就会命中它，全池被误判「已是最新」而跳过当天下载。
        stale_pool_count = dlr.query.filter(
            dlr.stock_code_id.in_(pool_ids),
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1),
            dlr.end_date < latest_trading_date
        ).count()

        success_reset_count = 0

        if download_force:
            need_reset = True
        elif stale_pool_count == 0:
            logging.info(
                f"数据已是最新：股票池内没有 end_date < {latest_trading_date} 的记录，无需重新下载"
            )
            # 不重置success记录，只处理之前失败的
            need_reset = False
        else:
            logging.info(
                f"检测到 {stale_pool_count} 只池内股票落后于最近交易日 {latest_trading_date}，需要重新下载"
            )
            need_reset = True

        if need_reset:
            # 将股票池 + 板块内非忽略的success记录重置为pending（板块也跟着每个新交易日刷新）
            reset_filters = [
                dlr.stock_code_id.in_(download_ids),
                dlr.download_status == 'success',
                dlr.end_date != date(2050, 1, 1),
                dlr.record_date != date(2050, 1, 1)
            ]
            if download_force:
                logging.info("用户选择强制重新下载，重置所有success记录")
            else:
                logging.info(f"重置落后的success记录以获取最新数据（最近交易日: {latest_trading_date}）")
                # 只重排落后的：已被数据修复补到最新交易日的股票不再白下一遍
                reset_filters.append(dlr.end_date < latest_trading_date)

            success_reset_count = dlr.query.filter(*reset_filters).update({
                'download_status': 'pending',
                'download_progress': 0.0,
                'updated_at': datetime.utcnow()
            }, synchronize_session=False)

            logging.info(f"重置了 {success_reset_count} 条success记录为pending状态")
        
        db.session.commit()
        logging.info(f"重置了 {failed_reset_count} 条失败记录为pending状态")

        # 计算符合条件的数据条数（需要下载且日期在今天之前，股票池 + 板块）
        total_count = dlr.query.filter(
            dlr.stock_code_id.in_(download_ids),
            dlr.download_status != 'success',  # 排除已下载成功的记录
            dlr.end_date <= today,  # 下载日期在今天或之前
            dlr.record_date <= today,  # 记录日期在今天或之前
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).count()
        
        if total_count == 0:
            if not need_reset:
                msg = f"数据已是最新（截至交易日 {latest_trading_date}），无需下载"
            else:
                msg = "没有需要下载的数据"
            logging.info(msg)
            # 即使无需下载，也按现有最新 15m 重算分布快照：保证股票池/筛选页 15m 趋势随收盘刷新
            _recompute_dist_snapshots_after_download()
            with download_lock:
                download_status = msg
            return

        logging.info(f"开始下载任务，总共需要下载 {total_count} 个股票")

        # 获取所有需要下载的记录（股票池 + 板块）
        records_to_download = dlr.query.filter(
            dlr.stock_code_id.in_(download_ids),
            dlr.download_status != 'success',
            dlr.end_date <= today,
            dlr.record_date <= today,
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).all()
        
        logging.info(f"获取到 {len(records_to_download)} 条需要下载的记录")

        # 初始化并发计数器
        with download_lock:
            pipeline_completed_count = 0
            pipeline_total_stocks = len(records_to_download)

        # 使用线程池并发下载，提高3倍速度
        app = current_app._get_current_object()
        total = len(records_to_download)

        def _process_single_stock(first_record):
            """处理单个股票的完整下载流程（在子线程中执行）"""
            global download_status, download_progress, stop_download
            global pipeline_current_stock, pipeline_current_step, pipeline_step_name
            global pipeline_stock_index, pipeline_completed_count
            global last_completed_preview

            with app.app_context():
                # 检查是否需要停止下载
                with download_lock:
                    if stop_download:
                        return

                if not first_record:
                    logging.info("记录为空，跳过。")
                    return

                # 关键修复：将主线程加载的ORM对象合并到子线程的session中
                # 否则子线程的db.session.commit()不会提交对first_record的修改
                first_record = db.session.merge(first_record)

                # 获取股票代码
                stock_code = get_stock_code_by_id(first_record.stock_code_id)
                if not stock_code:
                    logging.error(f'无法获取股票代码ID {first_record.stock_code_id} 对应的股票代码')
                    first_record.update_download_status(
                        status='failed',
                        error_msg=f'无法获取股票代码ID {first_record.stock_code_id} 对应的股票代码'
                    )
                    db.session.commit()
                    with download_lock:
                        pipeline_completed_count += 1
                        download_progress = round(pipeline_completed_count * 100 / total, 1)
                    return

                # 判断代码类型：板块代码以 'BK' 开头
                is_board = stock_code.startswith('BK')
                stock_type = StockType.BOARD_1M if is_board else StockType.STOCK_1M
                code_type_name = "板块" if is_board else "股票"

                # 更新流水线当前股票（显示最近启动的）
                with download_lock:
                    pipeline_current_stock = stock_code

                logging.info(f"正在下载{code_type_name} {stock_code} 的数据...")

                # ===== STEP 1/3: 下载日K数据（pytdx优先，AKShare备用，股票+板块通用） =====
                with download_lock:
                    download_status = f"进行中 - {stock_code} Step1/3 日K"

                try:
                    from App.models.data.StockDaily import StockDaily, save_daily_stock_data_to_sql
                    from App.models.data.DailyTaskStatus import DailyTaskStatus

                    # 计算需要补充的天数
                    daily_days = download_max_days + 30
                    try:
                        latest_daily = db.session.query(db.func.max(StockDaily.date)).filter(
                            StockDaily.stock_code == stock_code
                        ).scalar()
                        if latest_daily:
                            gap_days = (current - latest_daily).days
                            if gap_days > daily_days:
                                daily_days = min(gap_days + 10, 365)
                                logging.info(f"[STEP1] {stock_code} 日K数据落后 {gap_days} 天，扩大下载到 {daily_days} 天")
                    except Exception:
                        pass

                    daily_df = pd.DataFrame()

                    # 板块必须走 East Money（fqt=0），不能用 pytdx：pytdx 的 get_index_bars
                    # 返回"复权后"指数（约真实值的 56%），与东财官网不一致。
                    if is_board:
                        try:
                            from App.codes.downloads.eastmoney.board_downloader import BoardDownloader
                            daily_df = BoardDownloader.board_daily(stock_code, days=daily_days)
                            if not daily_df.empty:
                                logging.info(f"[STEP1] {stock_code} East Money 板块日K成功，{len(daily_df)} 条")
                        except Exception as e:
                            logging.warning(f"[STEP1] {stock_code} East Money 板块日K失败: {e}")
                    else:
                        # 个股：第一优先 pytdx（快速、稳定、凌晨可用）
                        try:
                            from App.codes.downloads.DlPytdx import download_daily_pytdx
                            daily_df, daily_end = download_daily_pytdx(stock_code, days=daily_days)
                            if not daily_df.empty:
                                logging.info(f"[STEP1] {stock_code} pytdx日K成功，{len(daily_df)} 条")
                        except ImportError:
                            logging.debug("[STEP1] pytdx未安装")
                        except Exception as e:
                            logging.warning(f"[STEP1] {stock_code} pytdx日K失败: {e}")

                    # 第二优先：AKShare（仅股票，数据更全）
                    if daily_df.empty and not is_board:
                        try:
                            from App.codes.downloads.DlAkshare import AkshareDownloader
                            ak_downloader = AkshareDownloader()
                            if ak_downloader.akshare_available:
                                daily_df, daily_end = ak_downloader.get_daily_data(stock_code, days=daily_days)
                                if not daily_df.empty:
                                    logging.info(f"[STEP1] {stock_code} AKShare日K成功，{len(daily_df)} 条")
                            else:
                                logging.warning(f"[STEP1] AKShare不可用")
                        except Exception as e:
                            logging.warning(f"[STEP1] {stock_code} AKShare日K失败: {e}")

                    # 保存日K数据
                    if not daily_df.empty:
                        save_daily_stock_data_to_sql(stock_code, daily_df)
                        daily_df['date'] = pd.to_datetime(daily_df['date'])
                        for d in daily_df['date'].dt.date.unique():
                            DailyTaskStatus.mark_task(stock_code, d, 'is_daily_processed')
                        logging.info(f"[STEP1] {stock_code} 日K数据保存成功，{len(daily_df)} 条")
                    else:
                        logging.warning(f"[STEP1] {stock_code} 日K数据为空（所有数据源均失败）")
                        _mark_recent_attempts_failed(stock_code, download_max_days,
                                                     '[STEP1] 日K 所有数据源均失败')

                except Exception as e:
                    logging.error(f"[STEP1] {stock_code} 日K数据下载失败: {e}")
                    _mark_recent_attempts_failed(stock_code, download_max_days,
                                                 f'[STEP1] 异常: {e}')

                # ===== STEP 2/3: 下载1分钟数据 =====
                with download_lock:
                    download_status = f"进行中 - {stock_code} Step2/3 1分钟"

                record_ending = first_record.end_date
                days = download_max_days

                logging.info(f"下载 {stock_code} 最近 {days} 天的数据...")

                first_record.update_download_status(status='processing')
                db.session.commit()

                # 重试机制：最多重试3次
                max_retries = 3
                retry_count = 0
                download_success = False

                while retry_count < max_retries and not download_success:
                    # 检查停止标志
                    with download_lock:
                        if stop_download:
                            return

                    try:
                        from App.codes.downloads.download_utils import UrlCode
                        from config import Config
                        lmt = min(days * 240, 2000)
                        if is_board:
                            url_template = 'board_1m_multiple_days'
                            # 模板顺序是 secid=90.{code} ... lmt={lmt}，别写反了
                            debug_url = Config.get_eastmoney_urls(url_template).format(stock_code, lmt)
                        else:
                            url_template = 'stock_1m_multiple_days'
                            debug_url = Config.get_eastmoney_urls(url_template).format(UrlCode(stock_code), lmt)
                    except Exception as url_error:
                        debug_url = f"构建URL失败: {url_error}"

                    try:
                        if retry_count > 0:
                            retry_delay = 3 * retry_count
                            logging.info(f"{code_type_name} {stock_code} 第 {retry_count + 1} 次重试下载，等待 {retry_delay} 秒...")
                            time.sleep(retry_delay)
                        else:
                            logging.info(f"开始下载{code_type_name} {stock_code} 的 {days} 天数据...")

                        logging.info(f"尝试访问URL: {debug_url}")

                        data, ending = download_1m_by_type(stock_code, days, stock_type)

                        if data.empty:
                            # 板块和个股一视同仁地重试：board_1m_multiple 内部已做
                            # East Money→AKShare 双兜底，单次返回空绝大多数是瞬时限流
                            # （rc=100 在限流时也会出现），而非真无数据。早先板块直接
                            # break、零重试，是它"经常失败"的根因。
                            retry_count += 1
                            logging.error(f"下载失败 - {code_type_name}: {stock_code}, URL: {debug_url}")

                            if retry_count < max_retries:
                                logging.warning(f'{code_type_name} {stock_code} 第 {retry_count} 次下载数据为空，准备重试...')
                                continue
                            else:
                                logging.error(f'{code_type_name} {stock_code} 下载失败，已达到最大重试次数 {max_retries}')
                                if is_board:
                                    err_msg = (f'板块数据不可用，已重试{max_retries}次'
                                               f'（限流/非交易时间/板块代码无效）\nURL: {debug_url}')
                                    fail_note = f'[STEP2] 板块下载空数据已重试 {max_retries} 次'
                                else:
                                    err_msg = (f'下载失败，已重试{max_retries}次'
                                               f'（网络连接问题或数据源限制）\nURL: {debug_url}')
                                    fail_note = f'[STEP2] 下载空数据已重试 {max_retries} 次'
                                first_record.update_download_status(
                                    status='failed',
                                    error_msg=err_msg
                                )
                                db.session.commit()
                                _mark_recent_attempts_failed(stock_code, days, fail_note)
                                break
                        else:
                            download_success = True
                            logging.info(f"{code_type_name} {stock_code} 下载成功，获得 {len(data)} 条记录")

                    except Exception as e:
                        retry_count += 1
                        logging.error(f"下载异常 - {code_type_name}: {stock_code}, URL: {debug_url}, 错误: {e}")

                        if retry_count < max_retries:
                            logging.warning(f'{code_type_name} {stock_code} 第 {retry_count} 次下载异常: {e}，准备重试...')
                            continue
                        else:
                            logging.error(f'{code_type_name} {stock_code} 下载异常，已达到最大重试次数 {max_retries}: {e}')
                            first_record.update_download_status(
                                status='failed',
                                error_msg=f'下载异常，已重试{max_retries}次: {str(e)}\nURL: {debug_url}'
                            )
                            db.session.commit()
                            _mark_recent_attempts_failed(stock_code, days,
                                                         f'[STEP2] 异常已重试 {max_retries} 次: {e}')
                            break

                # 如果下载失败，更新进度后返回
                if not download_success:
                    with download_lock:
                        pipeline_completed_count += 1
                        download_progress = round(pipeline_completed_count * 100 / total, 1)
                    return

                # 下载成功，继续处理数据
                try:
                    save_1m_to_csv(data, stock_code)
                    logging.info(f'成功保存 {stock_code} 数据到CSV文件，共 {len(data)} 条记录')
                except Exception as e:
                    logging.error(f'保存至CSV失败: {stock_code}, {e}')
                    first_record.update_download_status(
                        status='failed',
                        error_msg=f'保存至CSV失败: {str(e)}'
                    )
                    db.session.commit()
                    _mark_recent_attempts_failed(stock_code, days,
                                                 f'[STEP2] 保存 CSV 失败: {e}')
                    with download_lock:
                        pipeline_completed_count += 1
                        download_progress = round(pipeline_completed_count * 100 / total, 1)
                    return

                # 标记 DailyTaskStatus: 1分钟数据已下载
                try:
                    from App.models.data.DailyTaskStatus import DailyTaskStatus
                    data['date'] = pd.to_datetime(data['date'])
                    trade_dates = data['date'].dt.date.unique()
                    for d in trade_dates:
                        DailyTaskStatus.mark_task(stock_code, d, 'is_1m_downloaded')
                    logging.info(f"[STEP2] 已标记 {stock_code} 的 {len(trade_dates)} 个交易日 is_1m_downloaded")
                except Exception as e:
                    logging.warning(f"[STEP2] 标记1m任务状态失败: {stock_code}, {e}")

                # 更新数据库记录，标记下载成功
                first_record.update_download_status(
                    status='success',
                    progress=100.0
                )
                if ending > record_ending:
                    first_record.end_date = ending
                first_record.record_date = current
                first_record.last_download_time = datetime.now()
                first_record.downloaded_records = len(data)
                db.session.commit()
                logging.info(f'[STEP2] 成功下载 {stock_code} 的1分钟数据（{days}天），共 {len(data)} 条记录')

                # ===== STEP 3/3: 处理15分钟数据（加载完整历史1m数据） =====
                with download_lock:
                    download_status = f"进行中 - {stock_code} Step3/3 15分钟"

                try:
                    from App.codes.utils.Normal import ResampleData
                    from App.codes.Signals.StatisticsMacd import SignalMethod
                    from App.utils.file_utils import get_stock_data_path
                    from App.models.data.DailyTaskStatus import DailyTaskStatus
                    from App.codes.RnnDataFile.save_download import get_quarter_from_month

                    data['date'] = pd.to_datetime(data['date'])
                    last_date = data['date'].max()
                    cur_year = str(last_date.year)
                    cur_quarter = get_quarter_from_month(last_date.month)

                    q_num = int(cur_quarter[1])
                    if q_num == 1:
                        prev_year, prev_quarter = str(int(cur_year) - 1), 'Q4'
                    else:
                        prev_year, prev_quarter = cur_year, f'Q{q_num - 1}'

                    cur_1m_path = get_stock_data_path(stock_code, data_type='1m', year=cur_year, quarter=cur_quarter, create=False)
                    cur_1m_csv = cur_1m_path.replace('.parquet', '.csv')

                    df_1m_full = None
                    for p in [cur_1m_path, cur_1m_csv]:
                        if os.path.exists(p):
                            df_1m_full = pd.read_parquet(p) if p.endswith('.parquet') else pd.read_csv(p)
                            df_1m_full['date'] = pd.to_datetime(df_1m_full['date'])
                            break

                    if df_1m_full is None:
                        df_1m_full = data.copy()

                    prev_1m_path = get_stock_data_path(stock_code, data_type='1m', year=prev_year, quarter=prev_quarter, create=False)
                    prev_1m_csv = prev_1m_path.replace('.parquet', '.csv')
                    for p in [prev_1m_path, prev_1m_csv]:
                        if os.path.exists(p):
                            df_1m_prev = pd.read_parquet(p) if p.endswith('.parquet') else pd.read_csv(p)
                            df_1m_prev['date'] = pd.to_datetime(df_1m_prev['date'])
                            df_1m_full = pd.concat([df_1m_prev, df_1m_full]).sort_values('date').reset_index(drop=True)
                            df_1m_full = df_1m_full.drop_duplicates(subset=['date'], keep='last')
                            break

                    cur_quarter_start = data['date'].min()
                    data_15m_full = ResampleData.resample_1m_data(df_1m_full, '15m')

                    if not data_15m_full.empty:
                        macd_success = False
                        try:
                            data_15m_full = SignalMethod.signal_by_MACD_3ema(data_15m_full, df_1m_full)
                            macd_success = True
                        except Exception as macd_err:
                            logging.warning(f"[STEP3] {stock_code} MACD计算失败: {macd_err}")

                        data_15m = data_15m_full[data_15m_full['date'] >= cur_quarter_start].copy()

                        if 'SignalTimes' in data_15m.columns:
                            data_15m = data_15m.drop(columns=['SignalTimes'])

                        file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')

                        existing_15m_path = file_path_15m
                        if file_path_15m.endswith('.parquet') and not os.path.exists(file_path_15m):
                            csv_fallback = file_path_15m.replace('.parquet', '.csv')
                            if os.path.exists(csv_fallback):
                                existing_15m_path = csv_fallback

                        if os.path.exists(existing_15m_path):
                            try:
                                if existing_15m_path.endswith('.parquet'):
                                    try:
                                        existing_15m = pd.read_parquet(existing_15m_path)
                                    except ImportError:
                                        existing_15m = None
                                        logging.warning(f"[STEP3] {stock_code} pyarrow不可用，跳过读取旧parquet")
                                else:
                                    existing_15m = pd.read_csv(existing_15m_path, parse_dates=['date'])
                                if existing_15m is not None:
                                    existing_15m['date'] = pd.to_datetime(existing_15m['date'])
                                    if 'SignalTimes' in existing_15m.columns:
                                        existing_15m = existing_15m.drop(columns=['SignalTimes'])
                                    data_15m['date'] = pd.to_datetime(data_15m['date'])
                                    data_15m = pd.concat([existing_15m, data_15m], ignore_index=True)
                                    data_15m = data_15m.drop_duplicates(subset=['date'], keep='last')
                                    data_15m = data_15m.sort_values('date').reset_index(drop=True)
                            except Exception as merge_err:
                                logging.warning(f"[STEP3] {stock_code} 合并旧15m失败: {merge_err}")

                        save_ok = False
                        try:
                            if file_path_15m.endswith('.parquet'):
                                try:
                                    data_15m.to_parquet(file_path_15m, index=False, engine='pyarrow')
                                    save_ok = True
                                    logging.info(f"[STEP3] {stock_code} parquet保存成功: {len(data_15m)} 条")
                                except ImportError:
                                    csv_path = file_path_15m.replace('.parquet', '.csv')
                                    data_15m.to_csv(csv_path, index=False)
                                    save_ok = True
                                    logging.info(f"[STEP3] {stock_code} pyarrow不可用，回退CSV保存: {len(data_15m)} 条")
                            else:
                                data_15m.to_csv(file_path_15m, index=False)
                                save_ok = True
                                logging.info(f"[STEP3] {stock_code} CSV保存成功: {len(data_15m)} 条")
                        except Exception as save_err:
                            logging.error(f"[STEP3] {stock_code} 文件保存失败: {save_err}")

                        # 只有"保存成功"且"该交易日确实写进了 data_15m"才标记 is_15m_generated。
                        # 旧实现无条件按下载到的 1m 日期标记，导致保存失败/读到旧 1m 时
                        # DB 标志=已生成、parquet 却没有该日数据（曾使补漏 find_stocks_needing_backfill
                        # 看不到缺口、无法自愈）。现以"实际落库的日期"为准。
                        if save_ok:
                            try:
                                persisted = set(pd.to_datetime(data_15m['date']).dt.date.unique())
                                dl_dates = set(data['date'].dt.date.unique())
                                mark_dates = sorted(dl_dates & persisted)
                                for d in mark_dates:
                                    DailyTaskStatus.mark_task(stock_code, d, 'is_15m_generated')
                                    if macd_success:
                                        DailyTaskStatus.mark_task(stock_code, d, 'is_macd_calculated')
                                logging.info(f"[STEP3] {stock_code} DailyTaskStatus标记 {len(mark_dates)} 个交易日 (15m=True, macd={macd_success})")
                            except Exception as mark_err:
                                logging.error(f"[STEP3] {stock_code} DailyTaskStatus标记失败: {mark_err}")
                        else:
                            logging.warning(f"[STEP3] {stock_code} 15m 未成功保存，跳过 is_15m_generated 标记（留给补漏重试）")
                            _mark_recent_attempts_failed(stock_code, download_max_days, '[STEP3] 15m 保存失败')

                        logging.info(f"[STEP3] {stock_code} 15分钟处理完成，{len(data_15m)} 条")

                        # 缓存15m数据预览（最近30条）
                        try:
                            preview_cols = ['date', 'open', 'close', 'high', 'low', 'volume']
                            signal_cols = ['Signal', 'SignalChoice', 'MACD', 'Dif', 'Dea',
                                           'BollUp', 'BollMid', 'BollDn',
                                           'CycleAmplitudePerBar', 'CycleAmplitudeMax', 'CycleLengthPerBar']
                            use_cols = preview_cols + [c for c in signal_cols if c in data_15m.columns]
                            preview_df = data_15m[use_cols].tail(30).copy()
                            preview_df['date'] = preview_df['date'].dt.strftime('%m-%d %H:%M')
                            for c in preview_df.columns:
                                if c != 'date' and preview_df[c].dtype in ('float64', 'float32'):
                                    preview_df[c] = preview_df[c].round(3)
                            preview_records = preview_df.fillna('').to_dict('records')

                            stock_info = StockCodes.query.filter_by(code=stock_code).first()
                            s_name = stock_info.name if stock_info else stock_code

                            with download_lock:
                                last_completed_preview['stock_code'] = stock_code
                                last_completed_preview['stock_name'] = s_name
                                last_completed_preview['data_15m'] = preview_records
                                last_completed_preview['macd_success'] = macd_success
                                last_completed_preview['total_15m'] = len(data_15m)
                                last_completed_preview['updated_at'] = datetime.now().strftime('%H:%M:%S')
                            logging.info(f"[STEP3] {stock_code} 15m预览数据已缓存（{len(preview_records)}条）")
                        except Exception as preview_err:
                            logging.warning(f"[STEP3] {stock_code} 缓存预览数据失败: {preview_err}")

                    else:
                        logging.warning(f"[STEP3] {stock_code} 15分钟重采样结果为空")
                        _mark_recent_attempts_failed(stock_code, download_max_days,
                                                     '[STEP3] 15m 重采样结果为空')

                except Exception as e:
                    logging.error(f"[STEP3] {stock_code} 处理异常: {e}")
                    _mark_recent_attempts_failed(stock_code, download_max_days,
                                                 f'[STEP3] 异常: {e}')

                # ===== STEP 3.5: 板块日K的 15m 兜底补当天 =====
                # 结构性修复：STEP1 的日K在 STEP3 生成 15m 之前执行，所以当
                # push2his 被 RST、日K只能走 15m 兜底时，读到的是"还没更新的旧
                # 15m 文件"，永远落后一轮、补不上当天。这里在 15m 已落盘之后再用
                # 新 15m 聚合一次日K并 upsert，专门补上当天/近窗缺口（仅板块）。
                if is_board:
                    try:
                        from App.codes.downloads.eastmoney.board_downloader import BoardDownloader
                        from App.models.data.StockDaily import save_daily_stock_data_to_sql
                        from App.models.data.DailyTaskStatus import DailyTaskStatus

                        bd = BoardDownloader._board_daily_from_15m(stock_code)
                        if bd is not None and not bd.empty:
                            bd['date'] = pd.to_datetime(bd['date'])
                            save_daily_stock_data_to_sql(stock_code, bd)
                            for d in bd['date'].dt.date.unique():
                                DailyTaskStatus.mark_task(stock_code, d, 'is_daily_processed')
                            logging.info(f"[STEP3.5] {stock_code} 15m聚合补板块日K {len(bd)} 条"
                                         f"（最新 {bd['date'].max().date()}）")
                    except Exception as e:
                        logging.warning(f"[STEP3.5] {stock_code} 15m聚合补日K失败: {e}")

                # 更新完成计数和进度
                with download_lock:
                    pipeline_completed_count += 1
                    download_progress = round(pipeline_completed_count * 100 / total, 1)
                    pipeline_stock_index = pipeline_completed_count

                # 添加延迟，避免触发反爬虫机制
                import random
                delay = random.uniform(1, 2)  # 并发模式下缩短延迟
                time.sleep(delay)

        # === 使用线程池并发下载 ===
        logging.info(f"启动 {DOWNLOAD_WORKERS} 个并发线程下载 {total} 个股票...")
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
            futures = {
                executor.submit(_process_single_stock, record): record
                for record in records_to_download
            }
            for future in as_completed(futures):
                # 检查是否需要停止
                with download_lock:
                    if stop_download:
                        executor.shutdown(wait=False, cancel_futures=True)
                        download_status = "已停止"
                        download_progress = 0
                        pipeline_current_step = 0
                        pipeline_step_name = ""
                        logging.info("下载任务已停止")
                        return
                # 捕获子线程未处理的异常
                try:
                    future.result()
                except Exception as e:
                    record = futures[future]
                    logging.error(f"下载线程异常: {record.stock_code_id}, {e}")

        # 顺手刷一次全市场基本面（PE/PB/总市值/流通股本等），盘后跑一次即可
        with download_lock:
            download_status = "刷新基本面..."
        try:
            from App.services.stock_quote_service import refresh_basic_quotes_bulk
            br = refresh_basic_quotes_bulk()
            logging.info(f"[basics] 刷新完成：{br['pages']} 页 / {br['fetched']} 行 / "
                         f"upsert {br['upserted']} / errors {len(br['errors'])}")
        except Exception as e:
            logging.warning(f"[basics] 批量刷新基本面失败（不影响主下载）: {e}")

        # 下载完成 → 重算分布快照，让股票池/筛选页的 15m 趋势随收盘下载即时刷新
        # 板块(BKxxxx)已随主下载队列走完 日K+1m+15m，无需再单独补（见 download_ids）。
        _recompute_dist_snapshots_after_download()

        # 下载任务完成，更新下载状态和进度
        with download_lock:
            download_status = "已完成"
            download_progress = 100
            pipeline_current_step = 0
            pipeline_step_name = ""
            pipeline_current_stock = ""

        logging.info(f"下载任务完成（{DOWNLOAD_WORKERS}线程并发）")


@download_data_bp.route('/check_trading_day', methods=['GET'])
def check_trading_day():
    """检查今天是否是交易日，以及数据是否已是最新"""
    try:
        latest_trading_date, is_today_trading = get_latest_trading_date()
        now = datetime.now()
        from datetime import time as dt_time
        market_closed = now.time() >= dt_time(15, 0)

        # 查询最新的下载记录
        latest_record = dlr.query.filter(
            dlr.record_date != date(2050, 1, 1)
        ).order_by(dlr.end_date.desc()).first()

        data_up_to_date = False
        last_end_date = None
        if latest_record:
            last_end_date = latest_record.end_date.isoformat() if latest_record.end_date else None
            if latest_record.end_date and latest_record.end_date >= latest_trading_date:
                data_up_to_date = True

        return jsonify({
            'success': True,
            'is_today_trading': is_today_trading,
            'market_closed': market_closed if is_today_trading else None,
            'latest_trading_date': latest_trading_date.isoformat(),
            'last_end_date': last_end_date,
            'data_up_to_date': data_up_to_date,
            'message': _get_trading_day_message(is_today_trading, market_closed, data_up_to_date, latest_trading_date)
        })
    except Exception as e:
        logging.error(f"检查交易日失败: {e}")
        return jsonify({'success': False, 'message': f'检查失败: {str(e)}'})


def _get_trading_day_message(is_today_trading, market_closed, data_up_to_date, latest_trading_date):
    """生成交易日状态提示信息"""
    today = date.today()
    if data_up_to_date:
        return f"数据已是最新（截至交易日 {latest_trading_date}）"
    if not is_today_trading:
        weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        return f"今天（{weekday_names[today.weekday()]}）非交易日，最近交易日: {latest_trading_date}"
    if is_today_trading and not market_closed:
        return f"今天是交易日，盘中尚未收盘（15:00后可下载完整数据）"
    return f"今天是交易日，已收盘，可以下载最新数据"


def find_stocks_needing_backfill(look_back_days: int = 30, prefix: str = None) -> list:
    """扫 DailyTaskStatus，找出最近 look_back_days 个交易日内有缺口的 stock_code。

    缺口定义：is_1m_downloaded / is_15m_generated / is_daily_processed 任一为 False
    （MACD 因为依赖 15m，间接覆盖；不单独判定）。

    Args:
        look_back_days: 回看的交易日数，默认 30
        prefix: 可选，例如 'BK' 只查板块、'00' 只查深市

    Returns:
        list[str]: 需要补漏的 stock_code 列表（按代码字典序）
    """
    from App.models.data.DailyTaskStatus import DailyTaskStatus
    from App.exts import db
    from sqlalchemy import text

    all_dates = get_trading_dates() or []
    today = date.today()
    target_dates = sorted([d for d in all_dates if d <= today])[-look_back_days:]
    if not target_dates:
        return []

    eng = db.engines['quanttradingsystem']
    sql = '''
        SELECT DISTINCT stock_code
        FROM data_daily_task_status
        WHERE date >= :start_d AND date <= :end_d
          AND (is_1m_downloaded = 0
               OR is_15m_generated = 0
               OR is_daily_processed = 0)
    '''
    params = {'start_d': target_dates[0], 'end_d': target_dates[-1]}
    if prefix:
        sql += ' AND stock_code LIKE :p'
        params['p'] = f'{prefix}%'
    sql += ' ORDER BY stock_code'

    with eng.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [r[0] for r in rows]


@download_data_bp.route('/start_backfill_download', methods=['POST'])
def start_backfill_download():
    """启动"按缺口驱动"的补漏下载。

    与 /start_download 的区别：
      - /start_download：基于 data_download_records.download_status 全量驱动
      - /start_backfill_download：基于 DailyTaskStatus 找出哪些股票"最近 N 个交易日有缺口"，
        把这些股票的 data_download_records 重置为 pending，再触发常规下载流水线

    Body (JSON, 都可选):
        days: 下载窗口天数，默认 5（与 /start_download 一致）
        look_back_days: 缺口回溯窗口（交易日数），默认 30
        prefix: stock_code 前缀过滤，例如 'BK' 只补板块
    """
    global download_thread, download_status, download_progress, download_max_days

    try:
        if download_thread is not None and download_thread.is_alive():
            return jsonify({
                "success": False,
                "message": "下载正在进行中",
                "status": download_status,
                "progress": download_progress,
            }), 400

        body = request.get_json(silent=True) or {}
        days = max(1, min(5, int(body.get('days', 5))))
        look_back = max(5, min(120, int(body.get('look_back_days', 30))))
        prefix = body.get('prefix') or None

        with current_app.app_context():
            stocks = find_stocks_needing_backfill(look_back, prefix)
            if not stocks:
                return jsonify({
                    "success": True,
                    "message": f"近 {look_back} 个交易日内无缺口（prefix={prefix or '*'}）",
                    "stock_count": 0,
                })

            # 把这些股票的 download_records 重置为 pending
            from sqlalchemy import text
            eng = db.engines['quanttradingsystem']
            with eng.begin() as conn:
                result = conn.execute(text('''
                    UPDATE data_download_records r
                    JOIN data_stock_info i ON r.stock_code_id = i.id
                    SET r.download_status = 'pending',
                        r.download_progress = 0.0,
                        r.error_message = NULL,
                        r.updated_at = NOW()
                    WHERE i.code IN :codes
                      AND r.end_date != '2050-01-01'
                      AND r.record_date != '2050-01-01'
                '''), {'codes': tuple(stocks)})
                reset_n = result.rowcount

        download_max_days = days
        with download_lock:
            download_status = "初始化中（补漏模式）"
            download_progress = 0

        @copy_current_request_context
        def run_download():
            try:
                download_file()
            except Exception as e:
                logging.error(f"补漏下载执行失败: {e}", exc_info=True)
                with download_lock:
                    globals()['download_status'] = f"错误: {e}"

        download_thread = threading.Thread(target=run_download, daemon=True)
        download_thread.start()

        return jsonify({
            "success": True,
            "message": f"补漏下载已启动（{reset_n} 只股票/板块入队，回溯 {look_back} 个交易日）",
            "stock_count": len(stocks),
            "stocks_preview": stocks[:20],
        })
    except Exception as e:
        logging.exception("start_backfill_download 失败")
        return jsonify({"success": False, "message": str(e)}), 500


@download_data_bp.route('/get_backfill_overview', methods=['GET'])
def get_backfill_overview():
    """查看"按缺口驱动"的当前需补漏列表（不真启动下载，只看）。

    Query:
        look_back_days: int, default 30
        prefix: optional, e.g. 'BK'
    """
    try:
        look_back = max(5, min(120, int(request.args.get('look_back_days', 30))))
        prefix = (request.args.get('prefix') or '').strip() or None

        with current_app.app_context():
            stocks = find_stocks_needing_backfill(look_back, prefix)

        return jsonify({
            "success": True,
            "look_back_days": look_back,
            "prefix": prefix,
            "stock_count": len(stocks),
            "stocks": stocks,
        })
    except Exception as e:
        logging.exception("get_backfill_overview 失败")
        return jsonify({"success": False, "message": str(e)}), 500


@download_data_bp.route('/start_download', methods=['GET', 'POST'])
def start_download():
    """启动下载任务"""
    global download_thread, download_status, download_progress, download_max_days, download_force
    global pipeline_current_stock, pipeline_current_step, pipeline_step_name
    global pipeline_stock_index, pipeline_total_stocks

    try:
        # 检查是否有正在运行的下载任务
        if download_thread is not None and download_thread.is_alive():
            logging.warning("下载任务已在运行中")
            return jsonify({
                "success": False,
                "message": "下载正在进行中",
                "status": download_status,
                "progress": download_progress
            }), 400

        # 获取用户设置的下载天数和是否强制重下
        if request.is_json:
            data = request.get_json()
            days = data.get('days', 5)
            force = data.get('force', False)
        else:
            days = request.form.get('days', 5, type=int)
            force = request.form.get('force', False, type=bool)

        # 限制天数范围为1-5天
        download_max_days = max(1, min(5, int(days)))
        download_force = force
        logging.info(f"用户设置下载天数为: {download_max_days}, 强制重下: {download_force}")

        # 重置状态
        with download_lock:
            download_status = "初始化中"
            download_progress = 0
            pipeline_current_stock = ""
            pipeline_current_step = 0
            pipeline_step_name = ""
            pipeline_stock_index = 0
            pipeline_total_stocks = 0
        
        @copy_current_request_context
        def run_download():
            """在后台线程中运行下载任务"""
            try:
                download_file()
            except Exception as e:
                logging.error(f"下载任务执行失败: {e}", exc_info=True)
                with download_lock:
                    download_status = f"下载失败: {str(e)}"
                    download_progress = 0

        # 启动下载线程
        download_thread = threading.Thread(target=run_download, daemon=True)
        download_thread.start()
        
        logging.info("下载任务已启动")
        return jsonify({
            "success": True,
            "message": "下载已开始",
            "status": download_status,
            "progress": download_progress
        }), 200
        
    except Exception as e:
        logging.error(f"启动下载任务失败: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"启动下载失败: {str(e)}"
        }), 500


@download_data_bp.route('/get_download_status', methods=['GET'])
def get_download_status():
    return jsonify({"status": download_status, "progress": download_progress}), 200


@download_data_bp.route('/get_today_task_status', methods=['GET'])
def get_today_task_status():
    """获取今日 DailyTaskStatus 各步骤完成统计"""
    try:
        from App.models.data.DailyTaskStatus import DailyTaskStatus
        today = date.today()

        T = DailyTaskStatus
        total = T.query.filter(T.date == today).count()

        if total == 0:
            return jsonify({
                'date': today.strftime('%Y-%m-%d'),
                'total': 0,
                'daily': 0, 'dl_1m': 0, 'gen_15m': 0, 'macd': 0,
                'cleaned': 0, 'volume': 0, 'feature': 0, 'rnn': 0,
            }), 200

        daily = T.query.filter(T.date == today, T.is_daily_processed == True).count()
        dl_1m = T.query.filter(T.date == today, T.is_1m_downloaded == True).count()
        gen_15m = T.query.filter(T.date == today, T.is_15m_generated == True).count()
        macd = T.query.filter(T.date == today, T.is_macd_calculated == True).count()
        cleaned = T.query.filter(T.date == today, T.is_1m_cleaned == True).count()
        volume = T.query.filter(T.date == today, T.is_volume_processed == True).count()
        feature = T.query.filter(T.date == today, T.is_feature_generated == True).count()
        rnn = T.query.filter(T.date == today, T.is_rnn_predicted == True).count()

        return jsonify({
            'date': today.strftime('%Y-%m-%d'),
            'total': total,
            'daily': daily,
            'dl_1m': dl_1m,
            'gen_15m': gen_15m,
            'macd': macd,
            'cleaned': cleaned,
            'volume': volume,
            'feature': feature,
            'rnn': rnn,
        }), 200

    except Exception as e:
        logging.error(f"获取今日任务状态失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/get_last_15m_preview', methods=['GET'])
def get_last_15m_preview():
    """获取最近完成的股票15m数据预览"""
    with download_lock:
        return jsonify(last_completed_preview), 200


@download_data_bp.route('/get_pipeline_status', methods=['GET'])
def get_pipeline_status():
    """获取流水线详细状态（含当前步骤信息）"""
    with download_lock:
        return jsonify({
            "status": download_status,
            "progress": download_progress,
            "current_stock": pipeline_current_stock,
            "current_step": pipeline_current_step,
            "step_name": pipeline_step_name,
            "stock_index": pipeline_stock_index,
            "total_stocks": pipeline_total_stocks,
            "workers": DOWNLOAD_WORKERS,
            "completed": pipeline_completed_count,
        }), 200


@download_data_bp.route('/get_download_details', methods=['GET'])
def get_download_details():
    """获取详细的下载状态信息（最近下载的记录）"""
    try:
        # 结束当前事务并清除缓存，确保能读到并发线程提交的最新数据
        db.session.rollback()
        db.session.expire_all()

        # 注意：数据库中的时间戳使用UTC时间，所以这里也使用UTC时间进行比较
        utc_now = datetime.utcnow()

        # 获取最近的成功记录（最近1小时，用于实时显示）
        recent_success = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            dlr.last_download_time >= utc_now - timedelta(hours=1)
        ).order_by(dlr.last_download_time.desc()).limit(20).all()

        # 获取最近的失败记录
        recent_failed = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            dlr.updated_at >= utc_now - timedelta(hours=1)
        ).order_by(dlr.updated_at.desc()).limit(20).all()

        # 格式化成功记录
        success_list = []
        for record in recent_success:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                success_list.append({
                    'code': stock_code,
                    'records': record.downloaded_records or 0,
                    'time': record.last_download_time.strftime('%H:%M:%S') if record.last_download_time else '-'
                })

        # 格式化失败记录
        failed_list = []
        for record in recent_failed:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                failed_list.append({
                    'code': stock_code,
                    'error': record.error_message or '未知错误',
                    'time': record.updated_at.strftime('%H:%M:%S') if record.updated_at else '-'
                })

        return jsonify({
            "success": success_list,
            "failed": failed_list
        }), 200

    except Exception as e:
        logging.error(f"获取下载详情时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_success_stocks', methods=['GET'])
def get_success_stocks():
    """获取下载成功的股票列表（带分页）"""
    try:
        db.session.rollback()
        db.session.expire_all()
        today = date.today()

        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = min(per_page, 100)

        # 查询所有下载成功的股票
        success_query = dlr.query.filter(
            dlr.download_status == 'success',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).order_by(dlr.end_date.desc(), dlr.last_download_time.desc())

        total_count = success_query.count()
        success_records = success_query.offset((page - 1) * per_page).limit(per_page).all()

        success_list = []
        for record in success_records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                success_list.append({
                    'code': stock_code,
                    'records': record.downloaded_records or 0,
                    'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else '-',
                    'time': record.last_download_time.strftime('%Y-%m-%d %H:%M') if record.last_download_time else '-'
                })

        return jsonify({
            "success": success_list,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page
        }), 200

    except Exception as e:
        logging.error(f"获取成功下载股票列表时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_failed_stocks', methods=['GET'])
def get_failed_stocks():
    """获取下载失败的股票列表（带分页）"""
    try:
        db.session.rollback()
        db.session.expire_all()
        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = min(per_page, 100)

        # 查询下载失败的股票
        failed_query = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        ).order_by(dlr.updated_at.desc())

        total_count = failed_query.count()
        failed_records = failed_query.offset((page - 1) * per_page).limit(per_page).all()

        failed_list = []
        for record in failed_records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                failed_list.append({
                    'code': stock_code,
                    'error': record.error_message or '未知错误',
                    'time': record.updated_at.strftime('%Y-%m-%d %H:%M') if record.updated_at else '-'
                })

        return jsonify({
            "failed": failed_list,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page
        }), 200

    except Exception as e:
        logging.error(f"获取失败下载股票列表时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_download_statistics', methods=['GET'])
def get_download_statistics():
    """获取下载统计数据"""
    try:
        # 结束当前事务并清除缓存，确保能读到并发线程提交的最新数据
        db.session.rollback()
        db.session.expire_all()

        # scope: 'pool'=只统计股票池(盘后下载页) / 其余=全市场(数据修复页)
        scope = request.args.get('scope', '', type=str)
        # 统计跟随页面筛选（数据修复页专用），口径与 api_get_download_records 列表一致，
        # 但不含 status —— 面板本身按状态拆分，若再按 status 收窄会自相矛盾。
        stale_only = request.args.get('stale_only', '', type=str).lower() in ('1', 'true', 'yes')
        code_type = request.args.get('code_type', '', type=str)
        search = request.args.get('search', '', type=str)
        end_before = request.args.get('end_before', '', type=str)
        end_after = request.args.get('end_after', '', type=str)

        def _base_query():
            """应用与列表相同的非状态筛选：scope + 仅落后 + 代码类型/搜索 + 结束日期区间 + 排除忽略/退市。"""
            q = dlr.query.filter(
                dlr.end_date != date(2050, 1, 1),
                dlr.record_date != date(2050, 1, 1)
            )
            q = _apply_scope(q, scope)

            # 仅落后：end_date < 最新交易日
            if stale_only:
                latest_td, _is_td = get_latest_trading_date()
                q = q.filter(dlr.end_date.isnot(None), dlr.end_date < latest_td)

            # 代码搜索 + 个股/板块类型
            if search or code_type in ('stock', 'board'):
                id_query = db.session.query(StockCodes.id)
                if search:
                    id_query = id_query.filter(StockCodes.code.like(f'%{search}%'))
                if code_type == 'board':
                    id_query = id_query.filter(StockCodes.code.like('BK%'))
                elif code_type == 'stock':
                    id_query = id_query.filter(~StockCodes.code.like('BK%'))
                matching_ids = [s[0] for s in id_query.all()]
                # 无匹配时用空 in_()（SQLAlchemy 渲染为恒假），使统计恒为 0（与列表返回空一致）
                q = q.filter(dlr.stock_code_id.in_(matching_ids if matching_ids else []))

            # 排除退市（名称含"退"）
            delisted_ids = [s[0] for s in db.session.query(StockCodes.id)
                            .filter(StockCodes.name.like('%退%')).all()]
            if delisted_ids:
                q = q.filter(~dlr.stock_code_id.in_(delisted_ids))

            # 结束日期区间
            if end_before:
                try:
                    q = q.filter(dlr.end_date <= datetime.strptime(end_before, '%Y-%m-%d').date())
                except ValueError:
                    pass
            if end_after:
                try:
                    q = q.filter(dlr.end_date >= datetime.strptime(end_after, '%Y-%m-%d').date())
                except ValueError:
                    pass
            return q

        def _count(status=None):
            q = _base_query()
            if status:
                q = q.filter(dlr.download_status == status)
            return q.count()

        pending_count = _count('pending')
        success_count = _count('success')
        failed_count = _count('failed')
        processing_count = _count('processing')
        total_count = _count()

        # 待下载拆分：日常"开始下载"只处理股票池；非池板块成分股靠周/月「一键修复落后」补。
        # scope=pool 时统计本来就只有池内，池外恒为 0。
        pool_ids = _active_pool_stock_ids()
        pending_pool = _base_query().filter(
            dlr.download_status == 'pending',
            dlr.stock_code_id.in_(pool_ids)
        ).count() if pool_ids else 0
        pending_nonpool = pending_count - pending_pool if scope != 'pool' else 0

        # 落后分档（数据修复页专用）：按 end_date 距最新交易日的天数分轻/中/重，
        # 并给出最落后日期。仅 stale_only 时计算，避免下载页多跑几条查询。
        lag = None
        if stale_only:
            latest_td, _is_td = get_latest_trading_date()
            d7 = latest_td - timedelta(days=7)
            d30 = latest_td - timedelta(days=30)
            lag = {
                "le7": _base_query().filter(dlr.end_date >= d7).count(),
                "d8_30": _base_query().filter(dlr.end_date < d7, dlr.end_date >= d30).count(),
                "gt30": _base_query().filter(dlr.end_date < d30).count(),
                "oldest": None,
            }
            oldest = _base_query().with_entities(db.func.min(dlr.end_date)).scalar()
            lag["oldest"] = oldest.strftime('%Y-%m-%d') if oldest else None

        return jsonify({
            "pending": pending_count,
            "pending_pool": pending_pool,
            "pending_nonpool": pending_nonpool,
            "success": success_count,
            "failed": failed_count,
            "processing": processing_count,
            "total": total_count,
            "lag": lag
        }), 200

    except Exception as e:
        logging.error(f"获取下载统计数据时发生错误: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/get_pending_stocks', methods=['GET'])
def get_pending_stocks():
    """获取等待下载的股票列表（包括数据过期需要更新的）"""
    try:
        db.session.rollback()
        db.session.expire_all()
        today = date.today()

        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        # 限制每页最大数量
        per_page = min(per_page, 100)

        # 查询需要下载的股票：
        # 1. 状态为 pending 的
        # 2. 或者 end_date 不是今天（数据已过期需要更新）
        from sqlalchemy import or_
        pending_query = dlr.query.filter(
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            or_(
                dlr.download_status == 'pending',
                dlr.download_status == 'failed',
                dlr.end_date < today  # 数据过期需要更新
            )
        ).order_by(dlr.end_date.asc())  # 按过期时间排序，最旧的排前面

        # 获取总数
        total_count = pending_query.count()

        # 分页获取数据
        pending_records = pending_query.offset((page - 1) * per_page).limit(per_page).all()

        # 格式化数据
        pending_list = []
        for record in pending_records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            if stock_code:
                days_behind = (today - record.end_date).days if record.end_date else 0
                # 确定状态描述
                if record.download_status == 'pending':
                    status_text = '等待下载'
                elif record.download_status == 'failed':
                    status_text = '下载失败'
                elif days_behind > 0:
                    status_text = '数据过期'
                else:
                    status_text = '待更新'

                pending_list.append({
                    'code': stock_code,
                    'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else '-',
                    'record_date': record.record_date.strftime('%Y-%m-%d') if record.record_date else '-',
                    'days_behind': days_behind,
                    'status': record.download_status,
                    'status_text': status_text
                })

        return jsonify({
            "pending": pending_list,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page
        }), 200

    except Exception as e:
        logging.error(f"获取等待下载股票列表时发生错误: {e}")
        return jsonify({"error": str(e)}), 500


@download_data_bp.route('/reset_failed_stocks', methods=['POST'])
def reset_failed_stocks():
    """重置失败的股票为待下载状态"""
    try:
        # 将所有失败的股票重置为pending状态
        # 注意：使用UTC时间以保持与模型定义一致
        reset_count = dlr.query.filter(
            dlr.download_status == 'failed',
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).update({
            'download_status': 'pending',
            'download_progress': 0.0,
            'error_message': None,  # 清除错误信息
            'updated_at': datetime.utcnow()
        })
        
        db.session.commit()
        
        logging.info(f"重置了 {reset_count} 条失败记录为pending状态")
        
        return jsonify({
            'message': f'成功重置 {reset_count} 条失败记录为待下载状态',
            'reset_count': reset_count
        })
        
    except Exception as e:
        logging.error(f"重置失败股票失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/stop_download_request', methods=['GET', 'POST'])
def stop_download_request():
    global stop_download, download_status, download_progress

    with download_lock:
        stop_download = True  # 设置标志为 True，要求下载停止
        download_status = "请求停止中"  # 更新状态
        download_progress = 0  # 重置进度
    
    logging.info("用户请求停止下载")
    return jsonify({"message": "下载已请求停止"}), 200


# def load_progress():
@download_data_bp.route('/load_progress', methods=['GET'])
def load_progress():
    return render_template('data/progress.html')


@download_data_bp.route('/download_index_page')
def download_index_page():
    return render_template('data/股票下载.html')


@download_data_bp.route('/daily_renew_data', methods=['GET', 'POST'])
def daily_renew_data():
    # 下载股票每天的1M 数据
    rdd = RMDownloadData()
    rdd.daily_renew_data()
    return render_template('index.html')


@download_data_bp.route('/resample_to_daily_data', methods=['GET', 'POST'])
def resample_to_daily_data():
    month = None
    stock_code = None
    data_daily = None  # 默认情况下数据为空

    if request.method == 'POST':
        # 从表单获取参数
        stock_code = request.form.get('stock_code')
        month = request.form.get('month')

        if month and stock_code:
            file_name = f'{stock_code}.csv'

            data_path = StockDataPath.month_1m_data_path(month)

            data_1m = pd.read_csv(data_path)
            data_daily = ResampleData.resample_1m_data(data_1m, 'd')

            try:
                # 假设 ResampleData 和 pd.read_csv 是正确配置的

                flash("文件转换成功！", "success")
            except Exception as e:
                flash(f"文件转换失败: {e}", "danger")

    return render_template('data/resample_to_daily_data.html', stock_code=stock_code, month=month, data_daily=data_daily)


@download_data_bp.route('/download_stock_1m_close_data_today', methods=['GET', 'POST'])
def download_stock_1m_close_data_today():
    if request.method == 'POST':
        stock_code = request.form.get('stock_code')
        if stock_code:
            try:
                # 使用完整的下载流程
                result = complete_download_process(stock_code, days=1)
                
                if result['success']:
                    flash(f"成功完成 {stock_code} 的完整下载流程: {result['message']}", "success")
                    
                    # 获取各种数据类型的文件路径
                    from App.utils.file_utils import get_stock_data_path
                    file_path_1m = get_stock_data_path(stock_code, data_type='1m')
                    file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')
                    file_path_daily = get_stock_data_path(stock_code, data_type='daily')
                    
                    # 从数据库查询刚刚保存的日线数据
                    daily_data_from_db = None
                    try:
                        from App.models.data.StockDaily import StockDaily
                        from datetime import datetime, timedelta
                        import pandas as pd
                        
                        # 查询最近7天的日线数据
                        end_date = datetime.now().date()
                        start_date = end_date - timedelta(days=7)
                        
                        daily_records = StockDaily.query.filter(
                            StockDaily.stock_code == stock_code,
                            StockDaily.date >= start_date,
                            StockDaily.date <= end_date
                        ).order_by(StockDaily.date.desc()).all()
                        
                        if daily_records:
                            # 转换为DataFrame格式
                            data_list = [{
                                'date': record.date.strftime('%Y-%m-%d'),
                                'open': record.open,
                                'high': record.high,
                                'low': record.low,
                                'close': record.close,
                                'volume': record.volume,
                                'money': record.money
                            } for record in daily_records]
                            
                            daily_data_from_db = pd.DataFrame(data_list)
                            
                    except Exception as e:
                        logging.warning(f"查询日线数据失败: {str(e)}")
                        daily_data_from_db = None
                    
                    return render_template('data/success.html',
                                         file_path_1m=file_path_1m,
                                         file_path_15m=file_path_15m,
                                         file_path_daily=file_path_daily,
                                         daily_data_from_db=daily_data_from_db,
                                         stock_code=stock_code)
                else:
                    flash(f"下载流程部分失败: {result['message']}", "warning")
                    
            except Exception as e:
                flash(f"下载失败: {str(e)}", "danger")
    
    return render_template('data/success.html',
                         file_path_1m=None,
                         file_path_15m=None,
                         file_path_daily=None,
                         daily_data_from_db=None,
                         stock_code=None)


@download_data_bp.route('/download_stock_1m_close_data', methods=['GET', 'POST'])
def download_stock_1m_close_data():
    if request.method == 'POST':
        stock_code = request.form.get('stock_code')
        days = int(request.form.get('days', 5))
        if stock_code:
            try:
                # 使用完整的下载流程
                result = complete_download_process(stock_code, days=days)

                if result['success']:
                    flash(f"成功完成 {stock_code} 的完整下载流程（{days}天数据）: {result['message']}", "success")

                    # 获取各种数据类型的文件路径
                    from App.utils.file_utils import get_stock_data_path
                    from App.utils.path_manager import get_path_manager
                    import os

                    pm = get_path_manager()

                    # 智能查找1分钟数据文件路径
                    file_path_1m = None
                    current_year = int(pm.get_current_year())
                    quarters_to_check = []
                    for year in [current_year, current_year - 1]:
                        for q in ['Q4', 'Q3', 'Q2', 'Q1']:
                            quarters_to_check.append((str(year), q))

                    for year, quarter in quarters_to_check:
                        path = get_stock_data_path(stock_code, data_type='1m', year=year, quarter=quarter, create=False)
                        if os.path.exists(path):
                            file_path_1m = path
                            break

                    if not file_path_1m:
                        file_path_1m = get_stock_data_path(stock_code, data_type='1m')

                    file_path_15m = get_stock_data_path(stock_code, data_type='15m_normal')

                    # 智能查找日线数据
                    file_path_daily = None
                    for year, quarter in quarters_to_check:
                        path = get_stock_data_path(stock_code, data_type='daily', year=year, quarter=quarter, create=False)
                        if os.path.exists(path):
                            file_path_daily = path
                            break
                    if not file_path_daily:
                        file_path_daily = get_stock_data_path(stock_code, data_type='daily')

                    # ========== 数据验证部分 ==========
                    validation_result = {
                        'status': 'success',
                        'issues': [],
                        'stats': {}
                    }

                    data_1m_preview = None
                    if os.path.exists(file_path_1m):
                        try:
                            if file_path_1m.endswith('.parquet'):
                                df_1m = pd.read_parquet(file_path_1m)
                            else:
                                df_1m = pd.read_csv(file_path_1m, encoding='utf-8-sig')
                            df_1m['date'] = pd.to_datetime(df_1m['date'])

                            # 统计信息
                            validation_result['stats']['total_records'] = len(df_1m)
                            validation_result['stats']['first_time'] = df_1m['date'].min().strftime('%Y-%m-%d %H:%M')
                            validation_result['stats']['last_time'] = df_1m['date'].max().strftime('%Y-%m-%d %H:%M')

                            # 按天统计记录数
                            df_1m['trade_date'] = df_1m['date'].dt.date
                            daily_counts = df_1m.groupby('trade_date').size().to_dict()
                            validation_result['stats']['daily_counts'] = {str(k): v for k, v in daily_counts.items()}

                            # 检查数据质量
                            zero_open_count = (df_1m['open'] == 0).sum()
                            if zero_open_count > 0:
                                validation_result['issues'].append(f"⚠️ 发现 {zero_open_count} 条 open=0 的数据")
                                validation_result['status'] = 'warning'

                            from datetime import time as dt_time
                            first_time = df_1m['date'].min().time()
                            if first_time < dt_time(9, 30):
                                validation_result['issues'].append(f"⚠️ 数据包含 9:30 之前的记录（首条时间: {first_time}）")
                                validation_result['status'] = 'warning'

                            for date_str, count in daily_counts.items():
                                if count < 200:
                                    validation_result['issues'].append(f"⚠️ {date_str} 只有 {count} 条记录（正常约240条）")
                                    validation_result['status'] = 'warning'

                            if (df_1m['close'] <= 0).any():
                                validation_result['issues'].append("❌ 存在 close<=0 的异常数据")
                                validation_result['status'] = 'error'

                            # 准备全部数据
                            def convert_row(row):
                                return {
                                    'date': row['date'].strftime('%Y-%m-%d %H:%M') if hasattr(row['date'], 'strftime') else str(row['date']),
                                    'open': float(row['open']),
                                    'close': float(row['close']),
                                    'high': float(row['high']),
                                    'low': float(row['low']),
                                    'volume': int(row['volume']),
                                    'money': int(row['money'])
                                }

                            data_1m_preview = {
                                'all': [convert_row(row) for _, row in df_1m.iterrows()],
                                'total_count': len(df_1m)
                            }

                            if not validation_result['issues']:
                                validation_result['issues'].append("✅ 数据质量检查通过")

                        except Exception as e:
                            logging.error(f"读取1分钟数据失败: {str(e)}")
                            validation_result['issues'].append(f"❌ 读取CSV失败: {str(e)}")
                            validation_result['status'] = 'error'
                    else:
                        validation_result['issues'].append(f"❌ 1分钟数据文件不存在: {file_path_1m}")
                        validation_result['status'] = 'error'

                    # 从数据库查询日线数据
                    daily_data_from_db = None
                    try:
                        from App.models.data.StockDaily import StockDaily

                        end_date = datetime.now().date()
                        start_date = end_date - timedelta(days=7)

                        daily_records = StockDaily.query.filter(
                            StockDaily.stock_code == stock_code,
                            StockDaily.date >= start_date,
                            StockDaily.date <= end_date
                        ).order_by(StockDaily.date.desc()).all()

                        if daily_records:
                            data_list = [{
                                'date': record.date.strftime('%Y-%m-%d'),
                                'open': f"{record.open:.2f}",
                                'high': f"{record.high:.2f}",
                                'low': f"{record.low:.2f}",
                                'close': f"{record.close:.2f}",
                                'volume': f"{record.volume:,}",
                                'money': f"{record.money:,}"
                            } for record in daily_records]

                            daily_data_from_db = pd.DataFrame(data_list)

                    except Exception as e:
                        logging.warning(f"查询日线数据失败: {str(e)}")
                        daily_data_from_db = None

                    return render_template('data/success.html',
                                         file_path=file_path_1m,
                                         file_path_15m=file_path_15m,
                                         file_path_daily=file_path_daily,
                                         daily_data_from_db=daily_data_from_db,
                                         stock_code=stock_code,
                                         validation_result=validation_result,
                                         data_1m_preview=data_1m_preview)
                else:
                    flash(f"下载流程部分失败: {result['message']}", "warning")

            except Exception as e:
                flash(f"下载失败: {str(e)}", "danger")

    return render_template('data/success.html',
                         file_path=None,
                         file_path_15m=None,
                         file_path_daily=None,
                         daily_data_from_db=None,
                         stock_code=None,
                         validation_result=None,
                         data_1m_preview=None)


# @download_data_bp.route('/download_stock_daily_data', methods=['GET', 'POST'])
# def download_stock_daily_data():
#     if request.method == 'POST':
#         stock_code = request.form.get('stock_code')
#         if stock_code:
#             try:
#                 # 下载日线数据
#                 data, _ = download_1m_by_type(stock_code, 1, StockType.STOCK_1M)
#                 if not data.empty:
#                     # 转换为日线数据
#                     daily_data = ResampleData.resample_1m_data(data, 'd')
#                     daily_data = daily_data.fillna({'open': 0.0, 'close': 0.0,
#                                                   'high': 0.0, 'low': 0.0,
#                                                   'volume': 0, 'money': 0})
#                     # 保存数据
#                     save_daily_stock_data_to_sql(stock_code, daily_data)
#                     flash(f"成功下载 {stock_code} 的日线数据", "success")
#                 else:
#                     flash(f"未找到 {stock_code} 的数据", "warning")
#             except Exception as e:
#                 flash(f"下载失败: {str(e)}", "danger")
#     return render_template('download/success.html')


@download_data_bp.route('/download_fund_holdings', methods=['GET', 'POST'])
def download_fund_holdings():
    if request.method == 'POST':
        try:
            # 下载基金持仓数据
            rdd = RMDownloadData()
            rdd.download_fund_holdings()
            flash("成功下载基金持仓数据", "success")
        except Exception as e:
            flash(f"下载失败: {str(e)}", "danger")
    return render_template('data/success.html',
                         file_path_1m=None,
                         file_path_15m=None,
                         file_path_daily=None,
                         daily_data_from_db=None,
                         stock_code=None)


@download_data_bp.route('/download_minute_data_page')
def download_minute_data_page():
    # mode=download 盘后数据下载(默认) / mode=repair 数据修复，共用本页，路由参数区分
    mode = request.args.get('mode', 'download')
    if mode not in ('download', 'repair'):
        mode = 'download'
    return render_template('data/download_minute_data.html', mode=mode)

@download_data_bp.route('/open_data_folder', methods=['POST'])
def open_data_folder():
    """打开1分钟数据文件夹"""
    try:
        import subprocess
        import platform
        
        # 获取1分钟数据文件夹路径（1m 按季度存于 <项目根>/data/quarters）
        from config import Config
        data_folder = os.path.join(Config.get_project_root(), 'data', 'quarters')

        # 仅当真实数据目录不存在时才创建，避免拼错路径生成空目录
        if not os.path.isdir(data_folder):
            os.makedirs(data_folder, exist_ok=True)
        
        # 根据操作系统打开文件夹
        system = platform.system()
        
        if system == "Windows":
            # Windows explorer命令即使成功也可能返回非零状态，所以不使用check=True
            result = subprocess.run(['explorer', data_folder], capture_output=True, text=True)
            if result.returncode != 0 and result.stderr:
                # 只有在有错误输出时才认为是真正的错误
                raise Exception(f"打开文件夹失败: {result.stderr}")
        elif system == "Darwin":  # macOS
            subprocess.run(['open', data_folder], check=True)
        elif system == "Linux":
            subprocess.run(['xdg-open', data_folder], check=True)
        else:
            return jsonify({"success": False, "message": f"不支持的操作系统: {system}"}), 400
        
        logging.info(f"成功打开1分钟数据文件夹: {data_folder}")
        return jsonify({"success": True, "message": "数据文件夹已打开"}), 200
        
    except Exception as e:
        logging.error(f"打开1分钟数据文件夹时发生错误: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@download_data_bp.route('/resample_to_daily', methods=['POST'])
def resample_to_daily():
    """将已下载的1分钟数据重新采样为日线数据并保存到daily_stock_data表"""
    try:
        from App.codes.RnnDataFile.save_download import save_1m_to_daily
        from App.codes.RnnDataFile.stock_path import get_stock_data_path
        import glob
        
        logging.info("开始重新采样1分钟数据为日线数据...")
        
        # 获取所有已下载的1分钟数据文件
        data_folder = StockDataPath.get_stock_data_directory()
        csv_files = glob.glob(os.path.join(data_folder, "1m", "*.csv"))
        
        if not csv_files:
            return jsonify({
                "success": False, 
                "message": "没有找到1分钟数据文件"
            }), 400
        
        processed_count = 0
        success_count = 0
        error_count = 0
        error_details = []
        
        for csv_file in csv_files:
            try:
                # 从文件名提取股票代码
                filename = os.path.basename(csv_file)
                stock_code = filename.replace('.csv', '')
                
                # 读取1分钟数据
                df_1m = pd.read_csv(csv_file)
                
                if df_1m.empty:
                    logging.warning(f"股票 {stock_code} 的1分钟数据为空，跳过")
                    continue
                
                # 确保列名正确
                if 'date' not in df_1m.columns:
                    logging.warning(f"股票 {stock_code} 的1分钟数据缺少date列，跳过")
                    continue
                
                # 重新采样为日线数据并保存
                save_1m_to_daily(df_1m, stock_code)
                success_count += 1
                logging.info(f"成功处理股票 {stock_code} 的日线数据")
                
            except Exception as e:
                error_count += 1
                error_msg = f"处理股票 {stock_code} 时出错: {str(e)}"
                error_details.append(error_msg)
                logging.error(error_msg)
            
            processed_count += 1
        
        result_message = f"重新采样完成！处理了 {processed_count} 个文件，成功 {success_count} 个，失败 {error_count} 个"
        
        if error_details:
            result_message += f"\n错误详情: {'; '.join(error_details[:5])}"  # 只显示前5个错误
        
        logging.info(result_message)
        
        return jsonify({
            "success": True,
            "message": result_message,
            "processed_count": processed_count,
            "success_count": success_count,
            "error_count": error_count
        }), 200
        
    except Exception as e:
        logging.error(f"重新采样1分钟数据为日线数据时发生错误: {e}")
        return jsonify({
            "success": False, 
            "message": f"重新采样失败: {str(e)}"
        }), 500


@download_data_bp.route('/complete_download_single', methods=['POST'])
def complete_download_single():
    """
    单个股票完整下载流程API
    """
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')
        days = data.get('days', 1)
        
        if not stock_code:
            return jsonify({
                "success": False,
                "message": "股票代码不能为空"
            }), 400
        
        # 执行完整下载流程
        result = complete_download_process(stock_code, days)
        
        return jsonify({
            "success": result['success'],
            "message": result['message'],
            "steps": result['steps'],
            "data_info": result['data_info']
        }), 200
        
    except Exception as e:
        logging.error(f"完整下载流程API错误: {e}")
        return jsonify({
            "success": False,
            "message": f"下载失败: {str(e)}"
        }), 500


@download_data_bp.route('/complete_download_test', methods=['GET'])
def complete_download_test():
    """
    完整下载流程测试页面
    """
    return render_template('data/complete_download_test.html')


@download_data_bp.route('/set_download_end_date', methods=['POST'])
def set_download_end_date():
    """
    设置所有股票的下载结束日期，用于强制重新下载数据

    请求参数:
        end_date: 结束日期，格式 YYYY-MM-DD
    """
    try:
        data = request.get_json()
        end_date_str = data.get('end_date')

        if not end_date_str:
            return jsonify({
                "success": False,
                "message": "请提供结束日期 (end_date)"
            }), 400

        try:
            new_end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({
                "success": False,
                "message": "日期格式错误，请使用 YYYY-MM-DD 格式"
            }), 400

        # 更新所有非忽略股票的 end_date
        # 注意：使用UTC时间以保持与模型定义一致
        update_count = dlr.query.filter(
            dlr.end_date != date(2050, 1, 1),  # 排除被忽略的股票
            dlr.record_date != date(2050, 1, 1)  # 排除被忽略的股票
        ).update({
            'end_date': new_end_date,
            'download_status': 'pending',  # 重置为待下载状态
            'download_progress': 0.0,
            'updated_at': datetime.utcnow()
        })

        db.session.commit()

        logging.info(f"成功将 {update_count} 条记录的 end_date 设置为 {new_end_date}")

        return jsonify({
            "success": True,
            "message": f"成功将 {update_count} 条记录的结束日期设置为 {end_date_str}，状态已重置为待下载",
            "updated_count": update_count,
            "new_end_date": end_date_str
        }), 200

    except Exception as e:
        db.session.rollback()
        logging.error(f"设置下载结束日期时发生错误: {e}")
        return jsonify({
            "success": False,
            "message": f"设置失败: {str(e)}"
        }), 500


@download_data_bp.route('/download_records_editor')
def download_records_editor():
    """下载记录编辑页面"""
    return render_template('data/download_records_editor.html')


@download_data_bp.route('/api/download_records', methods=['GET'])
def api_get_download_records():
    """获取下载记录列表（带分页和搜索）"""
    try:
        # 获取参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        search = request.args.get('search', '', type=str)
        status_filter = request.args.get('status', '', type=str)
        # status_of: 状态筛选作用在哪条流水线上——'repair'=修复状态(数据修复页) / 其余=下载状态
        status_of = request.args.get('status_of', '', type=str)
        # code_type: 'stock'=个股 / 'board'=板块（代码以 BK 开头）/ ''=全部
        code_type = request.args.get('code_type', '', type=str)
        # 排序：sort=end_date/start_date/record_date/updated_at，order=asc/desc
        sort_by = request.args.get('sort', 'updated_at', type=str)
        order = request.args.get('order', 'desc', type=str)
        # 结束日期范围筛选（找落后股票）
        end_before = request.args.get('end_before', '', type=str)
        end_after = request.args.get('end_after', '', type=str)
        end_on = request.args.get('end_on', '', type=str)  # 结束日期正好等于某天
        # scope: 'pool'=只看股票池(盘后下载页) / 其余=全市场(数据修复页)
        scope = request.args.get('scope', '', type=str)
        # stale_only: 只看「落后」记录(end_date < 最新交易日)，数据修复页默认口径，
        # 与 /api/repair_stale_records（一键修复）完全一致，避免把已最新的股票也混进修复清单
        stale_only = request.args.get('stale_only', '', type=str).lower() in ('1', 'true', 'yes')

        per_page = min(per_page, 100)

        # 构建查询
        query = _apply_scope(dlr.query, scope)

        # 状态筛选（下载状态 / 修复状态各筛各的）
        if status_filter:
            query = query.filter(
                (dlr.repair_status if status_of == 'repair' else dlr.download_status) == status_filter)

        # 代码搜索 + 个股/板块类型，都要通过 stock_code_id 关联 StockCodes 解析
        if search or code_type in ('stock', 'board'):
            id_query = db.session.query(StockCodes.id)
            if search:
                id_query = id_query.filter(StockCodes.code.like(f'%{search}%'))
            if code_type == 'board':
                id_query = id_query.filter(StockCodes.code.like('BK%'))
            elif code_type == 'stock':
                id_query = id_query.filter(~StockCodes.code.like('BK%'))
            matching_ids = [s[0] for s in id_query.all()]
            if matching_ids:
                query = query.filter(dlr.stock_code_id.in_(matching_ids))
            else:
                # 没有匹配的股票，返回空结果
                return jsonify({
                    'records': [],
                    'total': 0,
                    'page': page,
                    'per_page': per_page,
                    'total_pages': 0
                })

        # 排除被忽略的记录
        query = query.filter(
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1)
        )

        # 排除退市股票：A股退市后名称会改成含"退"（如"乐视退""退市锐电"）；
        # 仅风险警示的 *ST/ST 名称不含"退"、仍在交易，不受影响。
        delisted_ids = [s[0] for s in db.session.query(StockCodes.id)
                        .filter(StockCodes.name.like('%退%')).all()]
        if delisted_ids:
            query = query.filter(~dlr.stock_code_id.in_(delisted_ids))

        # 只看落后：end_date < 最新交易日（数据修复页默认）。口径同 api_repair_stale_records。
        if stale_only:
            latest_td, _is_td = get_latest_trading_date()
            query = query.filter(
                dlr.end_date.isnot(None),
                dlr.end_date < latest_td,
            )

        # 结束日期筛选：正好等于某天 / 范围
        if end_on:
            try:
                query = query.filter(dlr.end_date == datetime.strptime(end_on, '%Y-%m-%d').date())
            except ValueError:
                pass
        if end_before:
            try:
                query = query.filter(dlr.end_date <= datetime.strptime(end_before, '%Y-%m-%d').date())
            except ValueError:
                pass
        if end_after:
            try:
                query = query.filter(dlr.end_date >= datetime.strptime(end_after, '%Y-%m-%d').date())
            except ValueError:
                pass

        # 排序（默认按更新时间倒序；可切结束日期等）
        sort_col = {
            'end_date': dlr.end_date, 'start_date': dlr.start_date,
            'record_date': dlr.record_date, 'updated_at': dlr.updated_at,
        }.get(sort_by, dlr.updated_at)
        query = query.order_by(sort_col.asc() if order == 'asc' else sort_col.desc())

        # 分页
        total = query.count()
        records = query.offset((page - 1) * per_page).limit(per_page).all()

        # 格式化数据
        result = []
        for record in records:
            stock_code = get_stock_code_by_id(record.stock_code_id)
            result.append({
                'id': record.id,
                'stock_code_id': record.stock_code_id,
                'stock_code': stock_code or f'ID:{record.stock_code_id}',
                'stock_name': get_stock_name_by_id(record.stock_code_id) or '-',
                'download_status': record.download_status,
                'download_progress': record.download_progress,
                'error_message': record.error_message,
                'repair_status': record.repair_status,
                'repair_progress': record.repair_progress,
                'repair_error': record.repair_error,
                'repair_time': record.repair_time.strftime('%Y-%m-%d %H:%M:%S') if record.repair_time else None,
                'start_date': record.start_date.strftime('%Y-%m-%d') if record.start_date else None,
                'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else None,
                'record_date': record.record_date.strftime('%Y-%m-%d') if record.record_date else None,
                'total_records': record.total_records,
                'downloaded_records': record.downloaded_records,
                'last_download_time': record.last_download_time.strftime('%Y-%m-%d %H:%M:%S') if record.last_download_time else None,
                'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else None,
                'updated_at': record.updated_at.strftime('%Y-%m-%d %H:%M:%S') if record.updated_at else None
            })

        return jsonify({
            'records': result,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })

    except Exception as e:
        logging.error(f"获取下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/repair_stale_records', methods=['POST'])
def api_repair_stale_records():
    """一键修复「下载表单里结束日期落后」的个股：从各自 end_date 增量补到市场最新交易日。
    复用板块修复 minute_data_repair（本地zip + TDX缺口 + 重生成15m + 补日K + 回写end_date）。
    body{end_before:YYYY-MM-DD?} 只修结束日期<=该日的；缺省=修所有 end_date<市场最新 的落后个股。
    body{preview:true} 只返回数量。自动排除退市(名称含"退")与板块码(BK)。进度/暂停复用 /board_data/api/repair_status|repair_pause。"""
    try:
        from App.services.minute_data_repair import start_repair, DEFAULT_ZIP_DIR
        data = request.get_json(silent=True) or {}
        end_before = (data.get('end_before') or '').strip()
        end_after = (data.get('end_after') or '').strip()   # 结束日期 >= 某天（大约从这天起的落后股）
        preview = bool(data.get('preview'))
        with_trend = bool(data.get('with_trend'))

        # 最新交易日（考虑15:00收盘；今天收盘后=07-11）——作为"是否已最新"的基准。
        # 硬性排除 end_date 已 >= 最新交易日 的股票：已是最新，不需要下载（用户要求）。
        latest_td, _is_td = get_latest_trading_date()

        q = dlr.query.filter(
            dlr.end_date.isnot(None),
            dlr.end_date != date(2050, 1, 1),
            dlr.record_date != date(2050, 1, 1),
            dlr.end_date < latest_td,   # 已达最新交易日 → 已最新，跳过
        )
        # 结束日期范围筛选：≥ end_after 且 ≤ end_before（可只给其一）；base 已含 < 最新交易日
        if end_after:
            try:
                q = q.filter(dlr.end_date >= datetime.strptime(end_after, '%Y-%m-%d').date())
            except ValueError:
                return jsonify({'success': False, 'message': '日期格式错误，用 YYYY-MM-DD'}), 400
        if end_before:
            try:
                thr = datetime.strptime(end_before, '%Y-%m-%d').date()
                q = q.filter(dlr.end_date <= thr)
            except ValueError:
                return jsonify({'success': False, 'message': '日期格式错误，用 YYYY-MM-DD'}), 400
        else:
            thr = latest_td

        # 按结束日期升序取（最落后的排前面），供"按页依次修复"从最落后开始逐批下载
        recs = q.order_by(dlr.end_date.asc()).with_entities(dlr.stock_code_id, dlr.end_date).all()
        codes = []
        if recs:
            info = {s.id: (s.code, s.name) for s in
                    StockCodes.query.filter(StockCodes.id.in_([r[0] for r in recs]))
                    .with_entities(StockCodes.id, StockCodes.code, StockCodes.name).all()}
            for sid, _end in recs:  # 已按 end_date 升序
                cn = info.get(sid)
                if not cn:
                    continue
                code, name = cn
                if not code or code.startswith('BK'):
                    continue
                if name and '退' in name:
                    continue
                codes.append(code)
            codes = list(dict.fromkeys(codes))  # 去重保序(最落后优先)

        # list_only：只返回有序代码列表（前端"按页依次修复"用它分批）
        if data.get('list_only'):
            return jsonify({'success': True, 'codes': codes, 'count': len(codes),
                            'latest_trading_date': str(latest_td)})
        if preview:
            return jsonify({'success': True, 'preview': True, 'count': len(codes),
                            'latest_trading_date': str(latest_td), 'threshold': str(thr)})
        if not codes:
            return jsonify({'success': False, 'message': '没有符合条件的落后个股'}), 200

        app = current_app._get_current_object()
        ok, msg = start_repair(app, '下载表单落后修复', codes,
                               zip_dir=DEFAULT_ZIP_DIR, then_trend=with_trend)
        return jsonify({'success': ok, 'message': msg, 'count': len(codes)}), (200 if ok else 409)
    except Exception as e:
        logging.error(f"一键修复落后记录失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@download_data_bp.route('/api/repair_codes', methods=['POST'])
def api_repair_codes():
    """修复指定的一批股票代码（下载详情页"按页依次修复"每批调用）。
    body{codes:[...], with_trend?}。自动去板块码(BK)/去重。进度复用 /board_data/api/repair_status。"""
    try:
        from App.services.minute_data_repair import start_repair, DEFAULT_ZIP_DIR
        data = request.get_json(silent=True) or {}
        raw = data.get('codes') or []
        codes = []
        for c in raw:
            c = str(c).strip()
            if c and not c.startswith('BK'):
                codes.append(c)
        codes = list(dict.fromkeys(codes))
        if not codes:
            return jsonify({'success': False, 'message': '没有可修复的股票代码'}), 200
        app = current_app._get_current_object()
        ok, msg = start_repair(app, '按页修复', codes,
                               zip_dir=DEFAULT_ZIP_DIR, then_trend=bool(data.get('with_trend')))
        return jsonify({'success': ok, 'message': msg, 'count': len(codes)}), (200 if ok else 409)
    except Exception as e:
        logging.error(f"按页修复失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@download_data_bp.route('/api/download_records/<int:record_id>', methods=['GET'])
def api_get_download_record(record_id):
    """获取单条下载记录"""
    try:
        record = dlr.query.get(record_id)
        if not record:
            return jsonify({'error': '记录不存在'}), 404

        stock_code = get_stock_code_by_id(record.stock_code_id)

        return jsonify({
            'id': record.id,
            'stock_code_id': record.stock_code_id,
            'stock_code': stock_code or f'ID:{record.stock_code_id}',
            'download_status': record.download_status,
            'download_progress': record.download_progress,
            'error_message': record.error_message,
            'start_date': record.start_date.strftime('%Y-%m-%d') if record.start_date else None,
            'end_date': record.end_date.strftime('%Y-%m-%d') if record.end_date else None,
            'record_date': record.record_date.strftime('%Y-%m-%d') if record.record_date else None,
            'total_records': record.total_records,
            'downloaded_records': record.downloaded_records,
            'last_download_time': record.last_download_time.strftime('%Y-%m-%d %H:%M:%S') if record.last_download_time else None
        })

    except Exception as e:
        logging.error(f"获取下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records', methods=['POST'])
def api_add_download_record():
    """添加下载记录"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code')

        if not stock_code:
            return jsonify({'error': '股票代码不能为空'}), 400

        # 查找股票ID
        stock = StockCodes.query.filter_by(code=stock_code).first()
        if not stock:
            return jsonify({'error': f'未找到股票代码: {stock_code}'}), 404

        # 检查是否已存在
        existing = dlr.query.filter_by(stock_code_id=stock.id).first()
        if existing:
            return jsonify({'error': f'股票 {stock_code} 的下载记录已存在'}), 400

        # 创建新记录
        new_record = dlr(
            stock_code_id=stock.id,
            download_status=data.get('download_status', 'pending'),
            download_progress=0.0,
            start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date() if data.get('start_date') else None,
            end_date=datetime.strptime(data['end_date'], '%Y-%m-%d').date() if data.get('end_date') else date.today(),
            record_date=date.today(),
            total_records=0,
            downloaded_records=0
        )

        db.session.add(new_record)
        db.session.commit()

        logging.info(f"成功添加下载记录: {stock_code}")

        return jsonify({
            'success': True,
            'message': f'成功添加股票 {stock_code} 的下载记录',
            'id': new_record.id
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"添加下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/<int:record_id>', methods=['PUT'])
def api_update_download_record(record_id):
    """更新下载记录"""
    try:
        record = dlr.query.get(record_id)
        if not record:
            return jsonify({'error': '记录不存在'}), 404

        data = request.get_json()

        # 更新字段
        if 'download_status' in data:
            record.download_status = data['download_status']
        if 'end_date' in data and data['end_date']:
            record.end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date()
        if 'start_date' in data and data['start_date']:
            record.start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
        if 'error_message' in data:
            record.error_message = data['error_message']

        record.updated_at = datetime.utcnow()
        db.session.commit()

        stock_code = get_stock_code_by_id(record.stock_code_id)
        logging.info(f"成功更新下载记录: {stock_code}")

        return jsonify({
            'success': True,
            'message': f'成功更新记录 {stock_code}'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"更新下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/<int:record_id>', methods=['DELETE'])
def api_delete_download_record(record_id):
    """删除下载记录"""
    try:
        record = dlr.query.get(record_id)
        if not record:
            return jsonify({'error': '记录不存在'}), 404

        stock_code = get_stock_code_by_id(record.stock_code_id)

        db.session.delete(record)
        db.session.commit()

        logging.info(f"成功删除下载记录: {stock_code}")

        return jsonify({
            'success': True,
            'message': f'成功删除记录 {stock_code}'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"删除下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/batch_delete', methods=['POST'])
def api_batch_delete_download_records():
    """批量删除下载记录"""
    try:
        data = request.get_json()
        ids = data.get('ids', [])

        if not ids:
            return jsonify({'error': '请选择要删除的记录'}), 400

        deleted_count = dlr.query.filter(dlr.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()

        logging.info(f"批量删除了 {deleted_count} 条下载记录")

        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 条记录',
            'deleted_count': deleted_count
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"批量删除下载记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/download_records/batch_update_status', methods=['POST'])
def api_batch_update_status():
    """批量更新下载状态"""
    try:
        data = request.get_json()
        ids = data.get('ids', [])
        new_status = data.get('status')

        if not ids:
            return jsonify({'error': '请选择要更新的记录'}), 400
        if not new_status:
            return jsonify({'error': '请指定新状态'}), 400

        updated_count = dlr.query.filter(dlr.id.in_(ids)).update({
            'download_status': new_status,
            'updated_at': datetime.utcnow()
        }, synchronize_session=False)
        db.session.commit()

        logging.info(f"批量更新了 {updated_count} 条记录状态为 {new_status}")

        return jsonify({
            'success': True,
            'message': f'成功更新 {updated_count} 条记录状态为 {new_status}',
            'updated_count': updated_count
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"批量更新下载记录状态失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/api/available_stocks', methods=['GET'])
def api_get_available_stocks():
    """获取可添加的股票列表（尚未在下载记录中的）"""
    try:
        search = request.args.get('search', '', type=str)
        limit = request.args.get('limit', 20, type=int)

        # 获取已有下载记录的股票ID
        existing_ids = db.session.query(dlr.stock_code_id).all()
        existing_ids = [s[0] for s in existing_ids]

        # 查询未添加的股票
        query = StockCodes.query.filter(~StockCodes.id.in_(existing_ids))

        if search:
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    StockCodes.code.like(f'%{search}%'),
                    StockCodes.name.like(f'%{search}%')
                )
            )

        stocks = query.limit(limit).all()

        return jsonify({
            'stocks': [
                {'id': s.id, 'code': s.code, 'name': s.name}
                for s in stocks
            ]
        })

    except Exception as e:
        logging.error(f"获取可用股票列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@download_data_bp.route('/complete_download_batch', methods=['POST'])
def complete_download_batch():
    """
    批量股票完整下载流程API
    """
    try:
        data = request.get_json()
        stock_codes = data.get('stock_codes', [])
        days = data.get('days', 1)
        
        if not stock_codes:
            return jsonify({
                "success": False,
                "message": "股票代码列表不能为空"
            }), 400
        
        if not isinstance(stock_codes, list):
            return jsonify({
                "success": False,
                "message": "股票代码必须是列表格式"
            }), 400
        
        # 执行批量下载流程
        result = batch_complete_download_process(stock_codes, days)
        
        return jsonify({
            "success": True,
            "message": f"批量下载完成: 成功 {result['success']}, 失败 {result['failed']}",
            "total": result['total'],
            "success_count": result['success'],
            "failed_count": result['failed'],
            "details": result['details']
        }), 200
        
    except Exception as e:
        logging.error(f"批量完整下载流程API错误: {e}")
        return jsonify({
            "success": False,
            "message": f"批量下载失败: {str(e)}"
        }), 500
