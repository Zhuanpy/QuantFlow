#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Flask应用启动文件
"""
import warnings
# 抑制numpy的DLL警告
warnings.filterwarnings('ignore', message='loaded more than 1 DLL from .libs')

from App import create_app

# 创建应用实例
app = create_app()

if __name__ == '__main__':
    # 运行应用
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True
    )

