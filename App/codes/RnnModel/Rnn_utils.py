import pandas as pd
from ..MySql.LoadMysql import LoadRnnModel
from ..MySql.DataBaseStockData1m import StockData1m
from App.codes.utils.Normal import ResampleData
from ..RnnDataFile.stock_path import file_root
import os


def reset_record_time(_date):
    time_ = (pd.to_datetime(_date) + pd.Timedelta(days=-150)).date()
    ids = LoadRnnModel.load_run_record()
    ids = tuple(ids['id'])

    sql = f'''SignalStartTime = %s, Time15m = %s where id in %s;'''

    params = (time_, time_, ids)

    LoadRnnModel.set_table_run_record(sql, params)


def reset_id_time(id_, _date):
    time_ = (pd.to_datetime(_date) + pd.Timedelta(days=-150)).date()

    sql = f'''SignalStartTime = %s, Time15m = %s where id = %s;'''
    params = (time_, time_, id_)
    LoadRnnModel.set_table_run_record(sql, params)


def _load_calendar_1m(year, code_=None) -> pd.DataFrame:
    """加载一只"日历股"的 1m 数据，用于推算交易日。

    依次尝试已知大概率有数据的代码（蓝筹/指数代表）。
    原来默认 'bk0424' 会被 6 位数字校验器拒绝，故改用几只蓝筹股兜底。
    """
    candidates = [c for c in [code_, '000001', '600000', '600519', '000002'] if c]
    for c in candidates:
        d = StockData1m.load_1m(c, year=str(year))
        if d is not None and not d.empty:
            return d
    return pd.DataFrame()


def latest_trading_day(code_=None):
    """返回当前有数据的最近一个交易日（<= 今天），全无数据时返回 None。

    盘前 / 节假日「今天」还没有 1m K 线时，用它回退到最近一个真正有数据的
    交易日，避免把检查日期定在一个无数据的日子上（否则 date_range 返回空）。
    今年若整年无数据，再回退看上一年。
    """
    today = pd.Timestamp.now().date()
    for year in (today.year, today.year - 1):
        data = _load_calendar_1m(year, code_)
        if data is None or data.empty or 'date' not in data.columns:
            continue
        data = ResampleData.resample_1m_data(data=data, freq='day').drop_duplicates(subset=['date'])
        days = [d for d in data['date'] if d <= today]
        if days:
            return max(days)
    return None


def date_range(_date, date_, code_=None) -> list:
    """返回 [_date, date_] 区间内的交易日列表。

    实现方式：找一只有数据的"日历股"，把它的 1m 数据按日聚合，取出现的日期。
    """
    if _date == date_:
        _date = pd.to_datetime(_date).date()
        date_ = pd.to_datetime(date_) + pd.Timedelta(days=1)  # .date()
        date_ = date_.date()
    else:
        _date = pd.to_datetime(_date).date()
        date_ = pd.to_datetime(date_).date()

    data = _load_calendar_1m(_date.year, code_)
    if data is None or data.empty or 'date' not in data.columns:
        return []

    data = ResampleData.resample_1m_data(data=data, freq='day').drop_duplicates(subset=['date'])
    data = data[(data['date'] >= _date) & (data['date'] <= date_)]
    return list(data['date'])


def rnn_data_path(month: str):
    """
    获取RNN数据路径
    :param month: 月份
    :return: 数据路径
    """
    # 实际数据落在项目根 data/RnnData/，与 StockDataPath 保持一致
    return os.path.join(file_root(), 'data', 'RnnData', month)


def rnn_data_pre_month_list(month: str, class_file: str) -> tuple:
    """
    获取以前月份列表
    :param month: 月份
    :class_file: 文件夹类型 ， 如 weigh , train_data ;
    :return: 上个月份列表

    获取RNN 文件夹名， 大到小并且排序；
    能用到的地方， 训练模型读取历史数据时，例如权重数据，例如训练数据；

    """

    # 实际数据落在 data/RnnData/，不是 code_data/RnnData/
    root_path = os.path.join(file_root(), 'data', 'RnnData')

    # 获取RnnData文件夹下全部月份文件夹名称
    folder_names = sorted(
        [folder for folder in os.listdir(root_path) if os.path.isdir(os.path.join(root_path, folder))], reverse=True)

    # 删除CommonFile文件夹（如果存在）
    if 'CommonFile' in folder_names:
        folder_names.remove('CommonFile')

    # 移除当前月份及以后月份的文件夹
    folder_names = folder_names[folder_names.index(month) + 1:]

    # 构建文件夹路径列表
    folder_path = [os.path.join(root_path, M, class_file) for M in folder_names]

    return folder_path, folder_names


def find_file_in_paths(month: str, classification: str, file_name: str):

    """ 找出输入月份的上一个历史数据文件夹名称及月份名称.
      Parameters:
          month (str): 输入月份；
          classification (str): 文件类型，文件夹名称；
          file_name (str): 文件名称；


      Returns:
          folder_path: 文件所在文件夹路径.
          M ： 文件所在文件夹月份名称.
    """

    folder_paths, month_list = rnn_data_pre_month_list(month, classification)

    for folder_path, M in zip(folder_paths, month_list):

        folder_path = os.path.join(folder_path, file_name)

        if os.path.exists(folder_path) and os.path.isfile(folder_path):
            return folder_path, M
            # 如果找到第一个存在的路径，立即返回并结束循环

    return False, False


if __name__ == '__main__':
    m = '2023-12'
    c = 'weight'
    f = 'weight_bar_volume_000651.h5'
    file_path, pre_month = find_file_in_paths(m, c, f)
    print(file_path)
    print(pre_month)
