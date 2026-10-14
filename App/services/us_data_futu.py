# -*- coding: utf-8 -*-
"""美股 1m 历史数据服务（数据源：富途 OpenAPI）。

与 A 股那条线完全隔离：数据落在 data/us/quarters/，交易日历用富途的美股日历，
不碰 data_download_records 账本——**parquet 就是唯一真相源**，每次现扫现算。
不建账本是刻意的：A 股那边 DailyTaskStatus 与 parquet 真值漂移、stock_stats 快照卡旧日期，
都是"账本说一套、文件是另一套"造成的。这里只有十来只标的，扫文件是毫秒级，没必要冒那个险。

几条实测确立的硬规则（2026-08-11 用 AAPL 实测）：
  * time_key 是每根 K 线的**结束**时间：全天是 09:31~16:00 共 390 根，
    不是 09:30~15:59。按开始时间做右开区间过滤会把收盘那根切掉，每天静默少 1 根。
  * 半日市（request_trading_days 标 MORNING）是 09:31~13:00 共 210 根。
  * 历史K线额度按「股票」计，与区间长短/请求次数无关；窗口内重复拉同一只不重复扣。
    因此策略是**一次拉满**，而不是分批拉。
  * request_trading_days 不消耗额度。
  * 不分页会在 max_count 处静默截断，必须用 page_req_key 翻到底。
"""
import logging
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

OPEND_HOST = '127.0.0.1'
OPEND_PORT = 11111

BARS_WHOLE = 390          # 全天 09:31~16:00
BARS_MORNING = 210        # 半日市 09:31~13:00
SESSION_START = '09:31'
SESSION_END = '16:00'

# 拉满：1m 在富途保留最近 8 年。额度按只计，拉多长都一样，所以默认顶格拉。
MAX_YEARS = 8

_state = {
    'running': False, 'total': 0, 'done': 0, 'ok': 0, 'fail': 0,
    'cur': None, 'cur_pages': 0, 'cur_bars': 0, 'added': 0,
    'failed': [], 'error': None, 'started': None, 'stopped': False,
    'log': [],
}
_lock = threading.Lock()
_stop = threading.Event()


def get_status() -> dict:
    with _lock:
        s = dict(_state)
        s['log'] = list(_state['log'])[-40:]
        return s


def stop_download():
    _stop.set()


def _push_log(msg):
    with _lock:
        _state['log'].append(f'{time.strftime("%H:%M:%S")} {msg}')
        if len(_state['log']) > 200:
            del _state['log'][:-200]


# ---------------------------------------------------------------- 基础设施

def normalize(sym: str) -> str:
    """用户可能输 aapl / AAPL / US.AAPL，统一成富途要的 US.AAPL。"""
    s = (sym or '').strip().upper().replace(' ', '')
    if not s:
        return ''
    if '.' in s:
        mkt, code = s.split('.', 1)
        return f'US.{code}' if mkt in ('US', 'USA') else s
    return f'US.{s}'


def to_symbol(code: str) -> str:
    """US.AAPL -> AAPL（落盘文件名）。"""
    return code.split('.')[-1]


def us_quarters_dir() -> Path:
    from config import Config
    return Path(Config.get_project_root()) / 'data' / 'us' / 'quarters'


class _Quote:
    """OpenQuoteContext 的上下文管理器，顺带把「连不上」翻译成人话。

    连不上必须显式报错，不能静默返回空——A 股那边 pytdx 把异常吞成"没数据"，
    页面显示"可能停牌退市"，排查了很久才发现是服务器问题。
    """

    def __enter__(self):
        try:
            from futu import OpenQuoteContext
        except ImportError:
            raise RuntimeError('futu-api 未安装：pip install futu-api')
        try:
            self.ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
        except Exception as e:
            raise RuntimeError(
                f'连不上 FutuOpenD（{OPEND_HOST}:{OPEND_PORT}）：{e}。'
                f'请确认 FutuOpenD 已启动并已登录牛牛号。')
        return self.ctx

    def __exit__(self, *a):
        try:
            self.ctx.close()
        except Exception:
            pass


def get_quota() -> dict:
    """历史K线额度：{used, remain, total, detail:[{code,name,request_time}]}。"""
    from futu import RET_OK
    with _Quote() as ctx:
        ret, data = ctx.get_history_kl_quota(get_detail=True)
        if ret != RET_OK:
            raise RuntimeError(f'查询额度失败：{data}')
        used, remain, detail = data
        return {
            'used': used, 'remain': remain, 'total': used + remain,
            'detail': [{'code': d.get('code'), 'name': d.get('name'),
                        'request_time': d.get('request_time')} for d in (detail or [])],
        }


def _trading_days(ctx, start: date, end: date) -> dict:
    """{date: 'WHOLE'|'MORNING'}。不消耗额度。"""
    from futu import RET_OK, Market
    ret, days = ctx.request_trading_days(
        market=Market.US, start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
    if ret != RET_OK:
        raise RuntimeError(f'获取美股交易日历失败：{days}')
    out = {}
    for d in days:
        out[pd.Timestamp(d['time']).date()] = d.get('trade_date_type', 'WHOLE')
    return out


def expected_bars(day_type: str) -> int:
    return BARS_MORNING if day_type == 'MORNING' else BARS_WHOLE


# ---------------------------------------------------------------- 本地体检

def _read_local(symbol: str) -> pd.DataFrame:
    """读该标的全部季度 parquet 的 date 列。没有则空。"""
    dq = us_quarters_dir()
    parts = sorted(dq.rglob(f'{symbol}.parquet'))
    if not parts:
        return pd.DataFrame(columns=['date'])
    frames = []
    for p in parts:
        try:
            frames.append(pd.read_parquet(p, columns=['date']))
        except Exception:
            logger.exception(f'[us] 读取失败 {p}')
    if not frames:
        return pd.DataFrame(columns=['date'])
    df = pd.concat(frames, ignore_index=True)
    df['date'] = pd.to_datetime(df['date'])
    return df


def check_local(codes, calendar_days=None) -> list:
    """逐只体检本地数据。这是本页最有价值的部分——不是只看「有没有」。

    只看最新日期是最弱的检查：A 股 600025 就是 end_date 显示最新、中间缺 11 个交易日，
    一直判为 success 没人发现。这里按交易日历逐日核对根数（全天390/半日210），
    缺一根都列出来。

    返回每只：本地起止、交易日数、总根数、缺失交易日、根数不足的日子。
    """
    out = []
    codes = [normalize(c) for c in codes if normalize(c)]
    if not codes:
        return out

    with _Quote() as ctx:
        quota = get_quota_with(ctx)
        in_quota = {d['code'] for d in quota['detail']}
        for code in codes:
            sym = to_symbol(code)
            local = _read_local(sym)
            item = {
                'code': code, 'symbol': sym,
                'has_data': bool(len(local)),
                'in_quota': code in in_quota,      # 已在额度窗口内 → 重拉免费
                'bars': int(len(local)),
                'start': None, 'end': None, 'days': 0,
                'missing_days': [], 'short_days': [], 'ok': False,
            }
            if len(local):
                d = local['date']
                item['start'] = d.min().strftime('%Y-%m-%d')
                item['end'] = d.max().strftime('%Y-%m-%d')
                per_day = d.dt.normalize().value_counts()
                item['days'] = int(len(per_day))
                # 只在本地已有数据的日期范围内核对，不把「还没下载的历史」算成缺口
                cal = _trading_days(ctx, d.min().date(), d.max().date())
                have = {k.date(): int(v) for k, v in per_day.items()}
                for day, dtype in sorted(cal.items()):
                    exp = expected_bars(dtype)
                    got = have.get(day, 0)
                    if got == 0:
                        item['missing_days'].append(day.strftime('%Y-%m-%d'))
                    elif got != exp:
                        item['short_days'].append(
                            {'date': day.strftime('%Y-%m-%d'), 'got': got,
                             'expect': exp, 'type': dtype})
                item['ok'] = not item['missing_days'] and not item['short_days']
            out.append(item)
    return out


def get_quota_with(ctx) -> dict:
    """已有连接时复用它查额度，避免为了查额度再开一条连接。"""
    from futu import RET_OK
    ret, data = ctx.get_history_kl_quota(get_detail=True)
    if ret != RET_OK:
        raise RuntimeError(f'查询额度失败：{data}')
    used, remain, detail = data
    return {'used': used, 'remain': remain, 'total': used + remain,
            'detail': [{'code': d.get('code'), 'name': d.get('name'),
                        'request_time': d.get('request_time')} for d in (detail or [])]}


# ---------------------------------------------------------------- 下载

def _fetch_all(ctx, code, start: date, end: date):
    """翻页拉满区间内的 1m。返回 DataFrame。

    必须翻到底：不翻页会在 max_count 处静默截断（实测拉一周只回 1000 根就停，
    看起来"成功"，实则少了两天）。
    """
    from futu import RET_OK, KLType, AuType
    frames, page_key, pages = [], None, 0
    while True:
        if _stop.is_set():
            break
        ret, df, page_key = ctx.request_history_kline(
            code,
            start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'),
            ktype=KLType.K_1M,
            autype=AuType.NONE,        # 不复权：增量库用前复权会因拆股整库失真
            max_count=1000,
            page_req_key=page_key,
            extended_time=False,       # 接口层排除盘前盘后
        )
        if ret != RET_OK:
            raise RuntimeError(f'{code} 拉取失败：{df}')
        if len(df):
            frames.append(df)
        pages += 1
        with _lock:
            _state['cur_pages'] = pages
            _state['cur_bars'] = sum(len(f) for f in frames)
        if not page_key:
            break
        time.sleep(0.05)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), pages


def _to_schema(df: pd.DataFrame) -> pd.DataFrame:
    """转成与 A 股 1m 完全一致的列结构，并过滤到常规时段（闭区间，含 16:00 收盘K）。"""
    d = df.copy()
    d['dt'] = pd.to_datetime(d['time_key'])
    tod = d['dt'].dt.strftime('%H:%M')
    d = d[(tod >= SESSION_START) & (tod <= SESSION_END)]
    if d.empty:
        return d
    out = pd.DataFrame({
        'date': d['dt'],
        'open': pd.to_numeric(d['open'], errors='coerce'),
        'close': pd.to_numeric(d['close'], errors='coerce'),
        'high': pd.to_numeric(d['high'], errors='coerce'),
        'low': pd.to_numeric(d['low'], errors='coerce'),
        'volume': pd.to_numeric(d['volume'], errors='coerce'),
        'money': pd.to_numeric(d.get('turnover'), errors='coerce'),
    })
    out['year'] = out['date'].dt.year
    out['month'] = out['date'].dt.month
    out['day'] = out['date'].dt.day
    out['hour'] = out['date'].dt.hour
    out['minute'] = out['date'].dt.minute
    try:
        from scripts.Others.import_minute_zips import _M1_COLS
        out = out[list(_M1_COLS)]
    except Exception:
        pass
    return out


def _write(df: pd.DataFrame, symbol: str) -> int:
    """按季度落盘，复用 A 股那套去重写入（已验证幂等：重复写新增 0）。"""
    from scripts.Others.import_minute_zips import _write_quarter_parquet
    dq = us_quarters_dir()
    added = 0
    g = df.assign(_q=((df['month'] - 1) // 3 + 1))
    for (y, q), part in g.groupby(['year', '_q']):
        out = dq / str(int(y)) / f'Q{int(q)}' / f'{symbol}.parquet'
        out.parent.mkdir(parents=True, exist_ok=True)
        added += _write_quarter_parquet(part.drop(columns=['_q']), out, False)['added']
    return added


def _run(app, codes, years):
    with app.app_context():
        try:
            end = date.today()
            start = end - timedelta(days=int(365.25 * years))
            with _Quote() as ctx:
                for code in codes:
                    if _stop.is_set():
                        with _lock:
                            _state['stopped'] = True
                        _push_log('已按请求停止')
                        break
                    sym = to_symbol(code)
                    with _lock:
                        _state['cur'] = code
                        _state['cur_pages'] = 0
                        _state['cur_bars'] = 0
                    _push_log(f'{code} 开始，回溯 {years} 年（{start} ~ {end}）')
                    try:
                        raw, pages = _fetch_all(ctx, code, start, end)
                        if raw.empty:
                            raise RuntimeError('未取到任何数据（该标的在富途无 1m 历史？）')
                        clean = _to_schema(raw)
                        n = _write(clean, sym)
                        with _lock:
                            _state['ok'] += 1
                            _state['added'] += n
                        _push_log(f'{code} 完成：{pages} 页 / {len(clean)} 根，去重后新增 {n}')
                    except Exception as e:
                        logger.exception(f'[us] {code} 下载失败')
                        with _lock:
                            _state['fail'] += 1
                            _state['failed'].append(code)
                        _push_log(f'{code} 失败：{e}')
                    finally:
                        with _lock:
                            _state['done'] += 1
        except Exception as e:
            logger.exception('[us] 下载任务异常')
            with _lock:
                _state['error'] = str(e)
            _push_log(f'任务异常：{e}')
        finally:
            with _lock:
                _state['running'] = False
                _state['cur'] = None


def start_download(app, codes, years=MAX_YEARS):
    """启动后台下载。返回 (ok, message)。"""
    codes = [normalize(c) for c in codes]
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return False, '没有可下载的股票代码'
    with _lock:
        if _state['running']:
            return False, '已有下载任务在进行中'
        _state.update({
            'running': True, 'total': len(codes), 'done': 0, 'ok': 0, 'fail': 0,
            'cur': None, 'cur_pages': 0, 'cur_bars': 0, 'added': 0,
            'failed': [], 'error': None, 'stopped': False,
            'started': time.strftime('%H:%M:%S'), 'log': [],
        })
    _stop.clear()
    threading.Thread(target=_run, args=(app, codes, years), daemon=True).start()
    return True, f'已开始下载 {len(codes)} 只美股的 1m 历史（回溯 {years} 年）'
