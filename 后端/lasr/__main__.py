"""Windows spawn安全入口：child导入模块不会递归启动Uvicorn。"""
from multiprocessing import freeze_support

from .cli import main

if __name__ == "__main__":
    freeze_support()
    main()
