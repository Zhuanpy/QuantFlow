import os
import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DataDirectoryManager:
    def __init__(self, base_path):
        """
        初始化数据目录管理器
        :param base_path: 基础路径，例如 'STOCK_RNN/data/data'
        """
        self.base_path = os.path.abspath(base_path)
        self.data_types = ['1m', '15m', 'day', 'funds_awkward']

    def _get_current_quarter(self):
        """
        获取当前季度
        :return: 当前季度字符串，格式：YYYYQN
        """
        now = datetime.datetime.now()
        quarter = (now.month - 1) // 3 + 1
        return f"{now.year}Q{quarter}"

    def create_quarter_structure(self, quarter=None):
        """
        创建指定季度的目录结构
        :param quarter: 季度，格式：YYYYQN，例如 2024Q1，如果不指定则使用当前季度
        """
        if quarter is None:
            quarter = self._get_current_quarter()
        
        # 验证季度格式
        if not (len(quarter) == 6 and quarter[4] == 'Q' and quarter[5] in '1234'):
            logger.error(f"季度格式错误: {quarter}，应为 YYYYQN 格式，例如 2024Q1")
            return False

        # 创建季度目录
        quarter_path = os.path.join(self.base_path, 'quarters', quarter)
        
        try:
            # 创建季度目录
            if not os.path.exists(quarter_path):
                os.makedirs(quarter_path)
                logger.info(f"创建季度目录: {quarter_path}")
            
            # 创建数据类型子目录
            for data_type in self.data_types:
                data_type_path = os.path.join(quarter_path, data_type)
                if not os.path.exists(data_type_path):
                    os.makedirs(data_type_path)
                    logger.info(f"创建数据类型目录: {data_type_path}")
                
            return True
            
        except Exception as e:
            logger.error(f"创建目录结构时出错: {e}")
            return False

    def create_multiple_quarters(self, start_quarter, end_quarter=None):
        """
        创建多个季度的目录结构
        :param start_quarter: 起始季度，格式：YYYYQN
        :param end_quarter: 结束季度，格式：YYYYQN，如果不指定则使用当前季度
        """
        if end_quarter is None:
            end_quarter = self._get_current_quarter()

        # 解析季度
        start_year = int(start_quarter[:4])
        start_q = int(start_quarter[5])
        end_year = int(end_quarter[:4])
        end_q = int(end_quarter[5])

        if start_year > end_year or (start_year == end_year and start_q > end_q):
            logger.error("起始季度不能大于结束季度")
            return False

        success = True
        current_year = start_year
        current_q = start_q

        while current_year < end_year or (current_year == end_year and current_q <= end_q):
            quarter = f"{current_year}Q{current_q}"
            if not self.create_quarter_structure(quarter):
                success = False
                logger.error(f"创建 {quarter} 季度目录结构失败")

            current_q += 1
            if current_q > 4:
                current_q = 1
                current_year += 1

        return success

    def verify_structure(self, quarter=None):
        """
        验证目录结构是否完整
        :param quarter: 要验证的季度，如果不指定则验证所有季度
        :return: (bool, list) 是否完整，缺失的目录列表
        """
        missing_dirs = []
        
        if quarter:
            quarters_to_check = [quarter]
        else:
            quarters_path = os.path.join(self.base_path, 'quarters')
            if not os.path.exists(quarters_path):
                return False, [quarters_path]
            quarters_to_check = os.listdir(quarters_path)
            
        for quarter in quarters_to_check:
            quarter_path = os.path.join(self.base_path, 'quarters', quarter)
            if not os.path.exists(quarter_path):
                missing_dirs.append(quarter_path)
                continue
                
            for data_type in self.data_types:
                data_type_path = os.path.join(quarter_path, data_type)
                if not os.path.exists(data_type_path):
                    missing_dirs.append(data_type_path)
                    
        return len(missing_dirs) == 0, missing_dirs

def main():
    # 使用示例
    base_path = os.path.abspath("STOCK_RNN/data/data")
    manager = DataDirectoryManager(base_path)
    
    # 创建当前季度的目录结构
    manager.create_quarter_structure()
    
    # 创建指定季度范围的目录结构
    manager.create_multiple_quarters("2020Q1", "2024Q4")
    
    # 验证目录结构
    is_complete, missing_dirs = manager.verify_structure()
    if not is_complete:
        logger.warning("发现缺失的目录:")
        for dir_path in missing_dirs:
            logger.warning(f"  - {dir_path}")
    else:
        logger.info("所有目录结构完整")

if __name__ == "__main__":
    main() 