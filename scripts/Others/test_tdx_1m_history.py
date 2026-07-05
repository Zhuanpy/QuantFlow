#!/usr/bin/env python3
"""测试 TDX(pytdx) 能下载某只个股多少天的 1 分钟数据。

原理：pytdx 的 get_security_bars(8=1分钟, market, code, start_pos, count) 每次最多返回 800 根，
start_pos 越大取的越"早"。本脚本从 start_pos=0 往回翻页，直到服务器没有更早的数据为止，
再统计：总根数 / 去重后交易日数 / 时间范围 / 平均每日根数。

用法：
    python scripts/Others/test_tdx_1m_history.py                 # 默认 000001 平安银行
    python scripts/Others/test_tdx_1m_history.py 600519          # 指定代码
    python scripts/Others/test_tdx_1m_history.py 002812 --max-batches 500

不依赖项目/数据库，只需要 pytdx：直接 python 跑即可。
"""
from __future__ import annotations

import argparse
import sys
import time

# Windows 默认控制台是 GBK，下面的 ✓/❌/→ 等字符会触发 UnicodeEncodeError，统一切到 UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from pytdx.hq import TdxHq_API

# 服务器列表（与 App/codes/downloads/DlPytdx.py 一致；放这里让脚本自包含）
AVAILABLE_SERVERS = [
    ('60.191.117.167', 7709),
    ('115.238.56.198', 7709),
    ('115.238.90.165', 7709),
    ('218.108.98.244', 7709),
    ('218.108.47.69', 7709),
    ('119.147.212.81', 7709),
    ('114.80.80.72', 7709),
]

CATEGORY_1MIN = 8
BATCH = 800   # pytdx 单次上限


def get_market(code: str) -> int:
    """0=深圳, 1=上海。6/5/9 开头为沪市，其余为深市。"""
    return 1 if code.startswith(('6', '5', '9')) else 0


def connect():
    """连第一个可用服务器，返回 (api, server) 或 (None, None)。"""
    for ip, port in AVAILABLE_SERVERS:
        try:
            api = TdxHq_API()
            if api.connect(ip, port, time_out=5):
                return api, (ip, port)
        except Exception:
            continue
    return None, None


def fetch_all_1m(api, code, max_batches):
    """从最新往回翻页取全部 1m，返回 {datetime: bar} 去重字典 + 停止原因。"""
    bars = {}
    prev_min = None
    stop = 'max-batches'
    market = get_market(code)
    for i in range(max_batches):
        start = i * BATCH
        try:
            data = api.get_security_bars(CATEGORY_1MIN, market, code, start, BATCH)
        except Exception as e:
            stop = f'异常: {e}'
            break
        if not data:
            stop = 'empty(没有更多)'
            break
        for b in data:
            bars[b['datetime']] = b
        cur_min = min(b['datetime'] for b in data)
        got = len(data)
        # 进度
        print(f'  批次 {i + 1:>3} start={start:>6} 取 {got:>3} 根  最早 {cur_min}  累计 {len(bars)} 根')
        # 没有更早的数据了 → 到头
        if prev_min is not None and cur_min >= prev_min:
            stop = 'no-older(已到最早)'
            break
        prev_min = cur_min
        if got < BATCH:
            stop = 'short-batch(已到最早)'
            break
        time.sleep(0.05)
    return bars, stop


def summarize(code, server, bars, stop):
    if not bars:
        print(f'\n❌ {code} 没取到任何 1m 数据（停止原因：{stop}）')
        return
    dts = sorted(bars.keys())                 # 'YYYY-MM-DD HH:MM'
    days = sorted({d[:10] for d in dts})      # 去重交易日
    earliest, latest = dts[0], dts[-1]
    print('\n' + '=' * 60)
    print(f'代码          : {code}   市场 {"沪" if get_market(code) else "深"}   服务器 {server[0]}:{server[1]}')
    print(f'总根数(去重)  : {len(bars)}')
    print(f'交易日数      : {len(days)} 天')
    print(f'时间范围      : {earliest}  →  {latest}')
    print(f'最早/最新交易日: {days[0]}  →  {days[-1]}')
    print(f'平均每日根数  : {len(bars) / len(days):.0f}（满日约 240 根）')
    print(f'停止原因      : {stop}')
    print('=' * 60)
    print(f'\n结论：TDX 对 {code} 的 1 分钟数据大约能回溯 **{len(days)} 个交易日**。')


def main():
    ap = argparse.ArgumentParser(description='测试 TDX 能下多少天个股 1 分钟数据')
    ap.add_argument('code', nargs='?', default='000001', help='股票代码，默认 000001')
    ap.add_argument('--max-batches', type=int, default=300,
                    help='最多翻多少批(每批800根)，默认300=24万根≈1000交易日，做安全上限')
    args = ap.parse_args()

    code = args.code.strip()
    print(f'连接 TDX 服务器…')
    api, server = connect()
    if not api:
        print('❌ 无法连接任何 TDX 服务器，请检查网络')
        return
    print(f'✓ 已连接 {server[0]}:{server[1]}\n开始翻页下载 {code} 的 1 分钟数据…')
    t0 = time.time()
    try:
        bars, stop = fetch_all_1m(api, code, args.max_batches)
    finally:
        try:
            api.disconnect()
        except Exception:
            pass
    summarize(code, server, bars, stop)
    print(f'耗时 {time.time() - t0:.1f}s')


if __name__ == '__main__':
    main()
