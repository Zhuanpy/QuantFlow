# -*- coding: utf-8 -*-
"""
基础仓库类

提供通用的数据库操作方法
"""

import logging
from typing import List, Optional, Type, TypeVar, Generic, Dict, Any
from contextlib import contextmanager

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from App.models import db

logger = logging.getLogger(__name__)

T = TypeVar('T')


class BaseRepository(Generic[T]):
    """
    基础仓库类

    提供 CRUD 操作的通用实现
    """

    def __init__(self, model_class: Type[T]):
        """
        初始化仓库

        Args:
            model_class: SQLAlchemy 模型类
        """
        self.model_class = model_class

    @contextmanager
    def session_scope(self):
        """提供事务作用域的上下文管理器"""
        session = db.session
        try:
            yield session
            session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"数据库操作失败: {e}")
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"操作失败: {e}")
            raise

    def get_by_id(self, id: int) -> Optional[T]:
        """
        根据 ID 获取记录

        Args:
            id: 记录 ID

        Returns:
            Optional[T]: 记录对象或 None
        """
        try:
            return self.model_class.query.get(id)
        except SQLAlchemyError as e:
            logger.error(f"获取记录失败 (ID={id}): {e}")
            return None

    def get_all(self, limit: int = None, offset: int = None) -> List[T]:
        """
        获取所有记录

        Args:
            limit: 最大返回数量
            offset: 偏移量

        Returns:
            List[T]: 记录列表
        """
        try:
            query = self.model_class.query
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)
            return query.all()
        except SQLAlchemyError as e:
            logger.error(f"获取记录列表失败: {e}")
            return []

    def get_by_filter(self, **filters) -> List[T]:
        """
        根据条件获取记录

        Args:
            **filters: 过滤条件

        Returns:
            List[T]: 符合条件的记录列表
        """
        try:
            return self.model_class.query.filter_by(**filters).all()
        except SQLAlchemyError as e:
            logger.error(f"查询记录失败: {e}")
            return []

    def get_first_by_filter(self, **filters) -> Optional[T]:
        """
        根据条件获取第一条记录

        Args:
            **filters: 过滤条件

        Returns:
            Optional[T]: 记录对象或 None
        """
        try:
            return self.model_class.query.filter_by(**filters).first()
        except SQLAlchemyError as e:
            logger.error(f"查询记录失败: {e}")
            return None

    def create(self, **kwargs) -> Optional[T]:
        """
        创建新记录

        Args:
            **kwargs: 记录属性

        Returns:
            Optional[T]: 创建的记录对象或 None
        """
        try:
            with self.session_scope() as session:
                instance = self.model_class(**kwargs)
                session.add(instance)
                session.flush()
                return instance
        except SQLAlchemyError as e:
            logger.error(f"创建记录失败: {e}")
            return None

    def create_many(self, records: List[Dict[str, Any]]) -> List[T]:
        """
        批量创建记录

        Args:
            records: 记录属性列表

        Returns:
            List[T]: 创建的记录列表
        """
        try:
            with self.session_scope() as session:
                instances = [self.model_class(**record) for record in records]
                session.add_all(instances)
                session.flush()
                return instances
        except SQLAlchemyError as e:
            logger.error(f"批量创建记录失败: {e}")
            return []

    def update(self, id: int, **kwargs) -> Optional[T]:
        """
        更新记录

        Args:
            id: 记录 ID
            **kwargs: 要更新的属性

        Returns:
            Optional[T]: 更新后的记录对象或 None
        """
        try:
            with self.session_scope() as session:
                instance = self.model_class.query.get(id)
                if instance:
                    for key, value in kwargs.items():
                        if hasattr(instance, key):
                            setattr(instance, key, value)
                    return instance
                return None
        except SQLAlchemyError as e:
            logger.error(f"更新记录失败 (ID={id}): {e}")
            return None

    def delete(self, id: int) -> bool:
        """
        删除记录

        Args:
            id: 记录 ID

        Returns:
            bool: 是否删除成功
        """
        try:
            with self.session_scope() as session:
                instance = self.model_class.query.get(id)
                if instance:
                    session.delete(instance)
                    return True
                return False
        except SQLAlchemyError as e:
            logger.error(f"删除记录失败 (ID={id}): {e}")
            return False

    def delete_by_filter(self, **filters) -> int:
        """
        根据条件删除记录

        Args:
            **filters: 过滤条件

        Returns:
            int: 删除的记录数
        """
        try:
            with self.session_scope():
                count = self.model_class.query.filter_by(**filters).delete()
                return count
        except SQLAlchemyError as e:
            logger.error(f"删除记录失败: {e}")
            return 0

    def count(self, **filters) -> int:
        """
        统计记录数

        Args:
            **filters: 过滤条件

        Returns:
            int: 记录数
        """
        try:
            if filters:
                return self.model_class.query.filter_by(**filters).count()
            return self.model_class.query.count()
        except SQLAlchemyError as e:
            logger.error(f"统计记录失败: {e}")
            return 0

    def exists(self, **filters) -> bool:
        """
        检查记录是否存在

        Args:
            **filters: 过滤条件

        Returns:
            bool: 是否存在
        """
        return self.count(**filters) > 0

    def upsert(self, lookup_keys: List[str], **kwargs) -> Optional[T]:
        """
        更新或插入记录

        Args:
            lookup_keys: 用于查找的键列表
            **kwargs: 记录属性

        Returns:
            Optional[T]: 记录对象或 None
        """
        try:
            lookup_filter = {key: kwargs[key] for key in lookup_keys if key in kwargs}
            existing = self.get_first_by_filter(**lookup_filter)

            if existing:
                for key, value in kwargs.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                with self.session_scope():
                    pass
                return existing
            else:
                return self.create(**kwargs)
        except SQLAlchemyError as e:
            logger.error(f"Upsert 操作失败: {e}")
            return None
