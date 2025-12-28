from sqlalchemy import text
from ..exts import db

def upgrade():
    """添加15分钟数据处理相关字段"""
    try:
        # 检查并删除旧的年份相关字段
        check_columns_sql = """
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'rnn_training_records'
            AND COLUMN_NAME LIKE 'processed_%'
            OR COLUMN_NAME LIKE 'process_message_%'
            OR COLUMN_NAME LIKE 'processed_time_%';
        """
        
        # 获取所有需要删除的列
        result = db.session.execute(text(check_columns_sql))
        columns_to_drop = [row[0] for row in result]
        
        if columns_to_drop:
            # 构建删除列的SQL
            drop_columns_sql = f"""
                ALTER TABLE rnn_training_records 
                {', '.join(f'DROP COLUMN {col}' for col in columns_to_drop)};
            """
            db.session.execute(text(drop_columns_sql))
            print(f"已删除以下旧字段: {', '.join(columns_to_drop)}")

        # 检查新字段是否存在
        check_new_columns_sql = """
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'rnn_training_records'
            AND (
                COLUMN_NAME LIKE 'original_15M_%'
                OR COLUMN_NAME LIKE 'standard_15M_%'
            );
        """
        
        result = db.session.execute(text(check_new_columns_sql))
        existing_columns = [row[0] for row in result]
        
        # 如果新字段不存在，则添加
        if len(existing_columns) < 8:  # 应该有8个新字段
            # 添加新的15分钟数据处理字段
            add_columns_sql = """
                ALTER TABLE rnn_training_records 
                ADD COLUMN IF NOT EXISTS original_15M_year VARCHAR(4) NULL COMMENT '原始数据处理年份',
                ADD COLUMN IF NOT EXISTS original_15M_status VARCHAR(10) NULL COMMENT '原始数据处理状态',
                ADD COLUMN IF NOT EXISTS original_15M_time DATETIME NULL COMMENT '原始数据处理时间',
                ADD COLUMN IF NOT EXISTS original_15M_message TEXT NULL COMMENT '原始数据处理消息',
                ADD COLUMN IF NOT EXISTS standard_15M_year VARCHAR(4) NULL COMMENT '标准化数据处理年份',
                ADD COLUMN IF NOT EXISTS standard_15M_status VARCHAR(10) NULL COMMENT '标准化数据处理状态',
                ADD COLUMN IF NOT EXISTS standard_15M_time DATETIME NULL COMMENT '标准化数据处理时间',
                ADD COLUMN IF NOT EXISTS standard_15M_message TEXT NULL COMMENT '标准化数据处理消息'
            """
            db.session.execute(text(add_columns_sql))
            print("已添加新字段")
        
        db.session.commit()
        print("✅ 数据库迁移成功完成")
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ 数据库迁移失败: {str(e)}")
        return False

def downgrade():
    """删除15分钟数据处理相关字段"""
    try:
        # 删除15分钟数据处理相关字段
        drop_columns_sql = """
            ALTER TABLE rnn_training_records 
            DROP COLUMN IF EXISTS original_15M_year,
            DROP COLUMN IF EXISTS original_15M_status,
            DROP COLUMN IF EXISTS original_15M_time,
            DROP COLUMN IF EXISTS original_15M_message,
            DROP COLUMN IF EXISTS standard_15M_year,
            DROP COLUMN IF EXISTS standard_15M_status,
            DROP COLUMN IF EXISTS standard_15M_time,
            DROP COLUMN IF EXISTS standard_15M_message
        """
        db.session.execute(text(drop_columns_sql))
        
        db.session.commit()
        print("✅ 数据库回滚成功")
        return True
    except Exception as e:
        db.session.rollback()
        print(f"❌ 数据库回滚失败: {str(e)}")
        return False 