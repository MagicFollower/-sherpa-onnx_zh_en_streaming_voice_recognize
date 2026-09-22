"""网络协调器是终态线性化的唯一入口；同步方法内不await，原子取得全局槽。

worker只给候选，不能直接发布final。终态提交和物理清理分开：cancel先封锁
结果，2秒内清理未确认则隔离旧进程；确认清理前busy始终为true。
"""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from .audio import parse_audio
from .auth import AuthError, AuthStore, Session, TokenBucket
from .protocol import (AUDIO_FORMAT, MAX_TEXT, START_KEYS, ProtocolError, error_message,
                       message, parse_client)
from .state import CachedTerminal, Connection, Round
from .supervisor import WorkerPort
from .worker import WorkerEvent


class Coordinator:
    def __init__(self, worker: WorkerPort, auth: AuthStore, clock=time.monotonic):
        self.worker, self.auth, self.clock = worker, auth, clock
        self.connections: dict[str, Connection] = {}
        self.owner: Connection | None = None
        self.next_token = 0
        self.restart_task: asyncio.Task | None = None
        self.task: asyncio.Task | None = None
        self.closed = False
        auth.on_revoke = self.revoked

    @property
    def busy(self) -> bool:
        return self.owner is not None

    async def open(self) -> None:
        await self.worker.open()
        self.task = asyncio.create_task(self.run())

    async def close(self) -> None:
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for connection in list(self.connections.values()):
            self.disconnect(connection)
        if self.restart_task:
            await asyncio.gather(self.restart_task, return_exceptions=True)
        await self.worker.close()
        self.owner = None
        self.auth.revoke_all()

    async def run(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(0.01)

    def connect(self, session: Session) -> Connection:
        if session.connection_id or len(self.connections) >= 4:
            raise AuthError("RATE_LIMITED", 429, 1)
        now, sid = self.clock(), str(uuid4())
        connection = Connection(sid, session, now, now, TokenBucket(100, now), TokenBucket(20, now))
        self.connections[sid] = connection
        session.connection_id = sid
        self.send(connection, message("hello", sid, model_ready=self.worker.ready,
                                     config_id=self.worker.config_id, max_active=1, max_utterance_ms=60000))
        return connection

    def send(self, connection: Connection, payload: dict) -> None:
        if not connection.outbox.offer([payload]) and not connection.closing:
            self.kick(connection, "PROTOCOL_ERROR", 1011)

    def request_error(self, connection: Connection, code: str, uid: str | None = None) -> None:
        self.send(connection, error_message(connection.session_id, code, utterance_id=uid))

    def revoked(self, session: Session) -> None:
        if session.connection_id in self.connections:
            self.kick(self.connections[session.connection_id], "AUTH_REQUIRED", 1008)

    def kick(self, connection: Connection, code: str, close_code: int) -> None:
        if connection.closing:
            return
        connection.closing = True
        if connection.active and connection.active.terminal is None:
            self.fail(connection, code)
        self.send(connection, error_message(connection.session_id, code, "connection"))
        connection.outbox.close(close_code)

    def disconnect(self, connection: Connection) -> None:
        """断连直接内部错误终结并回收，不伪造任何客户端cancel原因或恢复旧轮。"""
        connection.closing = True
        if connection.active and connection.active.terminal is None:
            self.fail(connection, "PROTOCOL_ERROR")
        self.connections.pop(connection.session_id, None)
        if connection.auth.connection_id == connection.session_id:
            connection.auth.connection_id = None
        connection.cache.clear()
        connection.seen.clear()
        connection.outbox.clear()
        connection.outbox.close(1001)

    def receive(self, connection: Connection, data: str | bytes) -> None:
        self.auth.sweep()
        if connection.closing:
            return
        now = self.clock()
        if not connection.all_rate.take(now) or (isinstance(data, str) and not connection.control_rate.take(now)):
            self.kick(connection, "RATE_LIMITED", 1008)
            return
        try:
            if isinstance(data, bytes):
                valid = self.audio(connection, data)
            else:
                payload = parse_client(data, connection.session_id)
                if payload["type"] == "heartbeat":
                    connection.last_valid = now
                    if payload["kind"] == "ping":
                        self.send(connection, message("heartbeat", connection.session_id,
                                                     kind="pong", nonce=payload["nonce"]))
                    return
                valid = self.control(connection, payload)
            if valid:
                connection.last_valid = now
                self.auth.touch(connection.auth)
        except ProtocolError as exc:
            self.kick(connection, exc.code, 1008)

    def replay(self, connection: Connection, payload: dict) -> bool:
        uid = payload["utterance_id"]
        connection.prune_cache(self.clock())
        cached = connection.cache.get(uid)
        if cached:
            if (payload["type"] == "stop" and cached.stop_signature is not None and
                    self.stop_signature(payload) != cached.stop_signature):
                self.request_error(connection, "AUDIO_MISMATCH", uid)
            else:
                bundle = ([cached.ack] if cached.ack else []) + [cached.message]
                if not connection.outbox.offer(bundle, terminal=True):
                    self.kick(connection, "PROTOCOL_ERROR", 1011)
            return True
        return False

    @staticmethod
    def stop_signature(payload: dict) -> tuple:
        return payload["last_seq"], payload["total_samples"], payload["reason"]

    def control(self, connection: Connection, payload: dict) -> bool:
        uid, kind = payload["utterance_id"], payload["type"]
        if self.replay(connection, payload):
            return False
        current = connection.active
        if current and current.utterance_id == uid:
            if current.terminal:
                self.request_error(connection, "STALE_UTTERANCE", uid)
                return False
            if kind == "start":
                signature = {key: payload[key] for key in START_KEYS}
                if signature != current.signature:
                    self.fail(connection, "PROTOCOL_ERROR")
                elif current.ready_message:
                    self.send(connection, current.ready_message)
            elif kind == "cancel":
                terminal = message("cancelled", connection.session_id, uid, reason=payload["reason"])
                self.commit(connection, terminal)
            else:
                self.stop(connection, payload)
            return True
        if uid in connection.seen:
            self.request_error(connection, "STALE_UTTERANCE", uid)
            return False
        if kind != "start":
            self.request_error(connection, "UNKNOWN_UTTERANCE", uid)
            return False
        # 基础校验成功的新start一律计墓碑，包括BUSY和MODEL_NOT_READY；不建第1001项。
        if len(connection.seen) >= 1000:
            self.request_error(connection, "RATE_LIMITED", uid)
            self.rotate(connection)
            return False
        connection.seen.add(uid)
        if self.busy:
            self.request_error(connection, "BUSY", uid)
            self.rotate(connection)
            return False
        if not self.worker.ready or self.restart_task:
            self.request_error(connection, "MODEL_NOT_READY", uid)
            self.rotate(connection)
            return False
        self.next_token += 1
        current = Round(uid, self.next_token, self.worker.generation,
                        {key: payload[key] for key in START_KEYS}, self.clock())
        connection.active, self.owner = current, connection
        if not self.worker.start(current.token):
            self.fail(connection, "WORKER_FAILED")
        return True

    def audio(self, connection: Connection, raw: bytes) -> bool:
        frame = parse_audio(raw)
        current = connection.active
        if not current or current.utterance_id != frame.utterance_id or current.terminal:
            self.request_error(connection, "STALE_UTTERANCE", frame.utterance_id)
            return False
        if current.ready_at is None or current.stopped_at is not None:
            self.fail(connection, "PROTOCOL_ERROR")
            return False
        try:
            current.ledger.validate(frame)
        except ProtocolError as exc:
            self.fail(connection, exc.code)
            return False
        if not self.worker.audio(current.token, frame):
            self.fail(connection, "OVERLOADED")
            return False
        current.ledger.commit(frame)
        current.ack_dirty = True
        return True

    def stop(self, connection: Connection, payload: dict) -> None:
        current = connection.active
        signature = self.stop_signature(payload)
        if current.ready_at is None:
            self.fail(connection, "PROTOCOL_ERROR")
            return
        if current.stop_signature is not None:
            if current.stop_signature != signature:
                self.fail(connection, "AUDIO_MISMATCH")
            else:
                self.ack(connection)
            return
        if signature[:2] != (current.ledger.received_seq, current.ledger.received_samples):
            self.fail(connection, "AUDIO_MISMATCH")
            return
        current.stop_signature, current.stopped_at = signature, self.clock()
        self.ack(connection)
        if not self.worker.stop(current.token, current.ledger.received_seq):
            self.fail(connection, "WORKER_FAILED")

    def ack(self, connection: Connection) -> dict:
        current = connection.active
        payload = message("ack", connection.session_id, current.utterance_id,
                          **current.ledger.ack_fields(current.stopped_at is not None))
        current.ack_dirty, current.last_ack = False, self.clock()
        self.send(connection, payload)
        return payload

    def fail(self, connection: Connection, code: str) -> None:
        if connection.active and connection.active.terminal is None:
            self.commit(connection, error_message(connection.session_id, code, "utterance",
                                                  connection.active.utterance_id, True))

    def commit(self, connection: Connection, terminal: dict, *, cleaned: bool = False) -> None:
        """唯一提交点：先固定终态，再发消息/通知取消，迟到worker候选只能被丢弃。"""
        current = connection.active
        if current is None or current.terminal is not None:
            return
        current.terminal, current.cleanup_at, current.last_text = terminal, self.clock(), ""
        ack = None
        if current.ready_at is not None:
            ack = message("ack", connection.session_id, current.utterance_id,
                          **current.ledger.ack_fields(current.stopped_at is not None))
        cached = CachedTerminal(self.clock(), terminal, ack, current.stop_signature)
        if not connection.closing:
            connection.cache[current.utterance_id] = cached
            connection.prune_cache(self.clock())
        bundle = ([ack] if ack else []) + [terminal]
        if not connection.outbox.offer(bundle, terminal=True):
            connection.closing = True
        if cleaned:
            self.release(connection)
        else:
            self.worker.cancel(current.token, current.ledger.received_seq)

    def release(self, connection: Connection) -> None:
        connection.active = None
        if self.owner is connection:
            self.owner = None
        self.rotate(connection)

    def rotate(self, connection: Connection) -> None:
        if not connection.active and len(connection.seen) >= 1000 and not connection.closing:
            self.kick(connection, "SESSION_ROTATE", 1000)

    def on_worker(self, event: WorkerEvent) -> None:
        if event.generation != self.worker.generation:
            return
        if event.kind in ("crashed", "load_failed"):
            if self.owner:
                self.fail(self.owner, "WORKER_FAILED")
            self.restart()
            return
        connection = self.owner
        current = connection.active if connection else None
        if current is None or event.token != current.token or event.generation != current.generation:
            return
        if event.kind == "failed":
            self.fail(connection, event.code or "WORKER_FAILED")
            self.restart()
            return
        if current.terminal:
            if event.kind in ("cleaned", "finished"):
                self.release(connection)
            return
        if event.kind == "ready":
            if current.ready_at is not None:
                return
            current.ready_at = self.clock()
            current.ready_message = message("ready", connection.session_id, current.utterance_id,
                                            config_id=self.worker.config_id, **AUDIO_FORMAT,
                                            punctuation_enabled=False, refinement_enabled=False,
                                            itn_enabled=False)
            self.send(connection, current.ready_message)
        elif event.kind in ("progress", "finished"):
            if current.stopped_at is not None and self.clock() - current.stopped_at >= 15:
                self.fail(connection, "FINAL_TIMEOUT")
                return
            try:
                if not isinstance(event.text, str) or len(event.text.encode("utf-8")) > MAX_TEXT:
                    self.fail(connection, "RESULT_LIMIT")
                    return
                changed = current.ledger.advance(event.coverage)
                current.ack_dirty |= changed
            except (ProtocolError, UnicodeError):
                self.fail(connection, "WORKER_FAILED")
                return
            if event.kind == "finished":
                if current.stopped_at is None or current.ledger.processed_samples != current.ledger.received_samples:
                    self.fail(connection, "WORKER_FAILED")
                    return
                current.revision += 1
                final = message("final", connection.session_id, current.utterance_id,
                                revision=current.revision, text=event.text,
                                status="ok" if event.text else "empty",
                                last_seq=current.ledger.received_seq, total_samples=current.ledger.received_samples,
                                result_mode="streaming", degraded=False, degradation_reason="none",
                                punctuation_status="disabled", refinement_status="disabled")
                self.commit(connection, final, cleaned=True)
            elif event.text != current.last_text:
                current.last_text = event.text
                current.revision += 1
                self.send(connection, message("partial", connection.session_id, current.utterance_id,
                                             revision=current.revision, text=event.text))

    def restart(self) -> None:
        if self.restart_task or self.closed:
            return
        self.worker.ready = False
        self.restart_task = asyncio.create_task(self._restart())

    async def _restart(self) -> None:
        try:
            await self.worker.restart()
            if self.owner:
                self.release(self.owner)
        except Exception:
            # 不能证明旧worker已死时不释放槽，也不制造第二个物理worker。
            self.worker.ready = False
        finally:
            self.restart_task = None

    def tick(self) -> None:
        now = self.clock()
        self.auth.sweep()
        # 先判期限再读取候选，避免超时后恰好到达的final逆转已超时事实。
        if self.owner and self.owner.active:
            current = self.owner.active
            if current.terminal:
                if now - current.cleanup_at >= 2:
                    self.restart()
            elif current.stopped_at is not None and now - current.stopped_at >= 15:
                self.fail(self.owner, "FINAL_TIMEOUT")
            elif current.ready_at is None and now - current.started >= 5:
                self.fail(self.owner, "START_TIMEOUT")
            elif current.ready_at is not None and current.stopped_at is None and now - current.ready_at >= 65:
                self.fail(self.owner, "STOP_TIMEOUT")
        if not self.restart_task:
            for event in self.worker.poll():
                self.on_worker(event)
        for connection in list(self.connections.values()):
            connection.prune_cache(now)
            if connection.closing:
                continue
            if now - connection.last_valid >= 30:
                self.kick(connection, "PROTOCOL_ERROR", 1001)
                continue
            if now - connection.last_ping >= 10:
                connection.last_ping = now
                self.send(connection, message("heartbeat", connection.session_id,
                                             kind="ping", nonce=uuid4().hex))
            current = connection.active
            if current and not current.terminal and current.ack_dirty and now - current.last_ack >= 0.1:
                self.ack(connection)
            self.rotate(connection)
