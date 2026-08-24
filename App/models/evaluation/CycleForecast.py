"""周期预估记录（eval_cycle_forecast）—— 把 live 页两个预估面板的结果存下来。

为什么单独一张表，而不是塞进 TradePlan
--------------------------------------
TradePlan 是"交易计划"，只装得下 目标价→entry_price、结束时间→expire_time；
而预估真正的价值在参数上：**分位 P、预估整段振幅%、起点价、预估长度、当时现价、
拟合的均值与σ** —— 这些塞进 notes 就变成一段死文本，事后没法统计。

留档是为了回答两个问题：
  1. 交易层面：我挂的这个价，到了没有？等了几天？段有没有先翻转让预估作废？
  2. 校准层面：现在「多周期自动定 P」用的是硬编码表（上涨 P55/65/80、下跌 P65/80/90），
     纯拍脑袋。攒够样本后就能统计"实际振幅落在预估的哪个分位""按 P65 挂单的触达率
     与平均等待时间"，把那张表从拍脑袋变成数据驱动。
     **这类记录不可回补** —— 今天不开始记，这段历史就永远没有。

段标识
------
一条预估绑定**一段周期**，不是绑一个孤立价格：`(stock_code, timeframe, seg_start_at)`，
其中 seg_start_at 取 15m parquet 的 `SignalStartIndex`（段起始时间戳，与页面
current_trend.started_at 同源）。段一旦翻转，基于它的预估立即作废（status=invalidated）
—— 没有这一条，就会拿着一个逻辑已经不成立的价格傻等。
"""
from datetime import datetime

from App.exts import db

TIMEFRAMES = ('15m',)
DIRECTIONS = ('up', 'down')
P_SOURCES = ('auto', 'manual')   # auto = 多周期趋势自动定 P

# —— 状态 ——
ST_PENDING = 'pending'            # 等待触达
ST_REACHED = 'reached'            # 价格已触达目标（还没决定买不买）
ST_EXPIRED = 'expired'            # 超过预估结束时间仍未触达（段还在）
ST_INVALIDATED = 'invalidated'    # 段已翻转，预估的前提没了
ST_SUPERSEDED = 'superseded'      # 同一段里被新的预估覆盖
ST_CONVERTED = 'converted'        # 已转成交易计划
ST_CANCELLED = 'cancelled'        # 手工作废
STATUSES = (ST_PENDING, ST_REACHED, ST_EXPIRED, ST_INVALIDATED,
            ST_SUPERSEDED, ST_CONVERTED, ST_CANCELLED)

# 还"活着"、需要每次刷新去判定的状态
LIVE_STATUSES = (ST_PENDING, ST_REACHED)

STATUS_LABEL = {
    ST_PENDING: '等待中', ST_REACHED: '已触达', ST_EXPIRED: '已过期',
    ST_INVALIDATED: '已作废(段翻转)', ST_SUPERSEDED: '被覆盖',
    ST_CONVERTED: '已转计划', ST_CANCELLED: '手工作废',
}


class CycleForecast(db.Model):
    __tablename__ = 'eval_cycle_forecast'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.Index('idx_cf_code_status', 'stock_code', 'status'),
        db.Index('idx_cf_seg', 'stock_code', 'timeframe', 'seg_start_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(50), comment='股票名称')
    timeframe = db.Column(db.String(10), default='15m', comment='周期，目前只有 15m')
    direction = db.Column(db.String(10), nullable=False, comment='段方向 up/down')
    signal_name = db.Column(db.String(40),
                            comment='下预估时该段的趋势名，如 ↓跌#260818-1000（方向+段起点，'
                                    '同段内稳定、趋势一翻就换名），用来一眼认出这条预估管的是哪一波')

    # —— 段标识（预估绑定的那一段）——
    seg_start_at = db.Column(db.DateTime, nullable=False,
                             comment='段起始时间(= 15m SignalStartIndex)，段标识')
    seg_start_price = db.Column(db.Float, comment='段起点价(振幅口径 StartPrice)')

    # —— 下预估时的现场 ——
    forecast_at = db.Column(db.DateTime, default=datetime.utcnow, comment='下预估的时刻')
    price_at = db.Column(db.Float, comment='下预估时的现价')
    bars_at = db.Column(db.Integer, comment='下预估时该段已持续根数')
    amp_walked_pct = db.Column(db.Float, comment='下预估时已走振幅%')

    # —— 幅度预估 ——
    amp_p = db.Column(db.Integer, comment='振幅分位 P (1-99)')
    amp_p_source = db.Column(db.String(10), comment='P 来源 auto/manual')
    proj_amp_pct = db.Column(db.Float, comment='预估整段振幅%')
    target_price = db.Column(db.Float, nullable=False, comment='预估目标价（核心）')
    amp_fit_mean = db.Column(db.Float, comment='振幅分布拟合均值%')
    amp_fit_std = db.Column(db.Float, comment='振幅分布拟合σ')

    # —— 周期预估 ——
    len_p = db.Column(db.Integer, comment='长度分位 P (1-99)')
    len_p_source = db.Column(db.String(10), comment='P 来源 auto/manual')
    proj_len_bars = db.Column(db.Integer, comment='预估整段长度(根)')
    remaining_bars = db.Column(db.Integer, comment='下预估时预计还剩(根)')
    proj_end_at = db.Column(db.DateTime, comment='预估结束时间')
    len_fit_mean = db.Column(db.Float, comment='长度分布拟合均值(根)')
    len_fit_std = db.Column(db.Float, comment='长度分布拟合σ')

    # —— 状态 ——
    status = db.Column(db.String(20), default=ST_PENDING, comment='见 STATUSES')
    reached_at = db.Column(db.DateTime, comment='触达目标价的时间')
    reached_price = db.Column(db.Float, comment='触达时的价格')
    status_note = db.Column(db.String(200), comment='状态变更说明')

    # —— 复盘（段结束后回填，用于校准 P 表）——
    actual_extreme = db.Column(db.Float, comment='该段实际极值（上涨取最高/下跌取最低）')
    actual_extreme_at = db.Column(db.DateTime, comment='实际极值出现时间')
    actual_amp_pct = db.Column(db.Float, comment='该段实际整段振幅%')
    actual_len_bars = db.Column(db.Integer, comment='该段实际总长度(根)')
    amp_err_pct = db.Column(db.Float, comment='实际振幅% − 预估振幅%（正=实际更大）')
    len_err_bars = db.Column(db.Integer, comment='实际长度 − 预估长度（正=实际更长）')
    reviewed_at = db.Column(db.DateTime, comment='复盘回填时间')

    # —— 关联 ——
    plan_id = db.Column(db.String(50), comment='转成交易计划后的 TradePlan.plan_id')
    note = db.Column(db.Text, comment='我的备注（为什么这么预估）')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 表已存在时靠这里补列（项目没有迁移工具）
    _ADD_COLUMNS = {
        'signal_name': 'VARCHAR(40) NULL COMMENT "下预估时的趋势名 如 ↓跌#260818-1000"',
    }

    @classmethod
    def ensure_table(cls):
        """惰性建表 + 缺列自动补齐（项目不做全局 create_all，也没有迁移工具）。"""
        import logging
        logger = logging.getLogger(__name__)
        eng = db.engines['quanttradingsystem']
        insp = db.inspect(eng)
        if not insp.has_table(cls.__tablename__):
            cls.__table__.create(bind=eng)
            return
        have = {c['name'] for c in insp.get_columns(cls.__tablename__)}
        missing = [(k, v) for k, v in cls._ADD_COLUMNS.items() if k not in have]
        if not missing:
            return
        from sqlalchemy import text
        with eng.begin() as conn:
            for name, ddl in missing:
                try:
                    conn.execute(text(
                        f'ALTER TABLE {cls.__tablename__} ADD COLUMN {name} {ddl}'))
                    logger.info(f'[forecast] 补列 {name}')
                except Exception as e:
                    logger.warning(f'[forecast] 补列 {name} 失败: {e}')

    def to_dict(self):
        def _dt(v):
            return v.strftime('%Y-%m-%d %H:%M:%S') if v else None
        return {
            'id': self.id,
            'stock_code': self.stock_code, 'stock_name': self.stock_name,
            'timeframe': self.timeframe, 'direction': self.direction,
            'signal_name': self.signal_name,
            'seg_start_at': _dt(self.seg_start_at), 'seg_start_price': self.seg_start_price,
            'forecast_at': _dt(self.forecast_at), 'price_at': self.price_at,
            'bars_at': self.bars_at, 'amp_walked_pct': self.amp_walked_pct,
            'amp_p': self.amp_p, 'amp_p_source': self.amp_p_source,
            'proj_amp_pct': self.proj_amp_pct, 'target_price': self.target_price,
            'amp_fit_mean': self.amp_fit_mean, 'amp_fit_std': self.amp_fit_std,
            'len_p': self.len_p, 'len_p_source': self.len_p_source,
            'proj_len_bars': self.proj_len_bars, 'remaining_bars': self.remaining_bars,
            'proj_end_at': _dt(self.proj_end_at),
            'len_fit_mean': self.len_fit_mean, 'len_fit_std': self.len_fit_std,
            'status': self.status, 'status_label': STATUS_LABEL.get(self.status, self.status),
            'reached_at': _dt(self.reached_at), 'reached_price': self.reached_price,
            'status_note': self.status_note,
            'actual_extreme': self.actual_extreme,
            'actual_extreme_at': _dt(self.actual_extreme_at),
            'actual_amp_pct': self.actual_amp_pct, 'actual_len_bars': self.actual_len_bars,
            'amp_err_pct': self.amp_err_pct, 'len_err_bars': self.len_err_bars,
            'reviewed_at': _dt(self.reviewed_at),
            'plan_id': self.plan_id, 'note': self.note,
        }

    def __repr__(self):
        return (f'<CycleForecast {self.stock_code} {self.direction} '
                f'target={self.target_price} {self.status}>')
