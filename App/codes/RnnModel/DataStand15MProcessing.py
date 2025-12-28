import pandas as pd
import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import logging
from tqdm import tqdm
from App.codes.utils.Normal import ResampleData
from App.codes.RnnModel.RnnCreationData import TrainingDataCalculate

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def process_single_standard_stock(file, data_15m_folder, output_15m_folder, year):
    stock_symbol = file.replace('.csv', '')
    file_path = Path(data_15m_folder) / file

    # 输出详细的路径信息
    logging.info(f"处理文件: {file}")
    logging.info(f"输入路径: {file_path}")
    logging.info(f"输出文件夹: {output_15m_folder}")

    # 检查输入文件是否存在
    if not file_path.exists():
        logging.error(f"输入文件不存在: {file_path}")
        return False

    # 读取数据
    df_15m = pd.read_csv(file_path, parse_dates=['date'])
    """处理单个股票数据"""


if __name__ == '__main__':
    # 示例调用
    process_single_standard_stock("2020")
