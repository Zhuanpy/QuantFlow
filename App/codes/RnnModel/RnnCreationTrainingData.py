from ..MySql.LoadMysql import LoadRnnModel
from .RnnCreationData import TrainingDataCalculate
import pandas as pd

"""
1. 创建训练数据

"""


class RMTrainingData:

    def __init__(self, months: str, start_: str, ):  # _month
        self.month = months
        self.start_date = start_

    def single_stock(self, stock: str):
        calculation = TrainingDataCalculate(stock, self.month)
        calculation.calculation_single()

    def update_train_records(self, records):
        """更新训练表格记录"""
        ids = tuple(records.id)

        sql = f'''ParserMonth = %s, ModelData = 'pending' where id in %s;'''

        params = (self.month, ids)
        LoadRnnModel.set_table_train_record(sql, params)

    def all_stock(self):

        load = LoadRnnModel.load_train_record()

        records = load[load['ParserMonth'] == self.month]

        # 更新训练记录中的状态
        if records.empty:
            records = load.copy()
            records['ParserMonth'] = self.month
            records['ModelData'] = 'pending'
            records['ModelCheck'] = 'pending'
            records['ModelError'] = 'pending'

            self.update_train_records(records)

        # 查看等待数据
        records = records[~records['ModelData'].isin(['success'])].reset_index(drop=True)

        if records.empty:
            print(f'{self.month}月训练数据创建完成')
            return False  # 结束运行，因为 records 为空

        for i, row in records.iterrows():
            stock_ = row['name']
            id_ = row['id']

            print(f'\n计算进度：'
                  f'\n剩余股票: {(records.shape[0] - i)} 个; 总股票数: {records.shape[0]}个;'
                  f'\n当前股票：{stock_};')

            try:
                run = TrainingDataCalculate(stock_, self.month)
                run.calculation_read_from_sql()

                sql = f'''ModelData = 'success', ModelDataTiming = %s where id = %s; '''

                params = (pd.Timestamp('now').date(), id_)
                LoadRnnModel.set_table_train_record(sql, params)

            except Exception as ex:
                print(f'Model Data Create Error: {ex}')
                sql = f'''ModelData = 'error', ModelDataTiming = NULL where id = %s; '''
                params = (LoadRnnModel.db_rnn, LoadRnnModel.tb_train_record, id_)
                LoadRnnModel.set_table_train_record(sql, params)

        return True


if __name__ == '__main__':
    month_ = '2023-01'
    start_d = '2018-01-01'
    running = RMTrainingData(month_, start_d)
    running.all_stock()
