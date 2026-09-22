"""ASGI组合根：生产工厂只能构造真实spawn worker，不提供fake环境变量。

测试通过tests内专用工厂向内部组合函数注入worker/clock；导入app本身无服务、
线程、模型或联网副作用。启动和关闭统一受lifespan管理。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import time

from fastapi import FastAPI

from .auth import AuthStore
from .config import Settings
from .console import LocalConsole
from .coordinator import Coordinator
from .routes import install_routes
from .security import SecurityMiddleware
from .supervisor import ProcessWorker, WorkerPort


def _assemble(settings: Settings, worker: WorkerPort, clock, *, console_enabled: bool) -> FastAPI:
    auth = AuthStore(clock)
    coordinator = Coordinator(worker, auth, clock)
    console = LocalConsole(auth)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await coordinator.open()
            if console_enabled:
                console.start(no_console=settings.no_console)
            yield
        finally:
            console.close()
            await coordinator.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
                  redirect_slashes=False)
    app.state.auth = auth
    app.state.coordinator = coordinator
    app.state.settings = settings
    app.add_middleware(SecurityMiddleware, settings=settings)
    install_routes(app)
    return app


def create_app(settings: Settings) -> FastAPI:
    settings.validate()
    return _assemble(settings, ProcessWorker(settings), time.monotonic, console_enabled=True)
