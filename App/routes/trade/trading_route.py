
# # -*- coding: utf-8 -*-
# import tkinter as tk
# from tkinter import *
# import tkinter.messagebox
# from App.my_code.Evaluation.StockPool import UpdateFakeStock, UpdateRealStock, ScoreStockPool, open_data_file
# from App.my_code.downloads.DlStockData import DataDailyRenew as ddr
# from App.my_code.RunMonitor import monitor
# from App.my_code.Evaluation.PlotStock import PlotsStock
# from App.code.Evaluation.PlotPoolCount import plot_pool_count
# from App.code.RnnModel.CheckModel import RMHistoryCheck
# from App.code.RnnModel.MonitorModel import RMMonitor
# from App.code.AutoTrade.AutoTrading import TradePoolFaker, TradePoolReal
# from App.code.downloads.DlFundsAwkward import DownloadFundsAwkward, AnalysisFundsAwkward
# from App.code.Evaluation.CountPool import PoolCount
#
#
# class UiFun:
#
#     @classmethod
#     def update_fake_stock_position(cls):
#         fake_ = UpdateFakeStock()
#         title, message = fake_.update_position()
#         tkinter.messagebox.showinfo(title, message)  # 添加弹窗
#
#     @classmethod
#     def update_fake_stock_history(cls):
#         fake_ = UpdateFakeStock()
#         title, message = fake_.update_history_trade()
#         tkinter.messagebox.showinfo(title, message)  # 添加弹窗
#
#     @classmethod
#     def update_fake_current_trade(cls):
#         fake_ = UpdateFakeStock()
#         title, message = fake_.update_current_trade()
#         tkinter.messagebox.showinfo(title, message)  # 添加弹窗
#
#     @classmethod
#     def update_real_stock_position(cls):
#         real_ = UpdateRealStock()
#         title, message = real_.update_position()
#         tkinter.messagebox.showinfo(title, message)  # 添加弹窗
#
#     @classmethod
#     def update_real_stock_history(cls):
#         real_ = UpdateRealStock()
#         title, message = real_.update_history()
#         tkinter.messagebox.showinfo(title, message)  # 添加弹窗
#
#
# class DownloadData:
#
#     @classmethod
#     def download_funds_awake(cls):
#         def get_awkward():
#             date_ = entry_.get()
#             dl = DownloadFundsAwkward(date_)
#             dl.multi_processing()
#
#         def get_analysis():
#             date_ = entry_.get()
#             al = AnalysisFundsAwkward(date_)
#             al.normalization_select_date()
#
#         window_ = Tk()
#         window_.title('基金重仓')
#         window_.geometry('300x100')
#
#         Label(window_, text='输入日期', background='white').place(x=10, y=10)
#
#         var2 = tk.StringVar()
#         var2.set('如: 2022-04-16')
#         entry_ = tk.Entry(window_, width=20, textvariable=var2)
#         entry_.place(x=80, y=10)
#
#         Button(window_, width=10, text='下载基金重仓', command=get_awkward).place(x=50, y=50)
#         Button(window_, width=10, text='分析基金重仓', command=get_analysis).place(x=150, y=50)
#
#
# class ModelPage:
#
#     def __init__(self):
#         self.root = Tk()
#         self.root.title('RnnModel')
#         self.root.geometry('300x200')
#
#     def create_data_fun(self):
#         pass
#
#     def train_model_fun(self):
#         pass
#
#     def test_model_fun(self):
#         pass
#
#
# class MonitorPage:
#
#     def __init__(self):
#         self.root = Tk()
#         self.root.title('日常监测')
#         self.root.geometry('300x100')
#
#         self.monitor_full_position()
#         self.monitor_half_position()
#
#     def monitor_full_position(self):
#         def monitor_cash1():
#             monitor()
#
#         Button(self.root, text='监测持仓', command=monitor_cash1).place(x=80, y=20)
#
#     def monitor_half_position(self):
#         def monitor_cash2():
#             rm = RMMonitor('2022-02')
#             rm.monitor_multiple_process()
#
#         Button(self.root, text='查看趋势', command=monitor_cash2).place(x=160, y=20)
#
#
# class MenuBar:
#
#     def __init__(self):
#         self.root = None
#         self.menubar = None
#
#     def menu_home(self):
#         menu_ = tk.Menu(self.menubar, tearoff=0)
#         menu_.add_command(label='首页模块')
#         menu_.add_command(label='Exit', command=self.root.quit)  # 用tkinter里面自带的quit()函数
#         return menu_
#
#     def menu_model(self):
#         menu_ = tk.Menu(self.menubar, tearoff=0)
#
#         def model_home_page():
#             ModelPage()
#
#         menu_.add_command(label='模型模块', command=model_home_page)
#         menu_.add_separator()  # 添加一条分隔线
#         menu_.add_command(label='ModelData')
#         menu_.add_command(label='CreateModel')
#         menu_.add_command(label='RunModel')
#         menu_.add_separator()  # 添加一条分隔线
#         menu_.add_command(label='Exit', command=self.root.quit)  # 用tkinter里面自带的quit()函数
#
#         return menu_
#
#     def menu_download(self):
#         menu_ = tk.Menu(self.menubar, tearoff=0)
#         menu_.add_command(label='下载模块', command=self.root.destroy)
#         menu_.add_separator()  # 添加一条分隔线
#         menu_.add_command(label='1M数据更新')
#         menu_.add_command(label='北向资金更新')
#         menu_.add_command(label='基金数据更新')
#         return menu_
#
#     def menu_show_data(self):
#         menu_ = tk.Menu(self.menubar, tearoff=0)
#         menu_.add_command(label='绘制模板')
#         menu_.add_separator()  # 添加一条分隔线
#         menu_.add_command(label='绘制趋势')
#         return menu_
#
#     def menu_pool(self):
#         fake_ = UpdateFakeStock()
#         real_ = UpdateRealStock()
#
#         menu_ = tk.Menu(self.menubar, tearoff=0)
#
#         menu_.add_command(label='股票池模块')
#
#         menu_.add_separator()  # 添加一条分隔线
#         menu_.add_command(label='更新真实持仓', command=real_.update_position)
#         menu_.add_command(label='更新真实交易记录', command=real_.update_history)
#
#         menu_.add_separator()  # 添加一条分隔线
#         menu_.add_command(label='更新模拟持仓', command=fake_.update_position)
#         menu_.add_command(label='更新模拟交易记录', command=fake_.update_history_trade)
#
#         stp = ScoreStockPool()
#         menu_.add_separator()  # 添加一条分隔线
#         menu_.add_command(label='更新板块数据', command=stp.analysis_Industry)
#         return menu_
#
#
# class Application(MenuBar):
#
#     def __init__(self, root_width=520, root_high=600):
#
#         MenuBar.__init__(self)
#         self.root_width = root_width
#         self.root_high = root_high
#
#         self.root = Tk()
#         self.root.geometry(f'{self.root_width}x{self.root_high}')
#
#         self.root.title('MyStock')
#         self.textMonitor = None
#
#         self.zone_menu()  # 菜单栏
#
#         self.zone_top()
#         self.trade_area()
#         self.plot_area()
#
#         self.root.mainloop()  # 循环
#
#     def zone_menu(self):
#
#         self.menubar = tk.Menu(self.root)
#
#         menuHome = self.menu_home()
#         self.menubar.add_cascade(label='主页', menu=menuHome)
#
#         menuModel = self.menu_model()
#         self.menubar.add_cascade(label='股票模型', menu=menuModel)
#
#         downMenu = self.menu_download()
#         self.menubar.add_cascade(label='股票数据', menu=downMenu)
#
#         showMenu = self.menu_show_data()
#         self.menubar.add_cascade(label='数据展示', menu=showMenu)
#
#         poolMenu = self.menu_pool()
#         self.menubar.add_cascade(label='股票池', menu=poolMenu)
#
#         self.root.config(menu=self.menubar)
#
#     def zone_top(self):
#
#         def monitor_fun():
#             MonitorPage()
#
#         def check_stock_fun():
#             check = RMHistoryCheck()  # 查看当天触发买卖的股票；
#             check.loop_by_date()
#
#         def board_trend_fun():
#             trend = ScoreStockPool()
#             trend.analysis_Industry()
#
#         y1, y2, y3, y4 = 10, 60, 110, 160
#         w1, w2, h1 = 12, 10, 1
#
#         fr1 = Frame(bd=10, width=self.root_width, height=self.root_high / 3, bg='blue')  #
#         fr1.pack()
#
#         # line 1
#         Label(fr1, text='日常运行', bg='white', width=w1, height=h1).place(x=10, y=y1)  # sticky
#
#         # line 2
#         Button(fr1, text='一键运行', command=monitor_fun, width=w2, height=h1).place(x=10, y=y2)
#         Button(fr1, text='基金重仓数据', width=w2, height=h1, command=DownloadData.download_funds_awake).place(x=110,
#                                                                                                                y=y2)
#
#         # line 3
#         Button(fr1, text='股票监测', command=monitor_fun, width=w2, height=h1).place(x=10, y=y3)
#         Button(fr1, text='更新1m', command=ddr.download_1m_data, width=w2, height=h1).place(x=110, y=y3)
#         Button(fr1, text='更新北向资金', command=ddr.renew_NorthFunds, width=w2, height=h1).place(x=210, y=y3)
#         Button(fr1, text='盘后股票预估', command=check_stock_fun, width=w2, height=h1).place(x=310, y=y3)
#         Button(fr1, text='盘后板块趋势', command=board_trend_fun, width=w2, height=h1).place(x=410, y=y3)
#
#     def trade_area(self):  # 策略运行
#
#         # fake_trade = TradePoolFaker()
#         real_trade = TradePoolReal()
#
#         y1, yFake, y3, yReal = 10, 40, 100, 130
#         w1, w2, h1 = 10, 12, 1
#
#         fr = Frame(bd=5, width=self.root_width, height=self.root_high / 3, bg='yellow')
#         fr.pack()
#
#         # line 1
#         Label(fr, text=' 模拟交易 ', bg='white', width=w2, height=h1).place(x=10, y=y1)  # sticky
#         Button(fr, text='数据文件夹', width=w1, command=open_data_file).place(x=410, y=y1 - 2)
#
#         Button(fr, text='模拟买入', width=w1, height=h1, command=TradePoolFaker.buy_pool).place(x=10, y=yFake)
#         Button(fr, text='模拟卖出', width=w1, height=h1, command=TradePoolFaker.sell_pool).place(x=110, y=yFake)
#         Button(fr, text='更新当天交易', width=w1, height=h1, command=UiFun.update_fake_current_trade).place(x=210,
#                                                                                                             y=yFake)
#         Button(fr, text='更新持仓记录', width=w1, height=h1, command=UiFun.update_fake_stock_position).place(x=310,
#                                                                                                              y=yFake)
#         Button(fr, text='更新交易记录', width=w1, height=h1, command=UiFun.update_fake_stock_history).place(x=410,
#                                                                                                             y=yFake)
#
#         # line 3
#         Label(fr, text=' 实盘交易 ', bg='white', width=w2, height=h1).place(x=10, y=y3)
#
#         # line 4
#         Button(fr, text='实盘买入', width=w1, height=h1, command=real_trade.buy_pool).place(x=10, y=yReal)
#         Button(fr, text='实盘卖出', width=w1, height=h1, command=real_trade.sell_pool).place(x=110, y=yReal)
#         Button(fr, text='更新当天交易', width=w1, height=h1).place(x=210, y=yReal)
#         Button(fr, text='更新持仓记录', width=w1, height=h1, command=UiFun.update_real_stock_position).place(x=310,
#                                                                                                              y=yReal)
#         Button(fr, text='更新交易记录', width=w1, height=h1, command=UiFun.update_real_stock_history).place(x=410,
#                                                                                                             y=yReal)
#
#     def plot_area(self, bg='red'):  # 模型处理
#
#         def plot1_command():
#
#             import pandas
#
#             try:
#                 stock = e1.get()
#                 pp = PlotsStock(stock)
#                 pp.plotting()
#
#             except pandas.io.sql.DatabaseError:
#                 tkinter.messagebox.showerror('报错', '请输入正确股票信息')
#
#         def plot2_command():
#             plot_pool_count()
#
#         fr = Frame(bd=5, width=self.root_width, height=self.root_high / 3, bg=bg)  # pack()
#         fr.pack()
#
#         Label(fr, text=' 数据绘图 ', bg='white').place(x=10, y=10)  # sticky
#
#         # 输入界面
#         y1, y2 = 50, 100
#         Label(fr, text=' 趋势绘图 ', bg='white').place(x=10, y=y1)
#
#         var1 = tk.StringVar()
#         var1.set('输入股票')
#
#         e1 = tk.Entry(fr, width=15, textvariable=var1)
#         e1.place(x=80, y=y1)
#
#         Button(fr, text='确认', width=3, command=plot1_command).place(x=200, y=y1 - 4)
#
#         Button(fr, text='涨跌数据', width=10, command=plot2_command).place(x=10, y=y2)
#         Button(fr, text='更新涨跌', width=10, command=PoolCount.count_trend).place(x=110, y=y2)
