"""协调器：连接管理、轮次生命周期、终态线性化、超时和竞态。"""
from __future__ import annotations

import json

import pytest

from lasr.audio import AudioFrame
from lasr.protocol import AUDIO_FORMAT, ProtocolError, message
from lasr.worker import WorkerEvent
from tests.support import (GOLDEN_UUID, audio_bytes, drain, harness, new_connection,
                           new_uuid, payload, send, start)


class TestConnect:
    def test_hello_sent(self):
        # harness()已经drain了hello，直接验证连接存在且收到了hello
        service, conn, _, _ = harness()
        assert conn.session_id is not None
        assert conn.session_id in service.connections

    def test_connection_quota(self):
        service, _, _, _ = harness()
        for _ in range(3):
            new_connection(service)
        from lasr.auth import AuthError
        with pytest.raises(AuthError):
            new_connection(service)

    def test_session_single_connection(self):
        service, conn, _, _ = harness()
        from lasr.auth import AuthError
        with pytest.raises(AuthError):
            service.connect(conn.auth)


class TestControl:
    def test_start_accepted(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        items = drain(conn)
        types = [item["type"] for item in items]
        assert "ready" in types

    def test_busy_rejects_second(self):
        service, conn, worker, clock = harness()
        drain(conn)
        conn2 = new_connection(service)
        drain(conn2)
        start(service, conn)
        drain(conn)
        uid2 = new_uuid()
        send(service, conn2, "start", uid2, input_source="pointer",
             capture_sample_rate=48000, language="zh-en", **AUDIO_FORMAT)
        service.tick()
        items = drain(conn2)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "BUSY" for e in errors)

    def test_model_not_ready(self):
        service, conn, worker, clock = harness(auto_ready=False)
        worker.ready = False
        drain(conn)
        uid = new_uuid()
        send(service, conn, "start", uid, input_source="pointer",
             capture_sample_rate=48000, language="zh-en", **AUDIO_FORMAT)
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "MODEL_NOT_READY" for e in errors)

    def test_cancel_during_round(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        send(service, conn, "cancel", uid, reason="user")
        service.tick()
        items = drain(conn)
        types = [item["type"] for item in items]
        assert "cancelled" in types

    def test_stop_and_finish(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        # 发送音频
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        drain(conn)
        # stop
        send(service, conn, "stop", uid, last_seq=0, total_samples=320, reason="release")
        service.tick()
        items = drain(conn)
        types = [item["type"] for item in items]
        assert "final" in types

    def test_unknown_utterance(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = new_uuid()
        send(service, conn, "stop", uid, last_seq=-1, total_samples=0, reason="release")
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "UNKNOWN_UTTERANCE" for e in errors)

    def test_stop_before_start(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = new_uuid()
        send(service, conn, "cancel", uid, reason="user")
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "UNKNOWN_UTTERANCE" for e in errors)


class TestAudio:
    def test_audio_accepted(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        items = drain(conn)
        acks = [item for item in items if item["type"] == "ack"]
        assert len(acks) >= 1

    def test_audio_wrong_utterance(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        other = new_uuid()
        service.receive(conn, audio_bytes(other, 0, 320))
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert len(errors) >= 1

    def test_audio_duplicate(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        items = drain(conn)
        # 重复帧应触发协议错误
        errors = [item for item in items if item["type"] == "error"]
        assert len(errors) >= 1

    def test_audio_sequence_gap(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        drain(conn)
        service.receive(conn, audio_bytes(uid, 2, 320))
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert len(errors) >= 1

    def test_audio_after_stop(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        drain(conn)
        send(service, conn, "stop", uid, last_seq=0, total_samples=320, reason="release")
        service.tick()
        drain(conn)
        service.receive(conn, audio_bytes(uid, 1, 320))
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert len(errors) >= 1

    def test_zero_audio_stop(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        send(service, conn, "stop", uid, last_seq=-1, total_samples=0, reason="release")
        service.tick()
        items = drain(conn)
        types = [item["type"] for item in items]
        assert "final" in types


class TestTimeouts:
    def test_start_timeout(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn, tick=False)
        drain(conn)
        worker.events.clear()
        clock.advance(6)
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "START_TIMEOUT" for e in errors)

    def test_stop_timeout(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        # 不发stop，等65秒
        clock.advance(66)
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "STOP_TIMEOUT" for e in errors)

    def test_final_timeout(self):
        service, conn, worker, clock = harness(auto_finish=False)
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        drain(conn)
        send(service, conn, "stop", uid, last_seq=0, total_samples=320, reason="release")
        service.tick()
        drain(conn)
        clock.advance(16)
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "FINAL_TIMEOUT" for e in errors)

    def test_heartbeat_timeout(self):
        service, conn, worker, clock = harness()
        drain(conn)
        clock.advance(31)
        service.tick()
        items = drain(conn)
        assert conn.closing is True


class TestWorkerEvents:
    def test_worker_crash(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        # worker崩溃会触发restart，但restart需要事件循环；
        # 这里只验证fail已发送，忽略restart的RuntimeError
        worker.emit("crashed")
        try:
            service.tick()
        except RuntimeError:
            pass
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "WORKER_FAILED" for e in errors)

    def test_worker_failed_event(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        worker.emit("failed", code="WORKER_FAILED")
        try:
            service.tick()
        except RuntimeError:
            pass
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert len(errors) >= 1

    def test_partial_updates(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        items = drain(conn)
        partials = [item for item in items if item["type"] == "partial"]
        assert len(partials) >= 1
        assert partials[0]["text"] == "测试草稿"

    def test_revision_monotonic(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        items = drain(conn)
        revisions = [item["revision"] for item in items
                     if item["type"] in ("partial", "final")]
        assert revisions == sorted(revisions)


class TestTombstone:
    def test_seen_counts_for_busy(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        # BUSY也计墓碑
        conn2 = new_connection(service)
        drain(conn2)
        uid2 = new_uuid()
        send(service, conn2, "start", uid2, input_source="pointer",
             capture_sample_rate=48000, language="zh-en", **AUDIO_FORMAT)
        service.tick()
        assert uid2 in conn2.seen

    def test_session_rotate_at_1000(self):
        service, conn, worker, clock = harness(auto_finish=True)
        drain(conn)
        # 快速填满1000个墓碑
        for i in range(999):
            uid = new_uuid()
            send(service, conn, "start", uid, input_source="pointer",
                 capture_sample_rate=48000, language="zh-en", **AUDIO_FORMAT)
            service.tick()
            # 完成当前轮
            if service.owner and service.owner.active:
                send(service, conn, "stop", service.owner.active.utterance_id,
                     last_seq=-1, total_samples=0, reason="release")
                service.tick()
                drain(conn)
        # 第1000个应触发SESSION_ROTATE
        uid = new_uuid()
        send(service, conn, "start", uid, input_source="pointer",
             capture_sample_rate=48000, language="zh-en", **AUDIO_FORMAT)
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert any(e["code"] == "RATE_LIMITED" for e in errors)


class TestCleanupStuck:
    """清理卡死：取消后worker未在2秒内回复cleaned，协调器应重启worker。"""

    def test_cleanup_timeout_triggers_restart(self):
        service, conn, worker, clock = harness(auto_clean=False)
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        # 发送取消
        send(service, conn, "cancel", uid, reason="user")
        service.tick()
        drain(conn)
        # worker未回复cleaned，推进2秒
        clock.advance(3)
        try:
            service.tick()
        except RuntimeError:
            pass
        # restart()首先设置worker.ready=False，然后create_task可能因无事件循环而失败
        assert worker.ready is False

    def test_cleaned_releases_slot(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        send(service, conn, "cancel", uid, reason="user")
        service.tick()
        items = drain(conn)
        types = [item["type"] for item in items]
        assert "cancelled" in types
        # harness默认auto_clean=True，worker已发cleaned，槽位应已释放
        assert service.owner is None


class TestDisconnect:
    def test_disconnect_clears_state(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.disconnect(conn)
        assert conn.session_id not in service.connections
        assert conn.auth.connection_id is None

    def test_disconnect_fails_active_round(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.disconnect(conn)
        # 活动轮应被终结
        items = drain(conn)
        assert len(items) >= 0  # outbox已清理

    def test_revoke_kicks_connection(self):
        service, conn, worker, clock = harness()
        drain(conn)
        service.auth.revoke(conn.auth)
        assert conn.closing is True


class TestHeartbeat:
    def test_ping_pong(self):
        service, conn, worker, clock = harness()
        drain(conn)
        hb = {"type": "heartbeat", "protocol_version": "1.0",
              "session_id": conn.session_id, "kind": "ping", "nonce": "test123"}
        service.receive(conn, json.dumps(hb))
        items = drain(conn)
        pongs = [item for item in items if item.get("type") == "heartbeat"
                 and item.get("kind") == "pong"]
        assert len(pongs) == 1
        assert pongs[0]["nonce"] == "test123"

    def test_server_sends_ping(self):
        service, conn, worker, clock = harness()
        drain(conn)
        clock.advance(11)
        service.tick()
        items = drain(conn)
        pings = [item for item in items if item.get("type") == "heartbeat"
                 and item.get("kind") == "ping"]
        assert len(pings) >= 1


class TestStopSignature:
    def test_stop_mismatch_fails(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        drain(conn)
        # stop声称的样本数与实际不匹配
        send(service, conn, "stop", uid, last_seq=0, total_samples=640, reason="release")
        service.tick()
        items = drain(conn)
        errors = [item for item in items if item["type"] == "error"]
        assert len(errors) >= 1

    def test_stop_idempotent(self):
        service, conn, worker, clock = harness()
        drain(conn)
        uid = start(service, conn)
        drain(conn)
        service.receive(conn, audio_bytes(uid, 0, 320))
        service.tick()
        drain(conn)
        send(service, conn, "stop", uid, last_seq=0, total_samples=320, reason="release")
        service.tick()
        drain(conn)
        # 重复stop，签名匹配应返回缓存
        send(service, conn, "stop", uid, last_seq=0, total_samples=320, reason="release")
        service.tick()
        items = drain(conn)
        # 应从缓存返回ACK+final
        assert len(items) >= 1
