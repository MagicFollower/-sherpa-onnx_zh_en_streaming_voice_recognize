"""网络音频是32字节LASR头加原始PCM；UUID使用RFC原始字节而非GUID混合端序。"""
from __future__ import annotations

from dataclasses import dataclass, field
from bisect import bisect_right
import struct
from uuid import UUID

from .protocol import ProtocolError

HEADER = struct.Struct("<4sBBH16sIHH")
MAX_SAMPLES = 960000
MAX_FRAMES = 3000
BACKLOG_WARN = 8000
BACKLOG_HARD = 32000


@dataclass(frozen=True)
class AudioFrame:
    utterance_id: str
    seq: int
    samples: int
    pcm: bytes


def parse_audio(raw: bytes) -> AudioFrame:
    if not 34 <= len(raw) <= 672:
        raise ProtocolError()
    magic, major, flags, length, uid, seq, samples, reserved = HEADER.unpack_from(raw)
    if (magic, major, flags, length, reserved) != (b"LASR", 1, 0, 32, 0):
        raise ProtocolError()
    parsed = UUID(bytes=uid)
    if parsed.version != 4 or not 1 <= samples <= 320 or len(raw) != 32 + 2 * samples:
        raise ProtocolError()
    return AudioFrame(str(parsed), seq, samples, raw[32:])


@dataclass
class AudioLedger:
    """接收与成功解码水位分离；帧结束位置最多3000项，不保存PCM。

    协调器先validate，再确认有界IPC入队成功后commit。processed仅由成功解码
    的覆盖样本推进，向下映射到完整网络帧；绝不在queue pop或accept时推进。
    """
    ends: list[int] = field(default_factory=list)
    received_samples: int = 0
    processed_samples: int = 0
    processed_seq: int = -1
    short_tail: bool = False

    @property
    def received_seq(self) -> int:
        return len(self.ends) - 1

    def validate(self, frame: AudioFrame) -> None:
        expected = len(self.ends)
        if frame.seq < expected:
            raise ProtocolError("AUDIO_DUPLICATE")
        if frame.seq > expected:
            raise ProtocolError("AUDIO_SEQUENCE")
        if self.short_tail:
            raise ProtocolError()
        total = self.received_samples + frame.samples
        if expected >= MAX_FRAMES or total > MAX_SAMPLES:
            raise ProtocolError("DURATION_LIMIT")
        if total - self.processed_samples > BACKLOG_HARD:
            raise ProtocolError("OVERLOADED")

    def commit(self, frame: AudioFrame) -> None:
        self.received_samples += frame.samples
        self.ends.append(self.received_samples)
        self.short_tail = frame.samples < 320

    def advance(self, coverage: int) -> bool:
        if type(coverage) is not int or not self.processed_samples <= coverage <= self.received_samples:
            raise ProtocolError("WORKER_FAILED")
        seq = bisect_right(self.ends, coverage) - 1
        if seq <= self.processed_seq:
            return False
        self.processed_seq = seq
        self.processed_samples = self.ends[seq]
        return True

    def ack_fields(self, stopped: bool) -> dict:
        return {"received_seq": self.received_seq, "received_samples": self.received_samples,
                "processed_seq": self.processed_seq, "processed_samples": self.processed_samples,
                "stop_received": stopped}
