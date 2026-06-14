"""个股三正态分布统计快照

每个 (stock_code, snapshot_date) 一行；记录 3 个指标 × 上涨/下跌 的正态分布参数 +
"当前未完成周期"的位置（z 分数 / 经验分位），用于筛选：
  - "周期长度比历史 80% 都长" → len_*_pct >= 0.8
  - "当前振幅偏离均值超过 2σ" → abs(amp_*_z) >= 2
  - ...

3 个指标：
  len  = CycleLengthMax           周期 K 线根数
  amp  = CycleAmplitudeMax        周期收盘振幅（小数，例如 0.183 = 18.3%）
  v5   = Cycle1mVolMax5           周期内 1m 量能 top5 平均
每个指标分别对"上涨周期"(up) 和 "下跌周期"(dn) 拟合正态分布。
"""
from App.exts import db
from datetime import datetime


class StockDistSnapshot(db.Model):
    """三指标 × 上/下跌的正态分布快照

    主键：自增 id；同时 (stock_code, snapshot_date) 加唯一约束方便 upsert。
    """
    __tablename__ = 'stock_dist_snapshot'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        # merge_below 纳入唯一键：同一天可同时存"原始口径(0)"与"合并<5%小周期"两套快照
        db.UniqueConstraint('stock_code', 'snapshot_date', 'merge_below', name='uq_stock_date'),
        db.Index('ix_dist_date', 'snapshot_date'),
        db.Index('ix_dist_code', 'stock_code'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)

    # ---- 基础 ----
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(50), nullable=True, comment='股票名称(冗余便于查询展示)')
    snapshot_date = db.Column(db.Date, nullable=False, comment='快照日期')
    # 周期合并阈值(%)：0=原始口径(每个信号周期独立)；>0=把振幅<该值的中间小周期与前后
    # 同向周期合并成大周期后再统计（去噪口径）。纳入唯一键，两套口径互不覆盖。
    merge_below = db.Column(db.Float, nullable=False, default=0, server_default='0',
                           comment='周期合并阈值(%)，0=不合并')
    last_bar_date = db.Column(db.DateTime, nullable=True, comment='快照所基于的最新一根 15m K 线时间')
    current_direction = db.Column(db.Integer, nullable=True,
                                  comment='当前未完成周期方向：1=上涨 / -1=下跌 / 0=未知')
    current_signal_name = db.Column(db.String(40), nullable=True,
                                    comment='当前周期趋势名（↑涨/↓跌#YYMMDD-HHMM，与15m页一致）')

    # ---- 周期长度 CycleLengthMax ----
    # 上涨周期
    len_up_mean = db.Column(db.Float, nullable=True, comment='上涨周期长度均值')
    len_up_std = db.Column(db.Float, nullable=True, comment='上涨周期长度标准差')
    len_up_current = db.Column(db.Float, nullable=True, comment='当前未完成上涨周期已走根数')
    len_up_z = db.Column(db.Float, nullable=True, comment='当前 z 分数 = (current-mean)/std')
    len_up_pct = db.Column(db.Float, nullable=True, comment='经验分位 = 历史中 ≤ current 的占比')
    len_up_n = db.Column(db.Integer, nullable=True, comment='参与拟合的已完成上涨周期样本数')
    # 下跌周期
    len_dn_mean = db.Column(db.Float, nullable=True)
    len_dn_std = db.Column(db.Float, nullable=True)
    len_dn_current = db.Column(db.Float, nullable=True)
    len_dn_z = db.Column(db.Float, nullable=True)
    len_dn_pct = db.Column(db.Float, nullable=True)
    len_dn_n = db.Column(db.Integer, nullable=True)

    # ---- 周期振幅 CycleAmplitudeMax（绝对值，单位 = 小数比例）----
    amp_up_mean = db.Column(db.Float, nullable=True)
    amp_up_std = db.Column(db.Float, nullable=True)
    amp_up_current = db.Column(db.Float, nullable=True)
    amp_up_z = db.Column(db.Float, nullable=True)
    amp_up_pct = db.Column(db.Float, nullable=True)
    amp_up_n = db.Column(db.Integer, nullable=True)
    amp_dn_mean = db.Column(db.Float, nullable=True)
    amp_dn_std = db.Column(db.Float, nullable=True)
    amp_dn_current = db.Column(db.Float, nullable=True)
    amp_dn_z = db.Column(db.Float, nullable=True)
    amp_dn_pct = db.Column(db.Float, nullable=True)
    amp_dn_n = db.Column(db.Integer, nullable=True)

    # ---- 1m 量能 Cycle1mVolMax5 ----
    v5_up_mean = db.Column(db.Float, nullable=True)
    v5_up_std = db.Column(db.Float, nullable=True)
    v5_up_current = db.Column(db.Float, nullable=True)
    v5_up_z = db.Column(db.Float, nullable=True)
    v5_up_pct = db.Column(db.Float, nullable=True)
    v5_up_n = db.Column(db.Integer, nullable=True)
    v5_dn_mean = db.Column(db.Float, nullable=True)
    v5_dn_std = db.Column(db.Float, nullable=True)
    v5_dn_current = db.Column(db.Float, nullable=True)
    v5_dn_z = db.Column(db.Float, nullable=True)
    v5_dn_pct = db.Column(db.Float, nullable=True)
    v5_dn_n = db.Column(db.Integer, nullable=True)

    # ---- 元信息 ----
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        """转换为 dict（前端 / API 用）"""
        out = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ('snapshot_date',):
            v = out.get(k)
            if v is not None:
                out[k] = v.isoformat()
        for k in ('last_bar_date', 'created_at', 'updated_at'):
            v = out.get(k)
            if v is not None:
                out[k] = v.strftime('%Y-%m-%d %H:%M:%S')
        return out
