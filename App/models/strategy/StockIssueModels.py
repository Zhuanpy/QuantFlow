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
    待实现/想法/问题清单。

    用于在脑海里突然冒出的想法、待修的 bug、待做的功能，
    一行记下，不至于忘掉，等有时间再回来实现。
    """
    __tablename__ = 'strategy_issues'
    __bind_key__ = 'quanttradingsystem'

    # 状态常量
    STATUS_PENDING = '待实现'
    STATUS_IN_PROGRESS = '进行中'
    STATUS_DONE = '已实现'
    STATUS_CLOSED = '已关闭'

    ALL_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_DONE, STATUS_CLOSED)

    # 兼容旧调用
    STATUS_UNRESOLVED = STATUS_PENDING
    STATUS_RESOLVED = STATUS_DONE

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    question = db.Column(db.String(200), nullable=False, comment='标题（一句话说明）')
    solution = db.Column(db.Text, nullable=True, comment='详细描述 / 实现说明 / 备注')
    status = db.Column(db.String(20), default=STATUS_PENDING, comment='状态')
    
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
            if status == self.STATUS_DONE and not self.resolved_at:
                self.resolved_at = datetime.utcnow()
            elif status != self.STATUS_DONE:
                self.resolved_at = None
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
