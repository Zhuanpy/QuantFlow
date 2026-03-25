"""
股票池表结构升级脚本
添加 score, score_trend, score_updated_at, target_price, stop_loss_price,
current_price, position_ratio, notes, tags 字段
并将 record_id 改为可空（支持手动添加股票）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App import create_app
from App.exts import db
from sqlalchemy import text


def migrate():
    app = create_app()
    with app.app_context():
        # 新增列定义: (列名, SQL类型, 默认值)
        new_columns = [
            ("score", "FLOAT DEFAULT 0.0 COMMENT '综合评分(0-100)'"),
            ("score_trend", "FLOAT DEFAULT 0.0 COMMENT '评分趋势'"),
            ("score_updated_at", "DATETIME NULL COMMENT '评分更新时间'"),
            ("target_price", "FLOAT NULL COMMENT '目标价'"),
            ("stop_loss_price", "FLOAT NULL COMMENT '止损价'"),
            ("current_price", "FLOAT NULL COMMENT '当前价格'"),
            ("position_ratio", "FLOAT DEFAULT 0.0 COMMENT '持仓比例(0-100)'"),
            ("notes", "TEXT NULL COMMENT '备注'"),
            ("tags", "VARCHAR(200) NULL COMMENT '标签，逗号分隔'"),
        ]

        table = 'strategy_stock_pool'

        for col_name, col_def in new_columns:
            try:
                db.session.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                ))
                db.session.commit()
                print(f"  + 添加列 {col_name}")
            except Exception as e:
                db.session.rollback()
                if 'Duplicate column' in str(e) or 'already exists' in str(e):
                    print(f"  = 列 {col_name} 已存在，跳过")
                else:
                    print(f"  ! 添加列 {col_name} 失败: {e}")

        # 将 record_id 改为可空
        try:
            db.session.execute(text(
                f"ALTER TABLE {table} MODIFY COLUMN record_id BIGINT NULL COMMENT '关联的下载记录ID'"
            ))
            db.session.commit()
            print("  ~ record_id 已改为可空")
        except Exception as e:
            db.session.rollback()
            print(f"  ! 修改 record_id 失败: {e}")

        # 更新旧的 pool_type 值
        try:
            db.session.execute(text(
                f"UPDATE {table} SET pool_type = 'candidate' WHERE pool_type = 'general'"
            ))
            db.session.commit()
            print("  ~ 旧类型 general -> candidate 已迁移")
        except Exception as e:
            db.session.rollback()
            print(f"  ! 迁移 pool_type 失败: {e}")

        print("\n升级完成！")


if __name__ == '__main__':
    migrate()
