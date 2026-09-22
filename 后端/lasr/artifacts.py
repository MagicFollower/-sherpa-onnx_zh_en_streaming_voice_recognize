"""固定模型制品约束：只读本地校验与联网准备相互隔离。

LFS摘要是上游发布元数据，不是作者数字签名；tokens仅能记录本地SHA256。
运行时不导入ONNX检查库、不下载文件；校验失败不能改为伪识别。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = "csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
REVISION = "98590b7ed6443e77b714204da2757d75e1a642f4"
MODEL_NAME = "zipformer-bilingual-98590b7e"
FILES = {
    "encoder-epoch-99-avg-1.int8.onnx": 181895032,
    "decoder-epoch-99-avg-1.onnx": 13876452,
    "joiner-epoch-99-avg-1.int8.onnx": 3228404,
    "tokens.txt": 56317,
}
BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DEFAULT_MODEL = ROOT / "models" / MODEL_NAME


class ArtifactError(ValueError):
    """制品未验证或清单与内容不一致；不向网络暴露本地路径。"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def config_id(manifest: dict, threads: int = 1) -> str:
    """配置标识覆盖制品、解码规则和线程数，不含路径或会话秘密。"""
    frozen = {"files": manifest["files"], "metadata": manifest["encoder_metadata"],
              "sherpa": "1.13.8", "threads": threads, "decoding": "greedy_search",
              "enhancements": False, "endpoint": [2.4, 1.2, 300]}
    blob = json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    return "zipformer-" + hashlib.sha256(blob).hexdigest()[:24]


def load_manifest(directory: Path, *, verify_hashes: bool = True) -> dict:
    """只接受四文件固定组合；全哈希验证在worker加载前进行一次。

    模型目录是受控本地配置而非客户端输入。拒绝未完整准备的目录、错误长度、
    metadata缺失或摘要变化，绝不以部分下载目录启动识别。
    """
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["repo"] != REPO or manifest["revision"] != REVISION:
            raise ArtifactError("模型来源版本不匹配")
        if set(manifest["files"]) != set(FILES):
            raise ArtifactError("模型文件组合不匹配")
        metadata = manifest["encoder_metadata"]
        chunk, context = int(metadata["decode_chunk_len"]), int(metadata["T"])
        if not 0 < chunk <= context <= 10000:
            raise ArtifactError("模型chunk元数据非法")
        for name, size in FILES.items():
            item = manifest["files"][name]
            path = directory / name
            if path.stat().st_size != size or item["size"] != size:
                raise ArtifactError("模型长度校验失败")
            digest = item["local_sha256"]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ArtifactError("模型摘要格式非法")
            if name.endswith(".onnx") and item["upstream_lfs_sha256"] != digest:
                raise ArtifactError("上游摘要与本地摘要不同")
            if verify_hashes and sha256(path) != digest:
                raise ArtifactError("模型摘要校验失败")
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ArtifactError("本地模型缺失或校验失败，请显式运行模型准备器") from exc
