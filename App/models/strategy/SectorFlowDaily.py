"""板块（行业+概念）每日热度快照 —— 热点追踪 L0 层。

定位（见《资金流热点监控系统_设计说明书》§2/§7）：
  L0 = **全量记录层**。行业(86) + 概念(300+) 一天一条，不做成分、不做合成日K、不做趋势评分，
  成本只有几个 HTTP 请求。热点"是一日游还是持续"靠的就是这张表的**纵向序列**。
  L1(候选池评分) / L2(生命周期状态机) 在它之上做，不在这里。

数据源：东方财富 push2 clist/get 板块列表（fs=m:90+t:2 行业 / t:3 概念），
失败回退 akshare（仅主线程，见 sector_flow_service）；行业还可用本地板块日K回补历史。

热度分 heat_score（v0）
----------------------
    heat_score = 50 × pct(涨跌幅) + 50 × pct(成交额占比)          # pct=同类型当日横截面百分位 0-100
故意只用两个"必得且难操纵"的字段等权合成：
  - 换手率/主力净额/涨跌家数只存不入分（主力净额是东财推断值，设计书定为 C 级）；
  - 等权 = 无先验，不是拍脑袋加权；真正的权重要等回测（设计书 §6）出来再定。
  - 三种来源(em/ak/hist)字段不同，用 heat_version 区分，避免把口径不同的分数混画一条线。

每天每板块一行（date + board_code 唯一），整日按 board_type 替换写入。
"""
from datetime import datetime, date

from App.exts import db

# 热度分版本：口径不同的分数不可混用
HEAT_V_EM = 'heat-v0'        # 东财全字段：涨幅 + 成交额占比
HEAT_V_AK = 'heat-v0-ak'     # akshare 兜底：无成交额，用换手率代理
HEAT_V_HIST = 'heat-v0-hist'  # 本地板块日K回补：涨幅 + 成交额占比（无换手/家数）

# 数据来源
SRC_EM = 'em'
SRC_EM_DELAY = 'em-delay'   # 东财延时行情主机（盘中延迟约15分钟；收盘后与实时等价）
SRC_AK = 'ak'
SRC_HIST = 'hist'


class SectorFlowDaily(db.Model):
    __tablename__ = 'mkt_sector_flow_daily'
    __bind_key__ = 'quanttradingsystem'
    __table_args__ = (
        db.UniqueConstraint('date', 'board_code', name='uq_sector_flow_date_code'),
        db.Index('idx_sector_flow_date', 'date'),
        db.Index('idx_sector_flow_type_date', 'board_type', 'date'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, nullable=False, comment='快照日期')
    board_type = db.Column(db.String(20), nullable=False,
                           comment='横截面单位：industry 一级行业 / industry_sub 细分行业 / concept 概念')
    board_code = db.Column(db.String(20), nullable=False, comment='板块代码 BKxxxx')
    board_name = db.Column(db.String(50), comment='板块名称')

    # —— 原始字段 ——
    change_pct = db.Column(db.Float, comment='涨跌幅%')
    amount = db.Column(db.Float, comment='成交额(元)')
    main_net = db.Column(db.Float, comment='主力净流入额(元)（东财推断值，C级，仅展示不入分）')
    main_pct = db.Column(db.Float, comment='主力净流入占比%')
    turnover_rate = db.Column(db.Float, comment='换手率%')
    total_cap = db.Column(db.Float, comment='总市值(元)')
    up_count = db.Column(db.Integer, comment='上涨家数')
    down_count = db.Column(db.Integer, comment='下跌家数')
    lead_stock = db.Column(db.String(50), comment='领涨股名称')
    lead_pct = db.Column(db.Float, comment='领涨股涨跌幅%')

    # —— 派生字段（写入时按当日同类型横截面算）——
    amount_share = db.Column(db.Float, comment='成交额占当日同类型板块合计之比%')
    rank_chg = db.Column(db.Integer, comment='当日涨幅排名（同类型内，1=最高）')
    rank_amt = db.Column(db.Integer, comment='当日成交额排名（同类型内，1=最高）')
    rank_heat = db.Column(db.Integer, comment='当日热度排名（同类型内，1=最热）')
    heat_score = db.Column(db.Float, comment='热度分 0-100（见模块 docstring）')
    heat_version = db.Column(db.String(16), comment='热度分口径版本 heat-v0/-ak/-hist')

    # —— 元数据 ——
    source = db.Column(db.String(10), default=SRC_EM,
                       comment='来源 em/em-delay/ak/hist')
    is_intraday = db.Column(db.Boolean, default=False,
                            comment='是否盘中快照（收盘后跑会覆盖为最终值）')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ---------------- 建表 / 加列 ----------------
    # 项目不做全局 create_all，也没有迁移工具：老表缺列时在这里惰性 ALTER 补上。
    _ADD_COLUMNS = {
        'amount': 'DOUBLE NULL COMMENT "成交额(元)"',
        'amount_share': 'DOUBLE NULL COMMENT "成交额占同类型合计之比%"',
        'rank_chg': 'INT NULL COMMENT "当日涨幅排名"',
        'rank_amt': 'INT NULL COMMENT "当日成交额排名"',
        'rank_heat': 'INT NULL COMMENT "当日热度排名"',
        'heat_score': 'DOUBLE NULL COMMENT "热度分 0-100"',
        'heat_version': 'VARCHAR(16) NULL COMMENT "热度分口径版本"',
        'source': 'VARCHAR(10) NULL DEFAULT "em" COMMENT "来源 em/em-delay/ak/hist"',
        'is_intraday': 'TINYINT(1) NULL DEFAULT 0 COMMENT "是否盘中快照"',
    }

    # 老表列宽不够时要 MODIFY（board_type 原为 VARCHAR(10)，装不下 'industry_sub'）
    _WIDEN_COLUMNS = {
        'board_type': (20, 'VARCHAR(20) NOT NULL COMMENT '
                           '"横截面单位 industry/industry_sub/concept"'),
    }

    @classmethod
    def ensure_table(cls):
        """惰性建表 + 缺列自动补齐 + 列宽自动加宽（项目不做全局 create_all）。"""
        import logging
        logger = logging.getLogger(__name__)
        from sqlalchemy import text
        eng = db.engines['quanttradingsystem']
        insp = db.inspect(eng)
        if not insp.has_table(cls.__tablename__):
            cls.__table__.create(bind=eng)
            return
        cols = {c['name']: c for c in insp.get_columns(cls.__tablename__)}

        # 先加宽（不够宽会直接插入报 1406）
        for name, (need_len, ddl) in cls._WIDEN_COLUMNS.items():
            col = cols.get(name)
            if col is None:
                continue
            cur_len = getattr(col.get('type'), 'length', None)
            if cur_len is None or cur_len < need_len:
                try:
                    with eng.begin() as conn:
                        conn.execute(text(
                            f'ALTER TABLE {cls.__tablename__} MODIFY COLUMN {name} {ddl}'))
                    logger.info(f'[sector_flow] 加宽列 {name} -> {need_len}')
                except Exception as e:
                    logger.warning(f'[sector_flow] 加宽列 {name} 失败: {e}')

        have = set(cols)
        missing = [(k, v) for k, v in cls._ADD_COLUMNS.items() if k not in have]
        if not missing:
            return
        with eng.begin() as conn:
            for name, ddl in missing:
                try:
                    conn.execute(text(f'ALTER TABLE {cls.__tablename__} ADD COLUMN {name} {ddl}'))
                    logger.info(f'[sector_flow] 补列 {name}')
                except Exception as e:
                    logger.warning(f'[sector_flow] 补列 {name} 失败: {e}')

    # ---------------- 查询 ----------------
    @classmethod
    def latest_date(cls, board_type=None):
        q = db.session.query(db.func.max(cls.date))
        if board_type:
            q = q.filter(cls.board_type == board_type)
        return q.scalar()

    @classmethod
    def trading_dates(cls, board_type=None, limit=None):
        """已记录的快照日期，按日期升序。"""
        q = db.session.query(cls.date).distinct()
        if board_type:
            q = q.filter(cls.board_type == board_type)
        rows = [r[0] for r in q.order_by(cls.date.desc()).limit(limit).all()] if limit else \
               [r[0] for r in q.order_by(cls.date.desc()).all()]
        return sorted(rows)

    def to_dict(self):
        return {
            'date': self.date.isoformat() if self.date else None,
            'board_type': self.board_type,
            'board_code': self.board_code,
            'board_name': self.board_name,
            'change_pct': self.change_pct,
            'amount': self.amount,
            'amount_share': self.amount_share,
            'main_net': self.main_net,
            'main_pct': self.main_pct,
            'turnover_rate': self.turnover_rate,
            'up_count': self.up_count,
            'down_count': self.down_count,
            'lead_stock': self.lead_stock,
            'lead_pct': self.lead_pct,
            'rank_chg': self.rank_chg,
            'rank_amt': self.rank_amt,
            'rank_heat': self.rank_heat,
            'heat_score': self.heat_score,
            'heat_version': self.heat_version,
            'source': self.source,
            'is_intraday': bool(self.is_intraday),
        }

    def __repr__(self):
        return (f'<SectorFlowDaily {self.date} {self.board_type} {self.board_code} '
                f'heat={self.heat_score}>')
