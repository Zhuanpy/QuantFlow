# -*- coding: utf-8 -*-
import os
from ..MySql.LoadMysql import LoadRnnModel

from keras import Sequential
from keras.layers import Dense
from keras.layers import Flatten
from keras.layers import Conv2D
from keras.layers import AveragePooling2D

from keras.optimizers import Adam
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

        # find_file_in_paths 找的是历史月份；当月文件用 StockDataPath 直接拼
        x_name = f'{model_name}_{self.code}_x.npy'
        y_name = f'{model_name}_{self.code}_y.npy'
        data_x_path = StockDataPath.train_data_path(self.months, x_name)
        data_y_path = StockDataPath.train_data_path(self.months, y_name)

        data_x = np.load(data_x_path)
        data_y = np.load(data_y_path)

        # 数据拆分
        len_data = int(data_y.shape[0] * 0.8)

        train_x = data_x[:len_data]
        train_y = data_y[:len_data]

        test_x = data_x[len_data:]
        test_y = data_y[len_data:]

        # 搭建模型
        model = create_model()

        # 尝试用上一月份的权重做增量训练（incremental fine-tune）；
        # find_file_in_paths 返回 (path, month) 元组，要解包
        epochs = 500
        try:
            wf = f'weight_{model_name}_{self.code}.h5'
            result = find_file_in_paths(self.months, 'weight', wf)
            weight_path = result[0] if isinstance(result, tuple) else result
            if weight_path and os.path.exists(weight_path):
                model.load_weights(filepath=weight_path)
                epochs = 100   # 已有历史权重时 fine-tune 用更少 epoch
        except (OSError, ValueError, TypeError):
            # 历史权重不可用（不存在 / 格式不兼容），从头训练
            pass

        model.compile(loss='mean_squared_error', optimizer=Adam(learning_rate=lr))  # 编译
        model.fit(train_x, train_y, epochs=epochs, batch_size=num_train)  # 训练
        loss = model.evaluate(test_x, test_y, batch_size=num_test)  # 评估
        print(loss)

        # 评估， 保存评估， 保存训练参数， 保存模型
        records = rf.read_json(self.months, self.code)
        records[model_name] = loss
        rf.save_json(records, self.months, self.code)

        # 保存权重（Keras 3 强制权重文件后缀 .weights.h5）
        # model_weight_path 只拼路径不建目录；新月份首次训练时 weight/ 还不存在，
        # save_weights 会报 "No such file or directory"。保存前确保目录存在。
        weight_file_name = f'weight_{model_name}_{self.code}.weights.h5'
        weight_path = StockDataPath.model_weight_path(self.months, weight_file_name)
        os.makedirs(os.path.dirname(weight_path), exist_ok=True)
        model.save_weights(weight_path)

        # 保存完整模型，仍使用 .h5 后缀以兼容现有 model_predictor 的查找逻辑
        # （Keras 3 仍支持 .h5 模型保存，只是 weight 必须 .weights.h5）
        model_file_name = f'{model_name}_{self.code}.h5'
        model_path = StockDataPath.model_path(self.months, model_file_name)
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        model.save(model_path)

    def model_one(self, model_name: str):
        self.train_model(model_name)

    def model_all(self):
        for name in ModelName:
            self.train_model(name)


class RMBuiltModel:
    """全部模式批量训练。

    与 RMTrainingData.all_stock 同样的套路：数据源用 ORM RnnTrainingRecords
    （quanttradingsystem.strategy_rnn_training），一次取 BATCH_SIZE 只标成
    processing，整批训练完再取下一批；状态写回 model_create，配合进度回调
    与停止开关，前端轮询 train_status / training_records 即可实时看到进度。
    """

    # 一批训练多少只：整批先标 processing，处理完再取下一批
    BATCH_SIZE = 10

    def __init__(self, months: str, ):
        self.months = months

    def train1(self, stock):

        train = BuiltModel(stock, self.months)
        train.model_all()

    def _mark_batch_processing(self, ids):
        """把一批记录的 model_create 一次性标成 processing。"""
        from App.exts import db
        from App.models.strategy.RnnTrainingRecords import RnnTrainingRecords
        if not ids:
            return
        try:
            db.session.rollback()
            (RnnTrainingRecords.query
             .filter(RnnTrainingRecords.id.in_(ids))
             .update({RnnTrainingRecords.model_create: 'processing'},
                     synchronize_session=False))
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _write_status(self, rec_id, ok: bool, err: str = None):
        """写回单只 model_create 状态（独立处理，避免训练异常污染 session）。"""
        from datetime import datetime
        from App.exts import db
        from App.models.strategy.RnnTrainingRecords import RnnTrainingRecords
        try:
            db.session.rollback()
            r = RnnTrainingRecords.query.get(rec_id)
            if not r:
                return
            if ok:
                r.model_create = 'success'
                r.model_create_timing = datetime.now()
            else:
                r.model_create = 'error'
                r.model_create_timing = None
                if err:
                    r.model_error = err[:500]
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f'状态写回失败 (id={rec_id}): {e}')

    def all_stock(self, progress_cb=None, should_stop=None):
        """只训练"数据已就绪(model_data=success)且尚未成功训练"的记录。

        尚未成功训练 = model_create != 'success'，且必须把 model_create IS NULL
        （从未训练过，列表显示为"-"）也算进来。SQL 里 NULL != 'success' 结果是
        NULL（非 TRUE）会被过滤掉，所以必须显式 or_ 上 is_(None)，否则只会选出
        极少数"曾训练失败(error)"的记录。
        """
        from sqlalchemy import or_
        from App.models.strategy.RnnTrainingRecords import RnnTrainingRecords

        pending = (RnnTrainingRecords.query
                   .filter_by(parser_month=self.months, model_data='success')
                   .filter(or_(RnnTrainingRecords.model_create.is_(None),
                               RnnTrainingRecords.model_create != 'success'))
                   .all())
        if not pending:
            print(f'{self.months} 没有待训练的记录（需 model_data=success 且 model_create!=success）')
            return False

        total = len(pending)
        if progress_cb:
            progress_cb(0, total, f'准备训练 {total} 只股票')

        failed = 0
        done = 0
        batch_count = (total + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        # 一次取 BATCH_SIZE 只标成 processing，整批训练完再取下一批
        for bi, start in enumerate(range(0, total, self.BATCH_SIZE)):
            if should_stop and should_stop():
                print('收到停止请求，提前结束训练循环')
                break

            batch = pending[start:start + self.BATCH_SIZE]
            self._mark_batch_processing([r.id for r in batch])
            if progress_cb:
                progress_cb(done, total,
                            f'加载第 {bi + 1}/{batch_count} 批（{len(batch)} 只），共 {total} 只')

            for rec in batch:
                if should_stop and should_stop():
                    print('收到停止请求，提前结束训练循环')
                    break

                rec_id = rec.id
                stock_ = rec.name or rec.code

                if progress_cb:
                    progress_cb(done, total, f'正在训练 {stock_}（{done + 1}/{total}）')
                print(f'\n训练进度：剩余 {total - done} 只 / 共 {total} 只；当前：{stock_}')

                try:
                    BuiltModel(stock_, self.months).model_all()
                    self._write_status(rec_id, ok=True)
                except Exception as ex:
                    failed += 1
                    print(f'ModelCreate Error: {ex}')
                    self._write_status(rec_id, ok=False, err=str(ex))

                done += 1
                if progress_cb:
                    msg = f'已训练 {done}/{total}'
                    if failed:
                        msg += f'，其中失败 {failed} 只'
                    progress_cb(done, total, msg)

        return True

    # 兼容旧调用名
    def train_remaining_models(self, progress_cb=None, should_stop=None):
        return self.all_stock(progress_cb=progress_cb, should_stop=should_stop)


if __name__ == '__main__':
    month_ = '2023-01'
    run = RMBuiltModel(month_)
    run.all_stock()
