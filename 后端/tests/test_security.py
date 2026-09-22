"""Host/Origin校验、传输安全中间件及会话cookie解析。"""
from __future__ import annotations

from pathlib import Path

import pytest

from lasr.auth import AuthError
from lasr.config import Settings, authority, loopback, origin
from lasr.security import SecurityMiddleware, header_values, session_cookie, validate_transport


def _scope(scheme="https", host="localhost:8765", origin_value="https://localhost:8765",
           method="GET", path="/", cookie=None):
    headers = [(b"host", host.encode())]
    if origin_value:
        headers.append((b"origin", origin_value.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return {"type": "http", "scheme": scheme, "method": method, "path": path,
            "headers": headers, "client": ("127.0.0.1", 1234)}


class TestLoopback:
    def test_localhost(self):
        assert loopback("localhost") is True

    def test_127(self):
        assert loopback("127.0.0.1") is True

    def test_lan(self):
        assert loopback("192.168.1.1") is False

    def test_case_insensitive(self):
        assert loopback("LOCALHOST") is True


class TestAuthority:
    def test_host_port(self):
        assert authority("localhost:8765", "https") == ("localhost", 8765)

    def test_default_https_port(self):
        assert authority("localhost", "https") == ("localhost", 443)

    def test_default_http_port(self):
        assert authority("localhost", "http") == ("localhost", 80)

    def test_ip_address(self):
        assert authority("192.168.1.1:443", "https") == ("192.168.1.1", 443)

    def test_rejects_path(self):
        with pytest.raises(ValueError):
            authority("localhost:8765/path", "https")

    def test_rejects_userinfo(self):
        with pytest.raises(ValueError):
            authority("user@localhost:8765", "https")

    def test_rejects_wildcard(self):
        with pytest.raises(ValueError):
            authority("*.localhost:8765", "https")

    def test_rejects_empty_port(self):
        with pytest.raises(ValueError):
            authority("localhost:", "https")

    def test_rejects_space(self):
        with pytest.raises(ValueError):
            authority("local host:8765", "https")


class TestOrigin:
    def test_valid(self):
        assert origin("https://localhost:8765") == ("https", "localhost", 8765)

    def test_rejects_path(self):
        with pytest.raises(ValueError):
            origin("https://localhost:8765/")

    def test_rejects_query(self):
        with pytest.raises(ValueError):
            origin("https://localhost:8765?x=1")

    def test_rejects_ftp(self):
        with pytest.raises(ValueError):
            origin("ftp://localhost:8765")


class TestValidateTransport:
    def _settings(self, dev=False):
        return Settings(
            host="localhost", port=8765, dev_localhost=dev,
            allowed_hosts=("localhost:8765",),
            allowed_origins=("https://localhost:8765",) if not dev else ("http://localhost:8765",),
            tls_cert=Path("test.pem") if not dev else None,
            tls_key=Path("test.key") if not dev else None,
        )

    def test_valid_https(self):
        validate_transport(_scope(), self._settings(), require_origin=True)

    def test_rejects_wrong_origin(self):
        with pytest.raises(AuthError) as exc:
            validate_transport(_scope(origin_value="https://evil.com:8765"),
                               self._settings(), require_origin=True)
        assert exc.value.status == 403

    def test_rejects_wrong_host(self):
        with pytest.raises(AuthError) as exc:
            validate_transport(_scope(host="evil.com:8765"),
                               self._settings(), require_origin=True)
        assert exc.value.status == 403

    def test_rejects_http_non_localhost(self):
        with pytest.raises(AuthError):
            validate_transport(_scope(scheme="http"), self._settings(), require_origin=False)

    def test_dev_localhost_allows_http(self):
        settings = self._settings(dev=True)
        scope = _scope(scheme="http", host="localhost:8765",
                        origin_value="http://localhost:8765")
        validate_transport(scope, settings, require_origin=True)

    def test_dev_localhost_rejects_remote(self):
        settings = self._settings(dev=True)
        scope = {"type": "http", "scheme": "http", "method": "GET", "path": "/",
                 "headers": [(b"host", b"192.168.1.1:8765")],
                 "client": ("192.168.1.1", 1234)}
        with pytest.raises(AuthError):
            validate_transport(scope, settings, require_origin=False)

    def test_no_origin_without_require(self):
        scope = _scope(origin_value=None)
        # 不要求origin时允许缺失
        validate_transport(scope, self._settings(), require_origin=False)

    def test_multiple_origins_rejected(self):
        scope = _scope()
        scope["headers"].append((b"origin", b"https://localhost:8765"))
        with pytest.raises(AuthError):
            validate_transport(scope, self._settings(), require_origin=True)


class TestSessionCookie:
    def test_single_cookie(self):
        scope = {"headers": [(b"cookie", b"__Host-lasr_session=abc123")]}
        assert session_cookie(scope) == "abc123"

    def test_multiple_values_rejected(self):
        scope = {"headers": [
            (b"cookie", b"__Host-lasr_session=abc"),
            (b"cookie", b"__Host-lasr_session=def"),
        ]}
        assert session_cookie(scope) is None

    def test_no_cookie(self):
        scope = {"headers": []}
        assert session_cookie(scope) is None

    def test_other_cookies_ignored(self):
        scope = {"headers": [(b"cookie", b"other=value; __Host-lasr_session=token")]}
        assert session_cookie(scope) == "token"

    def test_duplicate_in_same_header_rejected(self):
        scope = {"headers": [(b"cookie", b"__Host-lasr_session=a; __Host-lasr_session=b")]}
        assert session_cookie(scope) is None
