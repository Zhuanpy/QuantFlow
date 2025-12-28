"""
认证路由
"""
from flask import Blueprint, request, jsonify, render_template, g
import logging

from App.services.auth_service import AuthService
from App.utils.auth_decorators import login_required, admin_required

auth_bp = Blueprint('auth_bp', __name__, url_prefix='/auth')
auth_service = AuthService()
logger = logging.getLogger(__name__)


@auth_bp.route('/login', methods=['GET'])
def login_page():
    """登录页面"""
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET'])
def register_page():
    """注册页面"""
    return render_template('auth/register.html')


@auth_bp.route('/api/register', methods=['POST'])
def register():
    """用户注册API"""
    try:
        data = request.get_json()
        
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        full_name = data.get('full_name')
        
        # 参数验证
        if not username or not email or not password:
            return jsonify({
                'success': False,
                'message': '用户名、邮箱和密码不能为空'
            }), 400
        
        # 密码强度验证
        if len(password) < 6:
            return jsonify({
                'success': False,
                'message': '密码长度不能少于6位'
            }), 400
        
        # 注册用户
        success, message, user = auth_service.register_user(
            username, email, password, full_name
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'data': user.to_dict()
            })
        else:
            return jsonify({
                'success': False,
                'message': message
            }), 400
            
    except Exception as e:
        logger.error(f"注册失败: {e}")
        return jsonify({
            'success': False,
            'message': f'注册失败: {str(e)}'
        }), 500


@auth_bp.route('/api/login', methods=['POST'])
def login():
    """用户登录API"""
    try:
        data = request.get_json()
        
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({
                'success': False,
                'message': '用户名和密码不能为空'
            }), 400
        
        # 登录验证
        success, message, result = auth_service.login(username, password)
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'data': result
            })
        else:
            return jsonify({
                'success': False,
                'message': message
            }), 401
            
    except Exception as e:
        logger.error(f"登录失败: {e}")
        return jsonify({
            'success': False,
            'message': f'登录失败: {str(e)}'
        }), 500


@auth_bp.route('/api/logout', methods=['POST'])
@login_required
def logout():
    """用户登出API"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        success, message = auth_service.logout(token)
        
        return jsonify({
            'success': success,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"登出失败: {e}")
        return jsonify({
            'success': False,
            'message': f'登出失败: {str(e)}'
        }), 500


@auth_bp.route('/api/profile', methods=['GET'])
@login_required
def get_profile():
    """获取用户信息"""
    try:
        user = g.current_user
        return jsonify({
            'success': True,
            'data': user.to_dict(include_roles=True)
        })
    except Exception as e:
        logger.error(f"获取用户信息失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取用户信息失败: {str(e)}'
        }), 500


@auth_bp.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    """修改密码"""
    try:
        data = request.get_json()
        old_password = data.get('old_password')
        new_password = data.get('new_password')
        
        if not old_password or not new_password:
            return jsonify({
                'success': False,
                'message': '原密码和新密码不能为空'
            }), 400
        
        if len(new_password) < 6:
            return jsonify({
                'success': False,
                'message': '新密码长度不能少于6位'
            }), 400
        
        user = g.current_user
        success, message = auth_service.change_password(
            user.id, old_password, new_password
        )
        
        return jsonify({
            'success': success,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"修改密码失败: {e}")
        return jsonify({
            'success': False,
            'message': f'修改密码失败: {str(e)}'
        }), 500



