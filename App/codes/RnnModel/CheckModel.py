# -*- coding: utf-8 -*-
import pandas as pd
from .RnnRunModel import PredictionCommon
from ..MySql.LoadMysql import LoadRnnModel
from ..MySql.DataBaseStockPool import TableStockPool
from ..MySql.sql_utils import Stocks
import matplotlib.pyplot as plt
import multiprocessing
from ..Evaluation.CountPool import PoolCount
from .Rnn_utils import reset_id_time, reset_record_time, date_range
import logging
from typing import Optional

# logging.basicConfig(format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s', level=logging.info)
logging.basicConfig(filename='error_log.txt', level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

plt.rcParams['font.sans-serif'] = ['FangSong']
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)


class StockEvaluator:
    """
    用于评估股票的类，使用预测模型对指定范围内的股票进行评估，并更新模型检查状态。

    属性:
        DB_RNN (object): 数据库连接对象，用于操作RNN模型数据。
        TB_TRAIN_RECORD (str): 数据库中训练记录的表名。

    参数:
        day (str): 要评估的日期。
        start_index (int): 评估起始索引，决定从数据集的哪个位置开始评估。
        end_index (int): 评估结束索引，决定在哪个位置停止评估。
        code_data (pd.DataFrame): 包含股票信息的数据集。
        month (str): 指定月份的参数，用于模型预测。
        check_model (bool): 指示是否需要更新模型检查状态。
    """

    DB_RNN = LoadRnnModel.db_rnn
    TB_TRAIN_RECORD = LoadRnnModel.tb_train_record

    def __init__(self, day, start_index, end_index, data, month, check_model):
        """
        初始化 StockEvaluator 对象。

        参数:
            day (str): 要评估的日期。
            start_index (int): 评估起始索引。
            end_index (int): 评估结束索引。
            code_data (pd.DataFrame): 包含股票信息的数据集。
            month (str): 指定月份的参数。
            check_model (bool): 是否更新模型检查状态。
        """
        self.day = day
        self.start_index = start_index
        self.end_index = end_index
        self.data = data
        self.month = month
        self.check_model = check_model

    def evaluate_stock(self, index):
        """
        对单只股票进行评估，并根据需要更新模型检查状态。

        参数:
            index (int): 数据集中股票的索引位置。
        """

        stock_code = self.data.loc[index, 'code']
        record_id = self.data.loc[index, 'id']
        check_date = pd.Timestamp('now').date()

        try:
            logging.info(f'开始评估，股票代码: {stock_code} (ID: {record_id})')
            run = PredictionCommon(stock=stock_code, month_parsers=self.month, monitor=False, check_date=self.day)
            run.single_stock()

            if self.check_model:
                self.update_model_check_status(record_id, check_date, 'success')

            logging.info(f'股票代码: {stock_code} (ID: {record_id}) 评估成功')

        except Exception as ex:
            logging.error(f'Error evaluating stock code {stock_code} (ID: {record_id}): {ex}')

            if self.check_model:
                self.update_model_check_status(record_id, check_date, 'error')

    def update_model_check_status(self, record_id, check_date, status):
        """
        更新数据库中指定记录的模型检查状态。

        参数:
            record_id (int): 要更新的记录ID。
            check_date (date): 模型检查日期。
            status (str): 要设置的模型检查状态，可以是 'success' 或 'error'。
        """

        sql = '''
               UPDATE {table} SET ModelCheckTiming = %s, ModelCheck = %s, ModelError = %s
               WHERE id = %s;
               '''.format(table=self.TB_TRAIN_RECORD)

        parser = (pd.Timestamp.now(), status, status, check_date, record_id)

        try:
            LoadRnnModel.set_table_train_record(sql, parser)
            logging.info(f'更新记录ID {record_id}的模型检查状态为: {status}')

        except Exception as ex:
            logging.error(f'更新模型检查状态时发生错误: {ex}')

    def run_evaluation(self):
        """
        运行股票评估，在指定的索引范围内逐一评估股票。

        进度会实时打印，并调用 evaluate_stock 方法进行具体评估。
        """
        for count, index in enumerate(range(self.start_index, self.end_index), 1):
            remaining = self.end_index - index
            logging.info(f'当前进度，剩余{remaining}；')
            self.evaluate_stock(index)


def stock_evaluate(day_: str, _num: int, num_: int, data: pd.DataFrame,
                   month_parsers: str, check_model: bool) -> None:
    """
    评估股票的函数，对指定范围内的股票进行评估，并根据评估结果更新模型检查状态。

    参数:
        day_ (str): 要评估的日期，格式为 'YYYY-MM-DD'。
        _num (int): 评估起始索引，表示从数据集中哪个位置开始评估。
        num_ (int): 评估结束索引，表示在哪个位置停止评估。
        code_data (pd.DataFrame): 包含股票信息的 Pandas 数据集，至少应包含 'code' 和 'id' 两列。
        month_parsers (str): 指定月份的参数，用于模型预测，格式为 'YYYY-MM'。
        check_model (bool): 指示是否需要在评估后更新模型检查状态。

    返回:
        None: 该函数没有返回值。
    """

    count = 0

    for index in range(_num, num_):
        stock_ = data.loc[index, 'code']
        id_ = data.loc[index, 'id']
        check_date = pd.Timestamp('now').date()

        logging.info(f'当前进度，剩余{num_ - _num - count}； 当前股票：{stock_}')

        try:
            # 执行单只股票的预测
            run = PredictionCommon(stock=stock_, month_parsers=month_parsers, monitor=False, check_date=day_)
            run.single_stock()

            # 如果启用模型检查，更新数据库中的模型检查状态为 'success'
            if check_model:
                sql = '''
                UPDATE table_name SET 
                    ModelCheckTiming = %s, 
                    ModelCheck = 'success', 
                    ModelError = 'success' 
                WHERE id = %s;
                '''

                parser = (pd.Timestamp.now(), check_date, id_)
                LoadRnnModel.set_table_train_record(sql, parser)

        except Exception as ex:
            logging_test = f'stock code {stock_} Error: {ex}'
            logging.info(logging_test)
            logging.error("Error occurred: %s", str(ex))

            # 如果出现错误且启用模型检查，更新数据库中的模型检查状态为 'error'
            if check_model:
                sql = '''
                UPDATE table_name SET 
                    ModelCheck = 'error',
                    ModelError = 'error',
                    ModelCheckTiming = %s 
                WHERE id = %s;
                '''

                parser = (check_date, id_)
                LoadRnnModel.set_table_train_record(sql, parser)

        count += 1


def multiprocessing_count_pool(day_: str, month_parsers: str = '2022-02',
                               check_model: bool = False) -> None:
    """
    使用多进程处理股票评估任务，将数据分成三部分并行处理。

    参数:
        day_ (str): 要处理的日期，格式为 'YYYY-MM-DD'。
        month_parsers (str): 指定月份的参数，用于模型预测，格式为 'YYYY-MM'。
        check_model (bool): 指示是否需要在评估后更新模型检查状态。

    返回:
        None: 该函数没有返回值。
    """

    data = TableStockPool.load_StockPool()

    if not data.empty:
        shape_ = data.shape[0]

        print(f'处理日期{day_}， 处理个数：{shape_}')

        l1 = shape_ // 3
        l2 = shape_ // 3 * 2

        p1 = multiprocessing.Process(target=stock_evaluate, args=(day_, 0, l1, data, month_parsers, check_model,))
        p2 = multiprocessing.Process(target=stock_evaluate, args=(day_, l1, l2, data, month_parsers, check_model,))
        p3 = multiprocessing.Process(target=stock_evaluate, args=(day_, l2, shape_, data, month_parsers, check_model,))

        # 启动进程
        p1.start()
        p2.start()
        p3.start()

        # 等待进程完成
        p1.join()
        p2.join()
        p3.join()


class RMHistoryCheck:

    def __init__(self, _date: Optional[str] = None,
                 date_: Optional[str] = None,
                 month_parsers: str = '2022-02'):
        """
        初始化函数，设置日期和月解析器
        :param _date: 开始日期（可选）
        :param date_: 结束日期（可选）
        :param month_parsers: 月份解析器，默认值为 '2022-02'
        """

        self.month_parsers = month_parsers

        # 如果未提供结束日期，默认为当前日期
        if not date_:
            self.date_ = pd.Timestamp.now().date()

        else:
            self.date_ = pd.to_datetime(date_).date()

        # 如果未提供开始日期，默认为结束日期
        if not _date:
            self._date = self.date_

        else:
            self._date = pd.to_datetime(_date).date()

    def check1stock(self, stock: str, reset_record: bool = False) -> None:

        """
        检查单个股票记录，并执行相关操作
        :param stock: 股票名称
        :param reset_record: 是否重置记录，默认为False
        """

        name, code, id_ = Stocks(stock)

        if reset_record:
            reset_id_time(id_, self._date)

        dates = date_range(self._date, self.date_)
        for d_ in dates:
            run = PredictionCommon(stock=name, month_parsers=self.month_parsers, monitor=False, check_date=d_)
            run.single_stock()

    def loop_by_date(self) -> None:
        """
        按日期循环操作，执行特定的操作
        """

        list_day = date_range(self._date, self.date_)
        reset_record_time(list_day[0])

        for day_ in list_day:
            multiprocessing_count_pool(day_, check_model=False)

            my_pool_count = PoolCount()
            my_pool_count.count_trend()
            print(f'日期{day_}完成;')

    def loop_by_check_model(self) -> None:
        """
        按日期循环并检查模型，执行特定操作
        """

        list_day = date_range(self._date, self.date_)
        reset_record_time(list_day[0])

        for day_ in list_day:
            multiprocessing_count_pool(day_, check_model=True)
            my_pool_count = PoolCount()
            my_pool_count.count_trend()
            print(f'日期{day_}完成;')


if __name__ == '__main__':
    start_ = '2024-01-15'
    rm = RMHistoryCheck(_date=start_)
    rm.loop_by_date()
