import pandas as pd
import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import logging
from tqdm import tqdm
from App.codes.utils.Normal import ResampleData
from App.codes.Signals.StatisticsMacd import SignalMethod

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def validate_data(df):
    """验证数据完整性"""
    required_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
    if not all(col in df.columns for col in required_columns):
        raise ValueError(f"数据缺少必要列: {required_columns}")
    return True

def process_single_stock(file, data_1m_folder, data_1m_last_year_folder, output_15m_folder, year):
    """处理单个股票数据"""
    try:
        stock_symbol = file.replace('.csv', '')
        file_path = Path(data_1m_folder) / file
        
        # 输出详细的路径信息
        logging.info(f"处理文件: {file}")
        logging.info(f"输入路径: {file_path}")
        logging.info(f"输出文件夹: {output_15m_folder}")
        
        # 检查输入文件是否存在
        if not file_path.exists():
            logging.error(f"输入文件不存在: {file_path}")
            return False
        
        # 读取数据
        df_1m = pd.read_csv(file_path, parse_dates=['date'])
        logging.info(f"{stock_symbol}: 原始数据行数: {len(df_1m)}")
        validate_data(df_1m)
        
        # 尝试读取去年数据
        try:
            last_year_path = Path(data_1m_last_year_folder) / file

            if last_year_path.exists():
                df_1m_last_year = pd.read_csv(last_year_path, parse_dates=['date'])
                validate_data(df_1m_last_year)
                df_1m = pd.concat([df_1m_last_year, df_1m], ignore_index=True)
                logging.info(f"{stock_symbol}: 成功合并去年数据，合并后行数: {len(df_1m)}")

        except FileNotFoundError:
            logging.warning(f"{stock_symbol}: 未找到去年数据文件")

        except Exception as e:
            logging.error(f"{stock_symbol}: 处理去年数据时出错 - {str(e)}")

        # 计算 15 分钟数据
        df_15m = ResampleData.resample_1m_data(df_1m, '15m')
        logging.info(f"{stock_symbol}: 重采样后数据行数: {len(df_15m)}")
        
        df_15m = SignalMethod.signal_by_MACD_3ema(df_15m, df_1m)
        logging.info(f"{stock_symbol}: MACD计算后数据行数: {len(df_15m)}")

        # 计算年份和季度
        df_15m['Year'] = df_15m['date'].dt.year
        df_15m['Quarter'] = df_15m['date'].dt.quarter
        
        # 筛选目标年份的有效数据
        df_15m = df_15m[(df_15m['Year'] == int(year)) & (~df_15m['Signal'].isnull())]

        logging.info(f"{stock_symbol}: 筛选后数据行数: {len(df_15m)}")
        
        if df_15m.empty:
            logging.warning(f"{stock_symbol}: 处理后数据为空")
            return False

        # 检查输出目录是否存在，如果不存在则创建
        output_folder = Path(output_15m_folder)
        if not output_folder.exists():
            logging.info(f"创建输出目录: {output_folder}")
            output_folder.mkdir(parents=True, exist_ok=True)

        # 保存处理后的数据
        output_path = output_folder / f"{stock_symbol}.csv"
        logging.info(f"保存数据到: {output_path}")
        
        # 保存前检查数据
        if not validate_data(df_15m):
            logging.error(f"{stock_symbol}: 输出数据验证失败")
            return False
            
        # 保存数据
        df_15m.to_csv(output_path, index=False)
        
        # 验证文件是否成功保存
        if not output_path.exists():
            logging.error(f"{stock_symbol}: 文件保存失败，未找到输出文件")
            return False
            
        # 验证保存的文件大小
        file_size = output_path.stat().st_size
        if file_size == 0:
            logging.error(f"{stock_symbol}: 保存的文件大小为0")
            return False
            
        logging.info(f"{stock_symbol}: 数据处理完成，文件大小: {file_size} bytes")
        return True

    except Exception as e:
        logging.error(f"{stock_symbol}: 处理过程出错 - {str(e)}")
        return False

def process_stock_data_for_year(year, stock_code=None, data_base_path="E:/MyProject/MyStock/MyStock/Stock_RNN/App/static/data/years"):
    """处理指定年份的股票数据
    
    Args:
        year (str): 要处理的年份
        stock_code (str, optional): 要处理的股票代码。如果为None，则处理所有股票
        data_base_path (str): 数据文件基础路径
    
    Returns:
        bool: 处理是否成功
    """
    try:
        # 准备路径
        data_base_path = Path(data_base_path)
        output_15m_folder = data_base_path / year / "15m"
        
        # 检查并创建输出目录
        if not output_15m_folder.exists():
            logging.info(f"创建主输出目录: {output_15m_folder}")
            output_15m_folder.mkdir(parents=True, exist_ok=True)
            
        # 验证输出目录是否可写
        try:
            test_file = output_15m_folder / "test_write.tmp"
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            logging.error(f"输出目录写入权限测试失败: {str(e)}")
            return False
        
        data_1m_folder = data_base_path / year / "1m"
        last_year = str(int(year) - 1)
        data_1m_last_year_folder = data_base_path / last_year / "1m"

        if not data_1m_folder.exists():
            raise FileNotFoundError(f"未找到目标年份数据文件夹: {data_1m_folder}")

        # 获取需要处理的文件列表
        if stock_code:
            # 处理单只股票
            file = f"{stock_code}.csv"
            if not (data_1m_folder / file).exists():
                logging.error(f"未找到股票{stock_code}的数据文件")
                return False
            files = [file]
        else:
            # 处理所有股票
            files = [f for f in os.listdir(data_1m_folder) if f.endswith('.csv')]
            
        if not files:
            logging.warning(f"目标文件夹中没有CSV文件: {data_1m_folder}")
            return False
            
        logging.info(f"开始处理 {year} 年数据，共 {len(files)} 个文件")
        logging.info(f"输入目录: {data_1m_folder}")
        logging.info(f"输出目录: {output_15m_folder}")

        # 使用进度条显示处理进度
        success_count = 0
        with tqdm(total=len(files), desc="处理进度") as pbar:
            # 使用多进程处理数据
            with ProcessPoolExecutor() as executor:
                futures = []
                for file in files:
                    future = executor.submit(
                        process_single_stock,
                        file,
                        str(data_1m_folder),
                        str(data_1m_last_year_folder),
                        str(output_15m_folder),
                        year
                    )
                    futures.append(future)

                # 处理结果
                for future in futures:
                    if future.result():
                        success_count += 1
                    pbar.update(1)

        # 输出处理统计
        total_files = len(files)
        logging.info(f"处理完成: 总数 {total_files}, 成功 {success_count}, 失败 {total_files - success_count}")
        
        # 验证输出文件
        output_files = list(output_15m_folder.glob("*.csv"))
        logging.info(f"输出目录中的文件数量: {len(output_files)}")
        
        # 如果是处理单只股票，验证输出文件是否存在
        if stock_code:
            output_file = output_15m_folder / f"{stock_code}.csv"
            if not output_file.exists():
                logging.error(f"处理完成但未找到输出文件: {output_file}")
                return False
            if output_file.stat().st_size == 0:
                logging.error(f"输出文件大小为0: {output_file}")
                return False
        
        return success_count > 0

    except Exception as e:
        logging.error(f"处理过程出错: {str(e)}")
        return False

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
    process_stock_data_for_year("2020")
