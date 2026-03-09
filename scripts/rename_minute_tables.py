"""
迁移脚本：将分钟数据表从裸股票代码重命名为带前缀格式

旧表名: 000001, 600519, ...
新表名: data_1m_000001, data_1m_600519, ...  (在 data1mXXXX 库中)
新表名: data_15m_000001, data_15m_600519, ... (在 datadaily 库中)

使用方法:
    python scripts/rename_minute_tables.py --dry-run   # 预览，不执行
    python scripts/rename_minute_tables.py              # 正式执行
"""
import sys
import os
import argparse
import re

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App.config.secrets import Secrets
import pymysql


def get_connection(database: str):
    """获取数据库连接"""
    return pymysql.connect(
        host=Secrets.get_db_host(),
        port=int(Secrets.get_db_port()),
        user=Secrets.get_db_user(),
        password=Secrets.get_db_password(),
        database=database,
        charset='utf8mb4'
    )


def get_all_tables(conn) -> list:
    """获取数据库中所有表名"""
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        return [row[0] for row in cursor.fetchall()]


def is_stock_code_table(table_name: str) -> bool:
    """判断表名是否为裸股票代码（纯数字，6位）"""
    return bool(re.match(r'^\d{6}$', table_name))


def rename_tables(database: str, prefix: str, dry_run: bool = True):
    """
    重命名指定数据库中的裸股票代码表

    Args:
        database: 数据库名
        prefix: 新前缀，如 'data_1m_' 或 'data_15m_'
        dry_run: 是否仅预览
    """
    conn = get_connection(database)
    tables = get_all_tables(conn)

    # 筛选出裸股票代码的表
    stock_tables = [t for t in tables if is_stock_code_table(t)]

    if not stock_tables:
        print(f"  [{database}] 没有找到需要重命名的表")
        conn.close()
        return

    print(f"  [{database}] 找到 {len(stock_tables)} 张需要重命名的表")

    renamed = 0
    skipped = 0

    for old_name in stock_tables:
        new_name = f"{prefix}{old_name}"

        # 检查新表名是否已存在
        if new_name in tables:
            print(f"    跳过: {old_name} -> {new_name} (目标表已存在)")
            skipped += 1
            continue

        if dry_run:
            print(f"    [预览] RENAME TABLE `{old_name}` TO `{new_name}`")
        else:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(f"RENAME TABLE `{old_name}` TO `{new_name}`")
                conn.commit()
                print(f"    已重命名: {old_name} -> {new_name}")
                renamed += 1
            except Exception as e:
                print(f"    失败: {old_name} -> {new_name}, 错误: {e}")

    conn.close()

    action = "将重命名" if dry_run else "已重命名"
    print(f"  [{database}] {action} {renamed if not dry_run else len(stock_tables) - skipped} 张表, 跳过 {skipped} 张")


def main():
    parser = argparse.ArgumentParser(description='重命名分钟数据表，添加规范前缀')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不实际执行')
    args = parser.parse_args()

    if args.dry_run:
        print("=" * 50)
        print("  预览模式 - 不会实际修改数据库")
        print("=" * 50)
    else:
        confirm = input("即将重命名数据库表，是否继续？(y/N): ")
        if confirm.lower() != 'y':
            print("已取消")
            return

    # 1. 处理 data1mXXXX 库中的 1分钟数据表
    print("\n--- 处理1分钟数据库 ---")
    conn_info = get_connection('information_schema')
    with conn_info.cursor() as cursor:
        cursor.execute("SELECT SCHEMA_NAME FROM SCHEMATA WHERE SCHEMA_NAME LIKE 'data1m%'")
        m1_databases = [row[0] for row in cursor.fetchall()]
    conn_info.close()

    if m1_databases:
        for db_name in sorted(m1_databases):
            rename_tables(db_name, 'data_1m_', dry_run=args.dry_run)
    else:
        print("  没有找到 data1mXXXX 数据库")

    # 2. 处理 datadaily 库中的 15分钟数据表
    print("\n--- 处理15分钟数据库 (datadaily) ---")
    try:
        rename_tables('datadaily', 'data_15m_', dry_run=args.dry_run)
    except Exception as e:
        print(f"  datadaily 数据库处理失败: {e}")

    print("\n完成！")


if __name__ == '__main__':
    main()
