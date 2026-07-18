"""板块（行业+概念）资金流/热度下载 —— 东方财富 push2 板块列表接口。

一次调用拿到全部行业(t:2)或概念(t:3)板块的：涨幅/主力净额/换手/涨跌家数/领涨股，
写入 mkt_sector_flow_daily（每天每板块一行）。这就是「近期热点榜」的数据源，无需逐板块拉成分。

对外：
  fetch_sector_flow(board_type) -> list[dict]
  sync_sector_flow(app, snapshot_date=None, types=('industry','concept')) -> dict 统计
"""
import time
import json as _json
from datetime import date as _date, datetime

import requests

from App.exts import db
from App.models.strategy.SectorFlowDaily import SectorFlowDaily

_EM_HOSTS = ('push2.eastmoney.com', '48.push2.eastmoney.com', '45.push2.eastmoney.com')
_EM_UAS = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
)
_TYPE_FS = {'industry': 'm:90+t:2', 'concept': 'm:90+t:3'}
# f12代码 f14名称 f3涨幅 f62主力净额 f184主力净占比 f8换手 f20总市值 f104涨家数 f105跌家数 f128领涨股 f136领涨股涨幅
_FIELDS = 'f12,f14,f3,f62,f184,f8,f20,f104,f105,f128,f136'


def _num(v):
    if v is None or v in ('', '-'):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_sector_flow(board_type: str, retries: int = 4):
    """拉一类板块(industry/concept)的列表快照。返回 list[dict]。全部失败抛 RuntimeError。"""
    fs = _TYPE_FS[board_type]
    attempts = []
    for i in range(retries):
        host = _EM_HOSTS[i % len(_EM_HOSTS)]
        ua = _EM_UAS[i % len(_EM_UAS)]
        scheme = 'http' if i % 2 == 1 else 'https'
        url = f'{scheme}://{host}/api/qt/clist/get'
        params = {
            'pn': 1, 'pz': 2000, 'po': 1, 'np': 1,
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': 2, 'invt': 2, 'fid': 'f3',   # 按涨幅降序
            'fs': fs, 'fields': _FIELDS,
        }
        headers = {'User-Agent': ua, 'Referer': 'https://quote.eastmoney.com/', 'Accept': '*/*'}
        tag = f'{scheme}://{host}'
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                attempts.append(f'{tag} HTTP {resp.status_code}')
                time.sleep(1.0 * (i + 1))
                continue
            try:
                payload = _json.loads(resp.text)
            except _json.JSONDecodeError:
                attempts.append(f'{tag} non-JSON')
                time.sleep(1.0 * (i + 1))
                continue
            diff = ((payload or {}).get('data') or {}).get('diff') or []
            out = []
            for r in diff:
                if not r.get('f12'):
                    continue
                out.append({
                    'board_type': board_type,
                    'board_code': str(r.get('f12', '')).strip(),
                    'board_name': str(r.get('f14', '')).strip(),
                    'change_pct': _num(r.get('f3')),
                    'main_net': _num(r.get('f62')),
                    'main_pct': _num(r.get('f184')),
                    'turnover_rate': _num(r.get('f8')),
                    'total_cap': _num(r.get('f20')),
                    'up_count': int(_num(r.get('f104')) or 0),
                    'down_count': int(_num(r.get('f105')) or 0),
                    'lead_stock': str(r.get('f128', '')).strip() or None,
                    'lead_pct': _num(r.get('f136')),
                })
            return out
        except requests.exceptions.RequestException as e:
            attempts.append(f'{tag} {type(e).__name__}')
            time.sleep(1.0 * (i + 1))
    raise RuntimeError(f'东财板块列表({board_type})抓取失败：' + ' | '.join(attempts))


def sync_sector_flow(app, snapshot_date=None, types=('industry', 'concept')):
    """抓取并整日替换写入 mkt_sector_flow_daily。返回统计 dict。"""
    with app.app_context():
        SectorFlowDaily.ensure_table()
        d = snapshot_date or _date.today()
        result = {'date': str(d), 'per_type': {}, 'errors': []}
        now = datetime.utcnow()
        for bt in types:
            try:
                rows = fetch_sector_flow(bt)
            except Exception as e:
                result['errors'].append(f'{bt}: {e}')
                continue
            # 整日替换该类型
            (SectorFlowDaily.query
             .filter(SectorFlowDaily.date == d, SectorFlowDaily.board_type == bt)
             .delete(synchronize_session=False))
            db.session.bulk_insert_mappings(SectorFlowDaily, [
                {**r, 'date': d, 'created_at': now} for r in rows])
            db.session.commit()
            result['per_type'][bt] = len(rows)
        return result
