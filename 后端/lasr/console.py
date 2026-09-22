"""同进程本机控制台是唯一配对管理入口；没有LAN管理路由，也不使用logger。

no-console仍一次显示首次口令供受控本地联调，但完全不创建stdin读取线程。
口令不持久化；终端及其捕获策略由操作者控制，不应重定向到普通日志文件。
"""
from __future__ import annotations

import asyncio
import sys
import threading

from .auth import AuthStore


class LocalConsole:
    def __init__(self, auth: AuthStore, *, output=None, input_stream=None):
        self.auth = auth
        self.output = output if output is not None else sys.stdout
        self.input = input_stream if input_stream is not None else sys.stdin
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def show_pair(self) -> None:
        self.output.write("LASR_PAIRING_CODE=" + self.auth.generate_pairing_code() + "\n")
        self.output.flush()

    def execute(self, command: str) -> None:
        if self.stopped.is_set():
            return
        if command.strip() == "pair":
            self.show_pair()
        elif command.strip() == "revoke-all":
            self.auth.revoke_all()
            self.output.write("所有本地授权已撤销。\n")
            self.output.flush()
        else:
            # 不回显未知输入，防止操作者误将口令输入命令行后进入诊断日志。
            self.output.write("可用本机命令：pair、revoke-all。\n")
            self.output.flush()

    def start(self, *, no_console: bool) -> None:
        self.show_pair()
        if no_console:
            return
        loop = asyncio.get_running_loop()

        def reader():
            while not self.stopped.is_set():
                line = self.input.readline(257)
                if not line:
                    return
                if len(line) > 256:
                    continue
                try:
                    loop.call_soon_threadsafe(self.execute, line)
                except RuntimeError:
                    return
        self.thread = threading.Thread(target=reader, daemon=True, name="lasr-local-console")
        self.thread.start()

    def close(self) -> None:
        self.stopped.set()
