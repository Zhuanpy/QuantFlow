-- 迁移脚本：将 daily_stock_data 表的 signal_times 字段重命名为 signal_id
-- 执行日期：2025-10-21
-- 说明：统一信号标识字段命名，从 signal_times 改为 signal_id，并修改字段类型为 VARCHAR(20)

USE quanttradingsystem;

-- 1. 先备份表（可选，但强烈推荐）
-- CREATE TABLE daily_stock_data_backup_20251021 AS SELECT * FROM daily_stock_data;

-- 2. 重命名字段并修改类型
-- 从 INT 类型改为 VARCHAR(20) 以匹配 SignalId 的格式（如：202510211329）
ALTER TABLE daily_stock_data 
CHANGE COLUMN signal_times signal_id VARCHAR(20) NULL COMMENT '信号ID';

-- 3. 验证修改
-- 查看表结构
DESCRIBE daily_stock_data;

-- 4. 查看前几条数据验证
SELECT stock_code, date, signal_id, signal_start_time 
FROM daily_stock_data 
WHERE signal_id IS NOT NULL 
LIMIT 10;

-- 注意事项：
-- 1. 如果原来的 signal_times 存储的是整数时间戳，迁移后会被转换为字符串
-- 2. 建议在执行前先备份数据
-- 3. 如果数据库中已有数据且格式不兼容，可能需要先转换数据格式
-- 4. 执行后需要重启 Flask 应用以使 ORM 模型生效

