"""HTTP端点与ASGI集成：配对、注销、状态、静态资源及错误格式。"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lasr.auth import COOKIE
from tests.support import test_app_factory as make_test_app


@pytest.fixture
def app():
    application, worker, clock = make_test_app()
    return application, worker, clock


@pytest.fixture
async def client(app):
    application, _, _ = app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                           headers={"origin": "https://localhost:8765"}) as c:
        yield c


async def _pair(client: AsyncClient, code: str):
    return await client.post("/api/pair", json={"pairing_code": code})


class TestHealth:
    async def test_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_no_store(self, client):
        response = await client.get("/health")
        assert "no-store" in response.headers.get("cache-control", "")


class TestPair:
    async def test_success(self, app):
        application, worker, clock = app
        code = application.state.auth.generate_pairing_code()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            response = await _pair(client, code)
            assert response.status_code == 204
            assert response.text == ""
            cookie = response.cookies.get(COOKIE)
            assert cookie is not None
            assert len(cookie) == 43

    async def test_wrong_code(self, client):
        response = await _pair(client, "WRONGCODE")
        assert response.status_code == 401

    async def test_bad_content_type(self, app):
        application, _, _ = app
        code = application.state.auth.generate_pairing_code()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            response = await client.post("/api/pair", content=code,
                                         headers={"content-type": "text/plain"})
            assert response.status_code == 415

    async def test_extra_fields(self, app):
        application, _, _ = app
        code = application.state.auth.generate_pairing_code()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            response = await client.post("/api/pair",
                                         json={"pairing_code": code, "extra": 1})
            assert response.status_code == 400

    async def test_no_store_header(self, app):
        application, _, _ = app
        code = application.state.auth.generate_pairing_code()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            response = await _pair(client, code)
            assert "no-store" in response.headers.get("cache-control", "")


class TestStatus:
    async def test_requires_auth(self, client):
        response = await client.get("/api/status")
        assert response.status_code == 401

    async def test_authenticated(self, app):
        application, _, _ = app
        code = application.state.auth.generate_pairing_code()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            pair_response = await _pair(client, code)
            response = await client.get("/api/status")
            assert response.status_code == 200
            data = response.json()
            assert data["protocol_version"] == "1.0"
            assert data["max_active"] == 1
            assert data["max_utterance_ms"] == 60000
            assert isinstance(data["model_ready"], bool)
            assert isinstance(data["busy"], bool)
            assert isinstance(data["connection_available"], bool)


class TestLogout:
    async def test_requires_auth(self, client):
        response = await client.post("/api/logout", json={})
        assert response.status_code == 401

    async def test_success(self, app):
        application, _, _ = app
        code = application.state.auth.generate_pairing_code()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            await _pair(client, code)
            response = await client.post("/api/logout", json={})
            assert response.status_code == 204
            # 注销后状态应401
            response = await client.get("/api/status")
            assert response.status_code == 401

    async def test_rejects_body(self, app):
        application, _, _ = app
        code = application.state.auth.generate_pairing_code()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            await _pair(client, code)
            response = await client.post("/api/logout", json={"extra": True})
            assert response.status_code == 400


class TestNotFound:
    async def test_missing_path(self, client):
        response = await client.get("/nonexistent")
        assert response.status_code == 404

    async def test_method_not_allowed(self, client):
        response = await client.post("/health")
        assert response.status_code == 405


class TestErrorFormat:
    async def test_error_has_code_and_message(self, client):
        response = await client.get("/api/status")
        assert response.status_code == 401
        data = response.json()
        assert "code" in data
        assert "message" in data
        assert len(data["message"].encode("utf-8")) <= 256

    async def test_rate_limit_has_retry(self, app):
        application, _, _ = app
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://localhost:8765",
                               headers={"origin": "https://localhost:8765"}) as client:
            for _ in range(6):
                response = await _pair(client, "WRONG")
            assert response.status_code == 429
            assert "retry-after" in response.headers


class TestSecurityHeaders:
    async def test_present(self, client):
        response = await client.get("/health")
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("referrer-policy") == "no-referrer"

    async def test_csp(self, client):
        response = await client.get("/health")
        csp = response.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp


class TestOrigin:
    async def test_wrong_origin(self, app):
        application, _, _ = app
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="https://evil.com:8765",
                               headers={"origin": "https://evil.com:8765"}) as client:
            response = await client.get("/health")
            assert response.status_code == 403
