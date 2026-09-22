"""显式测试工厂与确定性替身。所有伪文本仅在此测试包，生产无选择入口。"""
from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from uuid import UUID, uuid4

from lasr.app import _assemble
from lasr.audio import HEADER
from lasr.auth import AuthStore
from lasr.config import Settings
from lasr.coordinator import Coordinator
from lasr.protocol import AUDIO_FORMAT, message
from lasr.worker import WorkerEvent

ORIGIN = "https://localhost:8765"
GOLDEN_UUID = "00112233-4455-4677-8899-aabbccddeeff"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeWorker:
    def __init__(self, *, auto_ready=True, auto_finish=True, auto_clean=True, process_audio=True):
        self.ready = True
        self.generation = 1
        self.config_id = "test-fixture-only"
        self.events = deque()
        self.commands = []
        self.total = 0
        self.token = 0
        self.auto_ready, self.auto_finish = auto_ready, auto_finish
        self.auto_clean, self.process_audio = auto_clean, process_audio
        self.reject_audio = False
        self.reject_control = False
        self.restarts = 0
        self.closed = False

    async def open(self):
        pass

    async def close(self):
        self.closed = True
        self.events.clear()

    async def restart(self):
        self.restarts += 1
        self.generation += 1
        self.ready = True
        self.events.clear()

    def start(self, token):
        self.token, self.total = token, 0
        self.commands.append(("start", token))
        if self.auto_ready:
            self.emit("ready")
        return not self.reject_control

    def audio(self, token, frame):
        if self.reject_audio:
            return False
        self.total += frame.samples
        self.commands.append(("audio", frame.seq))
        if self.process_audio:
            self.emit("progress", coverage=self.total, text="测试草稿")
        return True

    def stop(self, token, last_seq):
        self.commands.append(("stop", last_seq))
        if self.auto_finish:
            self.emit("finished", coverage=self.total, text="测试终稿" if self.total else "")
        return not self.reject_control

    def cancel(self, token, last_seq):
        self.commands.append(("cancel", last_seq))
        if self.auto_clean:
            self.emit("cleaned", token=token)

    def emit(self, kind, *, token=None, generation=None, **fields):
        self.events.append(WorkerEvent(generation or self.generation,
                                       token if token is not None else self.token, kind, **fields))

    def poll(self):
        events = list(self.events)
        self.events.clear()
        return events


def test_app_factory(*, worker=None, clock=None, settings=None):
    """只在测试中注入；ASGI模拟TLS scope，不生成证书、不启动网络服务。"""
    worker = worker or FakeWorker()
    clock = clock or Clock()
    settings = settings or Settings(tls_cert=Path("test-only.pem"), tls_key=Path("test-only.key"))
    app = _assemble(settings, worker, clock, console_enabled=False)
    return app, worker, clock


def harness(**worker_options):
    clock = Clock()
    worker = FakeWorker(**worker_options)
    auth = AuthStore(clock)
    service = Coordinator(worker, auth, clock)
    connection = new_connection(service)
    drain(connection)
    return service, connection, worker, clock


def new_connection(service):
    token = service.auth.pair(service.auth.generate_pairing_code(), "127.0.0.1")
    return service.connect(service.auth.authenticate(token))


def drain(connection):
    result = [item for item, _ in connection.outbox.items]
    connection.outbox.clear()
    return result


def payload(connection, kind, uid=GOLDEN_UUID, **fields):
    return message(kind, connection.session_id, uid, **fields)


def send(service, connection, kind, uid=GOLDEN_UUID, **fields):
    data = payload(connection, kind, uid, **fields)
    service.receive(connection, json.dumps(data, ensure_ascii=False))


def start(service, connection, uid=GOLDEN_UUID, *, tick=True):
    send(service, connection, "start", uid, input_source="pointer", capture_sample_rate=48000,
         language="zh-en", **AUDIO_FORMAT)
    if tick:
        service.tick()
    return uid


def audio_bytes(uid=GOLDEN_UUID, seq=0, samples=320, pcm=None):
    pcm = pcm if pcm is not None else b"\0\0" * samples
    return HEADER.pack(b"LASR", 1, 0, 32, UUID(uid).bytes, seq, samples, 0) + pcm


def new_uuid():
    return str(uuid4())
