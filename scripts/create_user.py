#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创建新用户脚本
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from App import create_app
from App.exts import db
from App.models.auth.user import User, Role

def create_user(username, email, password, full_name=None, role_code='normal'):
    """
    创建新用户
    
    Args:
        username: 用户名
        email: 邮箱
        password: 密码
        full_name: 真实姓名（可选）
        role_code: 角色代码（默认为normal普通用户）
    """
    app = create_app()
    with app.app_context():
        # 检查用户是否已存在
        existing_user = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()
        
        if existing_user:
            print(f"错误: 用户已存在 (用户名或邮箱已被使用)")
            return False
        
        # 创建新用户
        user = User(
            username=username,
            email=email,
            full_name=full_name,
            status='active'
        )
        user.set_password(password)
        
        # 分配角色
        role = Role.query.filter_by(role_code=role_code).first()
        if not role:
            print(f"警告: 角色 '{role_code}' 不存在，使用默认角色 'normal'")
            role = Role.query.filter_by(role_code='normal').first()
        
        if role:
            user.roles.append(role)
        
        # 保存到数据库
        db.session.add(user)
        db.session.commit()
        
        print("=" * 60)
        print("用户创建成功！")
        print("=" * 60)
        print(f"用户名: {username}")
        print(f"邮箱: {email}")
        print(f"真实姓名: {full_name or '未设置'}")
        print(f"角色: {role.role_name if role else '无'}")
        print(f"状态: {user.status}")
        print("=" * 60)
        print("\n您现在可以使用以下信息登录：")
        print(f"登录地址: http://localhost:5000/auth/login")
        print(f"用户名/邮箱: {username} 或 {email}")
        print(f"密码: [您设置的密码]")
        
        return True

def main():
    """主函数"""
    print("=" * 60)
    print("创建新用户")
    print("=" * 60)
    
    # 用户信息
    username = "zhangzhuan516"  # 从邮箱提取的用户名
    email = "zhangzhuan516@gmail.com"
    password = "zhangzhuan123"  # 默认密码，建议用户登录后修改
    full_name = "Zhang Zhuan"
    role_code = "premium"  # 高级用户角色
    
    print(f"\n正在创建用户: {username}")
    print(f"邮箱: {email}")
    print(f"角色: {role_code}")
    print()
    
    try:
        success = create_user(username, email, password, full_name, role_code)
        if success:
            print("\n重要提示:")
            print("1. 默认密码为: zhangzhuan123")
            print("2. 请登录后立即修改密码")
            print("3. 您的账号具有高级用户权限，可以使用系统的高级功能")
        else:
            print("\n用户创建失败")
            sys.exit(1)
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()



