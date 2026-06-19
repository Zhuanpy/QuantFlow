"""
板块趋势阶段分布每日快照数据模型

板块整体预览页（/board_overview）顶部的「趋势阶段分布」是对全部板块
最新一条趋势打分的横向统计。本表把每天的这份分布快照下来，便于做
「每日 4 类趋势数量变化」的时间序列展示。

按 record_date 唯一存储（一天一条，重复记录走 upsert 覆盖）。
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BoardTrendDistribution(db.Model):
    """板块趋势阶段分布每日快照（一天一条）"""
    __tablename__ = 'eval_board_trend_distribution'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.Index('ix_board_trend_dist_date', 'record_date'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    record_date = db.Column(db.Date, nullable=False, unique=True,
                            comment='快照日期（取最新评分日期），唯一')

    # —— 四个核心趋势阶段 + 未识别 + 无评分 ——
    up_early = db.Column(db.Integer, default=0, comment='上涨前期板块数')
    up_late = db.Column(db.Integer, default=0, comment='上涨后期板块数')
    down_early = db.Column(db.Integer, default=0, comment='下跌前期板块数')
    down_late = db.Column(db.Integer, default=0, comment='下跌后期板块数')
    unknown = db.Column(db.Integer, default=0, comment='未识别/横盘板块数')
    none = db.Column(db.Integer, default=0, comment='无评分板块数')
    total_boards = db.Column(db.Integer, default=0, comment='当日主表板块总数')

    source = db.Column(db.String(10), default='manual',
                       comment='来源 manual/auto，便于区分手工与自动快照')

    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return (f'<BoardTrendDistribution {self.record_date} '
                f'up={self.up_early}/{self.up_late} '
                f'down={self.down_early}/{self.down_late}>')

    def to_dict(self):
        return {
            'record_date': self.record_date.isoformat() if self.record_date else None,
            'up_early': self.up_early or 0,
            'up_late': self.up_late or 0,
            'down_early': self.down_early or 0,
            'down_late': self.down_late or 0,
            'unknown': self.unknown or 0,
            'none': self.none or 0,
            'total_boards': self.total_boards or 0,
            'source': self.source,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def ensure_table(cls):
        """惰性建表：项目无统一 create_all，首次使用时确保表存在。"""
        try:
            eng = db.engines.get(cls.__bind_key__)
            cls.__table__.create(bind=eng, checkfirst=True)
        except Exception as e:
            logger.warning(f'确保 {cls.__tablename__} 表存在失败: {e}')

    @classmethod
    def upsert(cls, record_date, stage_counts, total_boards, source='manual'):
        """按 record_date upsert 一份分布快照。

        stage_counts: dict，键为 up_early/up_late/down_early/down_late/unknown/none。
        """
        row = cls.query.filter_by(record_date=record_date).first()
        if row is None:
            row = cls(record_date=record_date)
            db.session.add(row)
        row.up_early = int(stage_counts.get('up_early', 0) or 0)
        row.up_late = int(stage_counts.get('up_late', 0) or 0)
        row.down_early = int(stage_counts.get('down_early', 0) or 0)
        row.down_late = int(stage_counts.get('down_late', 0) or 0)
        row.unknown = int(stage_counts.get('unknown', 0) or 0)
        row.none = int(stage_counts.get('none', 0) or 0)
        row.total_boards = int(total_boards or 0)
        row.source = source
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @classmethod
    def get_history(cls, start_date=None, end_date=None, limit=None):
        """取分布历史，按日期升序（便于前端直接画时间序列）。"""
        q = cls.query
        if start_date:
            q = q.filter(cls.record_date >= start_date)
        if end_date:
            q = q.filter(cls.record_date <= end_date)
        q = q.order_by(cls.record_date.asc())
        if limit:
            # limit 取最近 N 条：先按降序截断，再翻回升序
            rows = (q.order_by(None).order_by(cls.record_date.desc())
                    .limit(limit).all())
            return list(reversed(rows))
        return q.all()
