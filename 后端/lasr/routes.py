"""HTTP与WSS薄路由：手动有界解析避免FastAPI默认422回显敏感请求。

握手验证不调用accept；Starlette的HTTP denial扩展给出真实401/403/429。
网络发送独立任务，慢客户端不能阻塞全局推理监督及其他连接的取消。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.websockets import WebSocketDisconnect

from .auth import ABSOLUTE_TTL, AuthError
from .protocol import MAX_JSON, SUBPROTOCOL, ProtocolError, json_object
from .security import failure, session_cookie, validate_transport


async def body_object(request: Request) -> dict:
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise AuthError("UNSUPPORTED_MEDIA_TYPE", 415)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_JSON:
            raise AuthError("BAD_REQUEST", 400)
        raw.extend(chunk)
    try:
        return json_object(bytes(raw))
    except ProtocolError:
        raise AuthError("BAD_REQUEST", 400) from None


def install_routes(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def auth_error(request, exc):
        return failure(exc.code, exc.status, exc.retry)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = "METHOD_NOT_ALLOWED" if exc.status_code == 405 else "NOT_FOUND"
        return failure(code, exc.status_code)

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    @app.post("/api/pair", status_code=204)
    async def pair(request: Request):
        data = await body_object(request)
        code = data.get("pairing_code")
        if set(data) != {"pairing_code"} or not isinstance(code, str) or len(code.encode("utf-8")) > 128:
            raise AuthError("BAD_REQUEST", 400)
        token = app.state.auth.pair(code, request.client.host)
        cookie = app.state.settings.cookie_name
        secure = not app.state.settings.dev_localhost
        response = Response(status_code=204)
        response.set_cookie(cookie, token, max_age=ABSOLUTE_TTL, path="/", secure=secure,
                            httponly=True, samesite="strict")
        return response

    @app.post("/api/logout", status_code=204)
    async def logout(request: Request):
        session = app.state.auth.authenticate(session_cookie(request.scope))
        data = await body_object(request)
        if data:
            raise AuthError("BAD_REQUEST", 400)
        app.state.auth.revoke(session)
        cookie = app.state.settings.cookie_name
        secure = not app.state.settings.dev_localhost
        response = Response(status_code=204)
        response.delete_cookie(cookie, path="/", secure=secure, httponly=True, samesite="strict")
        return response

    @app.get("/api/status")
    async def status(request: Request):
        session = app.state.auth.authenticate(session_cookie(request.scope))
        coordinator = app.state.coordinator
        return {"protocol_version": "1.0", "config_id": coordinator.worker.config_id,
                "model_ready": coordinator.worker.ready, "busy": coordinator.busy,
                "max_active": 1, "max_utterance_ms": 60000,
                "connection_available": session.connection_id is None and len(coordinator.connections) < 4}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        coordinator = app.state.coordinator
        try:
            validate_transport(ws.scope, app.state.settings, require_origin=True)
            if SUBPROTOCOL not in ws.scope.get("subprotocols", []):
                raise AuthError("BAD_REQUEST", 400)
            session = app.state.auth.authenticate(session_cookie(ws.scope))
            connection = coordinator.connect(session)
        except AuthError as exc:
            await ws.send_denial_response(failure(exc.code, exc.status, exc.retry))
            return
        tasks = []
        try:
            await ws.accept(subprotocol=SUBPROTOCOL)

            async def sender():
                while (payload := await connection.outbox.take()) is not None:
                    await asyncio.wait_for(ws.send_json(payload), timeout=1.5)
                await asyncio.wait_for(ws.close(connection.outbox.close_code or 1000), timeout=0.5)

            async def receiver():
                while True:
                    event = await ws.receive()
                    if event["type"] == "websocket.disconnect":
                        return
                    data = event.get("bytes")
                    if data is None:
                        data = event.get("text", "")
                    coordinator.receive(connection, data)

            tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, OSError, RuntimeError, TimeoutError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            coordinator.disconnect(connection)

    @app.get("/")
    async def index():
        path = app.state.settings.static_dir / "index.html"
        if not path.is_file():
            return failure("STATIC_NOT_READY", 503)
        return FileResponse(path, media_type="text/html")

    @app.get("/{asset_path:path}")
    async def static_file(asset_path: str):
        root: Path = app.state.settings.static_dir.resolve()
        path = (root / asset_path).resolve()
        if asset_path.startswith(("api/", "ws")) or any(part.startswith(".") for part in Path(asset_path).parts):
            return failure("NOT_FOUND", 404)
        if not path.is_relative_to(root) or not path.is_file():
            return failure("NOT_FOUND", 404)
        return FileResponse(path)
