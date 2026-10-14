# -*- coding: utf-8 -*-
"""SEC EDGAR 披露监控（美股长期跟踪 · 方案 A）。

后台 daemon 线程每 30 分钟拉一次关注标的的 EDGAR submissions，新披露入库 us_sec_filing，
重要表格走 notifier 三渠道推送，并自动在 us_stock_event 时间线上生成一条事件。

几条规则：
  * 数据源只用 SEC 官方免费 JSON（data.sec.gov），不依赖任何第三方。
    SEC 要求请求带可识别的 User-Agent（名称 + 联系方式），否则 403。
    通过环境变量 SEC_USER_AGENT 配置，如 "QuantFlow yourname@example.com"。
  * **首次同步不推送**：SPCX 上市前后已有几十上百份披露（S-1 各修订版、Form 4、424B4…），
    第一次跑全部置 notified=True 静默回填，只推之后新增的。
  * Form 4 / 144 一轮内合并成一条推送：解禁期内部人减持可能一天几十份，逐条推会刷屏。
    它们也不自动生成时间线事件（太多），只在披露列表里可筛。
  * 不在请求线程里做网络 IO 之外的重活；单进程单例，与 holdings_1m_autofetch 同模式（项目未装调度器）。
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, date

logger = logging.getLogger(__name__)

DEFAULT_WATCH = ['SPCX']
DEFAULT_INTERVAL_SEC = 1800

TICKERS_URL = 'https://www.sec.gov/files/company_tickers.json'
SUBMISSIONS_URL = 'https://data.sec.gov/submissions/CIK{cik}.json'
ARCHIVE_URL = 'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}'

# 推送 + 自动生成事件的表格。前缀匹配（424B4、SC 13G/A 等变体都算）。
EVENT_FORMS = ('8-K', '10-Q', '10-K', 'DEF 14A', 'S-1', 'S-3', '424B', 'SC 13D', 'SC 13G',
               'SCHEDULE 13D', 'SCHEDULE 13G', '6-K')   # EDGAR 2025 起 13D/G 改名 SCHEDULE 13D/G
# 只推送（合并）不生成事件
INSIDER_FORMS = ('3', '4', '5', '144', '3/A', '4/A', '5/A', '144/A')

ITEMS_8K = {
    '1.01': '重大协议', '1.02': '终止重大协议', '1.05': '网络安全事件', '2.01': '收购/处置资产',
    '2.02': '经营业绩', '2.03': '新增直接债务', '2.05': '退出/重组成本',
    '3.01': '上市规则/退市通知', '3.02': '未注册股份发行', '3.03': '证券持有人权利变更',
    '4.01': '更换审计师', '4.02': '已发布财报不可依赖', '5.01': '控制权变更', '5.02': '高管/董事变动', '5.03': '章程修订',
    '5.07': '股东投票结果', '7.01': 'Reg FD 披露', '8.01': '其他事项', '9.01': '财务报表/附件',
}

_state = {
    'running': False, 'started': None, 'interval_sec': DEFAULT_INTERVAL_SEC,
    'cycles': 0, 'last_run': None, 'next_run': None, 'last_error': None,
    'last_result': {},
}
_lock = threading.Lock()
_stop = threading.Event()
_ever_started = False
_cik_cache: dict = {}


def watch_list() -> list:
    raw = os.environ.get('US_SEC_WATCH')
    if raw:
        return [s.strip().upper() for s in raw.replace(' ', ',').split(',') if s.strip()]
    return list(DEFAULT_WATCH)


def _headers() -> dict:
    ua = os.environ.get('SEC_USER_AGENT') or 'QuantFlow personal-research'
    return {'User-Agent': ua, 'Accept-Encoding': 'gzip, deflate'}


def _get_json(url: str):
    import requests
    r = requests.get(url, headers=_headers(), timeout=15)
    if r.status_code == 403:
        raise RuntimeError('SEC 返回 403：请设置环境变量 SEC_USER_AGENT（名称 + 联系邮箱）')
    r.raise_for_status()
    return r.json()


def resolve_cik(symbol: str) -> str:
    """ticker -> 10 位 CIK。进程内缓存；SEC ticker 表每天更新，新上市票当天可能还没有。"""
    symbol = symbol.upper()
    if symbol in _cik_cache:
        return _cik_cache[symbol]
    data = _get_json(TICKERS_URL)
    for row in data.values():
        _cik_cache[str(row.get('ticker', '')).upper()] = f"{int(row['cik_str']):010d}"
    if symbol not in _cik_cache:
        raise RuntimeError(f'SEC ticker 表里找不到 {symbol}')
    return _cik_cache[symbol]


def _form_match(form: str, prefixes) -> bool:
    return any(form == p or form.startswith(p) for p in prefixes)


def describe_items(items: str) -> str:
    if not items:
        return ''
    return '、'.join(ITEMS_8K.get(i.strip(), i.strip()) for i in items.split(',') if i.strip())


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date() if s else None
    except ValueError:
        return None


def _recent_rows(cik: str) -> list:
    """submissions JSON 的 filings.recent 是列式数组，转成行。recent 覆盖最近 1000 份，够用。"""
    data = _get_json(SUBMISSIONS_URL.format(cik=cik))
    rec = (data.get('filings') or {}).get('recent') or {}
    n = len(rec.get('accessionNumber') or [])
    cols = ('accessionNumber', 'filingDate', 'reportDate', 'acceptanceDateTime', 'form',
            'items', 'primaryDocument', 'primaryDocDescription')
    rows = []
    for i in range(n):
        rows.append({c: (rec.get(c) or [None] * n)[i] for c in cols})
    return rows


def _event_for(filing) -> dict:
    form = filing.form
    items_desc = describe_items(filing.items)
    if form.startswith('10-Q') or form.startswith('10-K') or (form.startswith('8-K') and '2.02' in (filing.items or '')):
        cat = 'earnings'
    else:
        cat = 'filing'
    desc = filing.description if filing.description and filing.description.upper() != form.upper() else ''
    title = form + (f'（{items_desc}）' if items_desc else (f' {desc}' if desc else ''))
    importance = 3 if cat == 'earnings' or form.startswith('424B') else 2
    return {
        'symbol': filing.symbol, 'event_date': filing.filed_date, 'category': cat,
        'title': title[:200], 'detail': filing.description, 'importance': importance,
        'source_url': filing.doc_url, 'source': 'sec', 'source_ref': filing.accession_no,
    }


def sync_symbol(symbol: str, push: bool = True) -> dict:
    """同步一只。需在 app_context 内调用。返回 {symbol, new, pushed, first_sync}。"""
    from App.exts import db
    from App.models.data.UsWatch import UsSecFiling, UsStockEvent

    symbol = symbol.upper()
    cik = resolve_cik(symbol)
    rows = _recent_rows(cik)
    first_sync = UsSecFiling.query.filter_by(symbol=symbol).first() is None
    known = {a for (a,) in db.session.query(UsSecFiling.accession_no).filter_by(symbol=symbol)}

    new = []
    for r in rows:
        acc = r.get('accessionNumber')
        if not acc or acc in known:
            continue
        doc = r.get('primaryDocument') or ''
        f = UsSecFiling(
            symbol=symbol, cik=cik, accession_no=acc, form=r.get('form') or '',
            filed_date=_parse_date(r.get('filingDate')) or date.today(),
            report_date=_parse_date(r.get('reportDate')),
            accepted_at=r.get('acceptanceDateTime'), items=(r.get('items') or '')[:100] or None,
            description=(r.get('primaryDocDescription') or '')[:255] or None,
            doc_url=ARCHIVE_URL.format(cik_int=int(cik), acc_nodash=acc.replace('-', ''), doc=doc) if doc else None,
            notified=first_sync,
        )
        db.session.add(f)
        new.append(f)
    db.session.flush()

    # 对全部已入库披露补齐事件（不仅本轮新增）：EVENT_FORMS 扩充后历史披露也能补上
    existing_refs = {a for (a,) in db.session.query(UsStockEvent.source_ref)
                     .filter(UsStockEvent.symbol == symbol, UsStockEvent.source == 'sec')}
    for f in UsSecFiling.query.filter_by(symbol=symbol):
        if _form_match(f.form, EVENT_FORMS) and f.accession_no not in existing_refs:
            db.session.add(UsStockEvent(**_event_for(f)))
    db.session.commit()

    pushed = 0
    if push and not first_sync:
        pushed = _notify(symbol, [f for f in new if not f.notified])
    return {'symbol': symbol, 'new': len(new), 'pushed': pushed, 'first_sync': first_sync}


def _notify(symbol: str, filings: list) -> int:
    from App.exts import db
    from App.services.notifier import push

    major = [f for f in filings if _form_match(f.form, EVENT_FORMS)]
    insider = [f for f in filings if f.form in INSIDER_FORMS]
    sent = 0
    for f in major:
        desc = describe_items(f.items) or (f.description or '')
        title = f'[{symbol}] 新披露 {f.form}' + (f'：{desc}' if desc else '')
        body = f'提交日 {f.filed_date}\n{f.doc_url or ""}'
        ok, _ = push(title, body)
        if ok:
            sent += 1
    if insider:
        lines = [f'{f.form}  {f.filed_date}  {f.doc_url or ""}' for f in insider[:20]]
        more = f'\n…另有 {len(insider) - 20} 份' if len(insider) > 20 else ''
        ok, _ = push(f'[{symbol}] 内部人披露 {len(insider)} 份（Form 4/144 等）', '\n'.join(lines) + more)
        if ok:
            sent += 1
    # 没配任何渠道时也置 notified，否则配上渠道后会把积压一次全推出来
    for f in filings:
        f.notified = True
    db.session.commit()
    return sent


def sync_all(app, push: bool = True) -> dict:
    out = {}
    with app.app_context():
        for sym in watch_list():
            try:
                out[sym] = sync_symbol(sym, push=push)
            except Exception as e:
                from App.exts import db
                db.session.rollback()
                logger.exception(f'[sec] {sym} 同步失败')
                out[sym] = {'symbol': sym, 'error': str(e)}
    return out


def _run(app, interval_sec):
    try:
        while not _stop.is_set():
            res = sync_all(app)
            errs = [v['error'] for v in res.values() if 'error' in v]
            with _lock:
                _state['cycles'] += 1
                _state['last_run'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _state['last_result'] = res
                _state['last_error'] = '; '.join(errs) or None
                _state['next_run'] = datetime.fromtimestamp(
                    datetime.now().timestamp() + interval_sec).strftime('%Y-%m-%d %H:%M:%S')
            _stop.wait(interval_sec)
    finally:
        with _lock:
            _state['running'] = False
            _state['next_run'] = None


def start_watch(app, interval_sec: int = DEFAULT_INTERVAL_SEC):
    global _ever_started
    with _lock:
        if _state['running']:
            return False, '已在运行'
        _stop.clear()
        _state.update(running=True, started=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                      interval_sec=interval_sec, cycles=0, last_error=None)
        _ever_started = True
    threading.Thread(target=_run, args=(app, interval_sec), name='us-sec-watch', daemon=True).start()
    return True, f'已启动（每 {interval_sec // 60} 分钟，关注 {",".join(watch_list())}）'


def stop_watch():
    if not _state['running']:
        return False, '未在运行'
    _stop.set()
    return True, '已请求停止'


def ensure_started(app):
    """首次访问档案页时兜底自启（尊重手动停止，不反复重启）。"""
    if _ever_started or _state['running']:
        return False, '已启动过'
    return start_watch(app)


def get_status() -> dict:
    with _lock:
        st = dict(_state)
    st['watch'] = watch_list()
    st['ever_started'] = _ever_started
    st['user_agent_set'] = bool(os.environ.get('SEC_USER_AGENT'))
    return st
