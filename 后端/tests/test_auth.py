"""认证存储：配对口令生命周期、会话摘要、限速及空闲/绝对期限。"""
from __future__ import annotations

import hashlib
import secrets

import pytest

from lasr.auth import ABSOLUTE_TTL, IDLE_TTL, PAIR_TTL, AuthError, AuthStore, PairLimiter, TokenBucket


class TestPairLimiter:
    def test_global_limit(self):
        limiter = PairLimiter()
        now = 100.0
        for i in range(20):
            limiter.check(f"10.0.0.{i}", now + i * 0.01)
        with pytest.raises(AuthError) as exc:
            limiter.check("10.0.0.99", now + 0.5)
        assert exc.value.status == 429

    def test_per_ip_limit(self):
        limiter = PairLimiter()
        now = 100.0
        for i in range(5):
            limiter.check("10.0.0.1", now + i)
        with pytest.raises(AuthError) as exc:
            limiter.check("10.0.0.1", now + 6)
        assert exc.value.status == 429

    def test_ip_dict_capacity(self):
        limiter = PairLimiter()
        now = 100.0
        # 每61秒一批，每批最多19个IP（低于全局20限制），累计到128个IP
        added = 0
        batch = 0
        while added < 128:
            count = min(19, 128 - added)
            for j in range(count):
                limiter.check(f"10.{added}.{j}.1", now + batch * 61)
                added += 1
            batch += 1
        # 128个IP后拒绝新IP
        with pytest.raises(AuthError) as exc:
            limiter.check("192.168.0.1", now + batch * 61)
        assert exc.value.status == 429

    def test_global_window_slides(self):
        limiter = PairLimiter()
        now = 100.0
        for i in range(20):
            limiter.check(f"10.0.0.{i}", now + i * 0.01)
        # 60秒后窗口滑过
        limiter.check("10.0.0.99", now + 61)

    def test_per_ip_window_slides(self):
        limiter = PairLimiter()
        now = 100.0
        for i in range(5):
            limiter.check("10.0.0.1", now + i)
        # 5分钟后窗口滑过
        limiter.check("10.0.0.1", now + 301)


class TestTokenBucket:
    def test_initial_tokens(self):
        bucket = TokenBucket(10, 0.0)
        for _ in range(10):
            assert bucket.take(0.0) is True
        assert bucket.take(0.0) is False

    def test_refill(self):
        bucket = TokenBucket(10, 0.0)
        for _ in range(10):
            bucket.take(0.0)
        # 1秒后补充10个
        assert bucket.take(1.0) is True

    def test_no_negative_time(self):
        bucket = TokenBucket(5, 10.0)
        for _ in range(5):
            bucket.take(10.0)
        # 时间倒退不补充
        assert bucket.take(5.0) is False


class TestAuthStore:
    def test_generate_pairing_code_format(self):
        store = AuthStore()
        code = store.generate_pairing_code()
        assert len(code) == 26  # 128位 → 26个Base32字符（无填充）
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in code)

    def test_pair_success(self):
        store = AuthStore()
        code = store.generate_pairing_code()
        token = store.pair(code, "127.0.0.1")
        assert len(token) == 43  # 32字节Base64URL

    def test_pair_strips_and_uppercases(self):
        store = AuthStore()
        code = store.generate_pairing_code()
        token = store.pair(f"  {code.lower()}  ", "127.0.0.1")
        assert len(token) == 43

    def test_pair_wrong_code(self):
        store = AuthStore()
        store.generate_pairing_code()
        with pytest.raises(AuthError) as exc:
            store.pair("WRONGCODE", "127.0.0.1")
        assert exc.value.code == "PAIR_FAILED"

    def test_pair_5_failures_invalidates(self):
        store = AuthStore()
        code = store.generate_pairing_code()
        for i in range(5):
            with pytest.raises(AuthError):
                store.pair(f"WRONG{i}", "127.0.0.1")
        # 第6次用正确口令也失败
        with pytest.raises(AuthError):
            store.pair(code, "127.0.0.1")

    def test_pair_code_expires(self):
        clock_value = 100.0
        store = AuthStore(lambda: clock_value)
        store.generate_pairing_code()
        clock_value += PAIR_TTL + 1
        store.sweep()
        with pytest.raises(AuthError):
            store.pair("ANYTHING", "127.0.0.1")

    def test_pair_capacity(self):
        store = AuthStore()
        for _ in range(4):
            code = store.generate_pairing_code()
            store.pair(code, "127.0.0.1")
        code = store.generate_pairing_code()
        with pytest.raises(AuthError) as exc:
            store.pair(code, "127.0.0.1")
        assert exc.value.status == 409

    def test_authenticate_valid(self):
        store = AuthStore()
        code = store.generate_pairing_code()
        token = store.pair(code, "127.0.0.1")
        session = store.authenticate(token)
        assert session.digest == hashlib.sha256(token.encode("ascii")).hexdigest()

    def test_authenticate_none(self):
        store = AuthStore()
        with pytest.raises(AuthError):
            store.authenticate(None)

    def test_authenticate_bad_format(self):
        store = AuthStore()
        with pytest.raises(AuthError):
            store.authenticate("short")

    def test_authenticate_unknown_token(self):
        store = AuthStore()
        fake = secrets.token_urlsafe(32)
        with pytest.raises(AuthError):
            store.authenticate(fake)

    def test_session_absolute_expiry(self):
        clock_value = 100.0
        store = AuthStore(lambda: clock_value)
        code = store.generate_pairing_code()
        token = store.pair(code, "127.0.0.1")
        clock_value += ABSOLUTE_TTL + 1
        store.sweep()
        with pytest.raises(AuthError):
            store.authenticate(token)

    def test_session_idle_expiry(self):
        clock_value = 100.0
        store = AuthStore(lambda: clock_value)
        code = store.generate_pairing_code()
        token = store.pair(code, "127.0.0.1")
        clock_value += IDLE_TTL + 1
        store.sweep()
        with pytest.raises(AuthError):
            store.authenticate(token)

    def test_touch_refreshes_idle(self):
        clock_value = 100.0
        store = AuthStore(lambda: clock_value)
        code = store.generate_pairing_code()
        token = store.pair(code, "127.0.0.1")
        session = store.authenticate(token)
        clock_value += IDLE_TTL - 10
        store.touch(session)
        clock_value += 5
        store.sweep()
        # 仍然有效因为touch续期了
        store.authenticate(token)

    def test_heartbeat_does_not_refresh(self):
        """touch只能在业务事件后调用；心跳和状态轮询不应续期。"""
        clock_value = 100.0
        store = AuthStore(lambda: clock_value)
        code = store.generate_pairing_code()
        token = store.pair(code, "127.0.0.1")
        session = store.authenticate(token)
        # 不touch，直接等idle过期
        clock_value += IDLE_TTL + 1
        store.sweep()
        with pytest.raises(AuthError):
            store.authenticate(token)

    def test_revoke(self):
        store = AuthStore()
        code = store.generate_pairing_code()
        token = store.pair(code, "127.0.0.1")
        session = store.authenticate(token)
        revoked = []
        store.on_revoke = lambda s: revoked.append(s)
        store.revoke(session)
        assert len(revoked) == 1
        with pytest.raises(AuthError):
            store.authenticate(token)

    def test_revoke_all(self):
        store = AuthStore()
        tokens = []
        for _ in range(3):
            code = store.generate_pairing_code()
            tokens.append(store.pair(code, "127.0.0.1"))
        store.revoke_all()
        for token in tokens:
            with pytest.raises(AuthError):
                store.authenticate(token)

    def test_pair_code_single_use(self):
        store = AuthStore()
        code = store.generate_pairing_code()
        store.pair(code, "127.0.0.1")
        # 同一口令不能再用
        with pytest.raises(AuthError):
            store.pair(code, "127.0.0.1")
