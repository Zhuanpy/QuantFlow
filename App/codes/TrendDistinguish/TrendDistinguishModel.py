# -*- coding: utf-8 -*-
import os
from keras import Sequential
from keras.utils import to_categorical
from keras.layers import Dense
from keras.layers import Flatten
from keras.layers import Conv2D
from keras.layers import AveragePooling2D

from keras.optimizers import SGD
import numpy as np
from keras import backend as k
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


class BuiltModel:

    def __init__(self):
        self.model_path = os.path.join('code_data', 'output', 'MacdTrend' )

    def load_data(self):

        # 导入数据 train code_data , test code_data
        X = np.load(f'{self.model_path}/X.npy')
        Y = np.load(f'{self.model_path}/Y.npy')

        index = np.arange(X.shape[0])
        np.random.shuffle(index)

        X = X[index, :, :, :]
        Y = Y[index]

        # 数据拆分
        lens = int(X.shape[0] * 0.8)

        train_x = X[:lens] / 255.0
        train_y = Y[:lens]

        test_x = X[lens:] / 255.0
        test_y = Y[lens:]

        train_y = to_categorical(train_y)
        test_y = to_categorical(test_y)

        return (train_x, train_y), (test_x, test_y)

    def train_model(self):

        k.clear_session()  # 清除缓存

        # 导入数据
        (train_x, train_y), (test_x, test_y) = self.load_data()

        # 搭建模型
        model = Sequential()
        model.add(Conv2D(filters=6, kernel_size=(5, 5), strides=(1, 1),
                         input_shape=(200, 150, 3), padding='valid', activation='relu'))
        model.add(AveragePooling2D(pool_size=(2, 2)))
        model.add(Conv2D(filters=16, kernel_size=(5, 5), strides=(1, 1), padding='valid', activation='relu'))
        model.add(AveragePooling2D(pool_size=(2, 2)))
        model.add(Flatten())
        model.add(Dense(units=120, activation='relu'))
        model.add(Dense(units=84, activation='relu'))
        model.add(Dense(units=4, activation='softmax'))

        try:
            weight_path = f'{self.model_path}/model.h5'
            model.load_weights(filepath=weight_path)
            epochs = 30

        except OSError:
            epochs = 100

        model.compile(loss='categorical_crossentropy', optimizer=SGD(lr=0.01), metrics=['accuracy'])  # 编译
        model.fit(train_x, train_y, epochs=epochs, batch_size=40)  # 训练
        loss, accuracy = model.evaluate(test_x, test_y, batch_size=40)  # 评估

        print(f'loss: {loss}, accuracy: {accuracy};')

        # 保存参数
        model.save_weights(f'{self.model_path}/weight.h5')

        # 保存模型
        model.save(f'{self.model_path}/model.h5')


if __name__ == '__main__':
    m = BuiltModel()
    m.train_model()
