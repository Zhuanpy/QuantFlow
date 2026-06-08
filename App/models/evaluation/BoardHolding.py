"""
板块持仓跟踪表

用户持有某板块对应的 ETF（如银行 ETF）时，把"板块本身"登记进来，
使其出现在 /trade/holdings/realtime 持仓盘中列表里，像个股一样跟踪板块趋势。

独立于交易系统（trade_records / trade_plans），避免把"非真实成交"的板块塞进成交/计划表。
etf_code/etf_name 只是用户登记的参考信息（实际持有的 ETF），不参与盈亏计算。
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BoardHolding(db.Model):
    """跟踪中的板块（一板块一行）"""
    __tablename__ = 'eval_board_holding'
    __bind_key__ = 'quanttradingsystem'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    board_code = db.Column(db.String(20), nullable=False, unique=True,
                           comment='板块代码（如 BK0475），唯一')
    board_name = db.Column(db.String(50), comment='板块名称')
    etf_code = db.Column(db.String(20), comment='用户实际持有的对应 ETF 代码（可空，仅参考）')
    etf_name = db.Column(db.String(50), comment='ETF 名称（可空）')
    note = db.Column(db.String(200), comment='备注')

    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, comment='更新时间')

    def to_dict(self):
        return {
            'board_code': self.board_code,
            'board_name': self.board_name,
            'etf_code': self.etf_code,
            'etf_name': self.etf_name,
            'note': self.note,
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
    def upsert(cls, board_code, board_name=None, etf_code=None, etf_name=None, note=None):
        """按 board_code upsert。None 值仅在传入时覆盖（不传则保留原值）。"""
        row = cls.query.filter_by(board_code=board_code).first()
        if row is None:
            row = cls(board_code=board_code)
            db.session.add(row)
        if board_name is not None:
            row.board_name = board_name
        if etf_code is not None:
            row.etf_code = etf_code or None
        if etf_name is not None:
            row.etf_name = etf_name or None
        if note is not None:
            row.note = note or None
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @classmethod
    def delete_by_code(cls, board_code):
        row = cls.query.filter_by(board_code=board_code).first()
        if not row:
            return False
        db.session.delete(row)
        db.session.commit()
        return True

    @classmethod
    def list_all(cls):
        return cls.query.order_by(cls.board_code.asc()).all()
