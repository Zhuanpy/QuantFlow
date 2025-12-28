import pandas as pd
from App.codes.TrendDistinguish.TrendDistinguishRunModel import TrendDistinguishModel
from App.codes.MySql.sql_utils import Stocks
from App.codes.MySql.LoadMysql import LoadBasicInform
from App.codes.MySql.DataBaseStockPool import TableStockPoolCount, TableStockBoard
import multiprocessing
from App.codes.Evaluation.CountPool import count_board_by_date


def distinguish_board(code_, date_, id_=None, freq='120m'):

    """
    辨别 board trend;
    """

    if not date_:
        date_ = pd.Timestamp('today').date()

    else:
        date_ = pd.to_datetime(date_).date()

    if not id_:
        name, code_, id_ = Stocks(code_)

    dis = TrendDistinguishModel()
    label, value_ = dis.distinguish_1m(stock_code=code_, freq=freq, date_=date_)

    # 更新数据
    sql = f''' Trends= '{value_}', RecordDate='{date_}' where id='{id_}';'''
    params = (value_, date_, id_)
    TableStockBoard.set_table_to_board(sql, params)


def board_evaluate(day_, _num, num_, data):
    """
    all boards code_data evaluate ,  loop;
    """

    count = 0

    for index in range(_num, num_):

        stock_ = data.loc[index, 'code']
        id_ = data.loc[index, 'id']

        print(f'当前进度，剩余{num_ - _num - count}；')

        try:
            distinguish_board(code_=stock_, id_=id_, date_=day_)

        except Exception as ex:
            print(f'Error: {ex}')

        count += 1


def multiprocessing_count_board(date_):
    """
    multi processing function ;
    """

    board_data = LoadBasicInform.load_minute()

    board_data = board_data[board_data['Classification'] == '行业板块']

    # print(board_data)
    # exit()

    shape_ = board_data.shape[0]

    print(f'处理板块日期: {date_}, 处理个数：{shape_}')

    l1 = shape_ // 3
    l2 = shape_ // 3 * 2

    p1 = multiprocessing.Process(target=board_evaluate, args=(date_, 0, l1, board_data))
    p2 = multiprocessing.Process(target=board_evaluate, args=(date_, l1, l2, board_data))
    p3 = multiprocessing.Process(target=board_evaluate, args=(date_, l2, shape_, board_data))

    p1.start()
    p2.start()
    p3.start()

    p1.join()
    p2.join()
    p3.join()


if __name__ == '__main__':

    count_data = TableStockPoolCount.load_poolCount()
    count_data = count_data.tail(2)
    print(count_data)
    # exit()
    for my_date_ in count_data.date:
        my_date_ = my_date_.strftime('%Y-%m-%d')

        # 执行了趋势统计
        multiprocessing_count_board(my_date_)

        # 更新统计表格表格
        count_board_by_date(my_date_)
