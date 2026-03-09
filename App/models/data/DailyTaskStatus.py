"""
每日任务状态模型

跟踪每只股票每个交易日的数据处理任务完成情况和预测结果。
与 StockDaily 通过 (stock_code, date) 对齐。
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class DailyTaskStatus(db.Model):
    """
    每日任务状态表

    复合主键 (stock_code, date) 与日K数据一一对应，
    用于跟踪每日数据处理流水线的完成情况和预测结果。
    """
    __tablename__ = 'data_daily_task_status'
    __bind_key__ = 'quanttradingsystem'

    # ==================== 复合主键 ====================
    stock_code = db.Column(db.String(10), primary_key=True, nullable=False, comment='股票代码')
    date = db.Column(db.Date, primary_key=True, nullable=False, comment='交易日期')

    # ==================== 任务完成状态（核心） ====================
    is_1m_downloaded = db.Column(db.Boolean, default=False, nullable=False, comment='1分钟数据是否下载')
    is_1m_cleaned = db.Column(db.Boolean, default=False, nullable=False, comment='1分钟数据是否清洗')
    is_15m_generated = db.Column(db.Boolean, default=False, nullable=False, comment='15分钟数据是否生成')
    is_macd_calculated = db.Column(db.Boolean, default=False, nullable=False, comment='MACD信号是否计算')
    is_volume_processed = db.Column(db.Boolean, default=False, nullable=False, comment='成交量特征是否处理')
    is_daily_processed = db.Column(db.Boolean, default=False, nullable=False, comment='日K数据是否处理')

    # ==================== 任务完成状态（可选） ====================
    is_feature_generated = db.Column(db.Boolean, default=False, nullable=False, comment='训练特征矩阵是否生成')
    is_rnn_predicted = db.Column(db.Boolean, default=False, nullable=False, comment='RNN预测是否完成')
    is_board_downloaded = db.Column(db.Boolean, default=False, nullable=False, comment='板块数据是否下载')
    is_fund_analyzed = db.Column(db.Boolean, default=False, nullable=False, comment='基金持仓是否分析')

    # ==================== 趋势/信号（从 StockDaily 迁移） ====================
    signal = db.Column(db.Integer, nullable=True, comment='当前趋势方向（1=上涨, -1=下跌）')
    signal_name = db.Column(db.String(10), nullable=True, comment='趋势名称（上涨/下跌）')
    signal_id = db.Column(db.String(20), nullable=True, comment='信号ID')
    signal_start_time = db.Column(db.DateTime, nullable=True, comment='信号开始时间')
    re_trend = db.Column(db.Integer, nullable=True, default=1, comment='重新趋势')

    # ==================== 周期信息（从 StockDaily 迁移） ====================
    cycle_amplitude_per_bar = db.Column(db.Float, nullable=True, default=0, comment='每根K线周期振幅')
    cycle_amplitude_max = db.Column(db.Float, nullable=True, default=0, comment='最大周期振幅')
    cycle_amplitude = db.Column(db.Float, nullable=True, default=0, comment='周期振幅')
    cycle_1m_vol_max1 = db.Column(db.Integer, nullable=True, default=0, comment='1分钟最大成交量1')
    cycle_1m_vol_max5 = db.Column(db.Integer, nullable=True, default=0, comment='1分钟最大成交量5')
    cycle_length_per_bar = db.Column(db.Integer, nullable=True, default=0, comment='每根K线周期长度')

    # ==================== 预测结果 ====================
    predict_cycle_length = db.Column(db.Integer, nullable=True, comment='预测周期长度（根）')
    predict_cycle_change = db.Column(db.Float, nullable=True, comment='预测周期振幅（点）')
    predict_cycle_price = db.Column(db.Float, nullable=True, comment='预测周期目标价（元）')
    predict_bar_change = db.Column(db.Float, nullable=True, comment='预测Bar振幅（点）')
    predict_bar_price = db.Column(db.Float, nullable=True, comment='预测Bar价格（元）')
    predict_bar_volume = db.Column(db.Integer, nullable=True, comment='预测Bar成交量（手）')

    real_cycle_length = db.Column(db.Integer, nullable=True, comment='真实周期长度（根）')
    real_cycle_change = db.Column(db.Float, nullable=True, comment='真实周期振幅（点）')
    real_bar_change = db.Column(db.Float, nullable=True, comment='真实Bar振幅（点）')
    real_bar_volume = db.Column(db.Integer, nullable=True, comment='真实Bar成交量（手）')

    # ==================== 评分信息（从 StockDaily 迁移） ====================
    score_rnn_model = db.Column(db.Float, nullable=True, default=0, comment='RNN模型得分')
    score_board_boll = db.Column(db.Float, nullable=True, default=0, comment='布林带得分')
    score_board_money = db.Column(db.Float, nullable=True, default=0, comment='资金得分')
    score_board_hot = db.Column(db.Float, nullable=True, default=0, comment='热度得分')
    score_funds_awkward = db.Column(db.Float, nullable=True, default=0, comment='基金重仓得分')
    trend_probability = db.Column(db.Float, nullable=True, default=0, comment='趋势概率')
    trend_score = db.Column(db.Float, nullable=True, comment='趋势综合评分')
    trade_action = db.Column(db.Integer, nullable=True, default=0, comment='交易动作（1=买入, -1=卖出, 0=持有）')

    # ==================== 辅助字段 ====================
    notes = db.Column(db.Text, nullable=True, comment='备注/错误信息')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='最后更新时间')

    def __repr__(self):
        return f'<DailyTaskStatus {self.stock_code} {self.date}>'

    def to_dict(self):
        """转换为字典格式"""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    @property
    def task_completion_rate(self):
        """计算核心任务完成率（6个核心任务）"""
        core_tasks = [
            self.is_1m_downloaded,
            self.is_1m_cleaned,
            self.is_15m_generated,
            self.is_macd_calculated,
            self.is_volume_processed,
            self.is_daily_processed,
        ]
        return sum(1 for t in core_tasks if t) / len(core_tasks)

    @property
    def all_task_completion_rate(self):
        """计算全部任务完成率（10个任务）"""
        all_tasks = [
            self.is_1m_downloaded,
            self.is_1m_cleaned,
            self.is_15m_generated,
            self.is_macd_calculated,
            self.is_volume_processed,
            self.is_daily_processed,
            self.is_feature_generated,
            self.is_rnn_predicted,
            self.is_board_downloaded,
            self.is_fund_analyzed,
        ]
        return sum(1 for t in all_tasks if t) / len(all_tasks)

    @classmethod
    def mark_task(cls, stock_code, date, task_name, value=True):
        """
        标记某个任务的完成状态

        Args:
            stock_code: 股票代码
            date: 交易日期
            task_name: 任务字段名（如 'is_1m_downloaded'）
            value: 状态值，默认 True
        """
        record = cls.query.filter_by(stock_code=stock_code, date=date).first()
        if not record:
            record = cls(stock_code=stock_code, date=date)
            db.session.add(record)
        setattr(record, task_name, value)
        record.updated_at = datetime.utcnow()
        db.session.commit()

    @classmethod
    def update_prediction(cls, stock_code, date, prediction_data: dict):
        """
        更新预测结果

        Args:
            stock_code: 股票代码
            date: 交易日期
            prediction_data: 预测结果字典
        """
        record = cls.query.filter_by(stock_code=stock_code, date=date).first()
        if not record:
            record = cls(stock_code=stock_code, date=date)
            db.session.add(record)
        for key, value in prediction_data.items():
            if hasattr(record, key):
                setattr(record, key, value)
        record.is_rnn_predicted = True
        record.updated_at = datetime.utcnow()
        db.session.commit()

    @classmethod
    def get_incomplete_tasks(cls, date, task_name=None):
        """
        查询指定日期未完成的任务

        Args:
            date: 交易日期
            task_name: 可选，指定任务字段名，为空则查所有核心任务未全部完成的记录

        Returns:
            list: 未完成任务的记录列表
        """
        query = cls.query.filter_by(date=date)
        if task_name:
            query = query.filter(getattr(cls, task_name) == False)
        else:
            query = query.filter(
                db.or_(
                    cls.is_1m_downloaded == False,
                    cls.is_1m_cleaned == False,
                    cls.is_15m_generated == False,
                    cls.is_macd_calculated == False,
                    cls.is_volume_processed == False,
                    cls.is_daily_processed == False,
                )
            )
        return query.all()

    @classmethod
    def get_missing_dates(cls, stock_code, start_date, end_date):
        """
        查询某只股票在日期范围内缺失的记录（完全没有记录的日期）

        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            list: 有记录的日期列表（用于与交易日历对比找出缺失日期）
        """
        records = cls.query.filter(
            cls.stock_code == stock_code,
            cls.date >= start_date,
            cls.date <= end_date,
        ).with_entities(cls.date).all()
        return [r.date for r in records]
