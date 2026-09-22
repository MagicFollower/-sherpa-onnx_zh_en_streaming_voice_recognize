"""协议消息严格解析：拒绝Python隐式类型、非有限浮点和超深嵌套。"""
from __future__ import annotations

import json
import math

import pytest

from lasr.protocol import (AUDIO_FORMAT, CANCEL_REASONS, MAX_JSON, MAX_SAFE_INT, VERSION,
                           ProtocolError, error_message, integer, json_object, message,
                           parse_client, uuid4)

SID = "10112233-4455-4677-8899-aabbccddeeff"
UID = "00112233-4455-4677-8899-aabbccddeeff"


def _start(**overrides):
    base = {"type": "start", "protocol_version": VERSION, "session_id": SID,
            "utterance_id": UID, "input_source": "pointer",
            "capture_sample_rate": 48000, "language": "zh-en", **AUDIO_FORMAT}
    base.update(overrides)
    return base


def _stop(**overrides):
    base = {"type": "stop", "protocol_version": VERSION, "session_id": SID,
            "utterance_id": UID, "last_seq": 0, "total_samples": 320, "reason": "release"}
    base.update(overrides)
    return base


def _cancel(**overrides):
    base = {"type": "cancel", "protocol_version": VERSION, "session_id": SID,
            "utterance_id": UID, "reason": "user"}
    base.update(overrides)
    return base


class TestInteger:
    def test_valid(self):
        assert integer(0) == 0
        assert integer(MAX_SAFE_INT) == MAX_SAFE_INT

    def test_rejects_float(self):
        with pytest.raises(ProtocolError):
            integer(1.0)

    def test_rejects_bool(self):
        with pytest.raises(ProtocolError):
            integer(True)

    def test_rejects_negative(self):
        with pytest.raises(ProtocolError):
            integer(-1)

    def test_rejects_over_max(self):
        with pytest.raises(ProtocolError):
            integer(MAX_SAFE_INT + 1)

    def test_custom_range(self):
        assert integer(-1, -1, 10) == -1
        with pytest.raises(ProtocolError):
            integer(-2, -1, 10)


class TestUuid4:
    def test_valid(self):
        assert uuid4(UID) == UID

    def test_rejects_uppercase(self):
        with pytest.raises(ProtocolError):
            uuid4(UID.upper())

    def test_rejects_wrong_version(self):
        with pytest.raises(ProtocolError):
            uuid4("00112233-4455-1677-8899-aabbccddeeff")

    def test_rejects_wrong_length(self):
        with pytest.raises(ProtocolError):
            uuid4("short")

    def test_rejects_non_string(self):
        with pytest.raises(ProtocolError):
            uuid4(123)


class TestJson:
    def test_valid_object(self):
        result = json_object('{"a": 1}')
        assert result == {"a": 1}

    def test_rejects_too_large(self):
        with pytest.raises(ProtocolError):
            json_object('{"v": "' + '汉' * 6000 + '"}')

    def test_rejects_array_root(self):
        with pytest.raises(ProtocolError):
            json_object("[]")

    def test_rejects_depth_5(self):
        with pytest.raises(ProtocolError):
            json_object('{"a": {"b": {"c": {"d": {"e": 1}}}}}')

    def test_accepts_depth_4(self):
        result = json_object('{"a": {"b": {"c": {}}}}')
        assert "a" in result

    def test_rejects_nan(self):
        raw = json.dumps({"v": float("nan")})
        with pytest.raises(ProtocolError):
            json_object(raw)

    def test_rejects_infinity(self):
        raw = json.dumps({"v": float("inf")})
        with pytest.raises(ProtocolError):
            json_object(raw)

    def test_rejects_duplicate_keys(self):
        with pytest.raises(ProtocolError):
            json_object('{"a": 1, "a": 2}')

    def test_bytes_reencode_check(self):
        # 确保重新序列化后UTF-8字节也不超限。
        big = {"v": "汉" * 5400}
        raw = json.dumps(big, ensure_ascii=False)
        assert len(raw.encode("utf-8")) <= MAX_JSON
        result = json_object(raw)
        assert "v" in result


class TestParseClient:
    def test_start_valid(self):
        data = parse_client(json.dumps(_start()), SID)
        assert data["type"] == "start"
        assert data["input_source"] == "pointer"

    def test_stop_valid(self):
        data = parse_client(json.dumps(_stop()), SID)
        assert data["type"] == "stop"
        assert data["last_seq"] == 0

    def test_cancel_valid(self):
        data = parse_client(json.dumps(_cancel()), SID)
        assert data["type"] == "cancel"
        assert data["reason"] == "user"

    def test_heartbeat_ping(self):
        data = {"type": "heartbeat", "protocol_version": VERSION, "session_id": SID,
                "kind": "ping", "nonce": "abc123"}
        result = parse_client(json.dumps(data), SID)
        assert result["kind"] == "ping"

    def test_heartbeat_pong(self):
        data = {"type": "heartbeat", "protocol_version": VERSION, "session_id": SID,
                "kind": "pong", "nonce": "x" * 32}
        result = parse_client(json.dumps(data), SID)
        assert result["kind"] == "pong"

    def test_wrong_version(self):
        with pytest.raises(ProtocolError) as exc:
            parse_client(json.dumps(_start(protocol_version="2.0")), SID)
        assert exc.value.code == "PROTOCOL_VERSION"

    def test_wrong_session(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_start()), "other-session-id-0000-000000000000")

    def test_unknown_type(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps({"type": "unknown", "protocol_version": VERSION,
                                     "session_id": SID, "utterance_id": UID}), SID)

    def test_start_rejects_keyboard_source(self):
        # keyboard是合法input_source
        data = _start(input_source="keyboard")
        result = parse_client(json.dumps(data), SID)
        assert result["input_source"] == "keyboard"

    def test_start_rejects_invalid_source(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_start(input_source="voice")), SID)

    def test_start_rejects_wrong_language(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_start(language="en")), SID)

    def test_start_rejects_wrong_sample_rate(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_start(sample_rate=44100)), SID)

    def test_start_rejects_wrong_format(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_start(format="float32")), SID)

    def test_start_rejects_wrong_channels(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_start(channels=2)), SID)

    def test_stop_zero_audio(self):
        data = _stop(last_seq=-1, total_samples=0)
        result = parse_client(json.dumps(data), SID)
        assert result["last_seq"] == -1

    def test_stop_rejects_bad_reason(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_stop(reason="timeout")), SID)

    def test_stop_rejects_negative_total(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_stop(total_samples=-1)), SID)

    def test_cancel_rejects_unknown_reason(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps(_cancel(reason="unknown")), SID)

    def test_cancel_all_valid_reasons(self):
        for reason in CANCEL_REASONS:
            data = _cancel(reason=reason)
            result = parse_client(json.dumps(data), SID)
            assert result["reason"] == reason

    def test_heartbeat_rejects_bad_kind(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps({"type": "heartbeat", "protocol_version": VERSION,
                                     "session_id": SID, "kind": "ack", "nonce": "x"}), SID)

    def test_heartbeat_rejects_long_nonce(self):
        with pytest.raises(ProtocolError):
            parse_client(json.dumps({"type": "heartbeat", "protocol_version": VERSION,
                                     "session_id": SID, "kind": "ping",
                                     "nonce": "x" * 33}), SID)

    def test_bytes_input(self):
        raw = json.dumps(_start()).encode("utf-8")
        result = parse_client(raw, SID)
        assert result["type"] == "start"


class TestMessage:
    def test_basic(self):
        msg = message("hello", SID, model_ready=True)
        assert msg["type"] == "hello"
        assert msg["protocol_version"] == VERSION
        assert msg["session_id"] == SID
        assert msg["model_ready"] is True

    def test_with_utterance(self):
        msg = message("ready", SID, UID, config_id="test")
        assert msg["utterance_id"] == UID


class TestErrorMessage:
    def test_busy(self):
        msg = error_message(SID, "BUSY")
        assert msg["type"] == "error"
        assert msg["code"] == "BUSY"
        assert msg["retryable"] is True
        assert msg["action"] == "retry_new"
        assert msg["terminal"] is False

    def test_protocol_error(self):
        msg = error_message(SID, "PROTOCOL_ERROR")
        assert msg["action"] == "configure"
        assert msg["retryable"] is False

    def test_session_rotate(self):
        msg = error_message(SID, "SESSION_ROTATE")
        assert msg["action"] == "reconnect"
        assert msg["retryable"] is True

    def test_terminal_error(self):
        msg = error_message(SID, "WORKER_FAILED", "utterance", UID, terminal=True)
        assert msg["terminal"] is True
        assert msg["scope"] == "utterance"
        assert msg["utterance_id"] == UID

    def test_unknown_code_defaults(self):
        msg = error_message(SID, "SOMETHING_NEW")
        # 未知码默认action为retry_new，retryable为True
        assert msg["retryable"] is True
        assert msg["action"] == "retry_new"
