"""
股票池管理模型
管理交易中、观察中、候选、归档等不同状态的股票池
"""
from App.exts import db
from datetime import datetime, date
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class StockPool(db.Model):
    """
    股票池管理表
    支持交易、观察、候选、归档等多种股票池类型
    """
    __tablename__ = 'strategy_stock_pool'
    __bind_key__ = 'quanttradingsystem'

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')

    # 关联到data_download_records表（可选，手动添加的股票可能没有下载记录）
    record_id = db.Column(db.BigInteger, db.ForeignKey('data_download_records.id', ondelete='SET NULL'),
                         nullable=True, comment='关联的下载记录ID')

    # 股票基本信息
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(50), nullable=True, comment='股票名称')

    # 股票池分类: trading(交易中) / watching(观察中) / candidate(候选) / archived(归档)
    pool_type = db.Column(db.String(20), default='candidate', comment='股票池类型')
    pool_priority = db.Column(db.Integer, default=3, comment='优先级：1-5，5最高')

    # 综合评分
    score = db.Column(db.Float, default=0.0, comment='综合评分(0-100)')
    score_trend = db.Column(db.Float, default=0.0, comment='评分趋势(正数上升，负数下降)')
    score_updated_at = db.Column(db.DateTime, nullable=True, comment='评分更新时间')

    # 数据质量评估
    data_quality_score = db.Column(db.Float, default=0.0, comment='数据质量评分(0-100)')
    data_completeness = db.Column(db.Float, default=0.0, comment='数据完整性(0-100)')
    last_data_update = db.Column(db.Date, nullable=True, comment='最后数据更新日期')

    # 模型训练相关
    is_training_ready = db.Column(db.Boolean, default=False, comment='是否准备好进行模型训练')
    training_status = db.Column(db.String(20), default='pending', comment='训练状态：pending/processing/completed/failed')
    last_training_date = db.Column(db.Date, nullable=True, comment='最后训练日期')

    # 交易相关
    target_price = db.Column(db.Float, nullable=True, comment='目标价')
    stop_loss_price = db.Column(db.Float, nullable=True, comment='止损价')
    current_price = db.Column(db.Float, nullable=True, comment='当前价格')
    position_ratio = db.Column(db.Float, default=0.0, comment='持仓比例(0-100)')

    # 技术指标
    market_cap = db.Column(db.BigInteger, nullable=True, comment='市值')
    pe_ratio = db.Column(db.Float, nullable=True, comment='市盈率')
    pb_ratio = db.Column(db.Float, nullable=True, comment='市净率')
    industry = db.Column(db.String(50), nullable=True, comment='所属行业')
    board = db.Column(db.String(20), nullable=True, comment='所属板块')

    # 备注与标签
    notes = db.Column(db.Text, nullable=True, comment='备注')
    tags = db.Column(db.String(200), nullable=True, comment='标签，逗号分隔')

    # 状态管理
    is_active = db.Column(db.Boolean, default=True, comment='是否激活')
    is_excluded = db.Column(db.Boolean, default=False, comment='是否排除')
    exclusion_reason = db.Column(db.Text, nullable=True, comment='排除原因')

    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f'<StockPool {self.stock_code}:{self.stock_name} - {self.pool_type}>'

    @classmethod
    def create_from_record(cls, record_id: int, stock_code: str, stock_name: str = None,
                          pool_type: str = 'candidate', **kwargs) -> 'StockPool':
        """从下载记录创建股票池条目"""
        try:
            stock_pool = cls(
                record_id=record_id,
                stock_code=stock_code,
                stock_name=stock_name,
                pool_type=pool_type,
                **kwargs
            )
            db.session.add(stock_pool)
            db.session.commit()
            logger.info(f"成功创建股票池条目: {stock_code}")
            return stock_pool
        except Exception as e:
            db.session.rollback()
            logger.error(f"创建股票池条目失败: {e}")
            raise

    @classmethod
    def create_manual(cls, stock_code: str, stock_name: str = None,
                     pool_type: str = 'watching', **kwargs) -> 'StockPool':
        """手动添加股票到股票池"""
        try:
            stock_pool = cls(
                stock_code=stock_code,
                stock_name=stock_name,
                pool_type=pool_type,
                **kwargs
            )
            db.session.add(stock_pool)
            db.session.commit()
            logger.info(f"手动添加股票池条目: {stock_code}")
            return stock_pool
        except Exception as e:
            db.session.rollback()
            logger.error(f"手动添加股票池条目失败: {e}")
            raise

    @classmethod
    def get_by_pool_type(cls, pool_type: str, is_active: bool = True) -> List['StockPool']:
        """根据股票池类型获取股票列表"""
        query = cls.query.filter_by(pool_type=pool_type)
        if is_active:
            query = query.filter_by(is_active=True, is_excluded=False)
        return query.order_by(cls.pool_priority.desc(), cls.score.desc()).all()

    @classmethod
    def get_training_ready_stocks(cls, limit: int = None) -> List['StockPool']:
        """获取准备好进行模型训练的股票"""
        query = cls.query.filter_by(
            is_training_ready=True,
            is_active=True,
            is_excluded=False
        ).order_by(cls.pool_priority.desc(), cls.score.desc())

        if limit:
            query = query.limit(limit)

        return query.all()

    @classmethod
    def get_by_industry(cls, industry: str, is_active: bool = True) -> List['StockPool']:
        """根据行业获取股票列表"""
        query = cls.query.filter_by(industry=industry)
        if is_active:
            query = query.filter_by(is_active=True, is_excluded=False)
        return query.order_by(cls.score.desc()).all()

    @classmethod
    def get_high_quality_stocks(cls, min_quality_score: float = 80.0,
                               min_completeness: float = 90.0) -> List['StockPool']:
        """获取高质量股票列表"""
        return cls.query.filter(
            cls.data_quality_score >= min_quality_score,
            cls.data_completeness >= min_completeness,
            cls.is_active == True,
            cls.is_excluded == False
        ).order_by(cls.score.desc()).all()

    @classmethod
    def update_score(cls, stock_id: int, score: float, trend: float = 0.0) -> bool:
        """更新股票综合评分"""
        try:
            stock = cls.query.get(stock_id)
            if stock:
                stock.score_trend = score - stock.score if stock.score else 0.0
                stock.score = score
                stock.score_updated_at = datetime.utcnow()
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新评分失败: {e}")
            return False

    @classmethod
    def move_to_pool(cls, stock_id: int, pool_type: str) -> bool:
        """将股票移动到指定池"""
        try:
            stock = cls.query.get(stock_id)
            if stock:
                stock.pool_type = pool_type
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"移动股票池失败: {e}")
            return False

    @classmethod
    def update_data_quality(cls, stock_code: str, quality_score: float,
                           completeness: float, last_update: date = None) -> bool:
        """更新股票数据质量信息"""
        try:
            stock = cls.query.filter_by(stock_code=stock_code).first()
            if stock:
                stock.data_quality_score = quality_score
                stock.data_completeness = completeness
                if last_update:
                    stock.last_data_update = last_update
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新数据质量信息失败: {e}")
            return False

    @classmethod
    def update_training_status(cls, stock_code: str, status: str,
                              training_date: date = None) -> bool:
        """更新训练状态"""
        try:
            stock = cls.query.filter_by(stock_code=stock_code).first()
            if stock:
                stock.training_status = status
                if training_date:
                    stock.last_training_date = training_date
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新训练状态失败: {e}")
            return False

    @classmethod
    def exclude_stock(cls, stock_code: str, reason: str) -> bool:
        """排除股票"""
        try:
            stock = cls.query.filter_by(stock_code=stock_code).first()
            if stock:
                stock.is_excluded = True
                stock.exclusion_reason = reason
                stock.is_active = False
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"排除股票失败: {e}")
            return False

    @classmethod
    def get_pool_statistics(cls) -> Dict[str, Any]:
        """获取股票池统计信息"""
        try:
            total_stocks = cls.query.count()
            active_stocks = cls.query.filter_by(is_active=True, is_excluded=False).count()
            training_ready = cls.query.filter_by(is_training_ready=True, is_active=True).count()

            # 按类型统计
            type_stats = db.session.query(
                cls.pool_type,
                db.func.count(cls.id).label('count')
            ).filter_by(is_active=True, is_excluded=False).group_by(cls.pool_type).all()

            # 按行业统计
            industry_stats = db.session.query(
                cls.industry,
                db.func.count(cls.id).label('count')
            ).filter_by(is_active=True, is_excluded=False).group_by(cls.industry).all()

            # 平均评分
            avg_score = db.session.query(
                db.func.avg(cls.score)
            ).filter(cls.is_active == True, cls.score > 0).scalar() or 0.0

            return {
                'total_stocks': total_stocks,
                'active_stocks': active_stocks,
                'training_ready': training_ready,
                'excluded_stocks': total_stocks - active_stocks,
                'avg_score': round(avg_score, 1),
                'type_distribution': {item.pool_type: item.count for item in type_stats},
                'industry_distribution': {item.industry: item.count for item in industry_stats if item.industry}
            }
        except Exception as e:
            logger.error(f"获取股票池统计信息失败: {e}")
            return {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'id': self.id,
            'record_id': self.record_id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'pool_type': self.pool_type,
            'pool_priority': self.pool_priority,
            'score': self.score,
            'score_trend': self.score_trend,
            'score_updated_at': self.score_updated_at.strftime('%Y-%m-%d %H:%M') if self.score_updated_at else None,
            'data_quality_score': self.data_quality_score,
            'data_completeness': self.data_completeness,
            'last_data_update': self.last_data_update.isoformat() if self.last_data_update else None,
            'is_training_ready': self.is_training_ready,
            'training_status': self.training_status,
            'last_training_date': self.last_training_date.isoformat() if self.last_training_date else None,
            'target_price': self.target_price,
            'stop_loss_price': self.stop_loss_price,
            'current_price': self.current_price,
            'position_ratio': self.position_ratio,
            'market_cap': self.market_cap,
            'pe_ratio': self.pe_ratio,
            'pb_ratio': self.pb_ratio,
            'industry': self.industry,
            'board': self.board,
            'notes': self.notes,
            'tags': self.tags,
            'is_active': self.is_active,
            'is_excluded': self.is_excluded,
            'exclusion_reason': self.exclusion_reason,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else None
        }
