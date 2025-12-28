# from ..my_code.Signals.StatisticsMacd import SignalMethod
# from ..models.StockData1M import load_1m_stock_data_from_sql_efficient as
#
# """
# 1分钟数据
# 15分钟原始数据
# 15分钟模型数据
# 日K数据
# 模型训练数据
# 模型参数
#
# """
#
# # 模型训练 15M 数据
# stock_code = ""
# month = ""
#
# data_15m = " "  # 从mysql 中读取 15M 原始数据
# data_1m = " "  # 从mysql 中读取 1M 原始数据
# data_daily = ""  # 从mysql 中读取 daily  原始数据
#
# data_15m = SignalMethod.signal_by_MACD_3ema(data_15m, data_1m).set_index('date', drop=True)  # 统计计算信号
#
#
# data_daily["DailyVolEma"] = data_daily['volume'].rolling(90, min_periods=1).mean()  # 计算日线成交量的EMA
#
# daily_volume_max = round(data_daily["DailyVolEma"].max(), 2) # 计算日线成交量的最大值
#
# # 15M 数据 原始数据 , 保存到 sql 中
# # 15M 数据 规范数据， 保存到 csv
#
# # 读取旧参数
# try:
#     file_name = f"{stock_code}.json"
#
#     file_path = find_file_in_paths(month, 'json', file_name)
#
#     parser_data = ReadSaveFile.read_json_by_path(file_path)
#     pre_daily_volume_max = parser_data[stock_code]["DailyVolEma"]
#
# except:
#
#     pre_daily_volume_max = daily_volume_max
#
#
# daily_volume_max = max(daily_volume_max, pre_daily_volume_max) # 使用 max 函数一行完成最大值的更新
#
# # 简化日线数据处理
# data_daily["DailyVolEmaParser"] = daily_volume_max / data_daily["DailyVolEma"]
# data_daily = data_daily[['date', "DailyVolEmaParser"]].set_index('date', drop=True)
#
# data_15m = data_15m.join([data_daily]).reset_index()
#
# data_15m["DailyVolEmaParser"] = data_15m["DailyVolEmaParser"].fillna(method='ffill')
#
# # 排除最后 signal Times , 可能此周期并未走完整
# last_signal_times = data_15m.iloc[-1]["SignalTimes"]
#
# data_15m = data_15m[data_15m["SignalTimes"] != last_signal_times]
