"""显式联网准备固定模型及公开演示音频；不加载recognizer、不执行推理。

来源被固定到HF commit；三份ONNX必须同时通过长度和LFS SHA256校验。
下载中断只留下.partial文件，绝不会被运行时启用；完整目录最后原子重命名。
已存在的已验证目录仅复核，不静默覆盖。样例仅留本地cache，不作人工原文或许可保证。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlparse
import wave

from .artifacts import (DEFAULT_MODEL, FILES, REPO, REVISION, ROOT,
                        ArtifactError, load_manifest, sha256)

BASE = f"https://huggingface.co/{REPO}/resolve/{REVISION}"
API = f"https://huggingface.co/api/models/{REPO}/tree/{REVISION}?recursive=true"


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    """仅允许TLS重定向；下载体始终受固定上游哈希与字节数约束。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise ArtifactError("拒绝非HTTPS重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_tree() -> dict:
    opener = urllib.request.build_opener(HTTPSRedirect())
    request = urllib.request.Request(API, headers={"User-Agent": "LASR-asset-preparer/1.0"})
    with opener.open(request, timeout=60) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ArtifactError("制品元数据超限")
    return {entry["path"]: entry for entry in json.loads(data)}


def download(url: str, destination: Path, size: int, upstream_sha: str | None) -> str:
    """按文件原子提交，长度不符或哈希不符不启用；不支持未验证断点续传。

    已有完整文件必须重新核验，不以文件名或HTTP成功状态代替完整性。
    partial可被下次同一准备命令覆盖，已验证目标文件不可被静默覆盖。
    """
    if destination.exists():
        digest = sha256(destination)
        if destination.stat().st_size != size or (upstream_sha and digest != upstream_sha):
            raise ArtifactError("已有制品校验失败，拒绝覆盖")
        return digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    opener = urllib.request.build_opener(HTTPSRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "LASR-asset-preparer/1.0"})
    digest = hashlib.sha256()
    count = 0
    with opener.open(request, timeout=120) as response, temporary.open("wb") as output:
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != size:
            raise ArtifactError("远端Content-Length不符")
        while block := response.read(1024 * 1024):
            count += len(block)
            if count > size:
                raise ArtifactError("制品长度超限")
            digest.update(block)
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    local_sha = digest.hexdigest()
    if count != size or (upstream_sha and local_sha != upstream_sha):
        raise ArtifactError("下载未通过长度或SHA256校验")
    temporary.rename(destination)
    return local_sha


def artifact_record(name: str, entry: dict, target: Path, expected_size: int | None = None) -> dict:
    size = entry["size"]
    if type(size) is not int or size <= 0 or (expected_size is not None and size != expected_size):
        raise ArtifactError("上游文件长度与冻结记录不符")
    upstream = entry.get("lfs", {}).get("oid")
    if name.endswith(".onnx") and (not upstream or len(upstream) != 64):
        raise ArtifactError("ONNX缺少LFS SHA256；禁止误用顶层Git oid")
    digest = download(f"{BASE}/{name}", target, size, upstream)
    return {"size": size, "url": f"{BASE}/{name}", "local_sha256": digest,
            "upstream_lfs_sha256": upstream, "git_blob_oid": entry.get("oid")}


def prepare_model(directory: Path, tree: dict) -> dict:
    if directory.exists():
        return load_manifest(directory)
    staging = directory.with_name(directory.name + ".partial")
    staging.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, size in FILES.items():
        print(f"准备模型制品：{name}", flush=True)
        records[name] = artifact_record(name, tree[name], staging / name, size)
    # 只解析protobuf元数据，不构建计算图执行器，更不执行真实识别。
    import onnx
    model = onnx.load(str(staging / next(iter(FILES))), load_external_data=False)
    metadata = {item.key: item.value for item in model.metadata_props}
    del model
    chunk, context = int(metadata["decode_chunk_len"]), int(metadata["T"])
    if not 0 < chunk <= context <= 10000:
        raise ArtifactError("实际模型chunk元数据不支持")
    manifest = {"repo": REPO, "revision": REVISION, "files": records,
                "encoder_metadata": metadata, "sherpa_onnx": "1.13.8",
                "feature_shift_samples": 160, "enhancements_enabled": False,
                "prepared_at": datetime.now(timezone.utc).isoformat(),
                "integrity_note": "LFS摘要非数字签名；tokens的SHA256仅为本地记录"}
    (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    load_manifest(staging)
    staging.rename(directory)
    return manifest


def prepare_audio(tree: dict) -> dict:
    directory = ROOT / ".cache" / "backend" / "test-audio"
    records = {}
    for name in ["0.wav", "1.wav", "2.wav", "3.wav", "8k.wav"]:
        relative = "test_wavs/" + name
        target = directory / name
        record = artifact_record(relative, tree[relative], target)
        with wave.open(str(target), "rb") as audio:
            record.update(sample_rate=audio.getframerate(), channels=audio.getnchannels(),
                          sample_width=audio.getsampwidth(), frames=audio.getnframes(),
                          compression=audio.getcomptype())
        records[name] = record
    manifest = {"repo": REPO, "revision": REVISION, "files": records,
                "purpose": "仅本地演示冒烟；不是人工金标准；不得默认再分发或报告CER"}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--audio", action="store_true", help="同时准备官方本地演示音频")
    parser.add_argument("--verify-only", action="store_true", help="仅校验本地制品，不联网")
    args = parser.parse_args()
    if args.verify_only:
        manifest = load_manifest(args.model_dir)
    else:
        tree = fetch_tree()
        manifest = prepare_model(args.model_dir, tree)
        if args.audio:
            prepare_audio(tree)
    print(json.dumps({"revision": manifest["revision"],
                      "decode_chunk_len": manifest["encoder_metadata"]["decode_chunk_len"],
                      "T": manifest["encoder_metadata"]["T"],
                      "bytes": sum(item["size"] for item in manifest["files"].values())}))


if __name__ == "__main__":
    main()
