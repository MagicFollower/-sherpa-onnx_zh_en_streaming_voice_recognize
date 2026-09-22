"""网络进程的有界IPC与spawn监督；不导入或持有原生recognizer。

队列容量固定，取消Event独立于音频通道。回收只在确认旧进程已死亡后增加
代数并重建全部IPC，旧输出即使迟到也无法匹配新轮。模型加载失败不无限重试。
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
from queue import Empty, Full
import time
from typing import Protocol

from .audio import AudioFrame
from .config import Settings
from .worker import WorkerEvent, worker_main


class WorkerPort(Protocol):
    ready: bool
    generation: int
    config_id: str

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def restart(self) -> None: ...
    def start(self, token: int) -> bool: ...
    def audio(self, token: int, frame: AudioFrame) -> bool: ...
    def stop(self, token: int, last_seq: int) -> bool: ...
    def cancel(self, token: int, last_seq: int) -> None: ...
    def poll(self) -> list[WorkerEvent]: ...


class ProcessWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.context = mp.get_context("spawn")
        self.generation = 0
        self.ready = False
        self.config_id = "zipformer-unavailable"
        self.process = None
        self.channels = []
        self.started = 0.0
        self.failed_reported = False
        self.load_failed = False
        self.restarts = 0
        self.closed = False

    async def open(self) -> None:
        self.generation += 1
        self.ready, self.failed_reported, self.load_failed = False, False, False
        self.audio_queue = self.context.Queue(maxsize=100)
        self.control_queue = self.context.Queue(maxsize=8)
        self.result_queue = self.context.Queue(maxsize=14)
        self.terminal_queue = self.context.Queue(maxsize=2)
        self.channels = [self.audio_queue, self.control_queue, self.result_queue, self.terminal_queue]
        self.cancel_event = self.context.Event()
        self.shutdown_event = self.context.Event()
        self.process = self.context.Process(
            target=worker_main, name="lasr-cpu-worker", daemon=True,
            args=(self.generation, str(self.settings.model_dir), self.settings.cpu_threads,
                  *self.channels, self.cancel_event, self.shutdown_event))
        self.process.start()
        self.started = time.monotonic()

    def _control(self, kind: str, token: int, value=None) -> bool:
        try:
            self.control_queue.put_nowait((kind, token, value))
            return True
        except (Full, ValueError, OSError):
            return False

    def start(self, token: int) -> bool:
        if not self.ready:
            return False
        self.cancel_event.clear()
        return self._control("start", token)

    def audio(self, token: int, frame: AudioFrame) -> bool:
        try:
            self.audio_queue.put_nowait((token, frame.seq, frame.pcm))
            return True
        except (Full, ValueError, OSError):
            return False

    def stop(self, token: int, last_seq: int) -> bool:
        return self._control("stop", token, last_seq)

    def cancel(self, token: int, last_seq: int) -> None:
        self.cancel_event.set()
        # Event先行；即使控制通道异常，2秒父协调器兜底也会隔离整个worker。
        self._control("cancel", token, last_seq)

    def poll(self) -> list[WorkerEvent]:
        events = []
        if self.closed or self.process is None:
            return events
        for channel, count in ((self.result_queue, 14), (self.terminal_queue, 2)):
            for _ in range(count):
                try:
                    event = channel.get_nowait()
                except (Empty, OSError, ValueError):
                    break
                if event.generation != self.generation:
                    continue
                if event.kind == "loaded":
                    self.ready, self.config_id = True, event.config_id
                elif event.kind in ("failed", "load_failed"):
                    self.ready = False
                    self.load_failed = event.kind == "load_failed"
                events.append(event)
        dead = not self.process.is_alive()
        load_hung = not self.ready and time.monotonic() - self.started >= 60
        if (dead or load_hung) and not self.failed_reported:
            self.failed_reported, self.ready = True, False
            events.append(WorkerEvent(self.generation, 0, "crashed"))
        return events

    def _dispose(self) -> None:
        """此函数在线程执行，不能让join阻塞ASGI循环或取消确认。

        终止后不再读取旧队列；cancel_join_thread避免损坏/未消费feeder无限阻塞。
        无法确认死亡时抛错且不启动第二个worker，保持单物理推理进程不变量。
        """
        process = self.process
        if process is not None:
            self.shutdown_event.set()
            self.cancel_event.set()
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if process.is_alive():
                raise RuntimeError("无法回收推理进程")
            process.close()
        for channel in self.channels:
            channel.cancel_join_thread()
            channel.close()
        self.channels, self.process = [], None

    async def restart(self) -> None:
        self.ready = False
        await asyncio.to_thread(self._dispose)
        if self.closed or self.load_failed or self.restarts >= 3:
            return
        self.restarts += 1
        await self.open()

    async def close(self) -> None:
        self.closed, self.ready = True, False
        await asyncio.to_thread(self._dispose)
