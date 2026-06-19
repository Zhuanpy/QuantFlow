# -*- coding: utf-8 -*-
"""
同花顺交易记录导入服务 + 持仓推断

输入：同花顺导出的交易记录文件（TSV / Excel）
输出：写入 trade_records 表 + 可推断为 trade_plans（持仓/已平仓）

字段映射（同花顺 → TradeRecord）：
  发生时间 + 成交日期 → order_time / execute_time （20260508 09:59:56）
  证券代码           → stock_code
  证券名称           → stock_name
  操作              → trade_type （证券买入=buy, 证券卖出=sell, 其他过滤）
  发生金额           → net_amount   （带符号：买入负、卖出正）
  本次金额           → account_balance
  成交数量           → quantity     （取绝对值；方向用 trade_type 区分）
  成交均价           → price
  成交金额           → total_amount
  印花税            → tax
  其他杂费           → other_fee
  合同编号           → contract_no
  成交编号           → trade_id     （主要唯一键；理财类无此字段所以默认跳过）
  交易市场           → market
  佣金              → commission
  过户费            → transfer_fee
  备注              → remarks

去重：用 trade_id（= 同花顺成交编号）做唯一识别。已存在的跳过。
过滤：默认只导入「证券买入」「证券卖出」，跳过理财类。
"""
from __future__ import annotations

import logging
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import IO, Iterable, Optional

import pandas as pd

from App.exts import db
from App.models.trade.trade_records import TradeRecord

logger = logging.getLogger(__name__)


# 同花顺列名 → 内部 key（兼容空格、不同分隔符场景）
# 兼容两种导出：
#   1) 历史成交：有「发生时间」+「成交日期」
#   2) 当日成交：只有「成交时间」(时分秒，无日期)，日期由 default_date 补
_COL_MAP = {
    '发生时间': 'time_s',
    '成交时间': 'time_s',       # 当日成交：成交时间(HH:MM:SS)，无日期列
    '成交日期': 'date_s',
    '委托时间': 'order_time_s',  # 当日成交里的委托时间（暂不单独用，保留）
    '证券代码': 'stock_code',
    '证券名称': 'stock_name',
    '操作':     'op',
    '发生金额': 'occurred_amount',
    '本次金额': 'account_balance',
    '成交数量': 'quantity_raw',
    '成交均价': 'price',
    '成交金额': 'total_amount',
    '印花税':   'tax',
    '其他杂费': 'other_fee',
    '合同编号': 'contract_no',
    '成交编号': 'trade_no',
    '交易市场': 'market',
    '佣金':     'commission',
    '过户费':   'transfer_fee',
    '备注':     'remarks',
}

# 操作 → trade_type
_OP_TO_TYPE = {
    '证券买入': TradeRecord.TRADE_TYPE_BUY,
    '证券卖出': TradeRecord.TRADE_TYPE_SELL,
}


def _to_decimal(v, default=Decimal('0')) -> Decimal:
    if v is None:
        return default
    s = str(v).strip()
    if s == '' or s.lower() == 'nan':
        return default
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


def _to_int(v, default=0) -> int:
    if v is None:
        return default
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def _parse_datetime(date_s: str, time_s: str) -> Optional[datetime]:
    """20260508 + 09:59:56 → datetime(2026,5,8,9,59,56)"""
    date_s = str(date_s).strip() if date_s is not None else ''
    time_s = str(time_s).strip() if time_s is not None else ''
    if not date_s:
        return None
    if not time_s:
        time_s = '00:00:00'
    # date 可能是 20260508 或 2026-05-08
    date_s = date_s.replace('-', '')
    try:
        # time 可能少一位（9:59:56），补齐
        if len(time_s.split(':')[0]) == 1:
            time_s = '0' + time_s
        return datetime.strptime(f'{date_s} {time_s}', '%Y%m%d %H:%M:%S')
    except ValueError:
        try:
            return datetime.strptime(f'{date_s} {time_s.split(".")[0]}', '%Y%m%d %H:%M:%S')
        except ValueError:
            return None


def _read_file(file_storage_or_path) -> pd.DataFrame:
    """支持 .csv / .tsv / .txt（Tab 分隔）/ .xls / .xlsx。

    file_storage_or_path 可以是：
      - werkzeug FileStorage（多文件上传）
      - 已打开的 file-like
      - 文件路径字符串
    """
    # 拿到文件名（用来判类型）和字节内容
    if hasattr(file_storage_or_path, 'filename') and hasattr(file_storage_or_path, 'read'):
        fname = (file_storage_or_path.filename or '').lower()
        raw = file_storage_or_path.read()
    elif isinstance(file_storage_or_path, (str,)):
        fname = file_storage_or_path.lower()
        with open(file_storage_or_path, 'rb') as f:
            raw = f.read()
    elif hasattr(file_storage_or_path, 'read'):
        fname = getattr(file_storage_or_path, 'name', '').lower()
        raw = file_storage_or_path.read()
    else:
        raise ValueError('不支持的文件输入类型')

    if fname.endswith(('.xlsx', '.xls')):
        # 同花顺导出的 .xls 实际上常常是 HTML / TSV 包装，pandas 一般能识别
        try:
            return pd.read_excel(io.BytesIO(raw), dtype=str)
        except Exception:
            # 退化到当 TSV 读
            text = raw.decode('gbk', errors='replace')
            return pd.read_csv(io.StringIO(text), sep='\t', dtype=str)

    # 纯文本：先 utf-8，否则 gbk
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030', 'gb2312'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode('utf-8', errors='replace')

    # 检测分隔符：Tab > 逗号
    first_line = text.splitlines()[0] if text else ''
    sep = '\t' if '\t' in first_line else ','
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str)


def parse_thsexport(file_input) -> list[dict]:
    """读同花顺导出文件，做列名标准化 + 类型转换。

    Returns:
        list of dict, 每个 dict 是一行原始解析后的内部字段（未过滤、未去重）
    """
    df = _read_file(file_input)
    df.columns = [str(c).strip() for c in df.columns]

    # 列名标准化
    rename = {k: v for k, v in _COL_MAP.items() if k in df.columns}
    df = df.rename(columns=rename)

    rows = df.to_dict('records')
    return rows


def _row_to_trade_record(row: dict, default_date: str = None) -> Optional[TradeRecord]:
    """单行 → TradeRecord（未 commit）。返回 None 表示跳过（理财/无效）。

    default_date: 'YYYY-MM-DD'，当行内没有「成交日期」列时用它补日期（当日成交场景）。
    """
    op = (row.get('op') or '').strip()
    trade_type = _OP_TO_TYPE.get(op)
    if trade_type is None:
        return None  # 跳过理财类、未知操作

    trade_no = (row.get('trade_no') or '').strip()
    if not trade_no:
        return None  # 没有成交编号的（理财类）跳过

    stock_code = (row.get('stock_code') or '').strip().zfill(6)
    stock_name = (row.get('stock_name') or '').strip()
    if not stock_code:
        return None

    # 日期：优先行内「成交日期」，缺失则用 default_date（当日成交无日期列）
    date_s = (str(row.get('date_s')).strip() if row.get('date_s') is not None else '')
    if not date_s or date_s.lower() == 'nan':
        date_s = default_date or ''
    dt = _parse_datetime(date_s, row.get('time_s'))
    if dt is None:
        return None

    quantity = abs(_to_int(row.get('quantity_raw'), 0))  # 取正
    price = _to_decimal(row.get('price'))
    total_amount = _to_decimal(row.get('total_amount'))
    commission = _to_decimal(row.get('commission'))
    tax = _to_decimal(row.get('tax'))
    other_fee = _to_decimal(row.get('other_fee'))
    transfer_fee = _to_decimal(row.get('transfer_fee'))
    net_amount = _to_decimal(row.get('occurred_amount'))  # 同花顺"发生金额"（带符号）
    # 当日成交没有「发生金额」列 → net_amount 会是 0。用成交金额 + 方向推导（忽略费用）：
    #   买入现金流出(负)、卖出现金流入(正)。保证下游 PnL 统计大致正确。
    if net_amount == 0 and total_amount != 0:
        net_amount = -total_amount if trade_type == TradeRecord.TRADE_TYPE_BUY else total_amount
    account_balance = _to_decimal(row.get('account_balance'), default=None)
    contract_no = (row.get('contract_no') or '').strip() or None
    market = (row.get('market') or '').strip() or None
    remarks = (row.get('remarks') or '').strip() or None

    return TradeRecord(
        trade_id=trade_no,                    # 用同花顺成交编号作为业务唯一键
        stock_code=stock_code,
        stock_name=stock_name,
        trade_type=trade_type,
        trade_mode=TradeRecord.MODE_REAL,     # 同花顺导出的都是实盘成交
        status=TradeRecord.STATUS_EXECUTED,   # 导入的都是已成交
        quantity=quantity,
        price=price,
        total_amount=total_amount,
        commission=commission,
        tax=tax,
        other_fee=other_fee,
        transfer_fee=transfer_fee,
        net_amount=net_amount,
        account_balance=account_balance,
        contract_no=contract_no,
        market=market,
        order_time=dt,
        execute_time=dt,
        remarks=remarks,
    )


def parse_thstext(text: str) -> list[dict]:
    """解析直接粘贴的同花顺成交文本（Tab 或多空格分隔）→ 标准化行 dict。

    适配「当日成交」复制出来的表格：列名行 + 多行数据，列之间 Tab 或连续空格。
    A 股证券名称无内部空格，所以空格分隔也安全。
    """
    lines = [ln for ln in (text or '').splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    first = lines[0]
    # 有 Tab 用 Tab；否则按连续空白分列（engine=python 才支持正则 sep）
    sep = '\t' if '\t' in first else r'\s+'
    try:
        df = pd.read_csv(io.StringIO('\n'.join(lines)), sep=sep, dtype=str, engine='python')
    except Exception as e:
        logger.warning(f'解析粘贴文本失败: {e}')
        return []
    df.columns = [str(c).strip() for c in df.columns]
    rename = {k: v for k, v in _COL_MAP.items() if k in df.columns}
    df = df.rename(columns=rename)
    return df.to_dict('records')


def import_from_text(text: str, dry_run: bool = False, default_date: str = None,
                     auto_infer: bool = True, infer_trade_mode: str = 'real') -> dict:
    """从粘贴的当日成交文本导入。default_date 缺省取今天。"""
    if not default_date:
        default_date = datetime.now().strftime('%Y-%m-%d')
    rows = parse_thstext(text)
    return _import_rows(rows, dry_run=dry_run, default_date=default_date,
                        auto_infer=auto_infer, infer_trade_mode=infer_trade_mode)


def import_from_ths(file_input, dry_run: bool = False,
                    auto_infer: bool = True, infer_trade_mode: str = 'real',
                    default_date: str = None) -> dict:
    """主入口：解析同花顺文件 → 写入 trade_records。

    Args:
        file_input: 文件上传对象 / 文件路径 / file-like
        dry_run: True 则只解析返回统计，不写库
        auto_infer: 导入成功且确实新增了记录时，自动重新推断该 mode 下的全部持仓。
                   这样新成交立刻反映到 trade_plans 的「持仓中/已完成」状态。
        infer_trade_mode: 自动推断时按哪个 mode 过滤源记录（同花顺导入固定 'real'）
        default_date: 文件里没有「成交日期」列时用它补（当日成交文件场景），格式 YYYY-MM-DD

    Returns:
        见 _import_rows 的返回结构
    """
    rows = parse_thsexport(file_input)
    return _import_rows(rows, dry_run=dry_run, default_date=default_date,
                        auto_infer=auto_infer, infer_trade_mode=infer_trade_mode)


def _import_rows(rows: list[dict], dry_run: bool = False, default_date: str = None,
                 auto_infer: bool = True, infer_trade_mode: str = 'real') -> dict:
    """把标准化后的行写入 trade_records（file / text 两条入口共用）。

    Returns:
        dict: {
            'parsed': N,        # 解析的总行数
            'skipped_fund': N,  # 跳过的理财类
            'skipped_invalid': N,  # 跳过的无效行（缺关键字段）
            'skipped_duplicate': N,  # 因 trade_id 已存在跳过
            'inserted': N,
            'samples': [...3 条预览],
            'infer': {...}      # 若 auto_infer 触发，包含推断结果摘要
        }
    """
    stats = {
        'parsed': len(rows),
        'skipped_fund': 0,
        'skipped_invalid': 0,
        'skipped_duplicate': 0,
        'inserted': 0,
        'samples': [],
    }

    new_records = []
    seen_trade_ids = set()

    for r in rows:
        op = (r.get('op') or '').strip()
        if '理财' in op:
            stats['skipped_fund'] += 1
            continue
        rec = _row_to_trade_record(r, default_date=default_date)
        if rec is None:
            stats['skipped_invalid'] += 1
            continue
        if rec.trade_id in seen_trade_ids:
            stats['skipped_duplicate'] += 1
            continue
        seen_trade_ids.add(rec.trade_id)
        new_records.append(rec)

    # 去除 DB 里已存在的 trade_id
    if new_records:
        existing = {
            r[0] for r in db.session.query(TradeRecord.trade_id)
            .filter(TradeRecord.trade_id.in_(seen_trade_ids)).all()
        }
        before = len(new_records)
        new_records = [r for r in new_records if r.trade_id not in existing]
        stats['skipped_duplicate'] += before - len(new_records)

    # 预览前 3 条
    stats['samples'] = [
        {
            'trade_id': r.trade_id,
            'stock_code': r.stock_code,
            'stock_name': r.stock_name,
            'trade_type': r.trade_type,
            'quantity': r.quantity,
            'price': float(r.price) if r.price else None,
            'total_amount': float(r.total_amount) if r.total_amount else None,
            'net_amount': float(r.net_amount) if r.net_amount else None,
            'execute_time': r.execute_time.isoformat() if r.execute_time else None,
        } for r in new_records[:3]
    ]

    if not dry_run and new_records:
        try:
            db.session.bulk_save_objects(new_records)
            db.session.commit()
            stats['inserted'] = len(new_records)
            logger.info(f'交易记录导入完成: {stats}')
        except Exception as e:
            db.session.rollback()
            logger.exception('导入失败')
            stats['error'] = str(e)
    else:
        stats['inserted'] = 0 if dry_run else len(new_records)

    # 导入成功并且确实新增了记录，自动重新推断该 mode 下的全部持仓
    # （持仓周期需要完整历史，所以每次都按 mode 全量重算）
    if auto_infer and not dry_run and stats.get('inserted', 0) > 0 and 'error' not in stats:
        try:
            infer_res = infer_plans_from_records(trade_mode=infer_trade_mode, dry_run=False)
            stats['infer'] = {
                'active_count': len(infer_res.get('active_cycles', [])),
                'completed_count': len(infer_res.get('completed_cycles', [])),
                'anomaly_count': len(infer_res.get('anomaly_stocks', [])),
                'inserted_or_updated': infer_res.get('inserted_or_updated', 0),
                'deleted_old': infer_res.get('deleted_old', 0),
                'cleared_manual_sltp': infer_res.get('cleared_manual_sltp', []),
            }
            logger.info(f'自动推断完成: {stats["infer"]}')
        except Exception as e:
            logger.exception('自动推断失败（不影响导入）')
            stats['infer'] = {'error': str(e)}

    return stats


# ============ 从 trade_records 推断持仓 → trade_plans ============

INFER_PLAN_ID_PREFIX = 'INFER-'   # 推断出的 plan 用这个前缀做 upsert 键
INFER_PLAN_ID_FMT = INFER_PLAN_ID_PREFIX + '{code}'  # 同一只股票只保留一个推断 plan


def _aggregate_records_by_stock(records):
    """按 stock_code 分组、按时间排序，返回 {code: {recs, buys, sells, ...}}"""
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in records:
        grouped[r.stock_code].append(r)

    out = {}
    for code, recs in grouped.items():
        recs.sort(key=lambda r: r.execute_time or datetime.min)
        buys = [r for r in recs if r.trade_type == TradeRecord.TRADE_TYPE_BUY]
        sells = [r for r in recs if r.trade_type == TradeRecord.TRADE_TYPE_SELL]
        bought_qty = sum(r.quantity or 0 for r in buys)
        sold_qty = sum(r.quantity or 0 for r in sells)
        out[code] = {
            'recs': recs,
            'buys': buys,
            'sells': sells,
            'bought_qty': bought_qty,
            'sold_qty': sold_qty,
            'net_position': bought_qty - sold_qty,
        }
    return out


def _weighted_avg_price(recs):
    """按数量加权的平均价格"""
    total_q = sum(r.quantity or 0 for r in recs)
    if total_q == 0:
        return None
    total_v = sum(float(r.price or 0) * (r.quantity or 0) for r in recs)
    return round(total_v / total_q, 4)


def _decimal_sum(values):
    """安全的 Decimal 求和，None 当 0 处理"""
    total = Decimal('0')
    for v in values:
        if v is not None:
            total += Decimal(str(v))
    return total


def _split_into_cycles(recs):
    """把单只股票的成交记录按"持仓周期"切分。

    一个周期定义：持仓从 0 → 正 → 0（完整开平仓）。
    如果最后还在持仓（位置 > 0），最后一个周期是 active（未平仓）。
    如果出现位置为负（卖多于买），跳过该笔卖出并记入 anomaly。

    Args:
        recs: 已按 execute_time 升序排好的 TradeRecord 列表

    Returns:
        (cycles, anomaly_count)
        cycles: list of dict {records, status, ...}, status='completed'|'active'
        anomaly_count: 因数据不全导致被跳过的卖出笔数（你最早的买入记录可能在更老的导出里）
    """
    cycles = []
    current = None       # 当前正在累积的周期
    position = 0
    anomaly_count = 0

    for r in recs:
        if r.trade_type == TradeRecord.TRADE_TYPE_BUY:
            if position == 0:
                # 开新周期
                current = {'records': [], 'status': 'active'}
                cycles.append(current)
            if current is None:
                current = {'records': [], 'status': 'active'}
                cycles.append(current)
            current['records'].append(r)
            position += (r.quantity or 0)
        elif r.trade_type == TradeRecord.TRADE_TYPE_SELL:
            if position <= 0 or current is None:
                # 没开仓就卖：缺历史买入数据，跳过
                anomaly_count += 1
                continue
            current['records'].append(r)
            position -= (r.quantity or 0)
            if position <= 0:
                # 平仓完成
                current['status'] = 'completed'
                current = None
                position = max(position, 0)  # 防御性
    return cycles, anomaly_count


def _summarize_cycle(cycle, stock_code, stock_name):
    """把一个 cycle 的 records 汇总成可读字段。"""
    recs = cycle['records']
    buys = [r for r in recs if r.trade_type == TradeRecord.TRADE_TYPE_BUY]
    sells = [r for r in recs if r.trade_type == TradeRecord.TRADE_TYPE_SELL]
    bought_qty = sum(r.quantity or 0 for r in buys)
    sold_qty = sum(r.quantity or 0 for r in sells)

    avg_entry = _weighted_avg_price(buys)
    avg_exit = _weighted_avg_price(sells) if sells else None

    first_buy = buys[0] if buys else None
    last_sell = sells[-1] if sells else None
    entry_time = first_buy.execute_time if first_buy else None
    exit_time = last_sell.execute_time if last_sell else None

    # 投入本金 = 所有买入的成交金额（不含费用）
    cost_basis = sum(float(r.total_amount or 0) for r in buys)

    # 盈亏 = 所有 net_amount 之和（已含费用：买入为负、卖出为正）
    pnl = _decimal_sum(r.net_amount for r in recs)
    pnl_f = float(pnl)
    pnl_pct = round(pnl_f / cost_basis * 100, 2) if cost_basis else None

    # 持仓时长（天）
    hold_days = None
    if entry_time and exit_time:
        hold_days = (exit_time.date() - entry_time.date()).days
    elif entry_time and cycle['status'] == 'active':
        from datetime import date as _date
        hold_days = (_date.today() - entry_time.date()).days

    commission_sum = _decimal_sum(r.commission for r in recs)
    tax_sum = _decimal_sum(r.tax for r in recs)
    transfer_fee_sum = _decimal_sum(r.transfer_fee for r in recs)

    if cycle['status'] == 'completed':
        if pnl_f > 0:
            result_val = 'win'
        elif pnl_f < 0:
            result_val = 'loss'
        else:
            result_val = 'break_even'
    else:
        result_val = 'pending'

    return {
        'stock_code': stock_code,
        'stock_name': stock_name,
        'status': cycle['status'],
        'bought_qty': bought_qty,
        'sold_qty': sold_qty,
        'remaining': bought_qty - sold_qty,
        'avg_entry_price': avg_entry,
        'avg_exit_price': avg_exit,
        'entry_time': entry_time,
        'exit_time': exit_time,
        'hold_days': hold_days,
        'cost_basis': round(cost_basis, 2),
        'profit_loss': round(pnl_f, 2),
        'profit_loss_pct': pnl_pct,
        'result': result_val,
        'commission': float(commission_sum),
        'tax': float(tax_sum),
        'transfer_fee': float(transfer_fee_sum),
    }


def infer_plans_from_records(trade_mode: str = 'real',
                              stock_code: str = None,
                              dry_run: bool = False,
                              wipe_old: bool = True) -> dict:
    """从 trade_records 按"持仓周期"推断 trade_plans。

    周期定义：持仓从 0 → 正 → 0（一次完整开平仓）。同一只股票多次开平仓 = 多个 plan。
    Upsert 键：plan_id = INFER-<code>-<entry_date>（用入场日期保持唯一且可重入）

    Args:
        trade_mode: 推断出的 plan 标记为哪种模式（默认 real）
        stock_code: 只推断指定股票（None 则全表）
        dry_run: True 不写库，只返回会做什么
        wipe_old: True 写库前先删掉所有 INFER- 开头的旧 plan（避免周期口径变更产生孤儿）

    Returns:
        dict: 见 result 结构
    """
    from App.models.trade.trade_plan import TradePlan

    q = TradeRecord.query
    if trade_mode:
        q = q.filter(TradeRecord.trade_mode == trade_mode)
    if stock_code:
        q = q.filter(TradeRecord.stock_code == stock_code)
    records = q.all()

    grouped = _aggregate_records_by_stock(records)

    result = {
        'completed_cycles': [],
        'active_cycles': [],
        'anomaly_stocks': [],  # 出现"卖出在前、没买入历史"的股票
        'inserted_or_updated': 0,
        'deleted_old': 0,
        'cleared_manual_sltp': [],  # 净持仓归 0 后被清空止损止盈的 MANUAL-<code>
    }

    cycles_for_db = []  # (plan_id, summary)

    for code, g in grouped.items():
        recs = g['recs']
        if not recs:
            continue
        stock_name = recs[0].stock_name

        cycles, anomaly = _split_into_cycles(recs)
        if anomaly > 0:
            result['anomaly_stocks'].append({
                'stock_code': code, 'stock_name': stock_name,
                'orphan_sell_count': anomaly,
            })

        for cyc in cycles:
            summary = _summarize_cycle(cyc, code, stock_name)
            if summary['status'] == 'completed':
                result['completed_cycles'].append(summary)
            else:
                result['active_cycles'].append(summary)

            # 生成稳定 plan_id：入场日期 8 位
            entry_d = summary['entry_time']
            if entry_d is None:
                continue
            entry_str = entry_d.strftime('%Y%m%d')
            plan_id = f'INFER-{code}-{entry_str}'
            cycles_for_db.append((plan_id, summary))

    if dry_run:
        return result

    try:
        if wipe_old:
            n = TradePlan.query.filter(TradePlan.plan_id.like('INFER-%')).delete(synchronize_session=False)
            result['deleted_old'] = int(n or 0)
            db.session.flush()

        for plan_id, s in cycles_for_db:
            existing = TradePlan.query.filter_by(plan_id=plan_id).first()
            tres = (s['result'] if s['status'] == 'completed' else 'pending')
            note_lines = [
                f"从 trade_records 推断（周期化）",
                f"买入 {s['bought_qty']} 股 @均价 {s['avg_entry_price']}",
            ]
            if s['status'] == 'completed':
                note_lines.append(f"卖出 {s['sold_qty']} 股 @均价 {s['avg_exit_price']}")
                note_lines.append(f"盈亏 {s['profit_loss']:+.2f}（{s['profit_loss_pct']:+.2f}%）")
                if s['hold_days'] is not None:
                    note_lines.append(f"持仓 {s['hold_days']} 天")
            elif s['hold_days'] is not None:
                note_lines.append(f"已持仓 {s['hold_days']} 天")

            _upsert_plan(
                existing_plan=existing,
                plan_id=plan_id,
                stock_code=s['stock_code'],
                stock_name=s['stock_name'],
                trade_mode=trade_mode,
                direction=TradePlan.DIRECTION_LONG,
                status=(TradePlan.STATUS_COMPLETED if s['status'] == 'completed'
                        else TradePlan.STATUS_ACTIVE),
                entry_price=s['avg_entry_price'],
                actual_entry_price=s['avg_entry_price'],
                actual_exit_price=s['avg_exit_price'] if s['status'] == 'completed' else None,
                quantity=s['bought_qty'],
                actual_quantity=s['bought_qty'],
                entry_time=s['entry_time'],
                exit_time=s['exit_time'] if s['status'] == 'completed' else None,
                profit_loss=Decimal(str(s['profit_loss'])) if s['status'] == 'completed' else None,
                profit_loss_pct=s['profit_loss_pct'] if s['status'] == 'completed' else None,
                result_value=tres if s['status'] == 'completed' else None,
                commission=Decimal(str(s['commission'])),
                tax=Decimal(str(s['tax'])),
                notes='\n'.join(note_lines),
            )
            result['inserted_or_updated'] += 1

        # 净持仓归 0（完全平仓）的票，清空对应 MANUAL-<code> 的止损/止盈。
        # MANUAL-<code> 是 15m 详情页止损止盈的存储容器（不随推断 wipe），
        # 平仓后旧止损止盈已失效，留着会在下次买回时误用旧值。
        # 注意：只处理本次 records 里出现过、且净持仓<=0 的票；从没成交过的
        # 待买入票（无 records）不在 grouped 内，其手动止损止盈保留不动。
        closed_codes = [code for code, g in grouped.items() if g['net_position'] <= 0]
        if closed_codes:
            stale_manuals = TradePlan.query.filter(
                TradePlan.plan_id.in_([f'MANUAL-{c}' for c in closed_codes])
            ).all()
            for m in stale_manuals:
                if m.stop_loss_price is None and m.take_profit_price is None:
                    continue  # 本就没设，跳过
                m.stop_loss_price = None
                m.take_profit_price = None
                m.updated_at = datetime.utcnow()
                result['cleared_manual_sltp'].append(m.stock_code)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('推断 plan 写库失败')
        result['error'] = str(e)

    return result


def calculate_trade_statistics(trade_mode: str = 'real') -> dict:
    """从 trade_records 周期化后算历史交易统计：胜率、盈亏比、平均盈利/亏损、最大回撤等。

    只统计**已完成的周期**（持仓从 0 → 正 → 0）。
    """
    q = TradeRecord.query
    if trade_mode:
        q = q.filter(TradeRecord.trade_mode == trade_mode)
    records = q.all()
    grouped = _aggregate_records_by_stock(records)

    completed_pnls = []  # list of (stock_code, profit_loss, profit_loss_pct, cost_basis)
    for code, g in grouped.items():
        cycles, _ = _split_into_cycles(g['recs'])
        for cyc in cycles:
            if cyc['status'] != 'completed':
                continue
            s = _summarize_cycle(cyc, code, g['recs'][0].stock_name)
            completed_pnls.append(s)

    if not completed_pnls:
        return {
            'total_trades': 0, 'win_count': 0, 'loss_count': 0, 'break_even_count': 0,
            'win_rate': 0, 'total_profit_loss': 0,
            'total_invested': 0, 'roi_pct': 0,
            'avg_profit': 0, 'avg_loss': 0,
            'max_profit': 0, 'max_loss': 0,
            'profit_factor': 0, 'avg_hold_days': 0,
            'recent_trades': [],
        }

    wins = [s for s in completed_pnls if s['result'] == 'win']
    losses = [s for s in completed_pnls if s['result'] == 'loss']
    breakevens = [s for s in completed_pnls if s['result'] == 'break_even']

    total_invested = sum(s['cost_basis'] for s in completed_pnls)
    total_pnl = sum(s['profit_loss'] for s in completed_pnls)
    total_win_amount = sum(s['profit_loss'] for s in wins)
    total_loss_amount = abs(sum(s['profit_loss'] for s in losses))

    hold_days_values = [s['hold_days'] for s in completed_pnls if s['hold_days'] is not None]

    # 按平仓年份分组
    by_year = {}
    for s in completed_pnls:
        if not s['exit_time']:
            continue
        y = s['exit_time'].year
        by_year.setdefault(y, []).append(s)

    by_year_summary = []
    for y in sorted(by_year.keys()):
        items = by_year[y]
        wins_y = [c for c in items if c['result'] == 'win']
        losses_y = [c for c in items if c['result'] == 'loss']
        pnl_y = sum(c['profit_loss'] for c in items)
        invested_y = sum(c['cost_basis'] for c in items)
        win_amt = sum(c['profit_loss'] for c in wins_y) if wins_y else 0
        loss_amt = abs(sum(c['profit_loss'] for c in losses_y)) if losses_y else 0
        by_year_summary.append({
            'year': y,
            'total_trades': len(items),
            'win_count': len(wins_y),
            'loss_count': len(losses_y),
            'win_rate': round(len(wins_y) / len(items) * 100, 2),
            'total_invested': round(invested_y, 2),
            'total_profit_loss': round(pnl_y, 2),
            'roi_pct': round(pnl_y / invested_y * 100, 2) if invested_y else 0,
            'avg_profit': round(win_amt / len(wins_y), 2) if wins_y else 0,
            'avg_loss': round(loss_amt / len(losses_y), 2) if losses_y else 0,
        })

    # 全部已完成交易（按 exit_time 倒序）
    all_completed = sorted(completed_pnls, key=lambda s: s['exit_time'] or s['entry_time'], reverse=True)

    return {
        'total_trades': len(completed_pnls),
        'win_count': len(wins),
        'loss_count': len(losses),
        'break_even_count': len(breakevens),
        'win_rate': round(len(wins) / len(completed_pnls) * 100, 2),
        'total_profit_loss': round(total_pnl, 2),
        'total_invested': round(total_invested, 2),
        'roi_pct': round(total_pnl / total_invested * 100, 2) if total_invested else 0,
        'avg_profit': round(total_win_amount / len(wins), 2) if wins else 0,
        'avg_loss': round(total_loss_amount / len(losses), 2) if losses else 0,
        'max_profit': round(max(s['profit_loss'] for s in wins), 2) if wins else 0,
        'max_loss': round(min(s['profit_loss'] for s in losses), 2) if losses else 0,
        # 盈亏比 = 平均盈利 / 平均亏损
        'profit_factor': round((total_win_amount / len(wins)) / (total_loss_amount / len(losses)), 2)
                          if wins and losses and total_loss_amount > 0 else 0,
        'avg_hold_days': round(sum(hold_days_values) / len(hold_days_values), 1) if hold_days_values else 0,
        'by_year': by_year_summary,
        'all_completed': [
            {
                'stock_code': s['stock_code'],
                'stock_name': s['stock_name'],
                'entry_time': s['entry_time'].isoformat() if s['entry_time'] else None,
                'exit_time': s['exit_time'].isoformat() if s['exit_time'] else None,
                'hold_days': s['hold_days'],
                'avg_entry_price': s['avg_entry_price'],
                'avg_exit_price': s['avg_exit_price'],
                'cost_basis': s['cost_basis'],
                'profit_loss': s['profit_loss'],
                'profit_loss_pct': s['profit_loss_pct'],
                'result': s['result'],
            } for s in all_completed
        ],
    }


def _upsert_plan(*, existing_plan, plan_id, stock_code, stock_name,
                 trade_mode, direction, status,
                 entry_price, actual_entry_price=None, actual_exit_price=None,
                 quantity, actual_quantity=None,
                 entry_time=None, exit_time=None,
                 profit_loss=None, profit_loss_pct=None, result_value=None,
                 commission=None, tax=None, notes=None):
    """插入或更新一个推断出来的 TradePlan。已存在则覆盖关键字段。"""
    from App.models.trade.trade_plan import TradePlan
    if existing_plan is None:
        plan = TradePlan(plan_id=plan_id)
        db.session.add(plan)
    else:
        plan = existing_plan

    plan.stock_code = stock_code
    plan.stock_name = stock_name
    plan.trade_mode = trade_mode
    plan.direction = direction
    plan.status = status
    plan.entry_price = entry_price
    plan.actual_entry_price = actual_entry_price
    if actual_exit_price is not None:
        plan.actual_exit_price = actual_exit_price
    plan.quantity = quantity
    plan.actual_quantity = actual_quantity
    if entry_time is not None:
        plan.entry_time = entry_time
    if exit_time is not None:
        plan.exit_time = exit_time
    if profit_loss is not None:
        plan.profit_loss = profit_loss
    if profit_loss_pct is not None:
        plan.profit_loss_pct = profit_loss_pct
    if result_value is not None:
        plan.result = result_value
    if commission is not None:
        plan.commission = commission
    if tax is not None:
        plan.tax = tax
    if notes is not None:
        plan.notes = notes
    plan.updated_at = datetime.utcnow()
