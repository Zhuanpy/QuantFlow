import time
from DlEastMoney import DownloadData as My_dle
import pandas as pd
from App.codes.RnnDataFile.save_download import save_1m_to_csv, save_1m_to_daily, save_1m_to_mysql
import datetime
import logging

logging.basicConfig(level=logging.INFO)  # 设置 logging 提醒级别

# 尝试导入 Flask 相关模块（如果可用）
try:
    from App.exts import db
    from App.models.data.Stock1m import RecordStockMinute
    from App.models.data.basic_info import StockCodes
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logging.warning("Flask 模块不可用，DataDailyRenew.download_1m_data() 功能可能受限")


class RecordStock:
    """
    RecordStock 适配层
    用于替代旧的 MySQL 访问层，使用 Flask-SQLAlchemy 模型
    """
    
    table_record_download_1m_data = 'record_stock_minute'
    
    @staticmethod
    def load_record_download_1m_data():
        """
        加载需要下载的记录
        返回包含 id, name, code, EsCode, Classification, EndDate 等字段的 DataFrame
        """
        if not FLASK_AVAILABLE:
            logging.error("Flask 模块不可用，无法加载下载记录")
            return pd.DataFrame()
        
        try:
            # 查询需要下载的记录（end_date < 今天 或 download_status != 'success'）
            today = datetime.date.today()
            records = db.session.query(
                RecordStockMinute.id,
                StockCodes.name,
                StockCodes.code,
                StockCodes.EsCode,
                StockCodes.id.label('stock_id'),
                RecordStockMinute.end_date.label('EndDate'),
                RecordStockMinute.record_date.label('RecordDate')
            ).join(
                StockCodes, RecordStockMinute.stock_code_id == StockCodes.id
            ).filter(
                RecordStockMinute.end_date < today,
                RecordStockMinute.download_status != 'success'
            ).all()
            
            if not records:
                return pd.DataFrame()
            
            # 转换为 DataFrame
            data = []
            for record in records:
                # 尝试获取分类信息（如果 StockClassification 表存在）
                classification = '股票'  # 默认值
                try:
                    from App.models.data.basic_info import StockClassification
                    stock_class = StockClassification.query.filter_by(code=record.code).first()
                    if stock_class:
                        classification = stock_class.classification
                except:
                    pass
                
                data.append({
                    'id': record.id,
                    'name': record.name,
                    'code': record.code,
                    'EsCode': record.EsCode or record.code,  # 如果没有 EsCode，使用 code
                    'Classification': classification,
                    'EndDate': record.EndDate,
                    'RecordDate': record.RecordDate
                })
            
            df = pd.DataFrame(data)
            logging.info(f"成功加载 {len(df)} 条需要下载的记录")
            return df
            
        except Exception as e:
            logging.error(f"加载下载记录失败: {e}", exc_info=True)
            return pd.DataFrame()
    
    @staticmethod
    def set_table_record_download_1m_data(sql: str, params: tuple):
        """
        更新下载记录
        sql: SQL UPDATE 语句的 SET 和 WHERE 部分
        params: 参数元组
        """
        if not FLASK_AVAILABLE:
            logging.error("Flask 模块不可用，无法更新下载记录")
            return False
        
        try:
            # 解析 SQL 语句，提取 id
            # SQL 格式: "EndDate= %s, RecordDate = %s, EsDownload = 'success' where id= '%s';"
            # 或: "EndDate = %s, RecordDate = %s where id = '%s';"
            
            # 提取 id（最后一个参数）
            record_id = params[-1] if params else None
            
            if not record_id:
                logging.error("无法从参数中提取记录 ID")
                return False
            
            # 查找记录
            record = RecordStockMinute.query.get(record_id)
            if not record:
                logging.error(f"未找到 ID 为 {record_id} 的记录")
                return False
            
            # 更新字段
            # params 格式: (ending, current, id_)
            if len(params) >= 2:
                record.end_date = params[0] if isinstance(params[0], datetime.date) else pd.to_datetime(params[0]).date()
                record.record_date = params[1] if isinstance(params[1], datetime.date) else pd.to_datetime(params[1]).date()
            
            # 如果 SQL 中包含 EsDownload，更新状态
            if 'EsDownload' in sql or 'success' in sql:
                record.download_status = 'success'
            
            db.session.commit()
            logging.info(f"成功更新记录 ID {record_id}")
            return True
            
        except Exception as e:
            logging.error(f"更新下载记录失败: {e}", exc_info=True)
            db.session.rollback()
            return False

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


class StockType:
    STOCK_1M = 'stock_1m'
    BOARD_1M = 'board_1m'


def download_1m_by_type(code: str, days: int, stock_type: str):
    """
    download stock code_data 1m code_data , return download code_data & code_data end date ;
    Download stock 1m code_data from Eastmoney.

    Parameters:
    - code (str): Stock code.
    - days (int): Number of days to retrieve, 小于5天.
    - stock_type (str): Type of code_data to download, should be 'stock_1m' or 'board_1m'.

    Returns:
    - code_data (pd.DataFrame): Downloaded stock code_data.
    - date_end (datetime.date or None): End date of the downloaded code_data, None if an error occurs.
    """

    print(f"[DEBUG] DlStockData.download_1m_by_type 被调用: code={code}, days={days}, stock_type={stock_type}")
    
    try:
        if stock_type == StockType.STOCK_1M:
            print(f"[DEBUG] 使用东方财富下载股票 {code} 数据")
            data = My_dle.stock_1m_days(code, days=days)
            print(f"[DEBUG] 东方财富下载结果: 数据形状={data.shape if not data.empty else 'Empty'}")

        elif stock_type == StockType.BOARD_1M:
            print(f"[DEBUG] 使用东方财富下载板块 {code} 数据")
            data = My_dle.board_1m_multiple(code, days=days)
            print(f"[DEBUG] 东方财富下载结果: 数据形状={data.shape if not data.empty else 'Empty'}")

        else:
            print(f"[DEBUG] 未知的股票类型: {stock_type}")
            raise ValueError(f"Invalid stock_type: {stock_type}")

        if data.empty:
            print(f"[DEBUG] 下载的数据为空")
            return data, None

        date_end = data.iloc[-1]['date'].date()
        print(f"[DEBUG] 数据结束日期: {date_end}")

        return data, date_end
        
    except Exception as e:
        logging.error(f"下载股票 {code} 数据时发生异常: {str(e)}")
        return pd.DataFrame(), None

    except Exception as ex:
        logging.error(f"Error in download_1m_by_type for {code} ({stock_type}): {ex}")
        return pd.DataFrame(), None


class DataDailyRenew:
    """
    每日数据更新

    """

    @classmethod
    def download_1m_data(cls):
        """
        download 1m code_data ;
        every day running method;
        """
        # todo 判断公共假期，周六补充下载数据
        today = datetime.date.today()
        current = pd.Timestamp(today)  # 2024-01-09 00:00:00

        while True:

            record = RecordStock.load_record_download_1m_data()  # alc.pd_read(database=db_basic, table=tb_basic)

            record['EndDate'] = pd.to_datetime(record['EndDate'])
            record['RecordDate'] = pd.to_datetime(record['RecordDate'])
            record = record[(record['EndDate'] < current)].reset_index(drop=True)

            if record.empty:
                logging.info('已是最新数据')
                break

            shapes = record.shape[0]

            for (i, row) in record.iterrows():
                logging.info(f'\n下载进度：\n总股票数: {shapes}个; 剩余股票: {(shapes - i)}个;')

                id_, stock_name, stock_code, escode, classification, record_ending = (
                    row['id'], row['name'], row['code'], row['EsCode'], row['Classification'], row['EndDate'])

                days = (current - record_ending).days  # 距当前的天数， 判断下载几天的数据

                if days <= 0:
                    logging.info(f'无最新1m数据: {stock_name}, {stock_code};')
                    continue

                days = min(5, days)

                stock_type = StockType.BOARD_1M if classification == '行业板块' else StockType.STOCK_1M  # 判断是行业板块还是个股
                data, ending = download_1m_by_type(escode, days, stock_type)

                if data.empty:  # 判断下载数据是否为空
                    logging.info(f'无最新1m数据: {stock_name}, {stock_code};')
                    continue

                select = pd.to_datetime(record_ending + pd.Timedelta(days=1))
                data = data[data['date'] > select]  # 筛选出重复数据；

                # 判断是否保存数据 及 更新记录表格
                if data.empty:
                    logging.info(f'无最新1m数据: {stock_name}, {stock_code};')
                    continue

                ending = pd.to_datetime(ending)

                if ending > record_ending:
                    year_ = str(ending.year)

                    save_1m_to_mysql(stock_code, year_, data)  # 将下载的1分钟数据，保存至 sql 数据库

                    save_1m_to_csv(data, stock_code)  # 将下载的1分钟数据，同时保存至 code_data 1m 文件夹中

                    if classification != '行业板块':
                        save_1m_to_daily(data, stock_code)  # 将下载的1分钟数据，同时保存至 daily sql table

                    # 更新参数
                    sql = f'''EndDate= %s, RecordDate = %s, EsDownload = 'success' where id= '%s'; '''
                    params = (ending, current, id_)
                    RecordStock.set_table_record_download_1m_data(sql, params)

                if ending == record_ending:
                    sql = f''' EndDate = %s, RecordDate = %s where id = '%s'; '''
                    params = (ending, current, id_)
                    RecordStock.set_table_record_download_1m_data(sql, params)

                    info_text = f'{RecordStock.table_record_download_1m_data} 数据更新成功: {stock_name}, {stock_code}'
                    logging.info(info_text)

                time.sleep(2)


class RMDownloadData(DataDailyRenew):
    """
    daily running
    """

    def __init__(self):
        super().__init__()

    def daily_renew_data(self):
        """
        每日数据更新，仅在收盘后执行
        """
        # 获取当前时间（包含日期和时间）
        current_time = pd.Timestamp.now()
        # 获取今天的日期
        today = pd.Timestamp('today').date()
        # 构建今天的收盘时间（15:30）
        market_close = pd.Timestamp.combine(today, datetime.time(15, 30))
        
        # 如果当前时间已经超过今天的收盘时间，则执行下载
        if current_time > market_close:
            logging.info(f"当前时间 {current_time} 已超过收盘时间 {market_close}，开始下载数据...")
            self.download_1m_data()  # 更新股票当天1m信息
        else:
            logging.info(f"当前时间 {current_time} 未到收盘时间 {market_close}，跳过下载")
    
    def download_fund_holdings(self):
        """
        下载基金持仓数据
        注意：此方法需要在实际的基金下载模块中实现
        """
        logging.warning("download_fund_holdings 方法尚未实现，请使用 download_fund_route 中的 download_fund_holdings 函数")
        # 这个方法应该调用实际的基金下载逻辑
        pass


def stock_name_data():
    """
    download 1m code_data ;
    every day running method;
    """
    # todo 判断公共假期，周六补充下载数据
    today = datetime.date.today()
    current = pd.Timestamp(today)  # 2024-01-09 00:00:00

    record = RecordStock.load_record_download_1m_data()  # alc.pd_read(database=db_basic, table=tb_basic)

    record['EndDate'] = pd.to_datetime(record['EndDate'])
    record['RecordDate'] = pd.to_datetime(record['RecordDate'])
    record = record[(record['EndDate'] < current)].reset_index(drop=True)
    record = record[record['Classification'] != '行业板块']
    record = record["code"]
    print(record)
    path = r"C:\Users\User\Desktop\临时文件"
    record.to_csv(f'{path}/stock_name.txt', index=False)
    # 更新股票当天1m信息；


if __name__ == '__main__':
    # rn = DataDailyRenew()
    # rn.download_1mData()
    # rn.download_1m_data()
    stock_name_data()
