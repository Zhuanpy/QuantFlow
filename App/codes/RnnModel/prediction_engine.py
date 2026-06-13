# -*- coding: utf-8 -*-
"""
预测引擎模块

组合各个模块提供完整的预测功能
"""

import pandas as pd

from App.codes.RnnModel.data_loader import ModelData
from App.codes.RnnModel.model_predictor import DlModel
from App.codes.RnnModel.data_updater import UpdateData
from App.codes.MySql.sql_utils import Stocks
from App.codes.utils.Normal import MathematicalFormula as MyFormula
from App.codes.utils.Normal import Useful, count_times, ReadSaveFile
from App.codes.RnnDataFile.stock_path import StockDataPath
from App.codes.parsers.RnnParser import (
    SignalTimes, Signal, SignalChoice, EndPrice, StartPrice,
    CycleLengthPerBar, CycleAmplitudeMax, CycleLengthMax, CycleAmplitudePerBar,
    EndPriceIndex, DailyVolEmaParser
)


class PredictionCommon(ModelData, DlModel, UpdateData):
    """
    预测通用类

    组合数据加载、模型预测和数据更新功能
    """

    def __init__(self, stock: str, month_parsers: str, check_date,
                 alpha: float = 1, monitor: bool = True,
                 stop_loss: float = None, position=None):
        """
        初始化预测引擎

        Args:
            stock: 股票代码或名称
            month_parsers: 月份解析器
            check_date: 检查日期
            alpha: 模型系数
            monitor: 是否为监控模式
            stop_loss: 止损价格
            position: 持仓信息
        """
        ModelData.__init__(self)
        DlModel.__init__(self)
        UpdateData.__init__(self)

        self.stock_name, self.stock_code, self.stock_id = Stocks(stock)

        self.position = position
        self.month_parsers = month_parsers
        self.monitor = monitor
        self.stopLoss = stop_loss

        path = StockDataPath.json_data_path(self.month_parsers, self.stock_code)
        self.jsons = ReadSaveFile.read_json_by_path(path)

        # 生成预测数据
        if monitor:
            check_date = pd.Timestamp('today').strftime('%Y-%m-%d')

        self.check_date = pd.to_datetime(check_date)
        self.alpha = alpha

        self.checking_data = None
        self.checking = None
        self.predict_data = None
        self._endPriceTime = None
        self._limitPrice = None
        self.area = None

        # 趋势分布统计（up/down 的 Amplitude/Vol/Length 均值方差），懒加载缓存。
        # 旧 json 自带 'models' 块直接用；新流水线不再写该块，按需现算（见 _models_stats）。
        self._models_cache = None

    def report_run(self):
        """输出运行报告"""
        headers = (
            f'{self.lines}\n'
            f'检测日期：{self.check_date.date()}；\n'
            f'股票:{self.stock_name}，代码:{self.stock_code}；\n'
            f'当前趋势:{self.signalValue}；\n'
            f'{self.line}\n'
        )

        cycles = (
            f'时间点：{self.trade_timing}；\n'
            f'预测周期长度：{self.predict_length} 根，现真实长度：{self.real_length} 根；\n'
            f'预测周期振幅：{self.predict_CycleChange} 点，现真实振幅：{self.real_CycleChange} 点；\n'
            f'预测周期{self.area}价：CNY {self.predict_CyclePrice}，现真实{self.area}价：CNY {self.real_CyclePrice}；\n'
        )

        bars = (
            f'预测Bar成交量：{self.predict_BarVolume} 手，真实成交量：{self.real_BarVolume} 手；\n'
            f'预测Bar振幅：{self.predict_bar_change} 点，真实振幅：{self.real_bar_change} 点；\n'
            f'预测Bar价格：CNY {self.predict_bar_price}，真实振幅：CNY {self.close}；\n'
        )

        score = f'\n趋势得分：{self.trend_score}\n'

        _cycles = (
            f'{self.line}\n前周期信息：\n'
            f'前周期峰值时间：{self._limitTradeTiming}；\n'
            f'前周期峰值价格：{self._limitPrice}；\n'
        )

        if self.predict_length >= self.real_length:
            shower = f'{headers}{cycles}{score}{_cycles}\n{self.lines}'
        else:
            shower = f'{headers}{cycles}{bars}{score}{_cycles}\n{self.lines}'

        print(shower)

    def report_trade(self):
        """输出交易点信息"""
        # 是否已存在本股票"有交易点"的历史记录（去重，避免重复发信）。
        # 旧实现基于 self.records(DataFrame)；改为 ORM 计数。
        try:
            from App.models.strategy.RnnRunningRecords import RnnRunningRecord
            shapes = (RnnRunningRecord.query
                      .filter(RnnRunningRecord.code == str(self.stock_code),
                              RnnRunningRecord.trade_point != 0)
                      .count())
        except Exception:
            shapes = 0

        if not shapes:
            title = f'股票:{self.stock_name},代码：{self.stock_code}; 触发{self.position_action}信号;'

            content = (
                f'交易时间点：{self.trade_timing};\n'
                f'预测此{self.signalValue}趋势周期bar: {self.predict_length}根,'
                f'现趋势真实bar: {self.real_length}根;\n;'
                f'预测此{self.signalValue}全周期振幅值：{self.predict_CycleChange}点,'
                f'现全周期真实振幅: {self.real_CycleChange}点;\n; '
                f'预测当前时间点振幅值：{self.predict_bar_change}点, '
                f'当前时间点真实振幅: {self.real_bar_change}点;\n'
                f'预测当前时间点成交量: {self.predict_BarVolume}手,'
                f'当前时间点真实成交量: {self.real_BarVolume}手;\n'
            )

            mails = f'{title}\n\n{content}'

            if self.monitor:
                Useful.sent_emails(message_title=title, mail_content=mails)

    def report_position(self):
        """输出持仓报告"""
        title, message = '', ''

        if self.close < self.stopLoss:
            title = f'持仓股：{self.stock_name}止损卖出；'
            message = (
                f'股票：{self.stock_name}, close: {self.close}, '
                f'stop loss: {self.stopLoss};\nTrend score: {self.trend_score}; '
            )
            self.sellAction = True

        # 止盈阈值随 trend_score 重标到 [-1,1] 同步下调（原 >4 对应旧 ±6~8 量纲）
        if self.trend_score > 0.5:
            title = f'持仓股：{self.stock_name}止盈卖出；'
            self.sellAction = True
            message = (
                f'股票：{self.stock_name}, close: {self.close}, '
                f'stop loss: {self.stopLoss};\nTrend score: {self.trend_score};'
            )

        if self.sellAction:
            Useful.sent_emails(title, message)

    def predict_cycle_values(self):
        """趋势转变点预判"""
        # 历史记录改用 ORM 的 last_record；无历史记录（首次预测/无库）则走重算分支。
        last = self.last_record
        R_signalStartTime = last.signal_start_time if last is not None else None

        # 判断是否运行转折点模型：与上次同一信号起点 → 复用上次预测值
        if last is not None and self.signalStartTime == R_signalStartTime:
            self.predict_length = last.predict_cycle_length
            self.predict_CycleChange = last.predict_cycle_change
            self.predict_CyclePrice = last.predict_cycle_price
        else:
            self.predict_data = self.data_15m[
                self.data_15m[SignalTimes] == self._signalTimes
            ]
            self._limitPrice = self.predict_data.iloc[0][EndPrice]

            self.predict_CycleChange = self.cycle_change()
            self.predict_length = self.cycle_length()

            counts = self.get_json_data(self.updown, 'Amplitude')
            mean_ = counts[2]
            std_ = counts[3]

            if self.signal == 1:
                p30 = MyFormula.normal_get_x(p=0.3, mean=mean_, std=counts[3])
                p95 = MyFormula.normal_get_x(p=0.95, mean=mean_, std=counts[3])

                if self.predict_CycleChange < p30:
                    self.predict_CycleChange = p30

                if self.predict_CycleChange > p95:
                    self.predict_CycleChange = MyFormula.normal_get_x(p=0.8, mean=mean_, std=std_)

            if self.signal == -1:
                p65 = MyFormula.normal_get_x(p=0.65, mean=mean_, std=std_)
                if self.predict_CycleChange > mean_:
                    self.predict_CycleChange = p65

            self.predict_CyclePrice = round(
                self._limitPrice * (1 + self.predict_CycleChange), 2
            )

            if self.predict_length < 16:
                self.predict_length = self.get_json_data(self.updown, 'Length')[2]

            self.predict_length = int(self.predict_length)
            self.predict_CycleChange = round(self.predict_CycleChange, 2)
            self.predict_CyclePrice = round(self.predict_CyclePrice, 2)

        mean_ = self.get_json_data(self.updown, 'Amplitude')[2]

        if self.signal == 1:
            self.ExpPrice = (1 + (mean_ + self.predict_CycleChange) / 2) * self._limitPrice

        if self.signal == -1:
            self.ExpPrice = (1 + (mean_ + self.predict_CycleChange) / 2) * self._limitPrice

        self.ExpPrice = round(self.ExpPrice, 2)

    def predict_bar_values(self):
        """预测 Bar 值"""
        self.predict_BarVolume = 0
        self.predict_bar_change = 0

        meanLength = self.get_json_data(self.updown, 'Length')[2]

        if self.real_length > min(meanLength, self.predict_length):
            self.predict_data = self.data_15m[
                (self.data_15m['date'] < self.trade_timing) &
                (self.data_15m[SignalTimes] == self.signalTimes)
            ].tail(30)

            vols = self.checking_data.iloc[-1][DailyVolEmaParser]
            self.predict_BarVolume = self.bar_volume(vols)
            self.predict_bar_change = self.bar_change()

            countChange = self.get_json_data(self.updown, 'Amplitude')
            countVol = self.get_json_data(self.updown, 'Vol')

            if self.signal == 1:
                p30Change = MyFormula.normal_get_x(p=0.3, mean=countChange[2], std=countChange[3])
                p80Change = MyFormula.normal_get_x(p=0.8, mean=countChange[2], std=countChange[3])
                p95Change = MyFormula.normal_get_x(p=0.95, mean=countChange[2], std=countChange[3])

                if self.predict_bar_change < p30Change:
                    self.predict_bar_change = p30Change

                if self.predict_bar_change > p95Change:
                    self.predict_bar_change = p80Change

                p30Vol = MyFormula.normal_get_x(p=0.3, mean=countVol[2], std=countVol[3])
                if self.predict_BarVolume < p30Vol:
                    self.predict_BarVolume = p30Vol

            if self.signal == -1:
                meanChange = countChange[2]
                p65Change = MyFormula.normal_get_x(p=0.65, mean=countChange[2], std=countChange[3])

                if self.predict_bar_change > meanChange:
                    self.predict_bar_change = p65Change

                meanVol = countVol[2]
                p65Vol = MyFormula.normal_get_x(p=0.65, mean=countVol[2], std=countVol[3])
                if self.predict_BarVolume < meanVol:
                    self.predict_BarVolume = p65Vol

        _price = self.checking_data.iloc[-1][StartPrice]

        self.predict_BarVolume = int(self.predict_BarVolume)
        self.predict_bar_change = round(self.predict_bar_change, 2)
        self.predict_bar_price = round(_price * (1 + self.predict_bar_change), 2)

    # 4 个维度的权重（和为 1）；缺失维度按剩余权重归一化
    SCORE_WEIGHTS = {'amp': 0.35, 'len': 0.25, 'vol': 0.20, 'bar': 0.20}
    # |score| ≥ 此值触发买卖；score 已归一到 [-1,1]
    TRADE_THRESHOLD = 0.7

    def _trend_pct(self, name: str, x: float) -> float:
        """实现值 x 在该趋势(up/down)历史分布里的分位 CDF(0~1)。std 无效时退化为 0.5。"""
        _, _, mean_, std_ = self.get_json_data(self.updown, name)
        if not std_ or pd.isna(std_):
            return 0.5
        p = MyFormula.normal_get_p(x=x, mean=mean_, std=std_)
        # 数值兜底，限制在 [0,1]
        return min(max(float(p), 0.0), 1.0)

    def trade_point_score(self):
        """趋势评分（重构版，score ∈ [-1, 1]）。

        思路：在「当前趋势方向」上把 4 个维度各换算成 0~1 的"衰竭/完成度"，加权求和得
        strength∈[0,1]，再按方向定正负 —— 顶部(signal=1)取正、底部(signal=-1)取负。
        - 振幅/长度/量：用历史 up/down 分布的分位(CDF)衡量"实现值有多极端"（越极端越衰竭）；
          下行振幅取 1-CDF（越跌越衰竭），长度/量两个方向都是越大越衰竭。
        - Bar 振幅：无独立历史分布，改用"实现 / 预测"的完成度(0~1)，且用对的变量
          real_bar_change（旧实现误用了 real_CycleChange）。
        - 四维恒计算（双边、上下行对称）；某维数据缺失则其权重剔除后归一化。
        - trade_boll: |score| ≥ TRADE_THRESHOLD；顶部→卖出，底部→买入。
        """
        up = (self.signal == 1)

        comps = {}

        # 周期振幅：up 用 CDF（越大越衰竭），down 用 1-CDF（越跌越衰竭）
        amp_cdf = self._trend_pct('Amplitude', self.real_CycleChange)
        comps['amp'] = amp_cdf if up else (1.0 - amp_cdf)

        # 周期长度：越长越衰竭（两方向同向）
        comps['len'] = self._trend_pct('Length', self.real_length)

        # Bar 成交量：越放量越像高潮/衰竭（两方向同向）
        comps['vol'] = self._trend_pct('Vol', self.real_BarVolume)

        # Bar 振幅：实现/预测 的完成度（同号→比值为正），缺预测则不计入
        if self.predict_bar_change:
            ratio = self.real_bar_change / self.predict_bar_change
            comps['bar'] = min(max(ratio, 0.0), 1.0)

        # 加权求和并按有效维度归一化 → strength ∈ [0,1]
        wsum = sum(self.SCORE_WEIGHTS[k] for k in comps)
        strength = (sum(self.SCORE_WEIGHTS[k] * v for k, v in comps.items()) / wsum
                    if wsum else 0.0)

        self.ScoreP = round(strength, 3)                 # 衰竭强度 0~1
        self.trend_score = round((1 if up else -1) * strength, 3)  # [-1, 1]

        if abs(self.trend_score) >= self.TRADE_THRESHOLD:
            self.trade_boll = True
            if up:
                self.position_action = '卖出'
                self.tradAction = -1
            else:
                self.position_action = '买入'
                self.tradAction = 1

    def get_json_data(self, trend: str, name: str) -> tuple:
        """
        获取某趋势(up/down)下某指标(Amplitude/Vol/Length)的分布统计。

        优先用 json 自带的 'models' 块（旧版数据）；新流水线不再写该块，
        缺失时回退到按全量 15m 历史现算（见 _models_stats）。

        Args:
            trend: 趋势类型 ('up' 或 'down')
            name: 指标名称 ('Amplitude' / 'Vol' / 'Length')

        Returns:
            tuple: (max, min, mean, std)
        """
        models = self.jsons.get('models') if isinstance(self.jsons, dict) else None
        if not models or trend not in models:
            models = self._models_stats()

        data = models[trend][name]
        return (data['max'], data['min'], data['mean'], data['std'])

    def _models_stats(self) -> dict:
        """
        从全量 15m 历史现算 up/down 的 Amplitude/Vol/Length 分布统计，
        复刻旧 json 'models' 块的语义（仅 get_json_data 用到 mean/std）。

        数据源：StockData15m 库里的原始(未归一化) 15m 数据，含多年历史，
        样本量远大于预测期截断后的 data_15m（仅最后 6 个周期），统计更稳。

        口径：
        - 取 SignalChoice 非空的「每周期一行」标记行，按 Signal(1=up/-1=down) 分组；
        - Amplitude = CycleAmplitudeMax（带符号，up 正 / down 负）；
        - Length    = CycleLengthMax（周期 bar 数）；
        - Vol       = EndDaily1mVolMax5 / 100（换算成「手」，与 bar_volume 预测口径一致）。

        结果按实例缓存一次。
        """
        if self._models_cache is not None:
            return self._models_cache

        empty = {'max': 0.0, 'min': 0.0, 'mean': 0.0, 'std': 1.0}

        def _agg(series):
            s = pd.to_numeric(series, errors='coerce').dropna()
            if len(s) == 0:
                return None
            std = float(s.std(ddof=0)) if len(s) > 1 else 0.0
            # std=0/NaN 会让 normal_get_x/normal_get_p 退化或抛错，给一个最小正值兜底
            if not std or pd.isna(std):
                std = max(abs(float(s.mean())) * 0.5, 1e-6)
            return {
                'max': round(float(s.max()), 5),
                'min': round(float(s.min()), 5),
                'mean': round(float(s.mean()), 5),
                'std': round(std, 5),
            }

        stats = {'up': {}, 'down': {}}
        try:
            from App.codes.MySql.DataBaseStockData15m import StockData15m
            df = StockData15m.load_15m(self.stock_code)

            df = df[df[SignalChoice].notna() & (df[SignalChoice] != '')].copy()
            df[Signal] = pd.to_numeric(df[Signal], errors='coerce')
            df['_Vol_'] = pd.to_numeric(df['EndDaily1mVolMax5'], errors='coerce') / 100.0

            for sig, trend in ((1, 'up'), (-1, 'down')):
                g = df[df[Signal] == sig]
                for name, col in (('Amplitude', CycleAmplitudeMax),
                                  ('Length', CycleLengthMax),
                                  ('Vol', '_Vol_')):
                    stats[trend][name] = _agg(g[col]) or dict(empty)
        except Exception as ex:
            # 库缺失/列缺失/无数据等：退回安全默认，避免预测主流程因统计失败而中断
            print(f'[predict] _models_stats 计算失败，使用默认值: {ex}')
            for trend in ('up', 'down'):
                for name in ('Amplitude', 'Length', 'Vol'):
                    stats[trend][name] = dict(empty)

        self._models_cache = stats
        return stats

    def get_bar_real(self):
        """获取 Bar 真实值"""
        con1 = (self.data_1m['date'] > self.check_date)
        con2 = (self.data_1m['date'] <= self.trade_timing)

        self.real_BarVolume = int(
            self.data_1m[con1 & con2].sort_values(by=['volume']).tail(5)['volume'].mean() / 100
        )
        self.real_bar_change = self.checking_data.iloc[-1][CycleAmplitudePerBar]
        self.real_bar_change = round(
            self.normal2value(self.real_bar_change, CycleAmplitudePerBar), 3
        )

    def get_cycle_real(self):
        """获取周期真实值"""
        self.real_length = self.checking.iloc[-1][CycleLengthPerBar]
        self.real_length = int(self.normal2value(self.real_length, CycleLengthPerBar))

        self.real_CycleChange = self.checking.iloc[-1][CycleAmplitudeMax]
        self.real_CycleChange = round(
            self.normal2value(self.real_CycleChange, CycleAmplitudeMax), 3
        )
        self.real_CyclePrice = self.checking.iloc[-1][EndPrice]

    def get_bar_data(self):
        """获取当前 Bar 数据"""
        self.trade_timing = self.checking.iloc[-1]['date']
        self.signalStartTime = self.checking.iloc[-1]['StartPriceIndex']
        self._endPriceTime = self.checking.iloc[-1]['StartPriceIndex']
        self.signalTimes = self.checking.iloc[-1][SignalTimes]
        self.signal = self.checking.iloc[-1][Signal]
        self.close = self.checking.iloc[-1]['close']

        reTrends = self.checking.tail(5).reset_index(drop=True)

        if self.signal == 1:
            self.area = '顶部'
            self.signalValue = '上涨'
            self.updown = 'up'

            self.reTrend = 0
            reTrends = reTrends[reTrends['ReTrends'] < 0]
            if reTrends.shape[0] == 5:
                self.reTrend = 1

        else:
            self.area = '底部'
            self.signalValue = '下跌'
            self.updown = 'down'

            self.reTrend = 0
            reTrends = reTrends[reTrends['ReTrends'] > 0]
            if reTrends.shape[0] == 5:
                self.reTrend = 1

        self._signalTimes = self.data_15m[
            self.data_15m['date'] < self.trade_timing
        ].dropna(subset=[SignalChoice]).iloc[-2][SignalTimes]

        self._limitPrice = self.data_15m[
            self.data_15m[SignalTimes] == self._signalTimes
        ].iloc[-1][EndPrice]

        self._limitTradeTiming = self.data_15m[
            self.data_15m[SignalTimes] == self._signalTimes
        ].iloc[-1][EndPriceIndex]

        self.get_cycle_real()
        self.get_bar_real()

    @count_times
    def single_stock(self) -> bool:
        """
        单股循环预测

        Returns:
            bool: 是否成功执行
        """
        # 生成数据
        self.checking_data = self.calculate_check_data()

        if self.checking_data.empty:
            return False

        # 循环处理每个 SignalTimes
        for s_ in self.checking_data.drop_duplicates(subset=[SignalTimes])[SignalTimes]:
            self.checking = self.checking_data[self.checking_data[SignalTimes] == s_]

            self.get_bar_data()

            # 周期点预测
            self.predict_cycle_values()
            self.predict_bar_values()

            self.trade_point_score()

            # 更新数据
            self.update_RecordRun()
            self.update_sql_15m_data()
            self.update_StockPool()

            if self.trade_boll:
                self.report_trade()

            if self.position:
                self.report_position()

            self.report_run()

        return True
