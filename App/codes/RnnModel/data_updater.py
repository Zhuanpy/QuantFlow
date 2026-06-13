# -*- coding: utf-8 -*-
"""
数据更新模块

提供数据库数据更新功能
"""

import pandas as pd

from App.codes.RnnModel.rnn_base import RnnBase
from App.codes.MySql.LoadMysql import LoadRnnModel
from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.MySql.DataBaseStockPool import TableStockPool


class UpdateData(RnnBase):
    """
    数据更新类

    提供更新股票池、运行记录和 15 分钟数据的功能
    """

    def __init__(self):
        super().__init__()

        self.current = pd.Timestamp('today').date()
        self.signalTimes = None
        self._signalTimes = None
        self.signalStartTime = None

        # 当前股票信息
        self.change_max = None
        self.trade_timing = None
        self.position_action = None
        self.trend_score = None

        self.RunDate = None
        self.trade_boll = False
        self._limitTradeTiming = None

    def update_StockPool(self):
        """更新股票池数据"""
        sql = ''' close = %s, ExpPrice = %s, RnnModel= %s, Trends= %s,
        ReTrend= %s, TrendProbability= %s, RecordDate= %s where id= %s; '''

        parser = (
            self.close, self.ExpPrice, self.trend_score, self.trendValue,
            self.reTrend, self.ScoreP, self.check_date, self.stock_id
        )
        TableStockPool.set_table_to_pool(sql, parser)

    def update_RecordRun(self):
        """更新运行记录（迁移到 ORM：strategy_rnn_runs / RnnRunningRecord）。

        旧实现 UPDATE 废弃的 rnn_model.RunRecord（本环境无此库，报 Unknown
        database 'rnn_model'）。现按 code+月份 upsert 最近一行；ORM 模型多列
        NOT NULL，故所有数值/时间字段做空值兜底（None→0 / now）。写库失败不致命。
        """
        from datetime import datetime
        from App.exts import db
        from App.models.strategy.RnnRunningRecords import RnnRunningRecord

        def _f(v, d=0.0):
            try:
                return float(v) if v is not None else d
            except (TypeError, ValueError):
                return d

        def _i(v, d=0):
            try:
                return int(v) if v is not None else d
            except (TypeError, ValueError):
                return d

        def _dt(v):
            if v is None:
                return None
            try:
                return pd.to_datetime(v).to_pydatetime()
            except Exception:
                return None

        now = datetime.now()
        try:
            db.session.rollback()
            rec = (RnnRunningRecord.query
                   .filter_by(code=str(self.stock_code), parser_month=self.month_parsers)
                   .order_by(RnnRunningRecord.id.desc()).first())
            if rec is None:
                rec = RnnRunningRecord(code=str(self.stock_code),
                                       parser_month=self.month_parsers)
                db.session.add(rec)

            rec.name = self.stock_name or str(self.stock_code)
            rec.trends = str(self.signalValue) if self.signalValue is not None else ''
            rec.signal_start_time = _dt(self.signalStartTime) or now
            rec.time_15m = _dt(getattr(self, 'record_time_15m', None)) or rec.time_15m or now
            rec.time_run_bar = _dt(self.trade_timing) or now
            rec.renew_date = _dt(self.current) or now
            rec.predict_cycle_length = _i(self.predict_length)
            rec.real_cycle_length = _i(self.real_length)
            rec.predict_cycle_change = _f(self.predict_CycleChange)
            rec.predict_cycle_price = _f(self.predict_CyclePrice)
            rec.real_cycle_change = _f(self.real_CycleChange)
            rec.predict_bar_change = _f(self.predict_bar_change)
            rec.real_bar_change = _f(self.real_bar_change)
            rec.predict_bar_volume = _f(self.predict_BarVolume)
            rec.real_bar_volume = _f(self.real_BarVolume)
            rec.score_trends = _f(self.trend_score)
            rec.trade_point = _f(self.tradAction)
            db.session.commit()
        except Exception as ex:
            db.session.rollback()
            print(f'[predict] 写运行记录(ORM)失败 {self.stock_code}: {ex}')

    def update_sql_15m_data(self):
        """更新 15 分钟数据"""
        sql = '''PredictCycleChange = %s,
        PredictCyclePrice = %s,
        PredictCycleLength = %s,
        PredictBarChange = %s,
        PredictBarPrice = %s,
        PredictBarVolume = %s,
        ScoreRnnModel = %s,
        TradePoint = %s where date = %s;'''

        parsers = (
            self.predict_CycleChange, self.predict_CyclePrice, self.predict_length,
            self.predict_bar_change, self.predict_bar_price, self.predict_BarVolume,
            self.trend_score, self.tradAction, self.trade_timing
        )

        StockData15m.set_data_15m_data(self.stock_code, sql, parsers)
