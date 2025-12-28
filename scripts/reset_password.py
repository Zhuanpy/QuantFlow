#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重置用户密码脚本
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from App import create_app
from App.exts import db
from App.models.auth.user import User

def list_all_users():
    """列出所有用户"""
    app = create_app()
    with app.app_context():
        users = User.query.filter_by(deleted_at=None).all()
        
        if not users:
            print("数据库中没有用户")
            return []
        
        print("=" * 80)
        print("所有用户列表")
        print("=" * 80)
        print(f"{'ID':<8} {'用户名':<20} {'邮箱':<30} {'状态':<10} {'最后登录':<20}")
        print("-" * 80)
        
        user_list = []
        for user in users:
            last_login = user.last_login_at.strftime('%Y-%m-%d %H:%M:%S') if user.last_login_at else '从未登录'
            print(f"{user.id:<8} {user.username:<20} {user.email:<30} {user.status:<10} {last_login:<20}")
            user_list.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'status': user.status
            })
        
        print("=" * 80)
        return user_list

def find_user_by_username_or_email(identifier):
    """根据用户名或邮箱查找用户"""
    app = create_app()
    with app.app_context():
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).filter_by(deleted_at=None).first()
        
        return user

def reset_password(username_or_email, new_password):
    """重置用户密码"""
    app = create_app()
    with app.app_context():
        user = find_user_by_username_or_email(username_or_email)
        
        if not user:
            print(f"错误: 未找到用户 '{username_or_email}'")
            return False
        
        # 重置密码
        user.set_password(new_password)
        user.reset_failed_login()  # 重置登录失败次数
        user.status = 'active'  # 确保账户是激活状态
        
        db.session.commit()
        
        print("=" * 60)
        print("密码重置成功！")
        print("=" * 60)
        print(f"用户名: {user.username}")
        print(f"邮箱: {user.email}")
        print(f"新密码: {new_password}")
        print("=" * 60)
        print("\n您现在可以使用以下信息登录：")
        print(f"登录地址: http://localhost:5000/auth/login")
        print(f"用户名/邮箱: {user.username} 或 {user.email}")
        print(f"密码: {new_password}")
        print("\n⚠️  请登录后立即修改密码！")
        
        return True

def main():
    """主函数"""
    print("=" * 60)
    print("用户密码重置工具")
    print("=" * 60)
    print()
    
    # 首先列出所有用户
    print("正在查询所有用户...")
    user_list = list_all_users()
    
    if not user_list:
        print("\n没有找到任何用户。")
        return
    
    print("\n请选择操作：")
    print("1. 重置指定用户的密码")
    print("2. 退出")
    
    choice = input("\n请输入选项 (1/2): ").strip()
    
    if choice == '1':
        identifier = input("\n请输入用户名或邮箱: ").strip()
        
        if not identifier:
            print("错误: 用户名或邮箱不能为空")
            return
        
        # 查找用户
        user = find_user_by_username_or_email(identifier)
        if not user:
            print(f"错误: 未找到用户 '{identifier}'")
            return
        
        print(f"\n找到用户:")
        print(f"  用户名: {user.username}")
        print(f"  邮箱: {user.email}")
        print(f"  状态: {user.status}")
        
        confirm = input("\n确认要重置此用户的密码吗？(y/n): ").strip().lower()
        if confirm != 'y':
            print("已取消操作")
            return
        
        # 输入新密码
        new_password = input("\n请输入新密码 (至少6位): ").strip()
        if len(new_password) < 6:
            print("错误: 密码长度不能少于6位")
            return
        
        confirm_password = input("请再次输入新密码: ").strip()
        if new_password != confirm_password:
            print("错误: 两次输入的密码不一致")
            return
        
        # 重置密码
        try:
            reset_password(identifier, new_password)
        except Exception as e:
            print(f"\n错误: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    elif choice == '2':
        print("退出")
    else:
        print("无效的选项")

if __name__ == '__main__':
    main()

