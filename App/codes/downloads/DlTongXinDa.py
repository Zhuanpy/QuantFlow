from struct import unpack
import multiprocessing
from App.codes.MySql.DB_MySql import MysqlAlchemy as alc
from App.codes.MySql.DB_MySql import *
import pandas as pd
import pandas
import math
import pymysql
from App.codes.utils.Normal import StockCode
from App.codes.utils.Normal import ReadSaveFile
import os

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 500)


def load_csv(file_path: str):
    """
    读取指定路径的CSV文件，跳过前两行头部信息，并返回数据框。

    参数:
        file_path (str): CSV文件的路径

    返回:
        pd.DataFrame: 读取的数据框

    """
    encodings = ['latin-1', 'gbk', 'cp1252', 'utf-8']

    dtype_spec = {"time": 'str',
                  "date": 'str'}

    columns = ["date", "time", "open", "close", "high", "low", "volume", "money"]

    for encoding in encodings:
        try:
            df = pd.read_csv(file_path, sep='\t', skiprows=2, header=None, names=columns, dtype=dtype_spec,
                             encoding=encoding)

            df['date'] = pd.to_datetime(df['date'] + ' ' + df['time'], format='%Y/%m/%d  %H%M')
            df = df.drop(columns=['time'])
            df = df.dropna(subset=["date"])

            return df

        except UnicodeDecodeError:
            print(f"编码错误: {encoding} 无法解码文件")

        except Exception as e:
            print(f"发生错误: {e}")

    print("所有尝试的编码方式均失败")

    return None


class DbTongxindaData:

    def __init__(self, primary_key):
        self.primary_key = primary_key

    def renew_TbRecordStockDailyData(self, column: str, new_data: str):
        pass


def tb_txd_record():
    data = pd.read_excel('code_data/output/Tx_code.xls')

    data.loc[(data['TxMarket'] == 'sh') & (data['HsMarket'] == 'sz'),
             'Classification'] = '指数'

    data['code'] = data['code'].astype(str)

    for i in data.index:
        faker_code = '000000'
        code = data.loc[i, 'code']

        if len(code) < 6:
            data.loc[i, 'code'] = f'{faker_code[len(code):]}{code}'  # .format(, )

    data = data.rename(columns={'TxMarket': 'TxdMarket'})

    data.loc[:, ['name', 'Level1', 'Level2']] = None
    data.loc[:, ['StartDate', 'EndDate', 'RecordDate']] = pd.Timestamp().today().date()

    columns_list = ['name', 'code', 'TxdMarket', 'HsMarket', 'Classification',
                    'Level1', 'Level2', 'StartDate', 'EndDate', 'RecordDate']

    data = data[columns_list]

    str_list = ['name', 'code', 'TxdMarket', 'HsMarket', 'Classification', 'Level1', 'Level2']

    data[str_list] = data[str_list].astype(str)

    alc.pd_replace(data=data, database='stock_basic_information', table='record_stock_daily_data')


class StockDailyData:

    def __init__(self):

        self.file_list = ['E:/SOFT/Finace_software/vipdoc/sz/lday/',
                          'E:/SOFT/Finace_software/vipdoc/sh/lday/']
    def open_file(self):
        vipdoc = "E:/SOFT/Finace_software/vipdoc"
        os.open(vipdoc)

    def read_stock_file(self) -> None:

        """
        读取股票文件列表，解析股票代码、市场代码和分类，并将结果保存到Excel文件中。

        返回:
        None
        """

        code_list, market_list, hs_market_list, hs_classification_list = [], [], [], []

        for path in self.file_list:
            stock_file = ReadSaveFile.find_all_file(path)

            for i in stock_file:
                stock_code = i[2:8]
                tx_market = i[:2]

                hs_market = StockCode.code2market(stock_code)
                hs_classification = StockCode.code2classification(stock_code)

                code_list.append(stock_code)
                market_list.append(tx_market)
                hs_market_list.append(hs_market)
                hs_classification_list.append(hs_classification)

        df = pd.DataFrame({
            'code': code_list,
            'TxMarket': market_list,
            'HsMarket': hs_market_list,
            'Classification': hs_classification_list
        })

        df['code'] = df['code'].astype(str)

        df.to_excel('code_data/output/Tx_code.xls', sheet_name='Sheet1', index=False, header=True)

        print(df)


class TongXinDaDailyData:

    def __init__(self):
        self.data = None

    # 解析日线数据
    def exact_data(self, path: str) -> pd.DataFrame:
        """
        从指定文件中提取数据，返回一个包含日期、开盘价、收盘价、最高价、最低价、成交量和金额的DataFrame。

        参数:
        FilePath (str): 文件路径

        返回:
        pd.DataFrame: 包含提取数据的DataFrame
        """

        try:
            ofile = open(path, 'rb')
            buf = ofile.read()
            ofile.close()
            num = len(buf)
            no = num / 32
            items = list()
            b = 0
            e = 32
            for i in range(int(no)):
                a = unpack('IIIIIfII', buf[b:e])
                dd = pd.to_datetime(str(a[0])).date()
                op = a[1] / 100.0
                high = a[2] / 100.0
                low = a[3] / 100.0
                close = a[4] / 100.0
                money = a[5]
                vol = int(a[6] / 100.0)
                item = [dd, op, close, high, low, vol, money]
                items.append(item)

                b = b + 32

                e = e + 32

            columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']

            df = pd.DataFrame(data=items, columns=columns)

        except FileNotFoundError:
            print(f"File not found: {path}")
            df = pd.DataFrame(data=None)

        except Exception as e:
            print(f"Error reading file {path}: {e}")
            df = pd.DataFrame()

        return df

    def daily_data(self, num_start, num_end):
        # E:\SOFT\Finace_software\T0002\export
        for index in range(num_start, num_end):
            code = self.data.loc[index, 'code']
            TxdMarket = self.data.loc[index, 'TxdMarket']
            HsMarket = self.data.loc[index, 'HsMarket']
            MarketCode = self.data.loc[index, 'MarketCode']
            file_path = None

            if TxdMarket == 'sz':
                file_path = f'E:/SOFT/Finace_software/vipdoc/sz/lday/sz{code}.day'

            if TxdMarket == 'sh':
                file_path = f'E:/SOFT/Finace_software/vipdoc/sh/lday/sh{code}.day'

            if file_path:
                td_date = pd.Timestamp('today').date()
                table_name = f'{HsMarket}{code}'
                stock_data = self.exact_data(file_path)

                if len(stock_data):
                    alc.pd_replace(data=stock_data, database='stock_daily_data', table=table_name)
                    # 更新表格记录信息
                    StartDate = stock_data.iloc[0]['date'].date()
                    EndDate = stock_data.iloc[-1]['date'].date()


                else:
                    print(f'{table_name}, 更新失败；')

    def multiple_process(self):
        self.data = alc.pd_read(database='stock_basic_information', table='record_stock_daily_data')
        self.data = self.data[self.data['RecordDate'] != pd.to_datetime('2021-12-14').date()].reset_index(drop=True)

        print(self.data)
        # exit()
        num_index = len(self.data)
        index1 = int(num_index * 0.2)
        index2 = int(num_index * 0.4)
        index3 = int(num_index * 0.6)
        index4 = int(num_index * 0.8)

        p1 = multiprocessing.Process(target=self.daily_data, args=(0, index1,))
        p2 = multiprocessing.Process(target=self.daily_data, args=(index1, index2,))
        p3 = multiprocessing.Process(target=self.daily_data, args=(index2, index3,))
        p4 = multiprocessing.Process(target=self.daily_data, args=(index3, index4,))
        p5 = multiprocessing.Process(target=self.daily_data, args=(index4, num_index,))

        p1.start()
        p2.start()
        p3.start()
        p4.start()
        p5.start()


def split_data(data: pd.DataFrame, num_chunks: int):
    """
    将数据划分为多个块。

    参数:
    code_data (pd.DataFrame): 要划分的数据
    num_chunks (int): 数据块的数量

    返回:
    Tuple[pd.DataFrame]: 数据块的元组
    """
    chunk_size = len(data) // num_chunks
    chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]

    return chunks


class TongXinDaMinuteData:

    def __init__(self):
        self.data = None

    def get_date_str(self, h1: int, h2: int) -> str:
        """
        根据两个整数值解析出日期和时间，并返回格式化的字符串。

        参数:
        h1 (int): 包含年、月、日信息的整数
        h2 (int): 包含小时、分钟信息的整数

        返回:
        str: 格式化后的日期时间字符串，格式为 "YYYY-MM-DD HH:MM"
        """
        # h1->0,1字节; h2->2,3字节;

        year = math.floor(h1 / 2048) + 2004  # 解析出年
        month = math.floor(h1 % 2048 / 100)  # 月
        day = h1 % 2048 % 100  # 日
        hour = math.floor(h2 / 60)  # 小时
        minute = h2 % 60  # 分钟

        # 使用f-string进行格式化，确保月份、日期、小时和分钟始终为两位数
        return f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"

    def exact_stock(self, path: str) -> pd.DataFrame:
        """
        从指定的二进制文件中提取股票数据，返回一个包含日期、开盘价、收盘价、最高价、最低价、成交量和金额的DataFrame。

        参数:
        FilePath (str): 文件路径

        返回:
        pd.DataFrame: 包含提取数据的DataFrame
        """
        try:
            ofile = open(path, 'rb')
            buf = ofile.read()
            ofile.close()
            num = len(buf)
            no = num / 32

            items = list()

            e = 32
            b = 0
            for i in range(int(no)):
                a = unpack('HHfffffif', buf[b:e])
                dd = self.get_date_str(a[0], a[1])
                dd = pd.to_datetime(dd)
                op = a[2]
                high = a[3]
                low = a[4]
                close = a[5]
                money = a[6]
                vol = a[7]
                item = [dd, op, close, high, low, vol, money]
                items.append(item)

                b = b + 32
                e = e + 32

            columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
            data = pd.DataFrame(data=items, columns=columns)

        except FileNotFoundError:
            data = pd.DataFrame(data=None)

        return data

    def minute_data(self, num_start, num_end):

        td_date = pd.Timestamp('today').date()

        for index in range(num_start, num_end):
            code = self.data.loc[index, 'code']
            TxdMarket = self.data.loc[index, 'TxdMarket']
            MarketCode = self.data.loc[index, 'MarketCode']

            file_path = None
            if TxdMarket == 'sz':
                file_path = f'E:/SOFT/Finace_software/vipdoc/sz/minline/sz{code}.lc1'

            if TxdMarket == 'sh':
                file_path = f'E:/SOFT/Finace_software/vipdoc/sh/minline/sh{code}.lc1'

            if file_path:
                table_name = f'{MarketCode}'
                stock_data = self.exact_stock(file_path)

                if len(stock_data):
                    alc.pd_replace(data=stock_data, database='stock_1m_data', table=table_name)

                else:
                    # 更新表格记录信息
                    failed_date = pd.to_datetime('2050-01-01').date()

    def multiple_process_minute_data(self) -> None:
        """
         使用多进程处理数据。
         """

        self.data = alc.pd_read(database='stock_basic_information', table='record_stock_minute_data')
        self.data = self.data[self.data['StartDate'].isnull()].reset_index(drop=True)
        print(self.data)
        # 分割数据
        num_chunks = 5
        data_chunks = split_data(self.data, num_chunks)
        num_index = len(self.data)
        index1 = int(num_index * 0.2)
        index2 = int(num_index * 0.4)
        index3 = int(num_index * 0.6)
        index4 = int(num_index * 0.8)

        p1 = multiprocessing.Process(target=self.minute_data, args=(0, index1,))
        p2 = multiprocessing.Process(target=self.minute_data, args=(index1, index2,))
        p3 = multiprocessing.Process(target=self.minute_data, args=(index2, index3,))
        p4 = multiprocessing.Process(target=self.minute_data, args=(index3, index4,))
        p5 = multiprocessing.Process(target=self.minute_data, args=(index4, num_index,))

        p1.start()
        p2.start()
        p3.start()
        p4.start()
        p5.start()


class CombineMinuteData:

    def __init__(self):
        self.db = 'stock_daily_data'
        self.table_list = None
        self.daily_record = None
        self.record_1m = None

    def combine_minute(self, stock_code, stock_name, MarketCode):

        # 读取 stock_data_1m;
        db_a = 'stock_data_1m'
        tb_a = f'1m_{stock_code}_{stock_name}'
        try:
            data_1ma = alc.pd_read(database=db_a, table=tb_a)
            data_1ma = data_1ma.rename(columns={'trade_date': 'date'})
            data_1ma = data_1ma[['date', 'open', 'close', 'high', 'low', 'volume', 'money']]

        except pandas.errors.DatabaseError:
            data_1ma = pd.DataFrame(data=None)

        # 读取 stock_1m_data;
        db_b = 'stock_1m_data'
        tb_b = f'{MarketCode}'
        try:
            data_1mb = alc.pd_read(database=db_b, table=tb_b)

        except pandas.errors.DatabaseError:  # pandas.errors.DatabaseError
            data_1mb = pd.DataFrame(data=None)

        # code_data combine
        data_all = pd.concat([data_1ma, data_1mb]).drop_duplicates(subset=['date']).sort_values(
            by=['date']).reset_index(drop=True)

        start_time = data_all.iloc[0]['date'].date()
        end_time = data_all.iloc[-1]['date'].date()

        alc.pd_replace(data=data_all, database=db_b, table=tb_b)

        print(f'{stock_name}, {MarketCode}, 数据合并成功；')

    def combine_minute_data(self, num_start=None, num_end=None):

        for index in range(num_start, num_end):
            stock_name = self.record_1m.loc[index, 'stock_name']
            stock_code = self.record_1m.loc[index, 'stock_code']
            MarketCode = self.record_1m.loc[index, 'MarketCode']

            # 记录表格
            try:
                self.combine_minute(stock_code=stock_code, stock_name=stock_name, MarketCode=MarketCode)

            except:
                # 修改表格记录
                print(f'{stock_name}, {MarketCode}, 数据合并失败；')

    def multiple_combine_minute(self):
        self.record_1m = alc.pd_read(database='stock_basic_information', table='stock_record_1m_data')
        print(self.record_1m)
        exit()
        num_index = len(self.record_1m)
        if num_index:
            index1 = int(num_index * 0.2)
            index2 = int(num_index * 0.4)
            index3 = int(num_index * 0.6)
            index4 = int(num_index * 0.8)

            p1 = multiprocessing.Process(target=self.combine_minute_data, args=(0, index1,))
            p2 = multiprocessing.Process(target=self.combine_minute_data, args=(index1, index2,))
            p3 = multiprocessing.Process(target=self.combine_minute_data, args=(index2, index3,))
            p4 = multiprocessing.Process(target=self.combine_minute_data, args=(index3, index4,))
            p5 = multiprocessing.Process(target=self.combine_minute_data, args=(index4, num_index,))

            p1.start()
            p2.start()
            p3.start()
            p4.start()
            p5.start()

    # sh603087 error
    def rename_daily_data_a(self, num_start=None, num_end=None):

        if num_start is None or num_end is None:
            raise ValueError("num_start and num_end must be provided")

        if num_start < 0 or num_end <= num_start:
            raise ValueError("num_start must be non-negative and num_end must be greater than num_start")

        for i in range(num_start, num_end):
            MarketCode = self.table_list[i][0]
            sql = f'alter table {self.db}.{MarketCode} change amount money int;'

            try:
                execute_sql(database=self.db, sql=sql, params=())
                print(f'success; {sql}')

            except pymysql.err.DataError:
                pass

    def multiple_process_rename_daily_a(self):

        self.table_list = 'return_all_table(database=self.db)'

        num_index = len(self.table_list)
        index1 = int(num_index * 0.2)
        index2 = int(num_index * 0.4)
        index3 = int(num_index * 0.6)
        index4 = int(num_index * 0.8)

        p1 = multiprocessing.Process(target=self.rename_daily_data_a, args=(0, index1,))
        p2 = multiprocessing.Process(target=self.rename_daily_data_a, args=(index1, index2,))
        p3 = multiprocessing.Process(target=self.rename_daily_data_a, args=(index2, index3,))
        p4 = multiprocessing.Process(target=self.rename_daily_data_a, args=(index3, index4,))
        p5 = multiprocessing.Process(target=self.rename_daily_data_a, args=(index4, num_index,))

        p1.start()
        p2.start()
        p3.start()
        p4.start()
        p5.start()

    def rename_daily_data_b(self, num_start=None, num_end=None):
        """
        将每日股票数据表中的 'amount' 列重命名为 'money'，并在记录表中更新状态。

        参数:
            num_start (int, 可选): 处理行的起始索引。默认为 None。
            num_end (int, 可选): 处理行的结束索引。默认为 None。

        异常:
            ValueError: 如果 `num_start` 或 `num_end` 为 None，或者 `num_start` 非负且 `num_end` 不大于 `num_start`。

        更新:
            如果操作成功，`DbTongxindaData` 记录表中的 'Transfer' 列将更新为 'success'，否则更新为 'ffed'。
        """

        # 参数验证
        if num_start is None or num_end is None:
            raise ValueError("num_start 和 num_end 必须提供")

        if num_start < 0 or num_end <= num_start:
            raise ValueError("num_start 必须非负且 num_end 必须大于 num_start")

        for index in range(num_start, num_end):
            MarketCode = self.daily_record.loc[index, 'MarketCode']
            print(MarketCode)
            tb = DbTongxindaData(primary_key=MarketCode)
            try:
                daily_data = alc.pd_read(database='stock_daily_data', table=MarketCode)
                daily_data = daily_data.rename(columns={'amount': 'money'})
                alc.pd_replace(data=daily_data, database='stock_daily_data', table=MarketCode)

                tb.renew_TbRecordStockDailyData(column='Transfer', new_data='success')

            except Exception as e:
                print(f'Error processing MarketCode {MarketCode}: {e}')
                tb.renew_TbRecordStockDailyData(column='Transfer', new_data='ffed')
                pass

    def multiple_process_rename_daily_b(self):
        self.daily_record = alc.pd_read(database='stock_basic_information', table='record_stock_daily_data')
        self.daily_record = self.daily_record[self.daily_record['Transfer'] == 'failed'].reset_index(drop=True)
        print(self.daily_record)
        # exit()
        num_index = len(self.daily_record)
        index1 = int(num_index * 0.2)
        index2 = int(num_index * 0.4)
        index3 = int(num_index * 0.6)
        index4 = int(num_index * 0.8)

        p1 = multiprocessing.Process(target=self.rename_daily_data_b, args=(0, index1,))
        p2 = multiprocessing.Process(target=self.rename_daily_data_b, args=(index1, index2,))
        p3 = multiprocessing.Process(target=self.rename_daily_data_b, args=(index2, index3,))
        p4 = multiprocessing.Process(target=self.rename_daily_data_b, args=(index3, index4,))
        p5 = multiprocessing.Process(target=self.rename_daily_data_b, args=(index4, num_index,))

        p1.start()
        p2.start()
        p3.start()
        p4.start()
        p5.start()


class TongXinDaData:

    def __init__(self):
        self.software_path = r"E:\SOFT\Finace_software\T0002\export\我的自选股"

    def data_SH(self, stock: str):
        file = f"SH#{stock}.csv"
        path = os.path.join(self.software_path, "T0002", "export", file)
        df = load_csv(path)
        return df

    def csv_to_mysql(self, ):

        """
        将指定文件夹中的CSV文件导入到MySQL数据库中。如果CSV文件中的数据对应的年份数据库不存在，则会自动创建。此csv数据，最好是通信达下载导出的数据。

        参数:
            folder_path (str): 包含CSV文件的文件夹路径，默认路径为示例路径。

            例如：
            folder_path = r"C:\\Users\\User\\Desktop\\临时文件\\自选股分钟数据"

        返回:
            failed_files (list): 导入失败的股票代码列表。

        功能:
        1. 获取指定文件夹中的所有CSV文件。
        2. 对于每个CSV文件，读取文件内容并根据文件名提取股票代码。
        3. 将CSV文件中的日期列转换为pandas的datetime对象，并提取年份信息。
        4. 根据年份将数据分组，并将每个年份的数据导入到对应年份的数据库中。
        5. 如果导入过程中出现问题，将失败的股票代码添加到failed_files列表中。
        6. 最后，返回导入失败的股票代码列表。

        """

        csv_files = [f for f in os.listdir(self.software_path) if f.endswith('.csv')]
        failed_files = []

        for file in csv_files:
            # 读取CSV文件
            stock_code = file.split('#')[1][:6]

            file_path = os.path.join(self.software_path, file)

            df = load_csv(file_path)

            # 假设日期列名为'Date'，并且格式为'YYYY-MM-DD HH:MM:SS'
            df['date'] = pd.to_datetime(df['date'])
            df['Year'] = df['date'].dt.year
            grouped = df.groupby('Year')

            for year, data in grouped:
                # 连接到对应年份的数据库  data1m2024
                db_name = f'data1m{year}'

                if hash(year) < 2024:
                    continue

                create_stock_table(db_name, stock_code)
                data = data.drop(columns=['Year'])

                success = upsert_dataframe_to_mysql(data, db_name, stock_code)

                if not success:
                    failed_files.append(stock_code)

        print(f"failed stocks {failed_files}")

        return failed_files


if __name__ == '__main__':
    pass
    # import_csv_to_mysql()

