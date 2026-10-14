# -*- coding: utf-8 -*-
"""富途 OpenAPI 美股 1m 可行性验证（独立脚本，不影响现有 A 股流水线）。

验证四件事，任何一件不过就别往下投入：
  1. FutuOpenD 连得上、美股行情权限是实时还是延迟
  2. 历史K线额度还剩多少、拉一只扣多少
  3. 历史 1m 能回溯多久、时间戳是什么时区、一天多少根
  4. 实时 K_1M 订阅能不能收到推送（需美股盘中运行才有意义）

默认**只读**，不写任何文件；确认无误后加 --write 才落盘到 data/us/quarters/。
落盘 schema 与 A 股 1m 对齐（复用 _M1_COLS 与 _write_quarter_parquet 的去重写入），
方便以后接进现有重采样逻辑；但目录独立（data/us/），A 股那套零风险。

用法：
    python scripts/Others/futu_us_probe.py                      # 只读验证，默认 AAPL
    python scripts/Others/futu_us_probe.py --codes US.AAPL,US.NVDA
    python scripts/Others/futu_us_probe.py --days 10 --write    # 验证通过后落盘
    python scripts/Others/futu_us_probe.py --realtime 120       # 盘中订阅 120 秒看推送

前置：
    pip install futu-api
    装并启动 FutuOpenD（登录牛牛号），默认监听 127.0.0.1:11111
    https://openapi.futunn.com/futu-api-doc/intro/intro.html
"""
import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 美股常规交易时段（美东）。本次只存盘中——拉取时已用 extended_time=False 在接口层排除
# 盘前 04:00-09:30 / 盘后 16:00-20:00，这里的过滤是二次校验，用来发现接口口径与预期不符。
#
# 关键：富途 1m 的 time_key 是每根的**结束**时间，不是开始时间。实测 AAPL 一天的范围是
# 09:31 ~ 16:00（第一根覆盖 09:30-09:31 记作 09:31，最后一根覆盖 15:59-16:00 记作 16:00），
# 共 390 根。之前按「开始时间 09:30~15:59」写成右开区间，正好把 16:00 那根收盘 K 线切掉，
# 每天静默少 1 根——收盘那根恰恰最重要。所以这里是**闭区间** 09:31 ~ 16:00。
SESSION_START = '09:31'
SESSION_END = '16:00'          # 闭区间，含 16:00 这根收盘 K
EXPECTED_BARS_PER_DAY = 390


def _fail(msg):
    print(f'\n✗ {msg}')
    sys.exit(1)


def _section(title):
    print('\n' + '=' * 74)
    print(title)
    print('=' * 74)


def connect(host, port):
    try:
        from futu import OpenQuoteContext
    except ImportError:
        _fail('futu-api 未安装。先执行：pip install futu-api')
    try:
        ctx = OpenQuoteContext(host=host, port=port)
    except Exception as e:
        _fail(f'连不上 FutuOpenD（{host}:{port}）：{e}\n'
              f'  确认 FutuOpenD 已启动并已登录牛牛号。')
    return ctx


def check_quota(ctx):
    """历史K线额度：这是决定能跟多少只票的硬指标，先看清楚再动手。"""
    from futu import RET_OK
    _section('1) 历史K线额度')
    try:
        ret, data = ctx.get_history_kl_quota(get_detail=True)
    except Exception as e:
        print(f'  查询额度异常（不阻断）：{e}')
        return
    if ret != RET_OK:
        print(f'  查询额度失败（不阻断）：{data}')
        return
    # get_detail=True 时 data 是 (已用, 剩余, 明细list)；直接 print 原始元组看不出哪个是哪个
    try:
        used, remain, detail = data
        print(f'  已用 {used} / 剩余 {remain}（合计 {used + remain}）')
        if detail:
            print(f'  已占用额度的股票（窗口内重复请求同一只不重复扣）：')
            for d in detail:
                print(f'    {d.get("code")} {d.get("name", "")} '
                      f'首次请求于 {d.get("request_time")}')
        else:
            print('  尚未占用任何额度')
    except Exception:
        print(f'  {data}')


def check_market_state(ctx, codes):
    """当前是盘前/盘中/盘后/休市——决定实时订阅这一步有没有意义。"""
    from futu import RET_OK
    _section('2) 市场状态与行情权限')
    try:
        ret, data = ctx.get_market_state(codes)
        print(f'  {data}' if ret == RET_OK else f'  获取失败：{data}')
    except Exception as e:
        print(f'  获取市场状态异常（不阻断）：{e}')

    # 用快照的时间戳判断拿到的是实时还是延迟行情
    try:
        ret, snap = ctx.get_market_snapshot(codes[:1])
        if ret == RET_OK and len(snap):
            t = snap.iloc[0].get('update_time')
            print(f'  快照 update_time = {t}   ← 与当前美东时间对比，'
                  f'差 15 分钟左右说明是延迟行情，几秒内说明是实时')
    except Exception as e:
        print(f'  快照获取异常（不阻断）：{e}')


def fetch_history(ctx, code, days):
    """分页拉历史 1m。分页很重要：单次超 1000 根必须用 page_req_key 续拉。

    extended_time=False：接口层就只给常规时段，不含盘前盘后（正合本次「只存盘中」的决定）。
    autype=NONE 不复权：本地是**增量累积**的分钟库，用前复权的话每遇到一次拆股/分红，
      历史每一根的价格都会被重算，与之前落盘的对不上，等于整个库要重下。
      不复权存原始值，复权因子留到分析层再套。A 股那边 pytdx 存的也是不复权，口径一致。
    """
    from futu import RET_OK, KLType, AuType
    end = datetime.now().date()
    start = end - timedelta(days=days)
    frames, page_key, pages = [], None, 0
    while True:
        ret, df, page_key = ctx.request_history_kline(
            code,
            start=start.strftime('%Y-%m-%d'),
            end=end.strftime('%Y-%m-%d'),
            ktype=KLType.K_1M,
            autype=AuType.NONE,
            max_count=1000,
            page_req_key=page_key,
            extended_time=False,
        )
        if ret != RET_OK:
            _fail(f'{code} 拉取历史失败：{df}\n'
                  f'  常见原因：无美股行情权限 / 历史K线额度用尽 / 代码格式应为 US.AAPL')
        frames.append(df)
        pages += 1
        if not page_key:
            break
        time.sleep(0.2)          # 首页限频 60次/30秒，翻页不受限，仍留点余量
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    print(f'  {code}: {pages} 页, 共 {len(out)} 根')
    return out


def analyse(code, df):
    """报告事实，不做假设——时区/作息/每日根数都让数据自己说话。"""
    if df.empty:
        print(f'  {code}: 空结果')
        return None
    d = df.copy()
    d['dt'] = pd.to_datetime(d['time_key'])
    print(f'  时间范围: {d["dt"].min()} ~ {d["dt"].max()}')
    tod = d['dt'].dt.strftime('%H:%M')
    print(f'  每日最早/最晚一根: {tod.min()} / {tod.max()}   '
          f'← 09:31/16:00 说明是美东时间且仅盘中（time_key 为K线结束时间）；'
          f'04:01/20:00 说明含盘前盘后')

    per_day = d.groupby(d['dt'].dt.date).size()
    print(f'  覆盖交易日 {len(per_day)} 天，每日根数分布: '
          f'{dict(per_day.value_counts().sort_index().tail(5))}')

    # 只保留常规时段（闭区间：time_key 是K线结束时间，16:00 那根是收盘K，必须留）
    mask = (tod >= SESSION_START) & (tod <= SESSION_END)
    reg = d[mask]
    rper = reg.groupby(reg['dt'].dt.date).size()
    print(f'  ── 过滤到盘中 {SESSION_START}~{SESSION_END} 后: {len(reg)} 根 / {len(rper)} 天')
    bad = rper[rper != EXPECTED_BARS_PER_DAY]
    if len(bad):
        print(f'     不足/超出 {EXPECTED_BARS_PER_DAY} 根的日子（半日市或缺口）:')
        for day, n in bad.items():
            print(f'       {day}: {n} 根')
    else:
        print(f'     每天都是 {EXPECTED_BARS_PER_DAY} 根，完整')
    return reg


def to_a_share_schema(reg, code):
    """转成与 A 股 1m 相同的列结构，方便以后复用现有重采样/信号代码。"""
    out = pd.DataFrame({
        'date': reg['dt'],
        'open': pd.to_numeric(reg['open'], errors='coerce'),
        'close': pd.to_numeric(reg['close'], errors='coerce'),
        'high': pd.to_numeric(reg['high'], errors='coerce'),
        'low': pd.to_numeric(reg['low'], errors='coerce'),
        'volume': pd.to_numeric(reg['volume'], errors='coerce'),
        'money': pd.to_numeric(reg.get('turnover'), errors='coerce'),
    })
    out['year'] = out['date'].dt.year
    out['month'] = out['date'].dt.month
    out['day'] = out['date'].dt.day
    out['hour'] = out['date'].dt.hour
    out['minute'] = out['date'].dt.minute
    try:    # 有就按 A 股列序对齐，没有就用上面的顺序
        from scripts.Others.import_minute_zips import _M1_COLS
        out = out[list(_M1_COLS)]
    except Exception:
        pass
    return out


def write_local(df, code, root):
    """按季度落盘到 data/us/quarters/，复用 A 股那套去重写入。"""
    from scripts.Others.import_minute_zips import _write_quarter_parquet
    sym = code.split('.')[-1]                    # US.AAPL -> AAPL
    dq = root / 'data' / 'us' / 'quarters'
    added = 0
    g = df.assign(_q=((df['month'] - 1) // 3 + 1))
    for (y, q), part in g.groupby(['year', '_q']):
        out = dq / str(int(y)) / f'Q{int(q)}' / f'{sym}.parquet'
        out.parent.mkdir(parents=True, exist_ok=True)
        added += _write_quarter_parquet(part.drop(columns=['_q']), out, False)['added']
    print(f'  {code} -> {dq}  去重后新增 {added} 根')
    return added


def watch_realtime(ctx, codes, seconds):
    """订阅实时 1m 推送。休市时收不到任何东西，属正常。"""
    # 注意类名：10.9 里是 CurKlineHandlerBase，没有 KLineHandlerBase
    from futu import SubType, RET_OK, CurKlineHandlerBase
    _section(f'5) 实时 K_1M 订阅（{seconds} 秒）')

    got = {'n': 0}

    class OnBar(CurKlineHandlerBase):
        def on_recv_rsp(self, rsp):
            ret, df = super().on_recv_rsp(rsp)
            if ret == RET_OK and len(df):
                got['n'] += len(df)
                r = df.iloc[-1]
                print(f'  推送 {r["code"]} {r["time_key"]} '
                      f'O{r["open"]} H{r["high"]} L{r["low"]} C{r["close"]} V{r["volume"]}')
            return ret, df

    ctx.set_handler(OnBar())
    ret, msg = ctx.subscribe(codes, [SubType.K_1M])
    if ret != RET_OK:
        print(f'  订阅失败：{msg}')
        return
    print(f'  已订阅 {codes}（占用 {len(codes)} 个订阅额度），等待推送…')
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass
    finally:
        # 必须反订阅：所有连接都反订阅后额度才释放，反复起停脚本容易把额度占死
        ctx.unsubscribe(codes, [SubType.K_1M])
        print(f'  已反订阅。本次收到 {got["n"]} 根推送'
              + ('（休市期间为 0 属正常）' if got['n'] == 0 else ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=11111)
    ap.add_argument('--codes', default='US.AAPL', help='逗号分隔，如 US.AAPL,US.NVDA')
    ap.add_argument('--days', type=int, default=7, help='历史回溯自然日数')
    ap.add_argument('--write', action='store_true', help='落盘到 data/us/quarters/（默认只读）')
    ap.add_argument('--realtime', type=int, default=0, help='订阅实时推送的秒数，0=跳过')
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(',') if c.strip()]
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))

    print(f'项目根: {root}')
    print(f'标的: {codes}   回溯 {args.days} 天   '
          f'{"落盘" if args.write else "只读（加 --write 才写）"}')

    ctx = connect(args.host, args.port)
    try:
        check_quota(ctx)
        check_market_state(ctx, codes)

        _section('3) 历史 1m 拉取与体检')
        frames = {}
        for code in codes:
            raw = fetch_history(ctx, code, args.days)
            reg = analyse(code, raw)
            if reg is not None and len(reg):
                frames[code] = to_a_share_schema(reg, code)
            print()

        _section('4) 落盘')
        if not args.write:
            print('  跳过（默认只读）。确认上面时区/根数无误后，加 --write 再跑一次。')
            for code, df in frames.items():
                print(f'  将写入 {len(df)} 根 -> data/us/quarters/…/{code.split(".")[-1]}.parquet')
        else:
            for code, df in frames.items():
                write_local(df, code, root)

        if args.realtime > 0:
            watch_realtime(ctx, codes, args.realtime)
    finally:
        ctx.close()
        print('\n完成。')


if __name__ == '__main__':
    main()
