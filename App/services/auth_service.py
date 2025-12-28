"""
认证服务
"""
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from flask import request
import os

from App.exts import db
from App.models.auth.user import User, Role, LoginLog

logger = logging.getLogger(__name__)


class AuthService:
    """认证服务类"""
    
    def __init__(self):
        # 暂时不使用Redis，使用简单的内存存储
        self._token_cache = {}
    
    def register_user(self, username: str, email: str, password: str, 
                     full_name: str = None) -> Tuple[bool, str, Optional[User]]:
        """
        用户注册
        
        Returns:
            (成功标志, 消息, 用户对象)
        """
        try:
            # 检查用户名是否存在
            if User.query.filter_by(username=username).first():
                return False, "用户名已存在", None
            
            # 检查邮箱是否存在
            if User.query.filter_by(email=email).first():
                return False, "邮箱已被注册", None
            
            # 创建新用户
            user = User(
                username=username,
                email=email,
                full_name=full_name,
                status='active'
            )
            user.set_password(password)
            
            # 分配默认角色（普通用户）
            default_role = Role.query.filter_by(role_code='normal').first()
            if default_role:
                user.roles.append(default_role)
            
            db.session.add(user)
            db.session.commit()
            
            logger.info(f"用户注册成功: {username}")
            return True, "注册成功", user
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"用户注册失败: {e}")
            return False, f"注册失败: {str(e)}", None
    
    def login(self, username: str, password: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        用户登录
        
        Returns:
            (成功标志, 消息, 用户数据和Token)
        """
        try:
            # 查找用户
            user = User.query.filter(
                (User.username == username) | (User.email == username)
            ).first()
            
            if not user:
                self._log_login_attempt(username, 'failed', '用户不存在')
                return False, "用户名或密码错误", None
            
            # 检查账户是否被锁定
            if user.is_locked():
                self._log_login_attempt(username, 'failed', '账户已锁定')
                return False, "账户已被锁定，请稍后再试", None
            
            # 验证密码
            if not user.check_password(password):
                user.increment_failed_login()
                self._log_login_attempt(username, 'failed', '密码错误')
                return False, "用户名或密码错误", None
            
            # 登录成功，重置失败次数
            user.reset_failed_login()
            user.last_login_at = datetime.utcnow()
            user.last_login_ip = request.remote_addr if request else None
            db.session.commit()
            
            # 生成Token
            token = user.generate_token()
            refresh_token = user.generate_token(expires_in=604800)  # 7天
            
            # 存储Token
            self._store_token(user.id, token)
            
            # 记录登录日志
            self._log_login_attempt(username, 'success', user_id=user.id)
            
            logger.info(f"用户登录成功: {username}")
            
            return True, "登录成功", {
                'user': user.to_dict(include_roles=True),
                'token': token,
                'refresh_token': refresh_token,
                'expires_in': 86400
            }
            
        except Exception as e:
            logger.error(f"用户登录失败: {e}")
            return False, f"登录失败: {str(e)}", None
    
    def logout(self, token: str) -> Tuple[bool, str]:
        """用户登出"""
        try:
            user = User.verify_token(token)
            if user:
                # 删除Token
                self._delete_token(user.id, token)
                logger.info(f"用户登出成功: {user.username}")
                return True, "登出成功"
            return False, "无效的Token"
        except Exception as e:
            logger.error(f"用户登出失败: {e}")
            return False, f"登出失败: {str(e)}"
    
    def verify_token(self, token: str) -> Optional[User]:
        """验证Token"""
        user = User.verify_token(token)
        if user and self._check_token(user.id, token):
            return user
        return None
    
    def change_password(self, user_id: int, old_password: str, 
                       new_password: str) -> Tuple[bool, str]:
        """修改密码"""
        try:
            user = User.query.get(user_id)
            if not user:
                return False, "用户不存在"
            
            if not user.check_password(old_password):
                return False, "原密码错误"
            
            user.set_password(new_password)
            db.session.commit()
            
            # 清除所有Token，要求重新登录
            self._clear_user_tokens(user_id)
            
            logger.info(f"用户修改密码成功: {user.username}")
            return True, "密码修改成功，请重新登录"
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"修改密码失败: {e}")
            return False, f"修改密码失败: {str(e)}"
    
    def _store_token(self, user_id: int, token: str):
        """存储Token（简化版，使用内存）"""
        key = f"user_{user_id}"
        if key not in self._token_cache:
            self._token_cache[key] = []
        self._token_cache[key].append(token)
    
    def _check_token(self, user_id: int, token: str) -> bool:
        """检查Token是否有效"""
        key = f"user_{user_id}"
        return key in self._token_cache and token in self._token_cache[key]
    
    def _delete_token(self, user_id: int, token: str):
        """删除Token"""
        key = f"user_{user_id}"
        if key in self._token_cache and token in self._token_cache[key]:
            self._token_cache[key].remove(token)
    
    def _clear_user_tokens(self, user_id: int):
        """清除用户所有Token"""
        key = f"user_{user_id}"
        if key in self._token_cache:
            del self._token_cache[key]
    
    def _log_login_attempt(self, username: str, status: str, reason: str = None, user_id: int = None):
        """记录登录日志"""
        try:
            log = LoginLog(
                user_id=user_id,
                username=username,
                login_type='web',
                login_status=status,
                failure_reason=reason,
                ip_address=request.remote_addr if request else None,
                user_agent=request.user_agent.string if request and hasattr(request, 'user_agent') else None
            )
            db.session.add(log)
            db.session.commit()
        except Exception as e:
            logger.error(f"记录登录日志失败: {e}")
            db.session.rollback()

