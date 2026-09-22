"""固定Zipformer适配器，仅由spawn子进程构建原生recognizer。

实际下载的encoder metadata冻结为C=32、T=39，10ms特征步长为160样本。
成功decode_stream一次才累加C；网络水位由协调器下取整到完整网络帧。
get_result返回字符串，token时间戳不是processed，也不作为进度的替代证据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .artifacts import load_manifest, config_id
from .protocol import MAX_TEXT

CHUNK_SHIFT = 32
CHUNK_SIZE = 39
FEATURE_SHIFT = 160
FEATURE_WINDOW = 400


class RecognitionError(RuntimeError):
    pass


class RecognitionCancelled(RecognitionError):
    pass


class Recognizer:
    def __init__(self, directory: Path, threads: int):
        # 延迟导入避免网络进程加载199MB权重及原生运行时；此处无任何下载调用。
        import sherpa_onnx
        manifest = load_manifest(directory)
        if (manifest["encoder_metadata"]["decode_chunk_len"],
                manifest["encoder_metadata"]["T"]) != (str(CHUNK_SHIFT), str(CHUNK_SIZE)):
            raise RecognitionError("模型chunk元数据与冻结适配器不一致")
        self.config_id = config_id(manifest, threads)
        self.native = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(directory / "tokens.txt"),
            encoder=str(directory / "encoder-epoch-99-avg-1.int8.onnx"),
            decoder=str(directory / "decoder-epoch-99-avg-1.onnx"),
            joiner=str(directory / "joiner-epoch-99-avg-1.int8.onnx"),
            sample_rate=16000, feature_dim=80, num_threads=threads, provider="cpu",
            decoding_method="greedy_search", model_type="zipformer",
            enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=1.2, rule3_min_utterance_length=300,
        )

    def stream(self, cancelled: Callable[[], bool]) -> "StreamAdapter":
        return StreamAdapter(self.native, cancelled)


class StreamAdapter:
    """一个外部轮次、一个流对象，多内部endpoint但唯一外部终稿。

    reset只重置厂商内部段状态，累计成功解码次数和网络实际样本从不清零。
    任意失败使本流不可复用，由worker销毁；取消检查位于每个native解码边界，
    native卡死时由父进程2秒监督终止，不声称能抢占正在执行的C++函数。
    """
    def __init__(self, native, cancelled: Callable[[], bool]):
        self.native = native
        self.stream = native.create_stream()
        self.cancelled = cancelled
        self.actual_samples = 0
        self.padding_samples = 0
        self.decode_count = 0
        self.endpoint_count = 0
        self.segments: list[str] = []
        self.finished = False

    @property
    def coverage(self) -> int:
        return min(self.actual_samples, self.decode_count * CHUNK_SHIFT * FEATURE_SHIFT)

    def check_cancel(self) -> None:
        if self.cancelled():
            raise RecognitionCancelled("本轮已取消")

    def text(self) -> str:
        current = self.native.get_result(self.stream)
        if not isinstance(current, str):
            raise RecognitionError("模型结果类型不支持")
        result = " ".join(part for part in [*self.segments, current.strip()] if part)
        if len(result.encode("utf-8")) > MAX_TEXT:
            raise RecognitionError("RESULT_LIMIT")
        return result

    def decode(self) -> None:
        while self.native.is_ready(self.stream):
            self.check_cancel()
            self.native.decode_stream(self.stream)
            # 只有成功返回才推进；accept_waveform、队列出队都不计数。
            self.decode_count += 1
            self.check_cancel()
            if self.native.is_endpoint(self.stream):
                segment = self.native.get_result(self.stream).strip()
                self.text()  # 在保存前施加整轮UTF-8上限，空段不堆积列表。
                if segment:
                    self.segments.append(segment)
                self.endpoint_count += 1
                self.native.reset(self.stream)

    def accept(self, pcm: bytes) -> tuple[int, str]:
        import numpy as np
        self.check_cancel()
        if self.finished or not pcm or len(pcm) % 2:
            raise RecognitionError("非法PCM输入状态")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(32768.0)
        self.stream.accept_waveform(16000, samples)
        self.actual_samples += len(samples)
        self.decode()
        return self.coverage, self.text()

    def finish(self) -> tuple[int, str]:
        """按实际C/T计算最小足够右上下文，再input_finished并证明覆盖真实尾部。

        ready条件是processed+T严格小于特征数，因此另留一帧与25ms特征窗。
        补零只进入模型，永不增加actual_samples、网络seq或客户端录音时长。
        flush结束仍未覆盖全部真实样本则明确失败，禁止强制ACK补齐。
        """
        import numpy as np
        self.check_cancel()
        if self.finished:
            raise RecognitionError("重复flush")
        self.finished = True
        if self.actual_samples == 0:
            self.stream.input_finished()
            return 0, ""
        steps = (self.actual_samples + CHUNK_SHIFT * FEATURE_SHIFT - 1) // (CHUNK_SHIFT * FEATURE_SHIFT)
        needed = ((steps - 1) * CHUNK_SHIFT + CHUNK_SIZE + 1) * FEATURE_SHIFT + FEATURE_WINDOW
        self.padding_samples = max(0, needed - self.actual_samples)
        if self.padding_samples:
            self.stream.accept_waveform(16000, np.zeros(self.padding_samples, dtype=np.float32))
        self.stream.input_finished()
        self.decode()
        if self.coverage != self.actual_samples:
            raise RecognitionError("flush未能证明完整覆盖")
        return self.coverage, self.text()
