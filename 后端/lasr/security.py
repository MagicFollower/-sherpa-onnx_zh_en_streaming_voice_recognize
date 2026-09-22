"""Host、Origin及传输安全校验在配对和WS升级之前执行，不信任代理头。"""
from __future__ import annotations

from starlette.responses import JSONResponse

from .auth import AuthError
from .config import COOKIE_PLAIN, COOKIE_SECURE, Settings, authority, loopback, origin


SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "worker-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'; object-src 'none'",
}


def failure(code: str, status: int, retry: int | None = None) -> JSONResponse:
    body = {"code": code, "message": "请求未能完成，请检查授权、来源或配置。"}
    headers = dict(SECURITY_HEADERS)
    if retry is not None:
        body["retry_after_seconds"] = retry
        headers["Retry-After"] = str(retry)
    return JSONResponse(body, status_code=status, headers=headers)


def header_values(scope: dict, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope["headers"] if key.lower() == name]


def validate_transport(scope: dict, settings: Settings, *, require_origin: bool) -> None:
    scheme = {"ws": "http", "wss": "https"}.get(scope["scheme"], scope["scheme"])
    if scheme != "https":
        peer = scope.get("client")
        if not settings.dev_localhost or scheme != "http" or not peer or not loopback(peer[0]):
            raise AuthError("ORIGIN_FORBIDDEN", 403)
    values = header_values(scope, b"host")
    try:
        if len(values) != 1:
            raise ValueError()
        host = authority(values[0], scheme)
        if host not in {authority(value, scheme) for value in settings.allowed_hosts}:
            raise ValueError()
        if settings.dev_localhost and not loopback(host[0]):
            raise ValueError()
    except ValueError:
        raise AuthError("HOST_FORBIDDEN", 403) from None
    origins = header_values(scope, b"origin")
    if not origins and not require_origin:
        return
    try:
        if len(origins) != 1:
            raise ValueError()
        parsed = origin(origins[0])
        if settings.dev_localhost:
            # 开发模式：后端仅绑定 loopback，Vite 代理转发请求的 TCP 对端必为
            # loopback。Origin 可以是 localhost（本机浏览器）或局域网 IP（手机经
            # Vite 代理访问），安全性已由 loopback 绑定保证，不再额外校验 Origin。
            pass
        else:
            if parsed not in {origin(value) for value in settings.allowed_origins}:
                raise ValueError()
            if parsed != (scheme, *host):
                raise ValueError()
    except ValueError:
        raise AuthError("ORIGIN_FORBIDDEN", 403) from None


def session_cookie(scope: dict) -> str | None:
    """重复同名授权cookie拒绝，避免多个解析层选择不同身份；不记录原始头。
    同时匹配__Host-前缀及普通名，兼容生产与开发模式。"""
    matches = []
    for value in header_values(scope, b"cookie"):
        for part in value.split(";"):
            key, separator, token = part.strip().partition("=")
            if separator and key in (COOKIE_SECURE, COOKIE_PLAIN):
                matches.append(token)
    return matches[0] if len(matches) == 1 else None


class SecurityMiddleware:
    def __init__(self, app, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        methods = {"/": "GET", "/health": "GET", "/api/pair": "POST",
                   "/api/logout": "POST", "/api/status": "GET"}
        expected = methods.get(scope["path"])
        try:
            validate_transport(scope, self.settings,
                               require_origin=expected == "POST" and scope["method"] == "POST")
        except AuthError as exc:
            await failure(exc.code, exc.status, exc.retry)(scope, receive, send)
            return
        if expected and scope["method"] != expected:
            response = failure("METHOD_NOT_ALLOWED", 405)
            response.headers["Allow"] = expected
            await response(scope, receive, send)
            return

        async def secure_send(event):
            if event["type"] == "http.response.start":
                headers = list(event.get("headers", []))
                keys = {key.lower() for key, _ in headers}
                headers.extend((key.lower().encode(), value.encode())
                               for key, value in SECURITY_HEADERS.items() if key.lower().encode() not in keys)
                event = dict(event, headers=headers)
            await send(event)
        await self.app(scope, receive, secure_send)
