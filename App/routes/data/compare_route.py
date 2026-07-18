# -*- coding: utf-8 -*-
"""多标的 K 线对比 / 相关性分析

页面：
    GET  /compare                          对比分析页

API：
    GET  /compare/api/series               多标的对齐收盘序列 + 收益率相关矩阵
        query:
          codes:  逗号分隔的标的代码/名称（个股 / 板块 BKxxxx / 合成板块 BKxxxxS 混选）
          period: 15m / 30m / 60m / daily（默认 daily）
          start:  起始日 YYYY-MM-DD（可选）
          end:    结束日 YYYY-MM-DD（可选）

设计说明：
  - 所有标的（个股 / 板块 / 合成指数）都以 code 走同一套数据源：
      * 日K   优先 data_stock_daily（历史更长），空则回退 15m→daily 重采样；
      * 分钟   统一读 data/15m/<code>.parquet 再 resample 到目标周期。
  - 对齐：按公共时间戳做内连接（只保留所有标的都有数据的 bar）。
  - 归一化：每条序列除以对齐区间首值 ×100，消除价格量级差异，肉眼可比走势。
  - 相关性：按「对数收益率」算 Pearson 相关（而非直接拿价格），
    避免同处上行趋势的标的因共同漂移造成的虚高相关。
"""
import logging

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, render_template, request

from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.utils.timeframe_resample import resample_15m, SUPPORTED_PERIODS
from App.models.data.StockDaily import get_daily_stock_data

logger = logging.getLogger(__name__)

compare_bp = Blueprint('compare_bp', __name__, url_prefix='/compare')

MAX_TARGETS = 8


# ==================== 页面 ====================
@compare_bp.route('/')
def compare_page():
    return render_template('data/compare.html', title='多标的对比分析')


# ==================== 工具 ====================
def _resolve_code(raw: str) -> str:
    """把用户输入规整成代码。含中文时反查个股(StockInfo)/板块(Board)名称。"""
    raw = (raw or '').strip()
    if not raw or not any('一' <= ch <= '鿿' for ch in raw):
        return raw
    try:
        from App.models.data.basic_info import StockInfo
        rows = StockInfo.query.filter_by(name=raw).all()
        for r in rows:
            mc = (r.MarketCode or '').lower()
            if mc.startswith(('sz', 'sh', 'bj')) and r.code:
                return r.code
        if rows and rows[0].code:
            return rows[0].code
    except Exception:
        logger.debug('按名称反查个股代码失败: %s', raw, exc_info=True)
    try:
        from App.models.evaluation.Board import Board
        b = Board.query.filter_by(board_name=raw).first()
        if b and b.board_code:
            return b.board_code
    except Exception:
        logger.debug('按名称反查板块代码失败: %s', raw, exc_info=True)
    return raw


def _resolve_name(code: str) -> str:
    """给 code 找个展示名（个股名 / 板块名 / 合成指数标注）。找不到就回代码本身。"""
    if not code:
        return code
    # 合成板块 BKxxxxS → 用底层板块名 + (合成)
    if code.upper().startswith('BK') and code.upper().endswith('S'):
        base = code[:-1]
        try:
            from App.models.evaluation.Board import Board
            b = Board.query.filter_by(board_code=base).first()
            if b and b.board_name:
                return f'{b.board_name}(合成)'
        except Exception:
            pass
        return f'{code}(合成)'
    if code.upper().startswith('BK'):
        try:
            from App.models.evaluation.Board import Board
            b = Board.query.filter_by(board_code=code).first()
            if b and b.board_name:
                return b.board_name
        except Exception:
            pass
        return code
    try:
        from App.models.data.basic_info import StockInfo
        s = StockInfo.query.filter_by(code=code).first()
        if s and s.name:
            return s.name
    except Exception:
        pass
    return code


def _load_close(code: str, period: str, start: str, end: str) -> pd.DataFrame:
    """加载单个标的的收盘序列，返回列 [date, close]（date 为 datetime），失败返回空。"""
    if period == 'daily':
        df = get_daily_stock_data(code, start or None, end or None)
        if df is not None and not df.empty and 'close' in df.columns:
            out = df[['date', 'close']].copy()
            out['date'] = pd.to_datetime(out['date'])
            out = out.dropna(subset=['close']).sort_values('date')
            if not out.empty:
                return out.reset_index(drop=True)
        # 回退：15m→daily 重采样（覆盖 data_stock_daily 里没有的标的，如合成指数）
        base = StockData15m.load_15m(code)
        if base is None or base.empty:
            return pd.DataFrame()
        df = resample_15m(base, 'daily')
    else:
        base = StockData15m.load_15m(code)
        if base is None or base.empty:
            return pd.DataFrame()
        df = resample_15m(base, period)

    if df is None or df.empty or 'close' not in df.columns:
        return pd.DataFrame()
    out = df[['date', 'close']].copy()
    out['date'] = pd.to_datetime(out['date'])
    if start:
        out = out[out['date'] >= pd.to_datetime(start)]
    if end:
        # end 当天含全天
        out = out[out['date'] <= pd.to_datetime(end) + pd.Timedelta(hours=23, minutes=59)]
    out = out.dropna(subset=['close']).sort_values('date')
    return out.reset_index(drop=True)


# ==================== API ====================
@compare_bp.route('/api/series')
def api_series():
    try:
        period = (request.args.get('period') or 'daily').strip().lower()
        if period not in SUPPORTED_PERIODS:
            period = 'daily'
        start = (request.args.get('start') or '').strip()
        end = (request.args.get('end') or '').strip()

        raw_codes = [c.strip() for c in (request.args.get('codes') or '').split(',') if c.strip()]
        # 去重保序 + 数量上限
        seen, codes = set(), []
        for c in raw_codes:
            rc = _resolve_code(c)
            if rc and rc not in seen:
                seen.add(rc)
                codes.append(rc)
        codes = codes[:MAX_TARGETS]
        if len(codes) < 2:
            return jsonify({'success': False, 'message': '请至少选择 2 个标的'}), 400

        warnings = []
        frames = {}
        for code in codes:
            df = _load_close(code, period, start, end)
            if df.empty or len(df) < 2:
                warnings.append(f'{code} 无足够数据（{period}），已跳过')
                continue
            frames[code] = df.set_index('date')['close']

        if len(frames) < 2:
            return jsonify({'success': False,
                            'message': '有效标的不足 2 个，无法对比',
                            'warnings': warnings}), 400

        # 对齐：内连接（只保留所有标的共有的时间戳）
        merged = pd.concat(frames, axis=1, join='inner').dropna()
        merged.columns = list(frames.keys())
        if len(merged) < 2:
            return jsonify({'success': False,
                            'message': '各标的公共交易时段不足，无法对齐（试试放宽时间段或改周期）',
                            'warnings': warnings}), 400

        valid_codes = list(merged.columns)
        dates = [d.strftime('%Y-%m-%d %H:%M') if period != 'daily' else d.strftime('%Y-%m-%d')
                 for d in merged.index]

        # 归一化到起点=100
        norm = merged.divide(merged.iloc[0]).multiply(100.0)

        # 对数收益率 + Pearson 相关矩阵
        rets = np.log(merged).diff().dropna()
        corr = rets.corr()

        targets = []
        for code in valid_codes:
            s = merged[code]
            targets.append({
                'code': code,
                'name': _resolve_name(code),
                'points': int(len(s)),
                'first': round(float(s.iloc[0]), 4),
                'last': round(float(s.iloc[-1]), 4),
                'change_pct': round(float(s.iloc[-1] / s.iloc[0] - 1) * 100, 2),
            })

        matrix = [[round(float(corr.loc[a, b]), 4) for b in valid_codes] for a in valid_codes]
        pairs = []
        for i in range(len(valid_codes)):
            for j in range(i + 1, len(valid_codes)):
                pairs.append({
                    'a': valid_codes[i], 'b': valid_codes[j],
                    'corr': round(float(corr.iloc[i, j]), 4),
                })
        pairs.sort(key=lambda p: abs(p['corr']), reverse=True)

        return jsonify({
            'success': True,
            'period': period,
            'aligned_from': dates[0],
            'aligned_to': dates[-1],
            'count': len(merged),
            'dates': dates,
            'series': {code: [round(float(v), 3) for v in norm[code].tolist()] for code in valid_codes},
            'targets': targets,
            'corr': {'labels': valid_codes, 'matrix': matrix},
            'pairs': pairs,
            'warnings': warnings,
        })
    except Exception as e:
        logger.exception('多标的对比失败')
        return jsonify({'success': False, 'message': str(e)}), 500
