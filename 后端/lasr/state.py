"""每连接的有界发送状态和无PCM轮次账本；所有变更限定在ASGI事件循环。"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import json

from .audio import AudioLedger
from .auth import Session, TokenBucket


class Outbox:
    """32项/256KiB硬上限，普通流量只用30项，为最终ACK和终态预留2项。

    ACK和草稿只保留同轮最新值。终态批次先移除未发草稿，再原子入队，确保
    最终ACK先于final；慢连接只能关闭，不能无界累积或丢弃终态假装成功。
    """
    def __init__(self):
        self.items: deque[tuple[dict, int]] = deque()
        self.bytes = 0
        self.event = asyncio.Event()
        self.close_code: int | None = None

    def _remove(self, predicate) -> None:
        self.items = deque((item, size) for item, size in self.items if not predicate(item))
        self.bytes = sum(size for _, size in self.items)

    def offer(self, messages: list[dict], *, terminal: bool = False) -> bool:
        if self.close_code is not None:
            return False
        uid = messages[-1].get("utterance_id")
        if terminal:
            self._remove(lambda item: item.get("utterance_id") == uid and
                         item["type"] in ("partial", "ack"))
        elif len(messages) == 1 and messages[0]["type"] in ("partial", "ack"):
            kind = messages[0]["type"]
            self._remove(lambda item: item["type"] == kind and item.get("utterance_id") == uid)
        encoded = [(item, len(json.dumps(item, ensure_ascii=False,
                                        separators=(",", ":")).encode("utf-8"))) for item in messages]
        limit = 32 if terminal else 30
        if len(self.items) + len(encoded) > limit or self.bytes + sum(n for _, n in encoded) > 262144:
            self.close(1011)
            return False
        self.items.extend(encoded)
        self.bytes += sum(size for _, size in encoded)
        self.event.set()
        return True

    def close(self, code: int) -> None:
        self.close_code = code
        self.event.set()

    async def take(self) -> dict | None:
        while not self.items:
            if self.close_code is not None:
                return None
            self.event.clear()
            await self.event.wait()
        item, size = self.items.popleft()
        self.bytes -= size
        return item

    def clear(self) -> None:
        self.items.clear()
        self.bytes = 0


@dataclass
class Round:
    utterance_id: str
    token: int
    generation: int
    signature: dict
    started: float
    ledger: AudioLedger = field(default_factory=AudioLedger)
    ready_at: float | None = None
    stopped_at: float | None = None
    cleanup_at: float | None = None
    ready_message: dict | None = None
    stop_signature: tuple | None = None
    terminal: dict | None = None
    revision: int = 0
    last_text: str = ""
    ack_dirty: bool = False
    last_ack: float = 0.0


@dataclass
class CachedTerminal:
    created: float
    message: dict
    ack: dict | None
    stop_signature: tuple | None


@dataclass
class Connection:
    session_id: str
    auth: Session
    last_valid: float
    last_ping: float
    all_rate: TokenBucket
    control_rate: TokenBucket
    outbox: Outbox = field(default_factory=Outbox)
    seen: set[str] = field(default_factory=set)
    cache: OrderedDict[str, CachedTerminal] = field(default_factory=OrderedDict)
    active: Round | None = None
    closing: bool = False

    def prune_cache(self, now: float) -> None:
        while self.cache:
            first = next(iter(self.cache))
            if len(self.cache) <= 16 and now - self.cache[first].created < 30:
                break
            self.cache.popitem(last=False)
