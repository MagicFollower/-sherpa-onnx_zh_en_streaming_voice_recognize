"""配置只来自本地CLI；禁止请求传入模型路径、假引擎或增强开关。"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from .artifacts import DEFAULT_MODEL, ROOT


def loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def authority(value: str, scheme: str) -> tuple[str, int]:
    """规范化域名/IP和有效端口，不采用前缀匹配、不接受userinfo及通配符。"""
    try:
        url = urlsplit(f"{scheme}://{value}")
        host = url.hostname
        if (not host or url.username is not None or url.password is not None or url.path
                or url.query or url.fragment or any(c.isspace() for c in value)
                or any(c in value for c in "\\*%@")):
            raise ValueError("非法主机")
        if value.endswith(":"):
            raise ValueError("端口为空")
        port = url.port if url.port is not None else (443 if scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError("非法端口")
        return host.lower(), port
    except (ValueError, UnicodeError):
        raise ValueError("主机必须是精确名称或IP及端口") from None


def origin(value: str) -> tuple[str, str, int]:
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or url.path or url.query or url.fragment:
        raise ValueError("Origin必须是精确scheme、主机和端口")
    host, port = authority(url.netloc, url.scheme)
    return url.scheme, host, port


COOKIE_SECURE = "__Host-lasr_session"
COOKIE_PLAIN = "lasr_session"


@dataclass(frozen=True)
class Settings:
    host: str = "localhost"
    port: int = 8765
    dev_localhost: bool = False
    allowed_hosts: tuple[str, ...] = ("localhost:8765",)
    allowed_origins: tuple[str, ...] = ("https://localhost:8765",)
    model_dir: Path = DEFAULT_MODEL
    static_dir: Path = ROOT / "\u524d\u7aef" / "dist"
    cpu_threads: int = 1
    tls_cert: Path | None = None
    tls_key: Path | None = None
    no_console: bool = False

    @property
    def cookie_name(self) -> str:
        """开发模式HTTP明文不能用Secure，故放弃__Host-前缀。"""
        return COOKIE_PLAIN if self.dev_localhost else COOKIE_SECURE

    def validate(self) -> None:
        if not 1 <= self.port <= 65535 or not 1 <= self.cpu_threads <= 32:
            raise ValueError("端口或CPU线程配置非法")
        if self.dev_localhost and not loopback(self.host):
            raise ValueError("明文开发模式只允许loopback绑定")
        if not self.dev_localhost and (not self.tls_cert or not self.tls_key):
            raise ValueError("生产LAN服务必须配置TLS证书链和私钥")
        if not self.allowed_hosts or not self.allowed_origins:
            raise ValueError("必须显式配置Host及Origin白名单")
        scheme = "http" if self.dev_localhost else "https"
        hosts = {authority(value, scheme) for value in self.allowed_hosts}
        for value in self.allowed_origins:
            parsed = origin(value)
            if parsed[0] != scheme or parsed[1:] not in hosts:
                raise ValueError("Origin必须与Host和传输scheme匹配")
            if self.dev_localhost and not loopback(parsed[1]):
                raise ValueError("开发白名单只允许loopback")
