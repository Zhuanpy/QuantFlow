"""
迁移脚本：重构 data_stock_daily 表

操作：
1. 将 趋势/周期/预测/评分 字段的数据迁移到 data_daily_task_status 表
2. 从 data_stock_daily 表中删除这些列
3. 将 volume/money 列类型从 INT 改为 BIGINT

使用方法:
    python scripts/migrate_stock_daily.py --dry-run   # 预览SQL，不执行
    python scripts/migrate_stock_daily.py              # 正式执行
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App.config.secrets import Secrets
import pymysql


def get_connection():
    return pymysql.connect(
        host=Secrets.get_db_host(),
        port=int(Secrets.get_db_port()),
        user=Secrets.get_db_user(),
        password=Secrets.get_db_password(),
        database=Secrets.get_db_name(),
        charset='utf8mb4'
    )


def column_exists(cursor, table, column):
    """检查列是否存在"""
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column)
    )
    return cursor.fetchone()[0] > 0


def run_sql(cursor, sql, dry_run, description=""):
    """执行或预览SQL"""
    if dry_run:
        print(f"  [预览] {description}")
        print(f"         {sql[:200]}{'...' if len(sql) > 200 else ''}")
    else:
        print(f"  执行: {description}")
        cursor.execute(sql)


def main():
    parser = argparse.ArgumentParser(description='迁移 data_stock_daily 表结构')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不实际执行')
    args = parser.parse_args()

    if args.dry_run:
        print("=" * 60)
        print("  预览模式 - 不会实际修改数据库")
        print("=" * 60)
    else:
        confirm = input("即将重构 data_stock_daily 表，建议先备份。是否继续？(y/N): ")
        if confirm.lower() != 'y':
            print("已取消")
            return

    conn = get_connection()
    cursor = conn.cursor()

    # ========== 第1步：在 data_daily_task_status 中添加新列 ==========
    print("\n--- 第1步：在 data_daily_task_status 中添加新列 ---")

    new_columns_for_task_status = [
        ("signal_id", "VARCHAR(20) DEFAULT NULL COMMENT '信号ID'"),
        ("re_trend", "INT DEFAULT 1 COMMENT '重新趋势'"),
        ("cycle_amplitude_per_bar", "FLOAT DEFAULT 0 COMMENT '每根K线周期振幅'"),
        ("cycle_amplitude_max", "FLOAT DEFAULT 0 COMMENT '最大周期振幅'"),
        ("cycle_amplitude", "FLOAT DEFAULT 0 COMMENT '周期振幅'"),
        ("cycle_1m_vol_max1", "INT DEFAULT 0 COMMENT '1分钟最大成交量1'"),
        ("cycle_1m_vol_max5", "INT DEFAULT 0 COMMENT '1分钟最大成交量5'"),
        ("cycle_length_per_bar", "INT DEFAULT 0 COMMENT '每根K线周期长度'"),
        ("score_rnn_model", "FLOAT DEFAULT 0 COMMENT 'RNN模型得分'"),
        ("score_board_boll", "FLOAT DEFAULT 0 COMMENT '布林带得分'"),
        ("score_board_money", "FLOAT DEFAULT 0 COMMENT '资金得分'"),
        ("score_board_hot", "FLOAT DEFAULT 0 COMMENT '热度得分'"),
        ("score_funds_awkward", "FLOAT DEFAULT 0 COMMENT '基金重仓得分'"),
        ("trend_probability", "FLOAT DEFAULT 0 COMMENT '趋势概率'"),
    ]

    added = 0
    for col_name, col_def in new_columns_for_task_status:
        if not column_exists(cursor, 'data_daily_task_status', col_name):
            sql = f"ALTER TABLE `data_daily_task_status` ADD COLUMN `{col_name}` {col_def}"
            run_sql(cursor, sql, args.dry_run, f"添加列 {col_name}")
            added += 1
        else:
            print(f"  跳过: {col_name} 已存在")

    print(f"  共需添加 {added} 列")

    # ========== 第2步：迁移数据 ==========
    print("\n--- 第2步：迁移数据（从 data_stock_daily → data_daily_task_status） ---")

    # 检查 data_stock_daily 是否还有旧字段
    has_old_fields = column_exists(cursor, 'data_stock_daily', 'trends')

    if has_old_fields:
        migrate_sql = """
        INSERT INTO `data_daily_task_status` (`stock_code`, `date`, `signal`, `signal_id`, `signal_start_time`, `re_trend`,
            `cycle_amplitude_per_bar`, `cycle_amplitude_max`, `cycle_amplitude`,
            `cycle_1m_vol_max1`, `cycle_1m_vol_max5`, `cycle_length_per_bar`,
            `predict_cycle_length`, `predict_cycle_change`, `predict_cycle_price`,
            `predict_bar_change`, `predict_bar_price`, `predict_bar_volume`,
            `score_rnn_model`, `score_board_boll`, `score_board_money`, `score_board_hot`,
            `score_funds_awkward`, `trend_probability`)
        SELECT `stock_code`, `date`, `trends`, `signal_id`, `signal_start_time`, `re_trend`,
            `cycle_amplitude_per_bar`, `cycle_amplitude_max`, `cycle_amplitude`,
            `cycle_1m_vol_max1`, `cycle_1m_vol_max5`, `cycle_length_per_bar`,
            `predict_cycle_length`, `predict_cycle_change`, `predict_cycle_price`,
            `predict_bar_change`, `predict_bar_price`, `predict_bar_volume`,
            `score_rnn_model`, `score_board_boll`, `score_board_money`, `score_board_hot`,
            `score_funds_awkward`, `trend_probability`
        FROM `data_stock_daily`
        WHERE `trends` != 1 OR `signal_id` IS NOT NULL OR `score_rnn_model` != 0
            OR `predict_cycle_length` != 0 OR `cycle_amplitude_per_bar` != 0
        ON DUPLICATE KEY UPDATE
            `signal` = VALUES(`signal`),
            `signal_id` = VALUES(`signal_id`),
            `signal_start_time` = VALUES(`signal_start_time`),
            `re_trend` = VALUES(`re_trend`),
            `cycle_amplitude_per_bar` = VALUES(`cycle_amplitude_per_bar`),
            `cycle_amplitude_max` = VALUES(`cycle_amplitude_max`),
            `cycle_amplitude` = VALUES(`cycle_amplitude`),
            `cycle_1m_vol_max1` = VALUES(`cycle_1m_vol_max1`),
            `cycle_1m_vol_max5` = VALUES(`cycle_1m_vol_max5`),
            `cycle_length_per_bar` = VALUES(`cycle_length_per_bar`),
            `predict_cycle_length` = VALUES(`predict_cycle_length`),
            `predict_cycle_change` = VALUES(`predict_cycle_change`),
            `predict_cycle_price` = VALUES(`predict_cycle_price`),
            `predict_bar_change` = VALUES(`predict_bar_change`),
            `predict_bar_price` = VALUES(`predict_bar_price`),
            `predict_bar_volume` = VALUES(`predict_bar_volume`),
            `score_rnn_model` = VALUES(`score_rnn_model`),
            `score_board_boll` = VALUES(`score_board_boll`),
            `score_board_money` = VALUES(`score_board_money`),
            `score_board_hot` = VALUES(`score_board_hot`),
            `score_funds_awkward` = VALUES(`score_funds_awkward`),
            `trend_probability` = VALUES(`trend_probability`)
        """
        run_sql(cursor, migrate_sql, args.dry_run, "迁移有效数据到 data_daily_task_status")
        if not args.dry_run:
            print(f"  迁移了 {cursor.rowcount} 条记录")
    else:
        print("  data_stock_daily 中已无旧字段，跳过数据迁移")

    # ========== 第3步：从 data_stock_daily 删除旧列 ==========
    print("\n--- 第3步：从 data_stock_daily 删除旧列 ---")

    columns_to_drop = [
        'trends', 'signal_id', 'signal_start_time', 're_trend',
        'cycle_amplitude_per_bar', 'cycle_amplitude_max',
        'cycle_1m_vol_max1', 'cycle_1m_vol_max5', 'cycle_length_per_bar',
        'predict_cycle_length', 'predict_cycle_change', 'predict_cycle_price',
        'predict_bar_change', 'predict_bar_price', 'predict_bar_volume',
        'score_rnn_model', 'score_board_boll', 'score_board_money',
        'score_board_hot', 'score_funds_awkward', 'trend_probability',
        'rnn_model_score', 'cycle_amplitude',
        'position', 'position_num', 'stop_loss',
    ]

    dropped = 0
    for col in columns_to_drop:
        if column_exists(cursor, 'data_stock_daily', col):
            sql = f"ALTER TABLE `data_stock_daily` DROP COLUMN `{col}`"
            run_sql(cursor, sql, args.dry_run, f"删除列 {col}")
            dropped += 1
        else:
            print(f"  跳过: {col} 不存在")

    print(f"  共需删除 {dropped} 列")

    # ========== 第4步：修改 volume/money 为 BIGINT ==========
    print("\n--- 第4步：修改 volume/money 列类型为 BIGINT ---")

    for col in ['volume', 'money']:
        sql = f"ALTER TABLE `data_stock_daily` MODIFY COLUMN `{col}` BIGINT NOT NULL COMMENT '{'成交量' if col == 'volume' else '成交额'}'"
        run_sql(cursor, sql, args.dry_run, f"修改 {col} 为 BIGINT")

    # ========== 提交 ==========
    if not args.dry_run:
        conn.commit()
        print("\n所有修改已提交！")
    else:
        print("\n预览完成。使用不带 --dry-run 的命令正式执行。")

    cursor.close()
    conn.close()


if __name__ == '__main__':
    main()
