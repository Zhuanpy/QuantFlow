#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版数据迁移脚本：将datadaily数据库中的股票数据迁移到quanttradingsystem.daily_stock_data表
"""
import sys
from pathlib import Path
import logging

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from App import create_app
from App.exts import db

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_stock_tables():
    """获取datadaily数据库中的所有股票表"""
    try:
        result = db.engine.execute("SHOW TABLES FROM datadaily")
        tables = [row[0] for row in result]
        
        # 过滤出股票代码表（排除daily_stock_data表）
        stock_tables = [t for t in tables if t != 'daily_stock_data' and t.isdigit() and len(t) == 6]
        
        logger.info(f"找到 {len(stock_tables)} 个股票表")
        return stock_tables
        
    except Exception as e:
        logger.error(f"获取股票表列表失败: {e}")
        return []

def migrate_single_stock(stock_code):
    """迁移单个股票的数据"""
    try:
        logger.info(f"迁移股票 {stock_code}...")
        
        # 使用简单的INSERT INTO ... SELECT语句
        insert_sql = f"""
        INSERT IGNORE INTO quanttradingsystem.daily_stock_data 
        (stock_code, date, open, close, high, low, volume, money)
        SELECT 
            '{stock_code}' as stock_code,
            date,
            open,
            close,
            high,
            low,
            volume,
            money
        FROM datadaily.`{stock_code}`
        """
        
        result = db.engine.execute(insert_sql)
        logger.info(f"股票 {stock_code} 迁移完成")
        return True
        
    except Exception as e:
        logger.error(f"迁移股票 {stock_code} 失败: {e}")
        return False

def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("开始数据迁移：datadaily -> quanttradingsystem.daily_stock_data")
    logger.info("=" * 60)
    
    app = create_app()
    with app.app_context():
        try:
            # 1. 获取所有股票表
            stock_tables = get_stock_tables()
            
            if not stock_tables:
                logger.error("未找到需要迁移的股票表")
                return
            
            logger.info(f"将迁移 {len(stock_tables)} 个股票的数据")
            
            # 2. 迁移数据
            success_count = 0
            failed_count = 0
            
            for i, stock_code in enumerate(stock_tables, 1):
                logger.info(f"[{i}/{len(stock_tables)}] 迁移股票 {stock_code}")
                
                if migrate_single_stock(stock_code):
                    success_count += 1
                else:
                    failed_count += 1
                
                # 每迁移50个股票显示一次进度
                if i % 50 == 0:
                    logger.info(f"进度: {i}/{len(stock_tables)} 个股票已处理，成功: {success_count}, 失败: {failed_count}")
            
            # 3. 验证迁移结果
            logger.info("\n" + "=" * 60)
            logger.info("验证迁移结果...")
            logger.info("=" * 60)
            
            # 检查总记录数
            result = db.engine.execute("SELECT COUNT(*) FROM quanttradingsystem.daily_stock_data")
            total_records = result.fetchone()[0]
            logger.info(f"quanttradingsystem.daily_stock_data 表总记录数: {total_records}")
            
            # 检查股票数量
            result = db.engine.execute("SELECT COUNT(DISTINCT stock_code) FROM quanttradingsystem.daily_stock_data")
            stock_count = result.fetchone()[0]
            logger.info(f"迁移的股票数量: {stock_count}")
            
            # 检查日期范围
            result = db.engine.execute("""
                SELECT MIN(date), MAX(date) 
                FROM quanttradingsystem.daily_stock_data
            """)
            date_range = result.fetchone()
            logger.info(f"数据日期范围: {date_range[0]} 到 {date_range[1]}")
            
            # 4. 输出总结
            logger.info("\n" + "=" * 60)
            logger.info("数据迁移总结")
            logger.info("=" * 60)
            logger.info(f"总股票数: {len(stock_tables)}")
            logger.info(f"成功迁移: {success_count}")
            logger.info(f"迁移失败: {failed_count}")
            logger.info(f"总记录数: {total_records}")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"数据迁移过程中发生错误: {e}")

if __name__ == '__main__':
    main()




