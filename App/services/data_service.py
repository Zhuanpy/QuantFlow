"""
数据服务层
整合数据下载、处理、存储、查询等功能
"""

import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
import sys

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from App.exts import db
from App.models.data.Stock1m import (
    RecordStockMinute, 
    save_1m_stock_data_to_sql, 
    get_1m_stock_data,
    create_1m_stock_model
)
from config import Config

logger = logging.getLogger(__name__)


class DataService:
    """
    数据服务类
    提供统一的数据操作接口
    """
    
    def __init__(self):
        self.config = Config()
    
    def download_stock_data(self, stock_code: str, start_date: str = None, end_date: str = None) -> bool:
        """
        下载股票数据
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            bool: 下载是否成功
        """
        try:
            logger.info(f"开始下载股票 {stock_code} 的数据")
            
            # 创建下载记录
            record = self._create_download_record(stock_code, start_date, end_date)
            
            # 更新状态为处理中
            record.update_download_status('processing', 0.0)
            db.session.commit()
            
            # 调用东方财富下载器
            from App.codes.downloads.DlEastMoney import DownloadData
            
            # 下载数据
            data = DownloadData.stock_1m_days(stock_code, days=5)
            
            if data is not None and not data.empty:
                # 保存数据到数据库
                year = datetime.now().year
                success = save_1m_stock_data_to_sql(stock_code, year, data)
                
                if success:
                    # 更新记录状态
                    record.update_download_status(
                        'success', 
                        100.0, 
                        f"成功下载 {len(data)} 条记录"
                    )
                    record.total_records = len(data)
                    record.downloaded_records = len(data)
                    db.session.commit()
                    
                    logger.info(f"成功下载股票 {stock_code} 的 {len(data)} 条数据")
                    return True
                else:
                    record.update_download_status('failed', 0.0, "数据保存失败")
                    db.session.commit()
                    return False
            else:
                record.update_download_status('failed', 0.0, "下载数据为空")
                db.session.commit()
                return False
                
        except Exception as e:
            logger.error(f"下载股票 {stock_code} 数据时发生错误: {e}")
            if 'record' in locals():
                record.update_download_status('failed', 0.0, str(e))
                db.session.commit()
            return False
    
    def get_stock_data(self, stock_code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        获取股票数据
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD HH:MM:SS)
            end_date: 结束日期 (YYYY-MM-DD HH:MM:SS)
            
        Returns:
            pd.DataFrame: 股票数据
        """
        try:
            year = datetime.now().year
            data = get_1m_stock_data(stock_code, year, start_date, end_date)
            
            if not data.empty:
                logger.info(f"成功获取股票 {stock_code} 的 {len(data)} 条数据")
            else:
                logger.warning(f"股票 {stock_code} 没有找到数据")
                
            return data
            
        except Exception as e:
            logger.error(f"获取股票 {stock_code} 数据时发生错误: {e}")
            return pd.DataFrame()
    
    def get_download_status(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取下载状态
        
        Args:
            stock_code: 股票代码
            
        Returns:
            Dict: 下载状态信息
        """
        try:
            record = RecordStockMinute.get_by_stock_code(stock_code)
            
            if record:
                return {
                    'stock_code': stock_code,
                    'status': record.download_status,
                    'progress': record.download_progress,
                    'error_message': record.error_message,
                    'total_records': record.total_records,
                    'downloaded_records': record.downloaded_records,
                    'last_download_time': record.last_download_time,
                    'start_date': record.start_date,
                    'end_date': record.end_date
                }
            else:
                return None
                
        except Exception as e:
            logger.error(f"获取股票 {stock_code} 下载状态时发生错误: {e}")
            return None
    
    def get_pending_downloads(self) -> List[Dict[str, Any]]:
        """
        获取待下载的股票列表
        
        Returns:
            List[Dict]: 待下载股票列表
        """
        try:
            records = RecordStockMinute.get_pending_downloads()
            return [
                {
                    'stock_code': record.stock_code_id,
                    'status': record.download_status,
                    'created_at': record.created_at
                }
                for record in records
            ]
        except Exception as e:
            logger.error(f"获取待下载列表时发生错误: {e}")
            return []
    
    def get_failed_downloads(self) -> List[Dict[str, Any]]:
        """
        获取下载失败的股票列表
        
        Returns:
            List[Dict]: 下载失败股票列表
        """
        try:
            records = RecordStockMinute.get_failed_downloads()
            return [
                {
                    'stock_code': record.stock_code_id,
                    'status': record.download_status,
                    'error_message': record.error_message,
                    'last_download_time': record.last_download_time
                }
                for record in records
            ]
        except Exception as e:
            logger.error(f"获取失败下载列表时发生错误: {e}")
            return []
    
    def retry_failed_downloads(self) -> Dict[str, int]:
        """
        重试失败的下载
        
        Returns:
            Dict: 重试结果统计
        """
        try:
            failed_records = RecordStockMinute.get_failed_downloads()
            success_count = 0
            fail_count = 0
            
            for record in failed_records:
                success = self.download_stock_data(record.stock_code_id)
                if success:
                    success_count += 1
                else:
                    fail_count += 1
            
            logger.info(f"重试完成: 成功 {success_count} 个，失败 {fail_count} 个")
            return {
                'success': success_count,
                'failed': fail_count,
                'total': len(failed_records)
            }
            
        except Exception as e:
            logger.error(f"重试失败下载时发生错误: {e}")
            return {'success': 0, 'failed': 0, 'total': 0}
    
    def batch_download_stocks(self, stock_codes: List[str], start_date: str = None, end_date: str = None) -> Dict[str, int]:
        """
        批量下载股票数据
        
        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            Dict: 下载结果统计
        """
        try:
            success_count = 0
            fail_count = 0
            
            for i, stock_code in enumerate(stock_codes):
                logger.info(f"批量下载进度: {i+1}/{len(stock_codes)} - {stock_code}")
                
                success = self.download_stock_data(stock_code, start_date, end_date)
                if success:
                    success_count += 1
                else:
                    fail_count += 1
            
            logger.info(f"批量下载完成: 成功 {success_count} 个，失败 {fail_count} 个")
            return {
                'success': success_count,
                'failed': fail_count,
                'total': len(stock_codes)
            }
            
        except Exception as e:
            logger.error(f"批量下载时发生错误: {e}")
            return {'success': 0, 'failed': 0, 'total': len(stock_codes)}
    
    def get_data_statistics(self) -> Dict[str, Any]:
        """
        获取数据统计信息
        
        Returns:
            Dict: 统计信息
        """
        try:
            # 统计下载记录
            total_records = RecordStockMinute.query.count()
            pending_records = RecordStockMinute.query.filter_by(download_status='pending').count()
            success_records = RecordStockMinute.query.filter_by(download_status='success').count()
            failed_records = RecordStockMinute.query.filter_by(download_status='failed').count()
            
            # 统计最近下载
            recent_downloads = RecordStockMinute.query.filter(
                RecordStockMinute.last_download_time >= datetime.now() - timedelta(days=7)
            ).count()
            
            return {
                'total_records': total_records,
                'pending_records': pending_records,
                'success_records': success_records,
                'failed_records': failed_records,
                'recent_downloads': recent_downloads,
                'success_rate': (success_records / total_records * 100) if total_records > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"获取数据统计时发生错误: {e}")
            return {}
    
    def _create_download_record(self, stock_code: str, start_date: str = None, end_date: str = None) -> RecordStockMinute:
        """
        创建下载记录
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            RecordStockMinute: 下载记录对象
        """
        # 检查是否已存在记录
        existing_record = RecordStockMinute.get_by_stock_code(stock_code)
        
        if existing_record:
            # 更新现有记录
            existing_record.start_date = pd.to_datetime(start_date).date() if start_date else None
            existing_record.end_date = pd.to_datetime(end_date).date() if end_date else None
            existing_record.record_date = datetime.now().date()
            existing_record.updated_at = datetime.utcnow()
            return existing_record
        else:
            # 创建新记录
            record = RecordStockMinute(
                stock_code_id=stock_code,
                start_date=pd.to_datetime(start_date).date() if start_date else None,
                end_date=pd.to_datetime(end_date).date() if end_date else None,
                record_date=datetime.now().date()
            )
            db.session.add(record)
            db.session.commit()
            return record


class DataValidationService:
    """
    数据验证服务
    提供数据质量检查和验证功能
    """
    
    @staticmethod
    def validate_stock_data(data: pd.DataFrame) -> Dict[str, Any]:
        """
        验证股票数据质量
        
        Args:
            data: 股票数据DataFrame
            
        Returns:
            Dict: 验证结果
        """
        try:
            if data.empty:
                return {
                    'valid': False,
                    'errors': ['数据为空'],
                    'warnings': []
                }
            
            errors = []
            warnings = []
            
            # 检查必需列
            required_columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
            missing_columns = [col for col in required_columns if col not in data.columns]
            if missing_columns:
                errors.append(f"缺少必需列: {missing_columns}")
            
            # 检查数据类型
            if 'date' in data.columns:
                if not pd.api.types.is_datetime64_any_dtype(data['date']):
                    warnings.append("日期列不是datetime类型")
            
            # 检查数值范围
            if 'open' in data.columns and 'close' in data.columns:
                if (data['open'] <= 0).any():
                    errors.append("存在非正开盘价")
                if (data['close'] <= 0).any():
                    errors.append("存在非正收盘价")
            
            if 'volume' in data.columns:
                if (data['volume'] < 0).any():
                    errors.append("存在负成交量")
            
            # 检查时间连续性
            if 'date' in data.columns and len(data) > 1:
                data_sorted = data.sort_values('date')
                time_diff = data_sorted['date'].diff().dropna()
                if not (time_diff == pd.Timedelta(minutes=1)).all():
                    warnings.append("时间间隔不连续")
            
            return {
                'valid': len(errors) == 0,
                'errors': errors,
                'warnings': warnings,
                'record_count': len(data)
            }
            
        except Exception as e:
            logger.error(f"数据验证时发生错误: {e}")
            return {
                'valid': False,
                'errors': [f"验证过程发生错误: {e}"],
                'warnings': []
            }


class DataCleanService:
    """
    数据清洗服务
    提供数据清洗和预处理功能
    """
    
    @staticmethod
    def clean_stock_data(data: pd.DataFrame) -> pd.DataFrame:
        """
        清洗股票数据
        
        Args:
            data: 原始股票数据
            
        Returns:
            pd.DataFrame: 清洗后的数据
        """
        try:
            if data.empty:
                return data
            
            # 复制数据避免修改原始数据
            cleaned_data = data.copy()
            
            # 删除重复行
            cleaned_data = cleaned_data.drop_duplicates(subset=['date'])
            
            # 按时间排序
            if 'date' in cleaned_data.columns:
                cleaned_data = cleaned_data.sort_values('date')
            
            # 处理异常值
            if 'open' in cleaned_data.columns:
                # 删除开盘价为0或负数的记录
                cleaned_data = cleaned_data[cleaned_data['open'] > 0]
            
            if 'close' in cleaned_data.columns:
                # 删除收盘价为0或负数的记录
                cleaned_data = cleaned_data[cleaned_data['close'] > 0]
            
            if 'volume' in cleaned_data.columns:
                # 删除成交量为负数的记录
                cleaned_data = cleaned_data[cleaned_data['volume'] >= 0]
            
            # 重置索引
            cleaned_data = cleaned_data.reset_index(drop=True)
            
            logger.info(f"数据清洗完成: 原始 {len(data)} 条 -> 清洗后 {len(cleaned_data)} 条")
            return cleaned_data
            
        except Exception as e:
            logger.error(f"数据清洗时发生错误: {e}")
            return data


# 创建服务实例
data_service = DataService()
validation_service = DataValidationService()
clean_service = DataCleanService()
