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

# 跟踪层级（见《资金流热点监控系统_设计说明书》§11.1）
#   0 = L0 仅记录（只在 mkt_sector_flow_daily 里留每日热度快照，不做成分/合成/评分）
#   1 = L1 候选池（做成分同步 + 合成指数 + v1-mtf 趋势评分）—— 概念限额，动态进出
#   2 = L2 主线深跟（生命周期状态机，尚未实现）
TIER_L0, TIER_L1, TIER_L2 = 0, 1, 2

# 被闸门挡在 L1 之外的原因
EXCL_ATTRIBUTE_TAG = 'attribute_tag'   # 属性标签类概念（融资融券/沪股通/机构重仓…）
EXCL_TOO_BROAD = 'too_broad'           # 成分股过多，覆盖太宽没有主线含义
EXCL_ALIAS = 'alias'                   # 与在池板块成分高度重叠，折叠到主线板块


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

    # —— L1 候选池 ——
    tracking_tier = db.Column(db.Integer, default=TIER_L0,
                              comment='跟踪层级 0=仅记录 1=候选池 2=主线深跟')
    is_pinned = db.Column(db.Boolean, default=False,
                          comment='手工钉选：恒在池内，不受出池规则约束')
    tier_since = db.Column(db.Date, comment='进入当前层级的日期')
    last_top_date = db.Column(db.Date, comment='最后一次进入热度榜(TOP N)的日期')
    alias_of = db.Column(db.String(20),
                         comment='成分高度重叠时折叠到的主线板块代码')
    exclude_reason = db.Column(db.String(40),
                               comment='被挡在 L1 外的原因 attribute_tag/too_broad/alias/manual')

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
            'tracking_tier': self.tracking_tier if self.tracking_tier is not None else TIER_L0,
            'is_pinned': bool(self.is_pinned),
            'tier_since': self.tier_since.isoformat() if self.tier_since else None,
            'last_top_date': self.last_top_date.isoformat() if self.last_top_date else None,
            'alias_of': self.alias_of,
            'exclude_reason': self.exclude_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    # 老表缺列时惰性 ALTER 补上（项目没有迁移工具）
    _ADD_COLUMNS = {
        'tracking_tier': 'INT NULL DEFAULT 0 COMMENT "跟踪层级 0仅记录/1候选池/2主线"',
        'is_pinned': 'TINYINT(1) NULL DEFAULT 0 COMMENT "手工钉选"',
        'tier_since': 'DATE NULL COMMENT "进入当前层级的日期"',
        'last_top_date': 'DATE NULL COMMENT "最后一次进热度榜的日期"',
        'alias_of': 'VARCHAR(20) NULL COMMENT "折叠到的主线板块代码"',
        'exclude_reason': 'VARCHAR(40) NULL COMMENT "被挡在L1外的原因"',
    }

    @classmethod
    def ensure_table(cls):
        """惰性建表 + 缺列自动补齐：项目无统一 create_all，也没有迁移工具。"""
        try:
            eng = db.engines.get(cls.__bind_key__)
            cls.__table__.create(bind=eng, checkfirst=True)
        except Exception as e:
            logger.warning(f'确保 {cls.__tablename__} 表存在失败: {e}')
            return
        try:
            from sqlalchemy import text
            insp = db.inspect(eng)
            have = {c['name'] for c in insp.get_columns(cls.__tablename__)}
            missing = [(k, v) for k, v in cls._ADD_COLUMNS.items() if k not in have]
            if missing:
                with eng.begin() as conn:
                    for name, ddl in missing:
                        conn.execute(text(
                            f'ALTER TABLE {cls.__tablename__} ADD COLUMN {name} {ddl}'))
                        logger.info(f'[board] 补列 {name}')
        except Exception as e:
            logger.warning(f'{cls.__tablename__} 补列失败: {e}')

    @classmethod
    def list_by_tier(cls, tier, classification=None):
        """取某一层级的板块（tier=1 即 L1 候选池）。"""
        q = cls.query.filter(cls.tracking_tier == tier, cls.enabled.isnot(False))
        if classification:
            q = q.filter(cls.classification == classification)
        return q.order_by(cls.board_code.asc()).all()

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
