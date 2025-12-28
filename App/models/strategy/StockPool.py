"""
股票池管理模型
基于record_stock_minute表建立股票池，用于模型数据整理
"""
from App.exts import db
from datetime import datetime, date
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class StockPool(db.Model):
    """
    股票池管理表
    基于record_stock_minute表建立，用于管理模型训练所需的股票池
    """
    __tablename__ = 'stock_pool'
    __bind_key__ = 'quanttradingsystem'

    # 主键
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 关联到record_stock_minute表
    record_id = db.Column(db.BigInteger, db.ForeignKey('record_stock_minute.id', ondelete='CASCADE'), 
                         nullable=False, comment='关联的下载记录ID')
    
    # 股票基本信息
    stock_code = db.Column(db.String(10), nullable=False, comment='股票代码')
    stock_name = db.Column(db.String(50), nullable=True, comment='股票名称')
    
    # 股票池分类
    pool_type = db.Column(db.String(20), default='general', comment='股票池类型：general/training/testing/validation')
    pool_priority = db.Column(db.Integer, default=1, comment='优先级：1-5，5最高')
    
    # 数据质量评估
    data_quality_score = db.Column(db.Float, default=0.0, comment='数据质量评分(0-100)')
    data_completeness = db.Column(db.Float, default=0.0, comment='数据完整性(0-100)')
    last_data_update = db.Column(db.Date, nullable=True, comment='最后数据更新日期')
    
    # 模型训练相关
    is_training_ready = db.Column(db.Boolean, default=False, comment='是否准备好进行模型训练')
    training_status = db.Column(db.String(20), default='pending', comment='训练状态：pending/processing/completed/failed')
    last_training_date = db.Column(db.Date, nullable=True, comment='最后训练日期')
    
    # 技术指标
    market_cap = db.Column(db.BigInteger, nullable=True, comment='市值')
    pe_ratio = db.Column(db.Float, nullable=True, comment='市盈率')
    pb_ratio = db.Column(db.Float, nullable=True, comment='市净率')
    industry = db.Column(db.String(50), nullable=True, comment='所属行业')
    board = db.Column(db.String(20), nullable=True, comment='所属板块')
    
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
                          pool_type: str = 'general', **kwargs) -> 'StockPool':
        """
        从record_stock_minute记录创建股票池条目
        
        Args:
            record_id: record_stock_minute表的ID
            stock_code: 股票代码
            stock_name: 股票名称
            pool_type: 股票池类型
            **kwargs: 其他字段
            
        Returns:
            StockPool: 创建的股票池对象
        """
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
    def get_by_pool_type(cls, pool_type: str, is_active: bool = True) -> List['StockPool']:
        """
        根据股票池类型获取股票列表
        
        Args:
            pool_type: 股票池类型
            is_active: 是否只获取激活的股票
            
        Returns:
            List[StockPool]: 股票池列表
        """
        query = cls.query.filter_by(pool_type=pool_type)
        if is_active:
            query = query.filter_by(is_active=True, is_excluded=False)
        return query.order_by(cls.pool_priority.desc(), cls.data_quality_score.desc()).all()

    @classmethod
    def get_training_ready_stocks(cls, limit: int = None) -> List['StockPool']:
        """
        获取准备好进行模型训练的股票
        
        Args:
            limit: 限制返回数量
            
        Returns:
            List[StockPool]: 训练就绪的股票列表
        """
        query = cls.query.filter_by(
            is_training_ready=True,
            is_active=True,
            is_excluded=False
        ).order_by(cls.pool_priority.desc(), cls.data_quality_score.desc())
        
        if limit:
            query = query.limit(limit)
            
        return query.all()

    @classmethod
    def get_by_industry(cls, industry: str, is_active: bool = True) -> List['StockPool']:
        """
        根据行业获取股票列表
        
        Args:
            industry: 行业名称
            is_active: 是否只获取激活的股票
            
        Returns:
            List[StockPool]: 股票列表
        """
        query = cls.query.filter_by(industry=industry)
        if is_active:
            query = query.filter_by(is_active=True, is_excluded=False)
        return query.order_by(cls.data_quality_score.desc()).all()

    @classmethod
    def get_high_quality_stocks(cls, min_quality_score: float = 80.0, 
                               min_completeness: float = 90.0) -> List['StockPool']:
        """
        获取高质量股票列表
        
        Args:
            min_quality_score: 最低质量评分
            min_completeness: 最低完整性评分
            
        Returns:
            List[StockPool]: 高质量股票列表
        """
        return cls.query.filter(
            cls.data_quality_score >= min_quality_score,
            cls.data_completeness >= min_completeness,
            cls.is_active == True,
            cls.is_excluded == False
        ).order_by(cls.data_quality_score.desc()).all()

    @classmethod
    def update_data_quality(cls, stock_code: str, quality_score: float, 
                           completeness: float, last_update: date = None) -> bool:
        """
        更新股票数据质量信息
        
        Args:
            stock_code: 股票代码
            quality_score: 质量评分
            completeness: 完整性评分
            last_update: 最后更新日期
            
        Returns:
            bool: 更新是否成功
        """
        try:
            stock = cls.query.filter_by(stock_code=stock_code).first()
            if stock:
                stock.data_quality_score = quality_score
                stock.data_completeness = completeness
                if last_update:
                    stock.last_data_update = last_update
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                logger.info(f"成功更新股票 {stock_code} 的数据质量信息")
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新股票数据质量信息失败: {e}")
            return False

    @classmethod
    def update_training_status(cls, stock_code: str, status: str, 
                              training_date: date = None) -> bool:
        """
        更新股票训练状态
        
        Args:
            stock_code: 股票代码
            status: 训练状态
            training_date: 训练日期
            
        Returns:
            bool: 更新是否成功
        """
        try:
            stock = cls.query.filter_by(stock_code=stock_code).first()
            if stock:
                stock.training_status = status
                if training_date:
                    stock.last_training_date = training_date
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                logger.info(f"成功更新股票 {stock_code} 的训练状态: {status}")
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新股票训练状态失败: {e}")
            return False

    @classmethod
    def exclude_stock(cls, stock_code: str, reason: str) -> bool:
        """
        排除股票
        
        Args:
            stock_code: 股票代码
            reason: 排除原因
            
        Returns:
            bool: 操作是否成功
        """
        try:
            stock = cls.query.filter_by(stock_code=stock_code).first()
            if stock:
                stock.is_excluded = True
                stock.exclusion_reason = reason
                stock.is_active = False
                stock.updated_at = datetime.utcnow()
                db.session.commit()
                logger.info(f"成功排除股票 {stock_code}: {reason}")
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"排除股票失败: {e}")
            return False

    @classmethod
    def get_pool_statistics(cls) -> Dict[str, Any]:
        """
        获取股票池统计信息
        
        Returns:
            Dict[str, Any]: 统计信息
        """
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
            
            return {
                'total_stocks': total_stocks,
                'active_stocks': active_stocks,
                'training_ready': training_ready,
                'excluded_stocks': total_stocks - active_stocks,
                'type_distribution': {item.pool_type: item.count for item in type_stats},
                'industry_distribution': {item.industry: item.count for item in industry_stats if item.industry}
            }
        except Exception as e:
            logger.error(f"获取股票池统计信息失败: {e}")
            return {}

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典格式
        
        Returns:
            Dict[str, Any]: 字典格式的数据
        """
        return {
            'id': self.id,
            'record_id': self.record_id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'pool_type': self.pool_type,
            'pool_priority': self.pool_priority,
            'data_quality_score': self.data_quality_score,
            'data_completeness': self.data_completeness,
            'last_data_update': self.last_data_update.isoformat() if self.last_data_update else None,
            'is_training_ready': self.is_training_ready,
            'training_status': self.training_status,
            'last_training_date': self.last_training_date.isoformat() if self.last_training_date else None,
            'market_cap': self.market_cap,
            'pe_ratio': self.pe_ratio,
            'pb_ratio': self.pb_ratio,
            'industry': self.industry,
            'board': self.board,
            'is_active': self.is_active,
            'is_excluded': self.is_excluded,
            'exclusion_reason': self.exclusion_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }



