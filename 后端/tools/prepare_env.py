"""仅准备隔离依赖，不执行测试、构建前端或启动识别服务。

缓存和安装临时目录均限制在工作区；不激活venv、不更改执行策略。
首次解析以pip正常依赖约束为准，随后写入带制品哈希的完整依赖锁。
锁文件只包含安装下载报告中的索引制品，不把本地可编辑项目视为可分发wheel。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
CACHE = BACKEND.parent / ".cache" / "backend"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locked", action="store_true", help="从已有哈希锁安装，不重新解析")
    args = parser.parse_args()
    (CACHE / "tmp").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEMP=str(CACHE / "tmp"), TMP=str(CACHE / "tmp"),
               PIP_CACHE_DIR=str(CACHE / "pip"), PYTHONDONTWRITEBYTECODE="1")
    python = BACKEND / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(BACKEND / ".venv")],
                       check=True, env=env)
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    if args.locked:
        subprocess.run(command + ["--require-hashes", "-r", str(BACKEND / "requirements.lock")],
                       env=env, check=True)
    else:
        report = CACHE / "pip-install.json"
        subprocess.run(command + ["--report", str(report), "-e", f"{BACKEND}[dev]"],
                       env=env, check=True)
        rows = []
        for item in json.loads(report.read_text(encoding="utf-8"))["install"]:
            info = item["download_info"]
            if "archive_info" not in info:
                continue
            sha = info["archive_info"]["hashes"]["sha256"]
            meta = item["metadata"]
            rows.append(f"{meta['name']}=={meta['version']} --hash=sha256:{sha}")
        if rows:
            lock = BACKEND / "requirements.lock"
            if lock.exists():
                raise SystemExit("已有依赖锁，拒绝用不完整安装报告静默覆盖；请使用--locked。")
            lock.write_text("\n".join(sorted(rows, key=str.lower)) + "\n", encoding="utf-8")
    print("依赖准备完成；正式pip check、lint与测试由验证阶段执行。")


if __name__ == "__main__":
    main()
