"""音频帧解析与AudioLedger：UUID原始字节、帧序号连续、水位分离。"""
from __future__ import annotations

import struct
from uuid import UUID

import pytest

from lasr.audio import (BACKLOG_HARD, BACKLOG_WARN, HEADER, MAX_FRAMES, MAX_SAMPLES,
                        AudioFrame, AudioLedger, parse_audio)
from lasr.protocol import ProtocolError

GOLDEN_UUID = "00112233-4455-4677-8899-aabbccddeeff"


def _frame(uid=GOLDEN_UUID, seq=0, samples=320, pcm=None):
    pcm = pcm if pcm is not None else b"\0\0" * samples
    return HEADER.pack(b"LASR", 1, 0, 32, UUID(uid).bytes, seq, samples, 0) + pcm


class TestParseAudio:
    def test_valid_frame(self):
        raw = _frame()
        frame = parse_audio(raw)
        assert frame.utterance_id == GOLDEN_UUID
        assert frame.seq == 0
        assert frame.samples == 320
        assert len(frame.pcm) == 640

    def test_short_tail(self):
        frame = parse_audio(_frame(samples=160))
        assert frame.samples == 160

    def test_single_sample(self):
        frame = parse_audio(_frame(samples=1))
        assert frame.samples == 1

    def test_rejects_bad_magic(self):
        raw = bytearray(_frame())
        raw[0] = ord('X')
        with pytest.raises(ProtocolError):
            parse_audio(bytes(raw))

    def test_rejects_bad_version(self):
        raw = bytearray(_frame())
        raw[4] = 2
        with pytest.raises(ProtocolError):
            parse_audio(bytes(raw))

    def test_rejects_bad_header_length(self):
        raw = bytearray(_frame())
        struct.pack_into("<H", raw, 6, 16)
        with pytest.raises(ProtocolError):
            parse_audio(bytes(raw))

    def test_rejects_nonzero_reserved(self):
        raw = bytearray(_frame())
        struct.pack_into("<H", raw, 30, 1)
        with pytest.raises(ProtocolError):
            parse_audio(bytes(raw))

    def test_rejects_too_small(self):
        with pytest.raises(ProtocolError):
            parse_audio(b"\x00" * 10)

    def test_rejects_too_large(self):
        with pytest.raises(ProtocolError):
            parse_audio(b"\x00" * 700)

    def test_rejects_pcm_mismatch(self):
        raw = _frame(samples=320)
        # 截断PCM使长度不匹配
        with pytest.raises(ProtocolError):
            parse_audio(raw[:40])

    def test_rejects_zero_samples(self):
        with pytest.raises(ProtocolError):
            parse_audio(_frame(samples=0))

    def test_rejects_over_320(self):
        with pytest.raises(ProtocolError):
            parse_audio(_frame(samples=321))

    def test_uuid_canonical_bytes(self):
        """UUID使用规范文本逐字节，不是Windows GUID混合端序。"""
        raw = _frame()
        uid_bytes = raw[8:24]
        parsed = UUID(bytes=uid_bytes)
        assert str(parsed) == GOLDEN_UUID

    def test_rejects_bad_uuid_version(self):
        raw = bytearray(_frame())
        # 修改version字段为1（第7字节的高4位）
        raw[14] = (raw[14] & 0x0F) | 0x10
        with pytest.raises(ProtocolError):
            parse_audio(bytes(raw))

    def test_rejects_bad_flags(self):
        raw = bytearray(_frame())
        raw[5] = 1  # flags非零
        with pytest.raises(ProtocolError):
            parse_audio(bytes(raw))


class TestAudioLedger:
    def test_initial_state(self):
        ledger = AudioLedger()
        assert ledger.received_seq == -1
        assert ledger.received_samples == 0
        assert ledger.processed_seq == -1
        assert ledger.processed_samples == 0

    def test_validate_sequential(self):
        ledger = AudioLedger()
        frame = AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640)
        ledger.validate(frame)
        ledger.commit(frame)
        assert ledger.received_seq == 0
        assert ledger.received_samples == 320

    def test_validate_rejects_duplicate(self):
        ledger = AudioLedger()
        frame = AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640)
        ledger.commit(frame)
        with pytest.raises(ProtocolError) as exc:
            ledger.validate(AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640))
        assert exc.value.code == "AUDIO_DUPLICATE"

    def test_validate_rejects_gap(self):
        ledger = AudioLedger()
        ledger.commit(AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640))
        with pytest.raises(ProtocolError) as exc:
            ledger.validate(AudioFrame(GOLDEN_UUID, 2, 320, b"\0" * 640))
        assert exc.value.code == "AUDIO_SEQUENCE"

    def test_short_tail_blocks_new_frames(self):
        ledger = AudioLedger()
        ledger.commit(AudioFrame(GOLDEN_UUID, 0, 160, b"\0" * 320))
        with pytest.raises(ProtocolError):
            ledger.validate(AudioFrame(GOLDEN_UUID, 1, 320, b"\0" * 640))

    def test_max_frames(self):
        ledger = AudioLedger()
        for i in range(MAX_FRAMES):
            frame = AudioFrame(GOLDEN_UUID, i, 320, b"\0" * 640)
            ledger.validate(frame)
            ledger.commit(frame)
            # 同步推进processed避免触发积压硬限制
            ledger.advance(ledger.received_samples)
        assert ledger.received_samples == MAX_SAMPLES
        with pytest.raises(ProtocolError) as exc:
            ledger.validate(AudioFrame(GOLDEN_UUID, MAX_FRAMES, 320, b"\0" * 640))
        assert exc.value.code == "DURATION_LIMIT"

    def test_backlog_hard_limit(self):
        ledger = AudioLedger()
        # 先提交大量已接收但未处理的帧
        for i in range(100):
            frame = AudioFrame(GOLDEN_UUID, i, 320, b"\0" * 640)
            ledger.validate(frame)
            ledger.commit(frame)
        # received=32000, processed=0, 差值=32000=BACKLOG_HARD
        with pytest.raises(ProtocolError) as exc:
            ledger.validate(AudioFrame(GOLDEN_UUID, 100, 320, b"\0" * 640))
        assert exc.value.code == "OVERLOADED"

    def test_advance_monotonic(self):
        ledger = AudioLedger()
        for i in range(3):
            ledger.commit(AudioFrame(GOLDEN_UUID, i, 320, b"\0" * 640))
        # 推进到第1帧末
        assert ledger.advance(320) is True
        assert ledger.processed_seq == 0
        assert ledger.processed_samples == 320
        # 推进到第2帧末
        assert ledger.advance(640) is True
        assert ledger.processed_seq == 1

    def test_advance_rejects_backward(self):
        ledger = AudioLedger()
        for i in range(3):
            ledger.commit(AudioFrame(GOLDEN_UUID, i, 320, b"\0" * 640))
        ledger.advance(640)
        # 倒退会触发ProtocolError因为coverage < processed_samples
        with pytest.raises(ProtocolError):
            ledger.advance(320)

    def test_advance_rejects_over_received(self):
        ledger = AudioLedger()
        ledger.commit(AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640))
        with pytest.raises(ProtocolError):
            ledger.advance(640)

    def test_advance_rejects_negative_coverage(self):
        ledger = AudioLedger()
        ledger.commit(AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640))
        with pytest.raises(ProtocolError):
            ledger.advance(-1)

    def test_advance_mid_frame(self):
        ledger = AudioLedger()
        ledger.commit(AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640))
        ledger.commit(AudioFrame(GOLDEN_UUID, 1, 160, b"\0" * 320))
        # 覆盖到400（第0帧内）→processed_seq仍为0
        assert ledger.advance(400) is True
        assert ledger.processed_seq == 0
        assert ledger.processed_samples == 320

    def test_ack_fields(self):
        ledger = AudioLedger()
        ledger.commit(AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640))
        ledger.advance(320)
        fields = ledger.ack_fields(stopped=True)
        assert fields["received_seq"] == 0
        assert fields["received_samples"] == 320
        assert fields["processed_seq"] == 0
        assert fields["processed_samples"] == 320
        assert fields["stop_received"] is True

    def test_ack_fields_unstopped(self):
        ledger = AudioLedger()
        ledger.commit(AudioFrame(GOLDEN_UUID, 0, 320, b"\0" * 640))
        fields = ledger.ack_fields(stopped=False)
        assert fields["stop_received"] is False
