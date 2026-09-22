"""本机运行入口。模型准备必须显式执行；serve离线运行且永不选择假识别。

开发示例：python -m lasr serve --host localhost --port 8765 --dev-localhost --no-console
正式LAN必须传入--tls-cert、--tls-key和精确--allowed-host/--allowed-origin。
服务固定一个Uvicorn进程；首次口令仅受控终端显示，不写普通日志。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import DEFAULT_MODEL, ROOT
from .config import Settings


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = root.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动真实本地识别服务（不下载模型）")
    serve.add_argument("--host", default="localhost")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--dev-localhost", action="store_true", help="仅loopback明文开发；LAN禁止使用")
    serve.add_argument("--no-console", action="store_true", help="不读取stdin，仍一次显示首次配对口令")
    serve.add_argument("--allowed-host", action="append", default=[])
    serve.add_argument("--allowed-origin", action="append", default=[])
    serve.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    serve.add_argument("--static-dir", type=Path, default=ROOT / "前端" / "dist")
    serve.add_argument("--cpu-threads", type=int, default=1)
    serve.add_argument("--tls-cert", type=Path)
    serve.add_argument("--tls-key", type=Path)
    prepare = sub.add_parser("prepare-models", help="显式联网校验下载固定四文件及可选演示音频，不执行推理")
    prepare.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    prepare.add_argument("--audio", action="store_true")
    prepare.add_argument("--verify-only", action="store_true")
    tls = sub.add_parser("certificates", help="生成项目私有CA/服务器链，不安装系统信任")
    tls.add_argument("--output", type=Path, default=ROOT / ".cache" / "backend" / "tls")
    tls.add_argument("--san", action="append", required=True, help="可重复指定DNS名称或IP")
    tls.add_argument("--days", type=int, default=90)
    return root


def main(argv: list[str] | None = None) -> None:
    root = parser()
    args = root.parse_args(argv)
    if args.command == "prepare-models":
        from .artifacts import load_manifest
        from .prepare_models import fetch_tree, prepare_audio, prepare_model
        if args.verify_only:
            manifest = load_manifest(args.model_dir)
        else:
            tree = fetch_tree()
            manifest = prepare_model(args.model_dir, tree)
            if args.audio:
                prepare_audio(tree)
        print(json.dumps({"revision": manifest["revision"], "metadata": manifest["encoder_metadata"]}))
        return
    if args.command == "certificates":
        from .tls import generate_certificates
        print(json.dumps(generate_certificates(args.output, args.san, args.days), ensure_ascii=False))
        return
    host = f"[{args.host}]" if ":" in args.host else args.host
    authority = f"{host}:{args.port}"
    scheme = "http" if args.dev_localhost else "https"
    settings = Settings(host=args.host, port=args.port, dev_localhost=args.dev_localhost,
                        allowed_hosts=tuple(args.allowed_host or [authority]),
                        allowed_origins=tuple(args.allowed_origin or [f"{scheme}://{authority}"]),
                        model_dir=args.model_dir.resolve(), static_dir=args.static_dir.resolve(),
                        cpu_threads=args.cpu_threads, tls_cert=args.tls_cert, tls_key=args.tls_key,
                        no_console=args.no_console)
    try:
        settings.validate()
    except ValueError as exc:
        root.error(str(exc))
    from .app import create_app
    import uvicorn
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                workers=1, reload=False, proxy_headers=False, server_header=False,
                access_log=False, log_level="warning", ws="websockets",
                ws_max_size=16384, ws_max_queue=4, ws_per_message_deflate=False,
                ws_ping_interval=10, ws_ping_timeout=30, timeout_keep_alive=5,
                ssl_certfile=str(settings.tls_cert) if settings.tls_cert else None,
                ssl_keyfile=str(settings.tls_key) if settings.tls_key else None)
