import os
import sys

path = os.path.dirname(os.path.abspath(__file__))

if path not in sys.path:
    sys.path.append(path)


"""
思路说明：

3个模块： 1. 训练模块 2. 测试模块 3.监测运行模块


"""