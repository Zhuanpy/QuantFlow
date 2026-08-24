"""板块热度快照的「页面访问驱动」兜底触发器（非阻塞 / 进程内单实例）。

为什么是这个形态
----------------
L0 快照数据**不可回补**（东财只给当日），漏一天就永久缺一天。首选调度是收盘后跑
`scripts/Others/daily_close_pipeline.py`（含 STEP3），但项目没装 APScheduler，
万一没配任务计划/机器没开，这里做第二道保险：只要有人打开热点页，就检查
「最近一个交易日是否已入库」，缺了就后台补抓。抄的是 news_auto_fetch 那套。

约束：
- 后台线程里跑 → **allow_akshare=False**（akshare 的 V8 只能主线程初始化，
  在线程里会 PartitionAlloc FATAL 整进程崩）。东财挂了就等调度那次主线程兜底。
- 盘中（交易日 15:00 前）不自动触发：那时抓到的是盘中值，让它由用户手动点或
  收盘后调度来写，避免每次打开页面都刷一次 is_intraday 行。
- 单进程内 Lock + RUNNING 去重；多 worker 部署下各自一份状态（当前是 Flask 单进程）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ---- 模块级状态（仅同进程） ----
_LOCK = threading.Lock()
_RUNNING = False
_LAST_TRIGGER_TS = None
_LAST_RESULT = None


def _snapshot():
    with _LOCK:
        return {
            'running': _RUNNING,
            'last_trigger_at': _LAST_TRIGGER_TS.isoformat() if _LAST_TRIGGER_TS else None,
            'last_result': dict(_LAST_RESULT) if _LAST_RESULT else None,
        }


def get_status(app=None) -> dict:
    """当前入库状态 + 是否需要补抓。"""
    from App.models.strategy.SectorFlowDaily import SectorFlowDaily
    from App.services.sector_flow_service import resolve_snapshot_date
    from App.exts import db

    SectorFlowDaily.ensure_table()
    target, intraday = resolve_snapshot_date()

    rows = (db.session.query(SectorFlowDaily.board_type,
                             db.func.count(SectorFlowDaily.id),
                             db.func.max(SectorFlowDaily.is_intraday))
            .filter(SectorFlowDaily.date == target)
            .group_by(SectorFlowDaily.board_type).all())
    have = {bt: {'rows': int(n or 0), 'intraday': bool(iv)} for bt, n, iv in rows}

    latest = SectorFlowDaily.latest_date()
    total_days = (db.session.query(db.func.count(db.distinct(SectorFlowDaily.date)))
                  .scalar() or 0)

    missing = [bt for bt in ('industry', 'concept') if not have.get(bt)]
    # 盘中不自动补；已有当日收盘行也不补
    stale = bool(missing) and not intraday

    return {
        'target_date': str(target),
        'is_intraday_now': intraday,
        'per_type': have,
        'missing_types': missing,
        'stale': stale,
        'latest_date': str(latest) if latest else None,
        'recorded_days': int(total_days),
        **_snapshot(),
    }


def trigger_async(app, force: bool = False) -> dict:
    """需要时启动后台补抓。返回 dict(triggered/reason/...)。"""
    global _RUNNING, _LAST_TRIGGER_TS

    status = get_status(app)
    if status['running']:
        return {'triggered': False, 'reason': 'already_running', **status}
    if not force and not status['stale']:
        reason = 'intraday' if status['is_intraday_now'] else 'fresh'
        return {'triggered': False, 'reason': reason, **status}

    with _LOCK:
        if _RUNNING:
            return {'triggered': False, 'reason': 'already_running', **status}
        _RUNNING = True
        _LAST_TRIGGER_TS = datetime.now()

    types = tuple(status['missing_types']) or ('industry', 'concept')
    t = threading.Thread(target=_run_in_thread, args=(app, types),
                         name='sector-flow-auto', daemon=True)
    t.start()
    return {'triggered': True, 'reason': 'started', 'types': list(types), **status}


def _run_in_thread(app, types):
    global _RUNNING, _LAST_RESULT
    from App.services.sector_flow_service import sync_sector_flow
    try:
        # 后台线程：禁用 akshare 兜底（V8 只能主线程）
        res = sync_sector_flow(app, types=types, allow_akshare=False)
        res['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with _LOCK:
            _LAST_RESULT = res
        logger.info(f'[sector_flow_auto] 自动补抓完成: {res}')
    except Exception as e:
        logger.exception('[sector_flow_auto] 自动补抓失败')
        with _LOCK:
            _LAST_RESULT = {'error': str(e),
                            'finished_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    finally:
        with _LOCK:
            _RUNNING = False
