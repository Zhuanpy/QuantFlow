# -*- coding: utf-8 -*-
from ..MySql.LoadMysql import LoadRnnModel

from keras import Sequential
from keras.layers import Dense
from keras.layers import Flatten
from keras.layers import Conv2D
from keras.layers import AveragePooling2D

from keras.optimizers import adam_v2  # Adam
from App.codes.utils.Normal import ReadSaveFile as rf
from ..MySql.sql_utils import Stocks
import numpy as np
from keras import backend as k
import pandas as pd
from ..parsers.RnnParser import *
from .Rnn_utils import find_file_in_paths
from ..RnnDataFile.stock_path import StockDataPath

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


def create_model():
    model = Sequential()
    model.add(Conv2D(filters=6, kernel_size=(5, 5),
                     strides=(1, 1), input_shape=(30, 30, 1),
                     padding='valid', activation='relu'))
    model.add(AveragePooling2D(pool_size=(2, 2)))
    model.add(Conv2D(filters=16, kernel_size=(5, 5), strides=(1, 1),
                     padding='valid', activation='relu'))
    model.add(AveragePooling2D(pool_size=(2, 2)))
    model.add(Flatten())
    model.add(Dense(units=120, activation='relu'))
    model.add(Dense(units=84, activation='relu'))
    model.add(Dense(units=1))
    return model


class BuiltModel:

    def __init__(self, stock: str, months: str):

        self.name, self.code, self.stock_id = Stocks(stock)

        self.months = months

    def train_model(self, model_name: str, lr=0.01, num_train=30, num_test=10):

        k.clear_session()  # 清除缓存

        x_name = f'{model_name}_{self.code}_x.npy'
        data_x_path = find_file_in_paths(self.months, 'train_data', x_name)

        y_name = f'{model_name}_{self.code}_y.npy'
        data_y_path = find_file_in_paths(self.months, 'train_data', y_name)

        data_x = np.load(data_x_path, f'{model_name}_{self.code}_x.npy')
        data_y = np.load(data_y_path, f'{model_name}_{self.code}_y.npy')

        # 数据拆分
        len_data = int(data_y.shape[0] * 0.8)

        train_x = data_x[:len_data]
        train_y = data_y[:len_data]

        test_x = data_x[len_data:]
        test_y = data_y[len_data:]

        # 搭建模型
        model = create_model()

        try:
            # 读取历史权重数据
            f = f'weight_{model_name}_{self.code}.h5'
            weight_path = find_file_in_paths(self.months, 'weight', f)
            model.load_weights(filepath=weight_path)
            epochs = 100

        except OSError:
            epochs = 500

        model.compile(loss='mean_squared_error', optimizer=adam_v2.Adam(lr))  # 编译
        model.fit(train_x, train_y, epochs=epochs, batch_size=num_train)  # 训练
        loss = model.evaluate(test_x, test_y, batch_size=num_test)  # 评估
        print(loss)

        # 评估， 保存评估， 保存训练参数， 保存模型
        records = rf.read_json(self.months, self.code)
        records[model_name] = loss
        rf.save_json(records, self.months, self.code)

        # 保存权重
        weight_file_name = 'weight_{model_name}_{self.code}.h5'
        weight_path = StockDataPath.model_weight_path(self.months, weight_file_name)
        model.save_weights(weight_path)  # 保存参数

        # 保存模型
        model_file_name = f'{model_name}_{self.code}.h5'
        model_path = StockDataPath.model_weight_path(self.months, model_file_name)
        model.save(model_path)  # 保存模型

    def model_one(self, model_name: str):
        self.train_model(model_name)

    def model_all(self):
        for name in ModelName:
            self.train_model(name)


class RMBuiltModel:

    def __init__(self, months: str, ):
        self.months = months

    def train1(self, stock):

        train = BuiltModel(stock, self.months)
        train.model_all()

    def train_remaining_models(self):
        training_records = LoadRnnModel.load_train_record()
        training_records = training_records[(training_records['ParserMonth'] == self.months) &
                                            (training_records['ModelData'] == 'success') &
                                            (training_records['ModelCreate'] != 'success')]

        if training_records.empty:
            return False

        training_records = training_records.reset_index(drop=True)
        shapes = training_records.shape[0]
        current = pd.Timestamp('now').date()

        for i, row in training_records.iterrows():
            stock_ = row['name']
            id_ = row.loc['id']

            print(f'当前股票：{stock_};\n训练进度：\n总股票数: {shapes}个; 剩余股票: {(shapes - i)}个;')

            try:
                train = BuiltModel(stock_, self.months)
                train.model_all()

                sql = f''' ModelCreate = 'success', ModelCreateTiming = %s where id = %s;'''

                params = (current, id_)

            except Exception as ex:
                print(f'ModelCreate Error : {ex}')
                sql = f''' ModelCreate = 'error', ModelCreateTiming = '{current}' where id = '{id_}';'''
                params = (current, id_)

            LoadRnnModel.set_table_train_record(sql, params)


if __name__ == '__main__':
    month_ = '2023-01'
    run = RMBuiltModel(month_)
    run.train_remaining_models()
