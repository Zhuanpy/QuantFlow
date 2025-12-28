import multiprocessing
import time
import pandas as pd
from App.codes.MySql.LoadMysql import LoadFundsAwkward as aw
from App.codes.MySql.LoadMysql import RecordStock
from App.codes.MySql.DataBaseStockPool import TableStockPool
from DlEastMoney import DownloadData as dle
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class DownloadFundsAwkward:
    """
    下载排名前3年收益排名前 600 基金的重仓前10股票数据。
    """

    def __init__(self, download_date):
        self.download_date = pd.to_datetime(download_date).date()  # 确保日期格式统一
        self.pending = None  # 保存需要处理的数据

    def pending_data(self):
        """
        检查并准备待处理数据。
        如果下载日期不同，将状态更新为 'pending' 并修改日期。
        """
        record = RecordStock.load_record_download_top500fundspositionstock()
        record['Date'] = pd.to_datetime(record['Date'])

        # 获取记录的日期
        record_date = record.loc[0, 'Date'].date() if not record.empty else None

        # 如果日期不匹配，更新状态和日期
        if record_date != self.download_date:
            record['Date'] = self.download_date
            record['Status'] = 'pending'
            sql = "UPDATE recordtopfunds500 SET `Status` = 'pending', `Date` = %s;"
            params = (self.download_date,)
            RecordStock.set_table_record_download_top500fundspositionstock(sql, params)

        # 筛选状态为 'pending' 的记录
        return record[record['Status'] == 'pending'].reset_index(drop=True)

    def awkward_top10(self, start: int, end: int):
        """
        下载并处理指定范围内的基金数据。
        :param start: 起始索引
        :param end: 结束索引
        """
        for index in range(start, end):
            fund = self.pending.loc[index]
            funds_name = fund['Name']
            funds_code = fund['Code']
            id_ = fund['id']

            logging.info(f"处理基金: {funds_name} ({funds_code})，进度: {index + 1}/{end - start}")

            try:
                # 下载数据
                data = dle.funds_awkward(funds_code)
            except Exception as ex:
                logging.error(f"下载失败，尝试备用方法。错误: {ex}")
                try:
                    data = dle.funds_awkward_by_driver(funds_code)
                except Exception as ex:
                    logging.error(f"备用方法失败，标记状态为 'failed'。错误: {ex}")
                    self._update_status(id_, 'failed')
                    continue

            if data.empty:
                logging.warning(f"{funds_name} 无有效数据，标记为 'failed'")
                self._update_status(id_, 'failed')
                continue

            # 处理并保存数据
            data['funds_name'] = funds_name
            data['funds_code'] = funds_code
            data['Date'] = self.download_date
            data = data[['stock_name', 'funds_name', 'funds_code', 'Date']]

            aw.append_fundsAwkward(data)
            self._update_status(id_, 'success')
            logging.info(f"{funds_name} 数据下载成功")
            time.sleep(5)

    def _update_status(self, id_, status):
        """
        更新记录状态。
        :param id_: 记录ID
        :param status: 更新后的状态 ('success' 或 'failed')
        """
        sql = "UPDATE recordtopfunds500 SET `Status` = %s WHERE `id` = %s;"
        params = (status, id_)
        RecordStock.set_table_record_download_top500fundspositionstock(sql, params)

    def multi_processing(self):
        """
        多进程处理基金数据下载。
        """
        self.pending = self.pending_data()

        if self.pending.empty:
            logging.info("没有待处理的基金数据")
            return

        indexes = self.pending.shape[0]
        num_processes = 3  # 设置并行进程数

        if indexes <= num_processes:
            # 数据量较少时，仅用一个进程处理
            self.awkward_top10(0, indexes)
        else:
            # 切分任务范围
            step = indexes // num_processes
            processes = []

            for i in range(num_processes):
                start = i * step
                end = indexes if i == num_processes - 1 else (i + 1) * step
                process = multiprocessing.Process(target=self.awkward_top10, args=(start, end))
                processes.append(process)
                process.start()

            for process in processes:
                process.join()

        logging.info("基金数据处理完成")


class AnalysisFundsAwkward:
    """
    # 函数说明
    # 分析统计数据 Analysis Funds Awkward Data
    # 1.统计出股票池；
    # 2.找出基金增持股票；
    # 3.找出基金减持股票；
    # 4.找出板块变动数据；
    """

    def __init__(self, dl_date):

        self.DlDate = pd.to_datetime(dl_date)
        self.pool = TableStockPool.load_StockPool()

        self.awkward = aw.load_fundsAwkward()
        self.awkward = self.awkward[self.awkward['Selection'] == 1]

        self.num_max = 200
        self.num_min = 1

        self.count_dic = {}

    def normalization_all_data(self):

        for _, row in self.pool.iterrows():

            stock_name = row['name']
            id_ = row['id']
            data_ = self.awkward[self.awkward['stock_name'] == stock_name].groupby('Date').count().reset_index()

            data_['stock_name'] = stock_name

            data_ = data_.rename(columns={'Selection': 'count'})
            data_['TrendCount'] = data_['count'] - data_['count'].shift(1)
            data_['score'] = round((data_['count'] - self.num_min) / (self.num_max - self.num_min), 4)
            data_ = data_[['stock_name', 'count', 'TrendCount', 'score', 'Date']]

            if data_.shape[0]:
                score = data_.iloc[0]['score']
                aw.append_awkwardNormalization(data_)

            else:
                score = 0

            # 更新股票池基金得分
            sql = f'''FundsAwkward = %s where id = '%s' ;'''
            parser = (score, id_)
            TableStockPool.set_table_to_pool(sql, params=parser)
            self.count_dic[stock_name] = score

        print(f'Success count: {self.count_dic}')

    def normalization_select_date(self):

        awkward = self.awkward[self.awkward['Date'] == self.DlDate]
        print(awkward.head())
        print(self.pool.head())
        # exit()
        # todo 此函数和用法有问题，需要改进
        if awkward.empty:
            return False

        for _, row in self.pool.iterrows():

            stock_name = row['name']
            stock_id = row['id']

            data_ = self.awkward[self.awkward['stock_name'] == stock_name].groupby('Date').count().tail(3).reset_index()

            # print(data_)

            # data_awkward = awkward[awkward['stock_name'] == stock_name].groupby('Date').count()
            # print(data_awkward)
            # exit()
            data_['stock_name'] = stock_name
            data_ = data_.rename(columns={'Selection': 'count'})
            data_['TrendCount'] = data_['count'] - data_['count'].shift(1)
            data_['score'] = round((data_['count'] - self.num_min) / (self.num_max - self.num_min), 4)
            data_ = data_[['stock_name', 'count', 'TrendCount', 'score', 'Date']].tail(1)

            if data_.shape[0]:
                score = data_.iloc[0]['score']
                aw.append_awkwardNormalization(data_)

            else:
                score = 0

            # 更新股票池基金得分
            sql = f'''FundsAwkward= %s where id='%s';'''
            parser = (score, stock_id)
            TableStockPool.set_table_to_pool(sql, parser)
            self.count_dic[stock_name] = score

        print(f'Success count: {self.count_dic}')


if __name__ == '__main__':
    DlDate = '2024-01-14'
    analysis = AnalysisFundsAwkward(DlDate)
    analysis.normalization_select_date()
    # download = DownloadFundsAwkward(DlDate)
    # download.multi_processing()
