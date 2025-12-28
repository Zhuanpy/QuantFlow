"""
认证装饰器
"""
from functools import wraps
from flask import request, jsonify, g
import logging

from App.services.auth_service import AuthService

logger = logging.getLogger(__name__)
auth_service = AuthService()


def login_required(f):
    """
    登录验证装饰器
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 获取Token
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({
                'success': False,
                'code': 401,
                'message': '未提供认证Token'
            }), 401
        
        # 移除Bearer前缀
        if token.startswith('Bearer '):
            token = token[7:]
        
        # 验证Token
        user = auth_service.verify_token(token)
        if not user:
            return jsonify({
                'success': False,
                'code': 401,
                'message': 'Token无效或已过期'
            }), 401
        
        # 检查账户状态
        if user.status != 'active':
            return jsonify({
                'success': False,
                'code': 403,
                'message': '账户已被禁用'
            }), 403
        
        # 将用户信息存储到g对象
        g.current_user = user
        
        return f(*args, **kwargs)
    
    return decorated_function


def permission_required(permission_code: str):
    """
    权限验证装饰器
    
    Args:
        permission_code: 权限代码
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            user = g.current_user
            
            if not user.has_permission(permission_code):
                logger.warning(f"用户 {user.username} 尝试访问无权限的功能: {permission_code}")
                return jsonify({
                    'success': False,
                    'code': 403,
                    'message': '没有权限访问此功能'
                }), 403
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator


def role_required(role_code: str):
    """
    角色验证装饰器
    
    Args:
        role_code: 角色代码
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            user = g.current_user
            
            if not user.has_role(role_code):
                logger.warning(f"用户 {user.username} 尝试访问需要 {role_code} 角色的功能")
                return jsonify({
                    'success': False,
                    'code': 403,
                    'message': f'需要 {role_code} 角色权限'
                }), 403
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator


def admin_required(f):
    """管理员权限装饰器"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        user = g.current_user
        
        if not (user.has_role('admin') or user.has_role('super_admin')):
            return jsonify({
                'success': False,
                'code': 403,
                'message': '需要管理员权限'
            }), 403
        
        return f(*args, **kwargs)
    
    return decorated_function



