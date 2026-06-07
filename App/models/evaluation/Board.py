"""
板块主表（板块注册表）

统一的板块清单：一个板块一行（board_code 唯一），作为全系统板块的权威注册表。
板块原先隐式散落在 industry_eastmoney / concept_board / data_stock_classification /
eval_board_trend_score 里，缺乏统一入口；本表把"板块本身的定义"集中管理，
便于增删改、同步、以及趋势/偏好/预览各模块统一引用。
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

CLASSIFICATIONS = ('行业板块', '概念板块', '自定义')
SOURCES = ('industry', 'concept', 'manual')


class Board(db.Model):
    """板块主表（每个板块一条）"""
    __tablename__ = 'eval_board'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    board_code = db.Column(db.String(20), nullable=False, unique=True,
                           comment='板块代码（如 BK0437），唯一')
    board_name = db.Column(db.String(50), comment='板块名称')
    classification = db.Column(db.String(20), default='行业板块',
                               comment='分类：行业板块/概念板块/自定义')
    source = db.Column(db.String(20), default='manual',
                       comment='来源：industry/concept/manual')
    member_count = db.Column(db.Integer, comment='成分股数量（来自 industry_eastmoney 最新快照）')
    has_daily_data = db.Column(db.Boolean, default=False, comment='是否有日 K 数据')
    enabled = db.Column(db.Boolean, default=True, comment='是否启用（参与趋势计算/预览）')
    notes = db.Column(db.String(200), comment='备注')
    last_member_sync = db.Column(db.DateTime, comment='成分股最近同步时间')

    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<Board {self.board_code}:{self.board_name} enabled={self.enabled}>'

    def to_dict(self):
        return {
            'id': self.id,
            'board_code': self.board_code,
            'board_name': self.board_name,
            'classification': self.classification,
            'source': self.source,
            'member_count': self.member_count,
            'has_daily_data': self.has_daily_data,
            'enabled': self.enabled,
            'notes': self.notes,
            'last_member_sync': self.last_member_sync.isoformat() if self.last_member_sync else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
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
    def upsert(cls, board_code, **fields):
        """按 board_code upsert。不允许覆盖主键/唯一键。"""
        fields.pop('id', None)
        fields.pop('board_code', None)
        row = cls.query.filter_by(board_code=board_code).first()
        if row is None:
            row = cls(board_code=board_code, **fields)
            db.session.add(row)
        else:
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @classmethod
    def list_enabled(cls, require_data=False):
        """返回启用的板块；require_data=True 时仅含有日 K 数据的。"""
        q = cls.query.filter(cls.enabled.isnot(False))
        if require_data:
            q = q.filter(cls.has_daily_data.is_(True))
        return q.order_by(cls.board_code.asc()).all()

    @classmethod
    def delete_by_id(cls, row_id):
        row = cls.query.get(row_id)
        if not row:
            return False
        db.session.delete(row)
        db.session.commit()
        return True
