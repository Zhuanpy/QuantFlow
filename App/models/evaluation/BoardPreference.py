"""
板块个人偏好打分数据模型

与 BoardTrendScore 的「机器打分」不同，这里记录的是用户对板块的主观喜好：
1 分（不喜欢）~ 10 分（很喜欢）。偏好是板块的固有属性，与具体交易日无关，
因此按 board_code 唯一存储，不随 record_date 变化。
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BoardPreference(db.Model):
    """板块个人偏好打分表（每个板块一条，1-10 分）"""
    __tablename__ = 'eval_board_preference'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    board_code = db.Column(db.String(20), nullable=False, unique=True,
                           comment='板块代码（如 BK0437），唯一')
    board_name = db.Column(db.String(50), comment='板块名称（冗余，便于查看）')
    preference_score = db.Column(db.Integer, comment='个人偏好 1-10，1=不喜欢 10=很喜欢；NULL=未打分')
    notes = db.Column(db.String(200), comment='偏好备注')

    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, comment='更新时间')

    def to_dict(self):
        return {
            'board_code': self.board_code,
            'board_name': self.board_name,
            'preference_score': self.preference_score,
            'notes': self.notes,
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
    def upsert(cls, board_code, preference_score=None, board_name=None, notes=None):
        """按 board_code upsert 偏好。preference_score 传 None 表示清空打分。"""
        row = cls.query.filter_by(board_code=board_code).first()
        if row is None:
            row = cls(board_code=board_code)
            db.session.add(row)
        row.preference_score = preference_score
        if board_name is not None:
            row.board_name = board_name
        if notes is not None:
            row.notes = notes
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @classmethod
    def map_for(cls, board_codes):
        """批量取偏好分，返回 {board_code: preference_score(int|None)}。"""
        if not board_codes:
            return {}
        rows = cls.query.filter(cls.board_code.in_(list(board_codes))).all()
        return {r.board_code: r.preference_score for r in rows}
