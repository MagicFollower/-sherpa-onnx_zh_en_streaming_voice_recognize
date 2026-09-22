"""仅在网络进程内维护授权摘要；状态轮询、心跳及垃圾消息均不延长业务空闲期。

原始口令仅作为generate_pairing_code的临时返回值交给受控控制台；原始cookie
仅在成功配对响应中出现，注册表、repr与普通logger不保存秘密。
"""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import math
import secrets
import time
from typing import Callable

COOKIE = "__Host-lasr_session"
ABSOLUTE_TTL = 28800
IDLE_TTL = 1800
PAIR_TTL = 300


class AuthError(ValueError):
    def __init__(self, code: str, status: int = 401, retry: int | None = None):
        super().__init__(code)
        self.code, self.status, self.retry = code, status, retry


@dataclass(repr=False)
class Session:
    digest: str
    created: float
    last_business: float
    connection_id: str | None = None


class PairLimiter:
    """滑动窗口及有界IP字典；满128项时拒绝新来源，不驱逐有效条目绕过限速。"""
    def __init__(self):
        self.global_attempts: deque[float] = deque()
        self.ips: dict[str, tuple[float, deque[float]]] = {}

    def check(self, ip: str, now: float) -> None:
        self.ips = {key: value for key, value in self.ips.items() if now - value[0] < 600}
        while self.global_attempts and now - self.global_attempts[0] >= 60:
            self.global_attempts.popleft()
        if len(self.global_attempts) >= 20:
            raise AuthError("RATE_LIMITED", 429, max(1, math.ceil(60 - now + self.global_attempts[0])))
        if ip not in self.ips and len(self.ips) >= 128:
            raise AuthError("RATE_LIMITED", 429, 600)
        _, attempts = self.ips.get(ip, (now, deque()))
        while attempts and now - attempts[0] >= 300:
            attempts.popleft()
        if len(attempts) >= 5:
            raise AuthError("RATE_LIMITED", 429, max(1, math.ceil(300 - now + attempts[0])))
        self.global_attempts.append(now)
        attempts.append(now)
        self.ips[ip] = (now, attempts)


class TokenBucket:
    """每连接消息与控制消息两个独立突发桶，无跨连接无界字典。"""
    def __init__(self, rate: int, now: float):
        self.rate, self.tokens, self.updated = rate, float(rate), now

    def take(self, now: float) -> bool:
        self.tokens = min(self.rate, self.tokens + max(0, now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class AuthStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.pair_digest: bytes | None = None
        self.pair_created = 0.0
        self.pair_failures = 0
        self.limiter = PairLimiter()
        self.on_revoke: Callable[[Session], None] = lambda session: None

    def generate_pairing_code(self) -> str:
        code = base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
        self.pair_digest = hashlib.sha256(code.encode("ascii")).digest()
        self.pair_created, self.pair_failures = self.clock(), 0
        return code

    def sweep(self) -> None:
        now = self.clock()
        for session in list(self.sessions.values()):
            if now - session.created >= ABSOLUTE_TTL or now - session.last_business >= IDLE_TTL:
                self.revoke(session)
        if self.pair_digest is not None and now - self.pair_created >= PAIR_TTL:
            self.pair_digest = None

    def pair(self, code: str, ip: str) -> str:
        self.sweep()
        self.limiter.check(ip, self.clock())
        normalized = code.strip().upper()
        candidate = hashlib.sha256(normalized.encode("utf-8")).digest()
        if self.pair_digest is None or not secrets.compare_digest(candidate, self.pair_digest):
            self.pair_failures += 1
            if self.pair_failures >= 5:
                self.pair_digest = None
            raise AuthError("PAIR_FAILED")
        if len(self.sessions) >= 4:
            raise AuthError("PAIR_UNAVAILABLE", 409)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = self.clock()
        self.sessions[digest] = Session(digest, now, now)
        self.pair_digest = None
        return token

    def authenticate(self, token: str | None) -> Session:
        self.sweep()
        if not token or len(token) != 43 or any(c not in
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token):
            raise AuthError("AUTH_REQUIRED")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        session = self.sessions.get(digest)
        if session is None:
            raise AuthError("AUTH_REQUIRED")
        return session

    def touch(self, session: Session) -> None:
        """只能由已通过协议及所有权校验的业务事件调用；不能在接收前续期。"""
        if session.digest in self.sessions:
            session.last_business = self.clock()

    def revoke(self, session: Session) -> None:
        if self.sessions.pop(session.digest, None) is not None:
            self.on_revoke(session)

    def revoke_all(self) -> None:
        self.pair_digest = None
        for session in list(self.sessions.values()):
            self.revoke(session)
