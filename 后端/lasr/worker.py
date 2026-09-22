"""常驻spawn推理进程：控制优先、取消独立Event、stop按音频序号屏障执行。

普通结果14项加终态/清理专位2项，共16项；草稿可合并但终态候选不可丢。
所有事件带进程generation及轮次token，父协调器仍是外部终态唯一提交者。
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from queue import Empty, Full
import time


@dataclass(frozen=True)
class WorkerEvent:
    generation: int
    token: int
    kind: str
    coverage: int = 0
    text: str = ""
    code: str = ""
    config_id: str = ""


def worker_main(generation: int, directory: str, threads: int,
                audio, control, results, terminal, cancelled, shutdown) -> None:
    """模型只在child内构建；异常正文不跨IPC，不落音频或识别正文日志。

    stop虽走控制通道，但仅记录目标seq，绝不越过先前音频直接flush。
    cancel先阻止解码，再按父进程声明的接收末序号丢弃尾队列；收到全部在途
    帧才报告cleaned，避免mp.Queue feeder迟到音频占据下一轮容量。
    """
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    from .recognizer import Recognizer, RecognitionCancelled

    def essential(event: WorkerEvent) -> None:
        terminal.put(event, timeout=2)

    try:
        recognizer = Recognizer(Path(directory), threads)
    except Exception:
        essential(WorkerEvent(generation, 0, "load_failed", code="MODEL_NOT_READY"))
        return
    results.put(WorkerEvent(generation, 0, "loaded", config_id=recognizer.config_id), timeout=2)
    token = 0
    stream = None
    consumed = -1
    stop_at = None
    cancel_at = None
    pending = None
    while not shutdown.is_set():
        try:
            # 每次最多8条，且全局只有一个活动轮；不会被无界控制输入饿死音频。
            for _ in range(8):
                try:
                    command = control.get_nowait()
                except Empty:
                    break
                kind, ident, value = command
                if kind == "start":
                    if token:
                        raise RuntimeError("重叠轮次")
                    token, consumed, stop_at, cancel_at = ident, -1, None, None
                    if not cancelled.is_set():
                        stream = recognizer.stream(cancelled.is_set)
                        results.put(WorkerEvent(generation, token, "ready"), timeout=2)
                elif ident == token and kind == "stop":
                    stop_at = value
                elif kind == "cancel":
                    if ident == token:
                        cancel_at = value
                    elif not token:
                        essential(WorkerEvent(generation, ident, "cleaned"))
            if cancelled.is_set() and token:
                stream, pending = None, None
            if token and cancel_at is not None and consumed >= cancel_at:
                old = token
                token, stream, pending = 0, None, None
                essential(WorkerEvent(generation, old, "cleaned"))
                continue
            if token and stop_at is not None and consumed == stop_at and not cancelled.is_set():
                if stream is None:
                    raise RuntimeError("流不存在")
                coverage, text = stream.finish()
                old = token
                # 先销毁流再报告finished；父进程可据此确认全局槽位的物理清理完成。
                token, stream, pending = 0, None, None
                essential(WorkerEvent(generation, old, "finished", coverage, text))
                continue
            if pending is not None:
                try:
                    results.put_nowait(pending)
                    pending = None
                except Full:
                    pass
            try:
                ident, seq, pcm = audio.get(timeout=0.01)
            except Empty:
                continue
            if ident != token:
                continue
            if seq != consumed + 1:
                raise RuntimeError("IPC音频不连续")
            consumed = seq
            if not cancelled.is_set():
                if stream is None:
                    raise RuntimeError("音频早于流初始化")
                coverage, text = stream.accept(pcm)
                pending = WorkerEvent(generation, token, "progress", coverage, text)
            pcm = None
        except RecognitionCancelled:
            stream, pending = None, None
        except Exception as exc:
            old = token
            token, stream, pending = 0, None, None
            code = "RESULT_LIMIT" if str(exc) == "RESULT_LIMIT" else "WORKER_FAILED"
            essential(WorkerEvent(generation, old, "failed", code=code))
            # 原生异常后整个进程退出；不允许受损recognizer被下一轮复用。
            return
    stream = None
    time.sleep(0)
