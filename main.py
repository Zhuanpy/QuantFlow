#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Flask应用启动文件
"""
import os
import warnings
# 抑制numpy的DLL警告
warnings.filterwarnings('ignore', message='loaded more than 1 DLL from .libs')

from App import create_app
from App.config import Config

# 创建应用实例
app = create_app()

if __name__ == '__main__':
    # 自动重载器默认关闭：开着会把整个 App（含 pandas/pytdx 等原生库）在
    # 「父(监视)+子(服务)」两个进程各导入一遍，触发 PartitionAlloc 二次初始化
    # 直接崩溃（[FATAL] partition_address_space.cc: IsConfigurablePoolInitialized）；
    # 且本 App 启动时有后台线程，也不适合重载器。需要热重载再显式设
    # FLASK_USE_RELOADER=true（自担崩溃风险）。debug 仍由 DEBUG 控制（错误页/调试器）。
    use_reloader = os.getenv('FLASK_USE_RELOADER', 'false').lower() in ('1', 'true', 'yes')
    # host/port/debug 由环境变量控制，默认仅本机、关闭 debug
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        use_reloader=use_reloader,
    )




