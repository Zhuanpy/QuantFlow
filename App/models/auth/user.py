"""
用户认证模型
"""
from App.exts import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import jwt
from config import Config
import logging

logger = logging.getLogger(__name__)


class User(db.Model):
    """用户模型"""
    __tablename__ = 'users'
    __bind_key__ = 'quanttradingsystem'
    
    # 基本信息
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True, comment='用户ID')
    username = db.Column(db.String(50), unique=True, nullable=False, comment='用户名')
    email = db.Column(db.String(100), unique=True, nullable=False, comment='邮箱')
    password_hash = db.Column(db.String(255), nullable=False, comment='密码哈希')
    full_name = db.Column(db.String(100), comment='真实姓名')
    phone = db.Column(db.String(20), comment='手机号')
    avatar_url = db.Column(db.String(255), comment='头像URL')
    
    # 状态信息
    status = db.Column(db.Enum('active', 'inactive', 'locked'), default='active', comment='账户状态')
    email_verified = db.Column(db.Boolean, default=False, comment='邮箱是否验证')
    phone_verified = db.Column(db.Boolean, default=False, comment='手机是否验证')
    
    # 安全信息
    failed_login_attempts = db.Column(db.Integer, default=0, comment='登录失败次数')
    locked_until = db.Column(db.DateTime, comment='锁定截止时间')
    last_login_at = db.Column(db.DateTime, comment='最后登录时间')
    last_login_ip = db.Column(db.String(45), comment='最后登录IP')
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')
    deleted_at = db.Column(db.DateTime, comment='软删除时间')
    
    # 关系（延迟定义）
    # roles = db.relationship('Role', secondary='user_roles', backref=db.backref('users', lazy='dynamic'))
    
    def set_password(self, password: str):
        """设置密码"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password: str) -> bool:
        """验证密码"""
        return check_password_hash(self.password_hash, password)
    
    def is_locked(self) -> bool:
        """检查账户是否被锁定"""
        if self.status == 'locked':
            return True
        if self.locked_until and self.locked_until > datetime.utcnow():
            return True
        return False
    
    def increment_failed_login(self):
        """增加失败登录次数"""
        self.failed_login_attempts += 1
        
        # 5次失败后锁定30分钟
        if self.failed_login_attempts >= 5:
            self.locked_until = datetime.utcnow() + timedelta(minutes=30)
            self.status = 'locked'
        
        db.session.commit()
    
    def reset_failed_login(self):
        """重置失败登录次数"""
        self.failed_login_attempts = 0
        self.locked_until = None
        if self.status == 'locked':
            self.status = 'active'
        db.session.commit()
    
    def generate_token(self, expires_in: int = 86400) -> str:
        """生成JWT Token"""
        try:
            payload = {
                'user_id': self.id,
                'username': self.username,
                'exp': datetime.utcnow() + timedelta(seconds=expires_in),
                'iat': datetime.utcnow()
            }
            token = jwt.encode(payload, Config.SECRET_KEY, algorithm='HS256')
            # PyJWT 2.x returns str, 1.x returns bytes
            return token if isinstance(token, str) else token.decode('utf-8')
        except Exception as e:
            logger.error(f"生成Token失败: {e}")
            return None
    
    @staticmethod
    def verify_token(token: str):
        """验证JWT Token"""
        try:
            payload = jwt.decode(token, Config.SECRET_KEY, algorithms=['HS256'])
            user_id = payload.get('user_id')
            return User.query.get(user_id)
        except jwt.ExpiredSignatureError:
            logger.warning("Token已过期")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"无效的Token: {e}")
            return None
    
    def has_permission(self, permission_code: str) -> bool:
        """检查用户是否拥有指定权限"""
        for role in self.roles:
            for permission in role.permissions:
                if permission.permission_code == permission_code:
                    return True
        return False
    
    def has_role(self, role_code: str) -> bool:
        """检查用户是否拥有指定角色"""
        return any(role.role_code == role_code for role in self.roles)
    
    def to_dict(self, include_roles: bool = False):
        """转换为字典"""
        data = {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'full_name': self.full_name,
            'phone': self.phone,
            'avatar_url': self.avatar_url,
            'status': self.status,
            'email_verified': self.email_verified,
            'phone_verified': self.phone_verified,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        
        if include_roles:
            data['roles'] = [role.to_dict() for role in self.roles]
        
        return data


class Role(db.Model):
    """角色模型"""
    __tablename__ = 'roles'
    __bind_key__ = 'quanttradingsystem'
    
    id = db.Column(db.Integer, primary_key=True, comment='角色ID')
    role_name = db.Column(db.String(50), unique=True, nullable=False, comment='角色名称')
    role_code = db.Column(db.String(50), unique=True, nullable=False, comment='角色代码')
    description = db.Column(db.Text, comment='角色描述')
    is_system = db.Column(db.Boolean, default=False, comment='是否系统角色')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关系（延迟定义）
    # permissions = db.relationship('Permission', secondary='role_permissions', 
    #                              backref=db.backref('roles', lazy='dynamic'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'role_name': self.role_name,
            'role_code': self.role_code,
            'description': self.description
        }


class Permission(db.Model):
    """权限模型"""
    __tablename__ = 'permissions'
    __bind_key__ = 'quanttradingsystem'
    
    id = db.Column(db.Integer, primary_key=True, comment='权限ID')
    permission_name = db.Column(db.String(100), nullable=False, comment='权限名称')
    permission_code = db.Column(db.String(100), unique=True, nullable=False, comment='权限代码')
    resource_type = db.Column(db.String(50), comment='资源类型')
    description = db.Column(db.Text, comment='权限描述')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'permission_name': self.permission_name,
            'permission_code': self.permission_code,
            'resource_type': self.resource_type,
            'description': self.description
        }


class UserRole(db.Model):
    """用户角色关联"""
    __tablename__ = 'user_roles'
    __bind_key__ = 'quanttradingsystem'
    
    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id'), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class RolePermission(db.Model):
    """角色权限关联"""
    __tablename__ = 'role_permissions'
    __bind_key__ = 'quanttradingsystem'
    
    id = db.Column(db.BigInteger, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=False)
    permission_id = db.Column(db.Integer, db.ForeignKey('permissions.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LoginLog(db.Model):
    """登录日志"""
    __tablename__ = 'login_logs'
    __bind_key__ = 'quanttradingsystem'
    
    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id'))
    username = db.Column(db.String(50), comment='用户名')
    login_type = db.Column(db.Enum('web', 'api', 'mobile'), default='web', comment='登录类型')
    login_status = db.Column(db.Enum('success', 'failed'), nullable=False, comment='登录状态')
    failure_reason = db.Column(db.String(255), comment='失败原因')
    ip_address = db.Column(db.String(45), comment='IP地址')
    user_agent = db.Column(db.Text, comment='User Agent')
    location = db.Column(db.String(100), comment='登录位置')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# 在所有模型定义完成后，添加关系
User.roles = db.relationship('Role', secondary=UserRole.__table__, backref=db.backref('users', lazy='dynamic'))
Role.permissions = db.relationship('Permission', secondary=RolePermission.__table__, backref=db.backref('roles', lazy='dynamic'))

