"""LASR 1.0控制消息严格解析，拒绝Python隐式bool/int及JSON非有限数陷阱。

错误只携带固定码，不保存或回显用户输入。未知可选字段可忽略，但仍受整体
UTF-8字节、深度和安全数值限制，防止解析器或后续队列成为无界存储。
"""
from __future__ import annotations

import json
import math
from uuid import UUID

VERSION = "1.0"
SUBPROTOCOL = "lasr.v1"
MAX_JSON = 16384
MAX_TEXT = 8192
MAX_SAFE_INT = 2**53 - 1
AUDIO_FORMAT = {"sample_rate": 16000, "channels": 1, "format": "pcm_s16le", "frame_samples": 320}
CANCEL_REASONS = frozenset({"user", "focus_lost", "page_hidden", "pointer_cancel",
                            "capture_lost", "device_lost", "client_timeout", "client_overload"})
START_KEYS = ("input_source", "capture_sample_rate", "sample_rate", "channels", "format",
              "frame_samples", "language")


class ProtocolError(ValueError):
    def __init__(self, code: str = "PROTOCOL_ERROR"):
        super().__init__(code)
        self.code = code


def integer(value, low: int = 0, high: int = MAX_SAFE_INT) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError()
    return value


def uuid4(value) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ProtocolError()
    try:
        parsed = UUID(value)
    except ValueError:
        raise ProtocolError() from None
    if parsed.version != 4 or str(parsed) != value:
        raise ProtocolError()
    return value


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError()
        result[key] = value
    return result


def _depth(value, depth: int = 1) -> None:
    if isinstance(value, (dict, list)):
        if depth > 4:
            raise ProtocolError()
        children = value.values() if isinstance(value, dict) else value
        for child in children:
            _depth(child, depth + 1)
    elif type(value) is int:
        integer(value, -MAX_SAFE_INT)
    elif type(value) is float and (not math.isfinite(value) or abs(value) > MAX_SAFE_INT):
        raise ProtocolError()


def json_object(text: str | bytes) -> dict:
    try:
        raw = text.encode("utf-8") if isinstance(text, str) else text
        if len(raw) > MAX_JSON:
            raise ProtocolError()
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if not isinstance(result, dict):
            raise ProtocolError()
        _depth(result)
        # 同时验证反转义后的UTF-8（拒绝孤立代理码点）。
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")) > MAX_JSON:
            raise ProtocolError()
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError() from None


def parse_client(text: str | bytes, session_id: str) -> dict:
    data = json_object(text)
    if data.get("protocol_version") != VERSION:
        raise ProtocolError("PROTOCOL_VERSION")
    if data.get("session_id") != session_id:
        raise ProtocolError()
    kind = data.get("type")
    if kind == "heartbeat":
        if data.get("kind") not in ("ping", "pong"):
            raise ProtocolError()
        nonce = data.get("nonce")
        if not isinstance(nonce, str) or len(nonce) > 32:
            raise ProtocolError()
        return data
    if kind not in ("start", "stop", "cancel"):
        raise ProtocolError()
    uuid4(data.get("utterance_id"))
    if kind == "start":
        integer(data.get("capture_sample_rate"), 1)
        if data.get("input_source") not in ("pointer", "keyboard") or data.get("language") != "zh-en":
            raise ProtocolError()
        for key, value in AUDIO_FORMAT.items():
            if type(data.get(key)) is not type(value) or data[key] != value:
                raise ProtocolError()
    elif kind == "stop":
        integer(data.get("last_seq"), -1, 2999)
        integer(data.get("total_samples"), 0, 960000)
        if data.get("reason") not in ("release", "duration_limit"):
            raise ProtocolError()
    elif not isinstance(data.get("reason"), str) or data["reason"] not in CANCEL_REASONS:
        raise ProtocolError()
    return data


def message(message_type: str, session_id: str, utterance_id: str | None = None, **fields) -> dict:
    result = {"type": message_type, "protocol_version": VERSION, "session_id": session_id}
    if utterance_id is not None:
        result["utterance_id"] = utterance_id
    result.update(fields)
    return result


ACTIONS = {"BUSY": "retry_new", "MODEL_NOT_READY": "retry_new", "AUTH_REQUIRED": "pair",
           "PROTOCOL_ERROR": "configure", "PROTOCOL_VERSION": "configure",
           "STALE_UTTERANCE": "none", "UNKNOWN_UTTERANCE": "none",
           "RATE_LIMITED": "reconnect", "SESSION_ROTATE": "reconnect"}
MESSAGES = {"BUSY": "识别资源正在使用，请稍后开始新一轮。",
            "MODEL_NOT_READY": "本地模型尚未就绪。", "AUTH_REQUIRED": "授权已失效，请重新配对。",
            "SESSION_ROTATE": "当前连接已达到轮次上限，请重新连接。",
            "WORKER_FAILED": "本地识别进程异常，本轮未完成。"}


def error_message(session_id: str, code: str, scope: str = "request",
                  utterance_id: str | None = None, terminal: bool = False) -> dict:
    action = ACTIONS.get(code, "retry_new")
    return message("error", session_id, utterance_id, code=code, scope=scope, terminal=terminal,
                   retryable=action in ("retry_new", "reconnect"), action=action,
                   message=MESSAGES.get(code, "请求未能完成，请按提示检查后重新操作。"))
