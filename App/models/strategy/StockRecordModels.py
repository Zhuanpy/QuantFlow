"""
股票记录模型
用于管理股票相关的记录数据
"""
from App.exts import db
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class Top500FundRecord(db.Model):
    """
    Top500 基金持仓记录模型
    
    用于记录和管理Top500基金的持仓信息
    """
    __tablename__ = "recordtopfunds500"
    __bind_key__ = 'quanttradingsystem'  # 绑定到主数据库

    # 主键
    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='主键ID')
    
    # 基金基本信息
    name = db.Column(db.String(255), nullable=False, comment='基金名称')
    code = db.Column(db.String(50), nullable=False, comment='基金代码')
    
    # 选择状态
    selection = db.Column(db.Integer, nullable=False, default=0, comment='选择状态：1代表选择，0代表未选择')
    
    # 状态信息
    status = db.Column(db.Text, nullable=True, comment='基金状态下载状态')
    date = db.Column(db.Date, nullable=True, comment='基金持仓日期')
    
    # 时间戳（可选字段）
    created_at = db.Column(db.DateTime, nullable=True, comment='创建时间')
    updated_at = db.Column(db.DateTime, nullable=True, comment='更新时间')

    def __repr__(self):
        """
        返回对象的字符串表示，便于调试和日志记录
        """
        return (
            f"<Top500FundRecord(id={self.id}, name={self.name}, code={self.code}, "
            f"selection={self.selection}, status={self.status}, date={self.date})>"
        )

    @staticmethod
    def validate_selection(value: int):
        """
        校验 selection 字段是否合法
        
        Args:
            value: 选择值，应为 0 或 1
            
        Raises:
            ValueError: 如果值不合法
        """
        if value not in (0, 1):
            raise ValueError("selection 字段只能是 0 或 1")

    @classmethod
    def get_selected_funds(cls):
        """
        获取所有被选择的基金
        
        Returns:
            List[Top500FundRecord]: 被选择的基金列表
        """
        return cls.query.filter_by(selection=1).all()

    @classmethod
    def get_funds_by_date(cls, date):
        """
        获取指定日期的基金记录
        
        Args:
            date: 日期对象或字符串
            
        Returns:
            List[Top500FundRecord]: 基金记录列表
        """
        return cls.query.filter_by(date=date).all()

    @classmethod
    def get_funds_by_status(cls, status: str):
        """
        获取指定状态的基金记录
        
        Args:
            status: 状态字符串
            
        Returns:
            List[Top500FundRecord]: 基金记录列表
        """
        return cls.query.filter_by(status=status).all()

    def update_by_id(self, record_id: int, status: str, date):
        """
        根据记录 ID 更新状态和日期
        
        Args:
            record_id: 记录ID
            status: 新状态
            date: 新日期
            
        Returns:
            bool: 更新是否成功
        """
        try:
            record = Top500FundRecord.query.get(record_id)
            if record:
                record.status = status
                record.date = date
                record.updated_at = datetime.utcnow()
                db.session.commit()
                logger.info(f"成功更新基金记录 {record_id} 的状态和日期")
                return True
            else:
                logger.warning(f"未找到ID为 {record_id} 的基金记录")
                return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新基金记录时出错: {str(e)}")
            return False

    def toggle_selection(self):
        """
        切换选择状态
        
        Returns:
            bool: 切换是否成功
        """
        try:
            self.selection = 1 if self.selection == 0 else 0
            self.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"成功切换基金 {self.code} 的选择状态为: {self.selection}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"切换选择状态时出错: {str(e)}")
            return False

    def to_dict(self):
        """
        转换为字典格式
        
        Returns:
            dict: 字典格式的数据
        """
        return {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'selection': self.selection,
            'status': self.status,
            'date': self.date.strftime('%Y-%m-%d') if self.date else None,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }

    @classmethod
    def get_pending_funds(cls, success_status: str):
        """
        获取待下载的基金列表（状态不等于成功状态的记录）
        
        Args:
            success_status: 成功状态标识
            
        Returns:
            List[Top500FundRecord]: 待下载的基金列表
        """
        return cls.query.filter(cls.status != success_status).all()

    @classmethod
    def get_funds_by_selection(cls, selection: int = 1):
        """
        根据选择状态获取基金列表
        
        Args:
            selection: 选择状态，1为已选择，0为未选择
            
        Returns:
            List[Top500FundRecord]: 基金列表
        """
        return cls.query.filter_by(selection=selection).all()

    def update_download_status(self, status: str, download_date):
        """
        更新下载状态和日期
        
        Args:
            status: 新的下载状态
            download_date: 下载日期
            
        Returns:
            bool: 更新是否成功
        """
        try:
            # 重新查询数据库中的记录，确保获取最新状态
            record = Top500FundRecord.query.get(self.id)
            if record:
                record.status = status
                record.date = download_date
                record.updated_at = datetime.utcnow()
                db.session.commit()
                
                # 更新当前对象的状态
                self.status = status
                self.date = download_date
                self.updated_at = record.updated_at
                
                logger.info(f"成功更新基金 {self.code} 的下载状态: {status}")
                return True
            else:
                logger.error(f"未找到基金记录 {self.id} ({self.code})")
                return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新基金 {self.code} 下载状态时出错: {str(e)}")
            return False
