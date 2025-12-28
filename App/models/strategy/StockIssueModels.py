"""
股票问题模型
用于管理股票相关的问题和解决方案
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class Issue(db.Model):
    """
    股票问题模型
    
    用于记录和管理股票相关的问题及其解决方案
    """
    __tablename__ = 'stock_issue'
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    # 状态常量
    STATUS_UNRESOLVED = '未解决'
    STATUS_RESOLVED = '已解决'
    STATUS_IN_PROGRESS = '处理中'
    STATUS_CLOSED = '已关闭'

    # 主键
    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 问题信息
    question = db.Column(db.String(200), nullable=False, comment='问题描述')
    solution = db.Column(db.String(500), nullable=True, comment='解决方案')
    status = db.Column(db.String(20), default=STATUS_UNRESOLVED, comment='问题状态')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')
    resolved_at = db.Column(db.DateTime, nullable=True, comment='解决时间')

    def __repr__(self):
        """
        返回对象的字符串表示
        """
        return f'<Issue {self.id} - {self.question}>'

    @classmethod
    def get_unresolved_issues(cls):
        """
        获取所有未解决的问题
        
        Returns:
            List[Issue]: 未解决问题列表
        """
        return cls.query.filter_by(status=cls.STATUS_UNRESOLVED).order_by(cls.created_at.desc()).all()

    @classmethod
    def get_resolved_issues(cls):
        """
        获取所有已解决的问题
        
        Returns:
            List[Issue]: 已解决问题列表
        """
        return cls.query.filter_by(status=cls.STATUS_RESOLVED).order_by(cls.resolved_at.desc()).all()

    @classmethod
    def get_issues_by_status(cls, status: str):
        """
        获取指定状态的问题
        
        Args:
            status: 状态字符串
            
        Returns:
            List[Issue]: 问题列表
        """
        return cls.query.filter_by(status=status).order_by(cls.created_at.desc()).all()

    def resolve_issue(self, solution: str):
        """
        解决问题
        
        Args:
            solution: 解决方案
            
        Returns:
            bool: 解决是否成功
        """
        try:
            self.solution = solution
            self.status = self.STATUS_RESOLVED
            self.resolved_at = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"成功解决问题 {self.id}: {self.question}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"解决问题时出错: {str(e)}")
            return False

    def update_status(self, status: str):
        """
        更新问题状态
        
        Args:
            status: 新状态
            
        Returns:
            bool: 更新是否成功
        """
        try:
            self.status = status
            self.updated_at = datetime.utcnow()
            if status == self.STATUS_RESOLVED and not self.resolved_at:
                self.resolved_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"成功更新问题 {self.id} 状态为: {status}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新问题状态时出错: {str(e)}")
            return False

    def to_dict(self):
        """
        转换为字典格式
        
        Returns:
            dict: 字典格式的数据
        """
        return {
            'id': self.id,
            'question': self.question,
            'solution': self.solution,
            'status': self.status,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None,
            'resolved_at': self.resolved_at.strftime('%Y-%m-%d %H:%M:%S') if self.resolved_at else None
        }

    def is_resolved(self):
        """
        检查问题是否已解决
        
        Returns:
            bool: 是否已解决
        """
        return self.status == self.STATUS_RESOLVED
