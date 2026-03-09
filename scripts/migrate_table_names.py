"""
数据库表名迁移脚本
将旧表名迁移到新的命名规范

新命名规范: {module}_{entity}
- auth_: 认证模块
- data_: 数据模块
- trade_: 交易模块
- strategy_: 策略模块
- eval_: 评估模块
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from App import create_app
from App.exts import db


# 表名映射: 旧表名 -> 新表名
TABLE_MAPPINGS = {
    # auth 模块
    'users': 'auth_users',
    'roles': 'auth_roles',
    'permissions': 'auth_permissions',
    'user_roles': 'auth_user_roles',
    'role_permissions': 'auth_role_permissions',
    'login_logs': 'auth_login_logs',

    # data 模块
    'stock_market_data': 'data_stock_info',
    'stock_classification': 'data_stock_classification',
    'daily_stock_data': 'data_stock_daily',
    'record_stock_minute': 'data_download_records',

    # trade 模块
    'accounts': 'trade_accounts',
    'positions': 'trade_positions',

    # strategy 模块
    'recordtopfunds500': 'strategy_fund_holdings',
    'rnn_training_records': 'strategy_rnn_training',
    'stock_pool': 'strategy_stock_pool',
    'rnn_running_records': 'strategy_rnn_runs',
    'stock_issue': 'strategy_issues',

    # evaluation 模块
    'strategy_performance': 'eval_performance',
    'risk_metrics': 'eval_risk_metrics',
    'count_board': 'eval_board_stats',
    'count_stock_pool': 'eval_pool_stats',
}


def get_existing_tables(engine):
    """获取数据库中已存在的表"""
    with engine.connect() as conn:
        result = conn.execute(text("SHOW TABLES"))
        return [row[0] for row in result]


def table_exists(engine, table_name):
    """检查表是否存在"""
    existing_tables = get_existing_tables(engine)
    return table_name in existing_tables


def rename_table(engine, old_name, new_name):
    """重命名表"""
    try:
        with engine.connect() as conn:
            conn.execute(text(f"RENAME TABLE `{old_name}` TO `{new_name}`"))
            conn.commit()
        print(f"  [OK] {old_name} -> {new_name}")
        return True
    except Exception as e:
        print(f"  [ERROR] {old_name} -> {new_name}: {e}")
        return False


def migrate_tables(dry_run=False):
    """执行表名迁移"""
    app = create_app()

    with app.app_context():
        # 获取主数据库引擎
        engine = db.get_engine(app, bind='quanttradingsystem')
        existing_tables = get_existing_tables(engine)

        print("=" * 60)
        print("数据库表名迁移脚本")
        print("=" * 60)
        print(f"\n数据库中现有表数量: {len(existing_tables)}")

        if dry_run:
            print("\n[DRY RUN MODE] 仅检查，不执行实际迁移\n")

        # 统计
        to_migrate = []
        already_migrated = []
        not_found = []

        print("\n检查表名状态:")
        print("-" * 60)

        for old_name, new_name in TABLE_MAPPINGS.items():
            old_exists = old_name in existing_tables
            new_exists = new_name in existing_tables

            if old_exists and not new_exists:
                to_migrate.append((old_name, new_name))
                print(f"  [待迁移] {old_name} -> {new_name}")
            elif new_exists:
                already_migrated.append((old_name, new_name))
                print(f"  [已完成] {new_name} (已存在)")
            else:
                not_found.append((old_name, new_name))
                print(f"  [未找到] {old_name} (表不存在)")

        print("\n" + "-" * 60)
        print(f"统计: 待迁移 {len(to_migrate)}, 已完成 {len(already_migrated)}, 未找到 {len(not_found)}")

        if not to_migrate:
            print("\n没有需要迁移的表!")
            return

        if dry_run:
            print("\n[DRY RUN] 如需执行迁移，请使用 --execute 参数")
            return

        # 执行迁移
        print("\n开始执行表名迁移:")
        print("-" * 60)

        success_count = 0
        fail_count = 0

        for old_name, new_name in to_migrate:
            if rename_table(engine, old_name, new_name):
                success_count += 1
            else:
                fail_count += 1

        print("\n" + "=" * 60)
        print(f"迁移完成! 成功: {success_count}, 失败: {fail_count}")
        print("=" * 60)


def show_current_tables():
    """显示当前数据库中的所有表"""
    app = create_app()

    with app.app_context():
        engine = db.get_engine(app, bind='quanttradingsystem')
        tables = get_existing_tables(engine)

        print("=" * 60)
        print("当前数据库中的表:")
        print("=" * 60)

        # 按模块分类显示
        modules = {
            'auth_': [],
            'data_': [],
            'trade_': [],
            'strategy_': [],
            'eval_': [],
            '其他': []
        }

        for table in sorted(tables):
            categorized = False
            for prefix in ['auth_', 'data_', 'trade_', 'strategy_', 'eval_']:
                if table.startswith(prefix):
                    modules[prefix].append(table)
                    categorized = True
                    break
            if not categorized:
                modules['其他'].append(table)

        for module, module_tables in modules.items():
            if module_tables:
                print(f"\n{module} ({len(module_tables)} 个表):")
                for table in module_tables:
                    print(f"  - {table}")

        print(f"\n总计: {len(tables)} 个表")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='数据库表名迁移工具')
    parser.add_argument('--execute', action='store_true', help='执行迁移（默认为dry-run模式）')
    parser.add_argument('--show', action='store_true', help='显示当前数据库中的所有表')

    args = parser.parse_args()

    if args.show:
        show_current_tables()
    else:
        migrate_tables(dry_run=not args.execute)
