#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户认证系统快速部署脚本（简化版）
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from App import create_app
from App.exts import db
from App.models.auth.user import User, Role, Permission
from werkzeug.security import generate_password_hash

def main():
    """主函数"""
    print("=" * 60)
    print("智能股票交易系统 - 用户认证系统部署")
    print("=" * 60)
    
    try:
        app = create_app()
        with app.app_context():
            # 1. 创建所有表
            print("\n[1/5] 正在初始化数据库表...")
            db.create_all()
            print("OK - 数据库表创建成功")
            
            # 2. 初始化角色
            print("\n[2/5] 正在初始化角色...")
            roles_data = [
                ('super_admin', '超级管理员', '拥有系统所有权限', True),
                ('admin', '管理员', '系统管理员，可管理用户和配置', True),
                ('premium', '高级用户', '付费用户，可使用高级功能', False),
                ('normal', '普通用户', '免费用户，使用基础功能', False),
                ('guest', '游客', '访客用户，只能查看公开内容', False)
            ]
            
            for role_code, role_name, description, is_system in roles_data:
                role = Role.query.filter_by(role_code=role_code).first()
                if not role:
                    role = Role(
                        role_code=role_code,
                        role_name=role_name,
                        description=description,
                        is_system=is_system
                    )
                    db.session.add(role)
                    print(f"  + 创建角色: {role_name}")
                else:
                    print(f"  - 角色已存在: {role_name}")
            
            db.session.commit()
            print("OK - 角色初始化完成")
            
            # 3. 初始化权限
            print("\n[3/5] 正在初始化权限...")
            permissions_data = [
                ('stock.data.view', '查看股票数据', 'data', '查看股票行情数据'),
                ('stock.data.download', '下载股票数据', 'data', '下载股票数据'),
                ('fund.data.view', '查看基金数据', 'data', '查看基金持仓数据'),
                ('fund.data.download', '下载基金数据', 'data', '下载基金数据'),
                ('fund.data.analyze', '分析基金数据', 'data', '分析基金持仓数据'),
                ('strategy.view', '查看策略', 'strategy', '查看交易策略'),
                ('strategy.create', '创建策略', 'strategy', '创建交易策略'),
                ('strategy.edit', '编辑策略', 'strategy', '编辑交易策略'),
                ('strategy.delete', '删除策略', 'strategy', '删除交易策略'),
                ('strategy.run', '运行策略', 'strategy', '运行交易策略'),
                ('trade.view', '查看交易记录', 'trade', '查看交易记录'),
                ('trade.simulate', '模拟交易', 'trade', '进行模拟交易'),
                ('trade.live', '实盘交易', 'trade', '进行实盘交易'),
                ('rnn.model.view', '查看模型', 'rnn', '查看RNN模型'),
                ('rnn.model.train', '训练模型', 'rnn', '训练RNN模型'),
                ('rnn.model.predict', '使用预测', 'rnn', '使用模型预测'),
                ('system.user.manage', '用户管理', 'system', '管理系统用户'),
                ('system.role.manage', '角色管理', 'system', '管理系统角色'),
                ('system.config.manage', '系统配置', 'system', '管理系统配置'),
                ('system.log.view', '查看日志', 'system', '查看系统日志'),
            ]
            
            for perm_code, perm_name, resource_type, description in permissions_data:
                permission = Permission.query.filter_by(permission_code=perm_code).first()
                if not permission:
                    permission = Permission(
                        permission_code=perm_code,
                        permission_name=perm_name,
                        resource_type=resource_type,
                        description=description
                    )
                    db.session.add(permission)
                else:
                    print(f"  - 权限已存在: {perm_name}")
            
            db.session.commit()
            print(f"OK - 权限初始化完成 (共{len(permissions_data)}个)")
            
            # 4. 分配权限给角色
            print("\n[4/5] 正在分配权限给角色...")
            super_admin = Role.query.filter_by(role_code='super_admin').first()
            all_permissions = Permission.query.all()
            super_admin.permissions = all_permissions
            print(f"  + 超级管理员: {len(all_permissions)} 个权限")
            
            admin = Role.query.filter_by(role_code='admin').first()
            admin_permissions = Permission.query.filter(
                Permission.permission_code != 'system.config.manage'
            ).all()
            admin.permissions = admin_permissions
            print(f"  + 管理员: {len(admin_permissions)} 个权限")
            
            premium = Role.query.filter_by(role_code='premium').first()
            premium_permissions = Permission.query.filter(
                Permission.resource_type.in_(['data', 'strategy', 'trade', 'rnn'])
            ).all()
            premium.permissions = premium_permissions
            print(f"  + 高级用户: {len(premium_permissions)} 个权限")
            
            normal = Role.query.filter_by(role_code='normal').first()
            normal_perm_codes = [
                'stock.data.view', 'fund.data.view', 'strategy.view', 
                'trade.view', 'rnn.model.view'
            ]
            normal_permissions = Permission.query.filter(
                Permission.permission_code.in_(normal_perm_codes)
            ).all()
            normal.permissions = normal_permissions
            print(f"  + 普通用户: {len(normal_permissions)} 个权限")
            
            guest = Role.query.filter_by(role_code='guest').first()
            guest_perm_codes = ['stock.data.view']
            guest_permissions = Permission.query.filter(
                Permission.permission_code.in_(guest_perm_codes)
            ).all()
            guest.permissions = guest_permissions
            print(f"  + 游客: {len(guest_permissions)} 个权限")
            
            db.session.commit()
            print("OK - 权限分配完成")
            
            # 5. 创建管理员账号
            print("\n[5/5] 正在创建默认管理员账号...")
            admin_user = User.query.filter_by(username='admin').first()
            if admin_user:
                print("  - 管理员账号已存在")
            else:
                admin_user = User(
                    username='admin',
                    email='admin@example.com',
                    full_name='系统管理员',
                    status='active'
                )
                admin_user.set_password('admin123')
                super_admin_role = Role.query.filter_by(role_code='super_admin').first()
                admin_user.roles.append(super_admin_role)
                db.session.add(admin_user)
                db.session.commit()
                print("  + 创建管理员账号成功")
                print("    用户名: admin")
                print("    密码: admin123")
                print("    警告: 请立即修改默认密码！")
            
            print("\n" + "=" * 60)
            print("用户认证系统部署完成！")
            print("=" * 60)
            print("\n下一步操作：")
            print("  1. 启动Flask应用: python run.py")
            print("  2. 访问登录页面: http://localhost:5000/auth/login")
            print("  3. 使用管理员账号登录 (admin/admin123)")
            print("  4. 修改默认管理员密码")
            print("\n安全提示：")
            print("  - 请立即修改默认管理员密码")
            print("  - 请配置环境变量中的SECRET_KEY")
            print("  - 生产环境请使用HTTPS")
            
    except Exception as e:
        print(f"\n部署失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()



