import sys, os

path = os.path.dirname(os.path.abspath(__file__))

if path not in sys.path:
    sys.path.append(path)
