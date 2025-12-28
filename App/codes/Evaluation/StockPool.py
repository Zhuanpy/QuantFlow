import pandas as pd
from App.codes.MySql.LoadMysql import LoadNortFunds, LoadFundsAwkward
from App.codes.MySql.DataBaseStockData15m import StockData15m
from App.codes.MySql.DataBaseStockPool import TableStockPool, TableStockBoard, TableTradeRecord
from App.codes.Signals.BollingerSignal import Bollinger
from App.codes.utils.Normal import MathematicalFormula as My_mfl
from App.codes.utils.Normal import StockCode
from App.codes.MySql.sql_utils import Stocks
from App.codes.parsers.BollingerParser import *
from matplotlib import pyplot as plt
from App.codes.RnnModel.RnnRunModel import PredictionCommon
from App.codes.TrendDistinguish.TrendDistinguishRunModel import TrendDistinguishModel

from sqlalchemy.exc import IntegrityError

from App.codes.RnnDataFile.stock_path import file_root


def ScoreCycleAmplitude():
    errors = []
    pool = TableStockPool.load_StockPool()

    for i in pool.index:
        stock_code = pool.loc[i, 'code']
        id_ = pool.loc[i, 'id']

        try:
            cols = ['SignalChoice', 'CycleAmplitudeMax', 'CycleLengthMax']
            data15 = StockData15m.load_15m(stock_code)
            data15 = data15[cols]
            data15 = data15.dropna(subset=['SignalChoice'])

            data15 = data15[data15['SignalChoice'] == '上涨'].tail(60).reset_index(drop=True)

            # 去除极值
            data15 = My_mfl.filter_median(data=data15, column='CycleAmplitudeMax')
            data15 = My_mfl.filter_median(data=data15, column='CycleLengthMax')

            AmpMean = data15['CycleAmplitudeMax'].mean()
            LengthMean = data15['CycleLengthMax'].mean()

            LA = round(AmpMean / LengthMean * 100, 3)

            sql = f'''CycleAmplitude = '%s' where id = %s; '''
            params = (LA, id_)
            TableStockPool.set_table_to_pool(sql, params)

            print(f'{stock_code}: {LA}')

        except Exception as ex:
            v = (stock_code, ex)
            errors.append(v)

    if len(errors):
        print(f'Score Cycle Amplitude Error list: {errors}')


def open_data_file():
    pass


class ScoreStockPool(TrendDistinguishModel):

    def __init__(self):
        TrendDistinguishModel.__init__(self)
        self.codeB, self.nameB = None, None
        self.dataB, self.scoreB = None, None
        self.dataF, self.scoreF = None, None

    def board_trends(self, code: str):

        try:
            # 获取趋势 df数据  和 得分
            data, score = self.distinguish_1m(stock_code=code, freq='120m', returnFreq=True, date_=None)

            data['close'] = My_mfl.data2normalization(data['close'])

            data = Bollinger(data=data)
            data['BollTrend'] = data[BollMid] - data[BollMid].shift(1)

            data['BollScore'] = 0.5

            # boll trends
            data.loc[data['BollTrend'] > 0, 'BollScore'] = 0.9
            data.loc[data['BollTrend'] <= 0, 'BollScore'] = 0.1

            # 得分记录
            boll_max = data[BollUp].max()
            boll_min = data[BollDn].min()

            data['pltbs'] = 0.0
            data.loc[data['BollScore'] == 0.9, 'pltbs'] = float(boll_max)
            data.loc[data['BollScore'] == 0.1, 'pltbs'] = float(boll_min)

            data = data.tail(60)

            s = score[1]

        except IndexError:
            data = pd.DataFrame(data=None)
            s = 0

        return data, s

    def funds_trends(self, code: str):

        try:
            cols = ['SECURITY_CODE', 'TRADE_DATE', 'ADD_MARKET_CAP']
            data = LoadNortFunds.load_funds2board()
            data = data[cols]
            data = data[data['SECURITY_CODE'] == code].reset_index(drop=True)

            if data.shape[0] > 12:
                data['ADD_MARKET_CAP'] = My_mfl.data2normalization(data['ADD_MARKET_CAP'])
                data['close'] = data['ADD_MARKET_CAP'].rolling(12, min_periods=1).mean()

                data = Bollinger(data)
                data['score'] = data['close'] - data[BollMid]
                data = data.tail(60)

                s = round(data.iloc[-1]['score'], 3)  # 更新评估得分

            else:
                data = pd.DataFrame()
                s = 0.0

        except IndexError:
            data = pd.DataFrame()
            s = 0.0

        return data, s

    def plotB(self, ax):  # 板块趋势， plot 1:

        date_ = self.dataB.iloc[-1]['date']
        ax.plot(self.dataB['date'], self.dataB['close'])
        ax.plot(self.dataB['date'], self.dataB[BollUp])
        ax.plot(self.dataB['date'], self.dataB[BollMid])
        ax.plot(self.dataB['date'], self.dataB[BollDn])
        ax.scatter(self.dataB['date'], self.dataB['pltbs'])
        ax.set_xlabel('Date', loc='right')
        ax.set_title(f'120m 趋势/ Date:{date_.date()}', loc='left')

    def plotF(self, ax):  # 资金趋势 plot 2
        date_ = self.dataF.iloc[-1]['TRADE_DATE']
        ax.plot(self.dataF['TRADE_DATE'], self.dataF['close'])
        ax.plot(self.dataF['TRADE_DATE'], self.dataF[BollMid])

        ax.set_xlabel('Date', loc='right')
        ax.set_title(f'北向资金/ Date:{date_}', loc='left')

        plt.subplots_adjust(wspace=0, hspace=0.5)  # 调整子图间距
        plt.suptitle(f'{self.nameB}({self.codeB})Analysis')
        # plt.show()

    def analysis_Industry(self):

        current = pd.Timestamp.now().date()
        pool = TableStockPool.load_StockPool()
        poolB = TableStockBoard.load_board()
        print(poolB)
        # exit()
        for index, row in poolB.iterrows():

            self.codeB = row['code']
            self.nameB = row['name']
            board_id = row['id']

            self.dataB, self.scoreB = self.board_trends(self.codeB)
            self.dataF, self.scoreF = self.funds_trends(self.codeB)

            # 更新股票池  板块数据
            board_date = self.dataB.iloc[-1]['date'].date()
            sql2 = f''' Trends = %s, RecordDate = %s where id = %s; '''

            params = (self.scoreB, board_date, board_id)
            TableStockBoard.set_table_to_board(sql2, params)

            # 更新数据
            industry_data = pool[pool['IndustryCode'] == self.codeB]

            if industry_data.empty:
                continue

            ids = tuple(industry_data['id'])

            if len(ids) == 1:

                where = f'''id = {ids[0]}'''

            else:
                where = f'''id in {ids}'''

            # 绘图
            fig, ax = plt.subplots(2, 1, figsize=(9, 7), dpi=120)

            if not self.dataB.empty and self.dataF.empty:
                sql1 = f''' BoardBoll= %s, BoardMoney= '%s, RecordDate = %s where %s; '''

                params = (self.scoreB, self.scoreF, current, where)
                # 更新股票池 个股对应板块数据
                TableStockPool.set_table_to_pool(sql1, params)

                self.plotB(ax[0])
                self.plotF(ax[1])

            if not self.dataB.empty and self.dataF.empty:
                # 更新股票池 个股对应板块数据
                sql1 = f''' BoardBoll= %s, BoardMoney= null, RecordDate = %s where %s; '''

                params = (self.scoreB, current, where)
                TableStockPool.set_table_to_pool(sql1, params)

                # 更新股票池  板块数据
                board_date = self.dataB.iloc[-1]['date'].date()

                sql2 = f''' Trends =  %s, RecordDate =  %s, where id =  %s; '''
                params = (self.scoreB, board_date, board_id)
                TableStockBoard.set_table_to_board(sql2, params)

                self.plotB(ax[0])

            if self.dataB.empty and not self.dataF.empty:
                sql = f''' BoardBoll= null, BoardMoney= %s, RecordDate = %s where %s; '''
                params = (self.scoreF, current, where)
                TableStockPool.set_table_to_pool(sql, params)

                self.plotF(ax[0])

            # 保存图片
            from App.codes.RnnDataFile.stock_path import AnalysisDataPath
            file_name = f'{self.codeB}_{self.nameB}_Analysis.jpg'
            path = AnalysisDataPath.analysis_industry_trend_jpg_path(file_name)

            plt.rcParams['font.sans-serif'] = ['SimHei']
            plt.rcParams['axes.unicode_minus'] = False
            plt.savefig(path)
            plt.close()

            print(f'{self.nameB}完成; 剩余{poolB.shape[0] - index - 1}个；')

        print(f'Analysis Stock Pool Board and Funds Complete;')

    def ScoreBoardHot(self):
        # TODO : here need calculate hot board score , 2022-01-06;
        pass

    def ScoreFundsAwkward(self):

        pool = TableStockPool.load_StockPool()
        awkward = LoadFundsAwkward.load_awkwardNormalization()
        max_ = awkward['trade_date'].max()
        bb = awkward[awkward['trade_date'] == max_]

        for index in pool.index:

            id_ = pool.loc[index, 'id']
            stock_name = pool.loc[index, 'name']

            if stock_name not in list(bb['stock_name']):
                continue

            ss = bb[bb['stock_name'] == stock_name].iloc[0]['TrendScore']

            sql = f'''FundsAwkward = %s where id = %s; '''

            params = (ss, id_)
            TableStockPool.set_table_to_pool(sql, params)

        print(f'Process Score Funds Awkward')


class UpdateTradeHistory:

    def __init__(self, months_='2022-02'):
        self.months = months_
        # self.db_pool, self.tb_pool, self.tb_trade = 'stockpool', 'stockpool', 'traderecord'
        self.name, self.code, self.id_ = None, None, None

        self.tradeDate = None
        self.tradeTime = None

        self.tradeDate = None  # 成交日期
        self.tradeTime = None  # 成交时间

        self.tradeAction = None  # 操作
        self.signalTimes_ = None  # 信号编号
        self.tradeNum = None  # 成交数量
        self.tradePrice = None  # 成交均价
        self.stopLoss_ = None  # 止损价
        self.tradeAmount = None  # 成交金额
        self.tradeAmount_ = None  # 股票余额
        self.tradeContract = None  # 合同编号

        self.tradeFee1 = None  # 手续费
        self.tradeFee2 = None  # 印花税
        self.tradeFee3 = None  # 其他杂费
        self.tradeFee4 = None  # 发生金额

        self.tradeMarket = None  # 交易市场
        self.tradeAccount = None  # 股东帐户
        self.tradeCancel = None  # 撤单数量
        self.tradeReal = None  # 真实操作
        self.tradeNo = None  # 成交编号

    def get_index_value(self, data, index):
        self.tradeNo = data.loc[index, '成交编号']
        self.tradeDate = data.loc[index, '成交日期']
        self.tradeTime = data.loc[index, '成交时间']
        self.name = data.loc[index, '证券名称']
        self.tradeAction = data.loc[index, '操作']
        self.tradeNum = data.loc[index, '成交数量']
        self.tradePrice = data.loc[index, '成交均价']

        self.tradeAmount = data.loc[index, '成交金额']
        self.tradeAmount_ = data.loc[index, '股票余额']

        self.tradeContract = data.loc[index, '合同编号']
        self.tradeFee1 = data.loc[index, '手续费']
        self.tradeFee2 = data.loc[index, '印花税']
        self.tradeFee3 = data.loc[index, '其他杂费']
        self.tradeFee4 = data.loc[index, '发生金额']
        self.tradeMarket = data.loc[index, '交易市场']
        self.tradeAccount = data.loc[index, '股东帐户']
        self.tradeCancel = data.loc[index, '撤单数量']
        self.tradeReal = data.loc[index, '真实操作']

    def update_record(self, method='模拟'):
        sql = f''' 成交日期 = '%s', 成交时间 = '%s', 证券代码 ='%s', 证券名称 = '%s',
        操作 = '%s', 信号编号 = '%s', 成交数量 = '%s', 成交均价 = '%s',
        止损价 = '%s', 成交金额 = '%s', 股票余额 = '%s', 合同编号 = '%s',
        手续费 = '%s', 印花税 = '%s', 其他杂费 = '%s', 发生金额 = '%s',
        交易市场 ='%s', 股东帐户 = '%s', 撤单数量 ='%s', 真实操作 ='%s',
        where 成交编号 = '%s'; '''

        params = (self.tradeDate, self.tradeTime, self.code, self.name,
                  self.tradeAction, self.signalTimes_, self.tradeNum, self.tradePrice,
                  self.stopLoss_, self.tradeAmount, self.tradeAmount_, self.tradeContract,
                  self.tradeFee1, self.tradeFee2, self.tradeFee3, self.tradeFee4,
                  self.tradeMarket, self.tradeAccount, self.tradeCancel, self.tradeReal,
                  self.tradeNo,)

        TableTradeRecord.set_table_trade_record(sql, params)
        print(f'{method}账户股票：{self.name} 更新成功;')

    def update_stock_pool(self):
        sql = f''' set StopLoss = %s where id = %s; '''

        params = (self.stopLoss_, self.id_)
        TableStockPool.set_table_to_pool(sql, params)


class UpdateData:

    @classmethod
    def data_history(cls, file_):
        root_ = file_root()
        data = pd.read_excel(f'{root_}/data/output/stock_pool/{file_}.xls', sheet_name='table')
        print(data)
        data.loc[:, ['信号编号', '止损价']] = None
        data = data[['成交编号', '信号编号', '成交日期', '成交时间', '证券代码', '证券名称',
                     '操作', '成交数量', '成交均价', '止损价', '成交金额', '股票余额',
                     '合同编号', '手续费', '印花税', '其他杂费', '发生金额',
                     '交易市场', '股东帐户', '撤单数量', '真实操作']]
        return data

    @classmethod
    def data_current_trade(cls, file_):
        root_ = file_root()
        data = pd.read_excel(f'{root_}/data/output/stock_pool/{file_}.xls', sheet_name='table')
        data['成交日期'] = pd.Timestamp.now().strftime('%Y%m%d')
        data['信号编号', '交易市场', '股东帐户'] = None
        data['股票余额'] = data['成交数量']
        data['止损价', '手续费', '印花税', '其他杂费', '发生金额', '真实操作', '撤单数量'] = 0
        data = data[['成交编号', '合同编号', '成交日期', '成交时间',
                     '操作', '信号编号', '证券代码', '证券名称',
                     '成交数量', '成交均价', '止损价', '成交金额', '股票余额',
                     '手续费', '印花税', '其他杂费', '发生金额', '交易市场',
                     '股东帐户', '撤单数量', '真实操作']]
        return data

    @classmethod
    def reset_position(cls):
        data = TableStockPool.load_StockPool()
        data = data[(data['Position'] == 1) &
                    (data['TradeMethod'] == 1)]

        if data.shape[0]:
            for i in data.index:
                id_ = data.loc[i, 'id']
                sql = f'''Position = 0, TradeMethod = 0, PositionNum = 0 where id = %s;'''
                params = (id_, )
                TableStockPool.set_table_to_pool(sql, params)

        print('重置模拟持仓成功;')


class UpdateFakeStock(UpdateTradeHistory):  # 更新模拟账户交易数据

    def __init__(self):
        UpdateTradeHistory.__init__(self)
        self.root = file_root()

    def update_position(self, ):

        file_ = 'position_fake'
        errors = []

        try:
            UpdateData.reset_position()  # 将模拟交易持仓数据先重置；
            data = pd.read_excel(f'{self.root}/data/output/stock_pool/{file_}.xls', sheet_name='table')
            data = data[data['股票余额'] > 0]

            for index in data.index:
                stock = data.loc[index, '证券代码']
                stock = StockCode.stand_code(stock)

                self.name, self.code, id_ = Stocks(stock)
                num_position = data.loc[index, '股票余额']

                import pymysql
                try:
                    sql = f''' Position = 1, TradeMethod = 1, PositionNum = %s where id = %s;'''
                    params = (num_position, id_)
                    TableStockPool.set_table_to_pool(sql, params)
                    print(f'{self.name}模拟持仓更新成功;')

                except pymysql.err.OperationalError:
                    errors.append(self.name)

            if len(errors):
                print(f'错误列表： {errors}')

            title = '更新成功;'
            message = '模拟持仓更新成功;'

        except Exception as ex:
            title = '更新失败;'
            message = f'模拟持仓更新失败:\n{ex};'

        return title, message

    def update_trade_record(self, ):

        data = pd.read_excel(f'{self.root}/data/output/stock_pool/history_fake.xls', sheet_name='table')

        for index in data.index:

            code = data.loc[index, '证券代码']

            self.name, self.code, self.id_ = Stocks(code)

            date_ = data.loc[index, '成交日期']
            time_ = data.loc[index, '成交时间']

            dateTime_ = pd.to_datetime(f'{date_} {time_}', format='%Y%m%d %H:%M:%S')

            date_ = pd.to_datetime(date_, format='%Y%m%d').date()

            predict = PredictionCommon(stock=self.code, month_parsers=self.months, monitor=False, check_date=date_)

            data15 = predict.calculate_check_data()
            data15 = data15[data15['date'] >= dateTime_]

            self.stopLoss_ = data15.iloc[0]['StopLoss']
            self.signalTimes_ = data15.iloc[0]['SignalTimes']
            signal = data15.iloc[0]['Signal']

            print(predict)
            # print(data15)

            print(self.name)
            print(dateTime_)
            print(self.stopLoss_)
            print(self.signalTimes_)
            print(signal)
            # exit()

            data.loc[index, '证券代码'] = self.code
            data.loc[index, '止损价'] = self.stopLoss_
            data.loc[index, '信号编号'] = self.signalTimes_

            self.update_stock_pool()  # 更新止损价
            import sqlalchemy

            try:
                new_ = data.loc[index:index, :]
                new_['股东帐户'] = '43539099'
                print(new_)
                TableTradeRecord.append_tradeRecord(data=new_)
                print(f'模拟账户股票：{self.name} 更新成功;')

            except sqlalchemy.exc.IntegrityError:
                self.get_index_value(data, index)  # 获取 中的各个值；
                self.update_record(method='模拟')  # 更新资料

    def update_history_trade(self, file_='history_fake'):

        try:
            data = UpdateData.data_history(file_)  # 整理数据

            self.update_trade_record()  # 更新数据

            title = '更新成功'
            message = '模拟账户交易历史记录更新成功'

        except Exception as ex:
            title = '更新失败'
            message = f'模拟账户交易历史记录更新失败：\n{ex}'

        print(message)
        return title, message

    def update_current_trade(self, file_='current_fake'):

        try:
            data = UpdateData.data_current_trade(file_)
            self.update_trade_record()

            title = '更新成功'
            message = '模拟账户当日记录更新成功'

        except Exception as ex:
            title = '更新失败'
            message = f'模拟账户当日记录更新失败：\n{ex}'

        print(message)
        return title, message


class UpdateRealStock(UpdateTradeHistory):  # 更新真实账户交易数据

    def __init__(self):
        UpdateTradeHistory.__init__(self)
        self.root = file_root()

    def update_position(self, file_='position_real'):

        try:
            pool = TableStockPool.load_StockPool()
            pool = pool[(pool['Position'] == 1) &
                        (pool['TradeMethod'] == 2)]

            if pool.shape[0]:
                for index in pool.index:
                    id_ = pool.loc[index, 'id']
                    sql = f''' Position = 0, TradeMethod = 0, PositionNum = 0 where id = %s;'''
                    params = (id_,)
                    TableStockPool.set_table_to_pool(sql, params)

            position = pd.read_excel(f'{self.root}/data/output/stock_pool/{file_}.xls', sheet_name='table')
            position = position.dropna(subset=['股票余额'])

            if position.shape[0]:

                for index in position.index:
                    stock_ = position.loc[index, '证券名称']
                    name, code, id_ = Stocks(stock_)
                    nums = position.loc[index, '股票余额']
                    sql = f''' Position = 1, TradeMethod = 2, PositionNum = %s where id = %s;'''
                    params = (nums, id_)
                    TableStockPool.set_table_to_pool(sql, params)

            title = '更新成功'
            message = '实盘持仓更新成功;'

        except Exception as ex:
            title = '更新失败'
            message = f'实盘持仓更新失败:\n{ex};'

        print(message)
        return title, message

    def update_history(self, file_='history_real'):

        try:
            data = pd.read_excel(f'{self.root}/data/output/stock_pool/{file_}.xls', sheet_name='table')

            data[['成交日期', '成交时间', '证券代码']] = data[['成交日期', '成交时间', '证券代码']].astype(str)

            data.loc[:, ['信号编号', '止损价', '交易市场']] = None
            data.loc[:, '真实操作'] = 1
            data.loc[:, ['股票余额', '撤单数量', '发生金额']] = 0

            data = data.rename(columns={'股东代码': '股东帐户', '佣金': '手续费',
                                        '成交价格': '成交均价', '买卖标志': '操作',
                                        '委托编号': '合同编号', '其他费': '其他杂费'})

            data = data[['成交编号', '信号编号', '成交日期', '成交时间', '证券代码', '证券名称',
                         '操作', '成交数量', '成交均价', '止损价', '成交金额', '股票余额',
                         '合同编号', '手续费', '印花税', '其他杂费', '发生金额',
                         '交易市场', '股东帐户', '撤单数量', '真实操作']]

            data['发生金额'] = data['成交金额'] + data['手续费'] + data['印花税'] + data['其他杂费']

            for index in data.index:
                code_ = data.loc[index, '证券代码']
                self.code = StockCode.stand_code(code_)

                date_ = data.loc[index, '成交日期']

                time_ = data.loc[index, '成交时间']

                if len(time_) == 5:
                    time_ = '0' + time_

                dateTime_ = pd.to_datetime(f'{date_} {time_}', format='%Y%m%d %H:%M:%S')

                date_ = pd.to_datetime(date_, format='%Y%m%d').date()

                predict = PredictionCommon(stock=code_, month_parsers=self.months, monitor=False, check_date=date_)
                data15 = predict.calculate_check_data()
                data15 = data15[data15['date'] >= dateTime_]

                self.stopLoss_ = data15.iloc[0]['StopLoss']
                self.signalTimes_ = data15.iloc[0]['SignalTimes']

                data.loc[index, '证券代码'] = self.code
                data.loc[index, '成交时间'] = f'{time_[:2]}:{time_[2:4]}:{time_[4:]}'

                data.loc[index, '止损价'] = self.stopLoss_
                data.loc[index, '信号编号'] = self.signalTimes_
                data.loc[index, '成交数量'] = abs(data.loc[index, '成交数量'])

                try:
                    new_ = data.loc[index:index, :]
                    TableTradeRecord.append_tradeRecord(new_)

                    print(f'实盘账户股票：{code_} 更新成功;')

                except IntegrityError:
                    self.get_index_value(data, index)  # 获取 中的各个值；
                    self.update_record(method='实盘')  # 更新资料

            title = '更新成功'
            message = '实盘账户交易历史记录更新成功'

        except Exception as ex:
            title = '更新失败'
            message = f'实盘账户交易历史记录更新失败：\n{ex}'

        print(message)
        return title, message


if __name__ == '__main__':
    # todo # UserWarning: Glyph 21271 (\N{CJK UNIFIED IDEOGRAPH-5317}) missing from current font.

    stock_score = ScoreStockPool()
    stock_score.analysis_Industry()
