from copy import deepcopy
import gzip
import json
import struct
import unittest

from app.services.realtime_voice_context import (
    RealtimeContextFrames, RealtimeContextError, _parse_config_frame,
    issue_context_grant, role_hash, verify_context_grant,
)


def frame(event, config, session="provider-session", compressed=True):
    data = json.dumps(config, ensure_ascii=False).encode()
    if compressed:
        data = gzip.compress(data)
    sid = session.encode()
    return bytes([0x11, 0x14, 0x11 if compressed else 0x10, 0]) + struct.pack(">II", event, len(sid)) + sid + struct.pack(">I", len(data)) + data


class ContextFramesTests(unittest.TestCase):
    def setUp(self):
        self.lease = dict(ticketId="ticket-a", productSessionId="product-a", userId="owner-a",
                          authorityEpoch=3, memoryRevision=7, contextUpdateKey="ab" * 32,
                          providerContextHash=role_hash("original"))
        self.config = {"dialog": {"system_role": "original", "speaking_style": "gentle",
                                  "extra": {"model": "1.2.1.1", "volc_websearch_api_key": "test-only"}},
                       "tts": {"speaker": "original-speaker", "audio_config": {"sample_rate": 24000}}}
        self.frames = RealtimeContextFrames(self.lease, clock=lambda: 100)
        self.start = frame(100, self.config)

    def grant(self, **kwargs):
        return issue_context_grant(self.lease, role="related formal facts",
                                   previous_hash=self.lease["providerContextHash"],
                                   sequence=1, now=99, **kwargs)

    def test_applied_grant_preserves_original_provider_configuration(self):
        self.assertEqual(self.frames.transform(self.start), self.start)
        grant = self.grant()
        updated = self.frames.transform(frame(201, {"dialog": {"system_role": grant["envelope"]}}))
        result = _parse_config_frame(updated)[-1]
        expected = deepcopy(self.config)
        expected["dialog"]["system_role"] = "related formal facts"
        self.assertEqual(result, expected)
        self.assertNotIn("DJ_CONTEXT", json.dumps(result))

    def test_unbound_legacy_frame_is_unchanged(self):
        data = frame(201, {"dialog": {"system_role": "legacy"}})
        self.assertEqual(RealtimeContextFrames({}).transform(data), data)

    def test_unsigned_and_tampered_and_replayed_grants_are_dropped(self):
        self.frames.transform(self.start)
        grant = self.grant()["envelope"]
        for value in ("raw arbitrary prompt", grant[:-3] + "abc"):
            self.assertIsNone(self.frames.transform(frame(201, {"dialog": {"system_role": value}})))
        data = frame(201, {"dialog": {"system_role": grant}})
        self.assertIsNotNone(self.frames.transform(data))
        self.assertIsNone(self.frames.transform(data))
        self.assertEqual(self.frames.next_sequence, 2)

    def test_expired_future_cross_ticket_revision_epoch_and_sequence(self):
        grant = self.grant()["envelope"]
        for patch in ({"ticketId": "other"}, {"productSessionId": "other"},
                      {"authorityEpoch": 4}, {"memoryRevision": 8}, {"contextUpdateKey": "cd" * 32}):
            with self.subTest(patch=patch), self.assertRaises(RealtimeContextError):
                verify_context_grant(grant, {**self.lease, **patch}, previous_hash=self.lease["providerContextHash"], sequence=1, now=100)
        for now, seq, previous in ((104, 1, self.lease["providerContextHash"]),
                                   (98, 1, self.lease["providerContextHash"]),
                                   (100, 2, self.lease["providerContextHash"]),
                                   (100, 1, role_hash("stale"))):
            with self.subTest(now=now, seq=seq), self.assertRaises(RealtimeContextError):
                verify_context_grant(grant, self.lease, previous_hash=previous, sequence=seq, now=now)

    def test_provider_session_and_configuration_injection_rejected(self):
        self.frames.transform(self.start)
        config = {"dialog": {"system_role": self.grant()["envelope"]}}
        self.assertIsNone(self.frames.transform(frame(201, config, session="different")))
        self.assertIsNone(self.frames.transform(frame(201, {**config, "tts": {"speaker": "evil"}})))
        self.assertEqual(self.frames.next_sequence, 1)

    def test_audio_is_byte_identical_even_after_invalid_update(self):
        self.frames.transform(self.start)
        self.frames.transform(frame(201, {"dialog": {"system_role": "bad"}}))
        for data in (bytes([0x11, 0x24, 0, 0]) + b"audio" * 100, b"\x00\x00\x01", frame(200, {"pcm": "opaque"})):
            self.assertEqual(self.frames.transform(data), data)

    def test_out_of_order_and_start_replay_rejected(self):
        self.assertIsNone(self.frames.transform(frame(201, {"dialog": {"system_role": self.grant()["envelope"]}})))
        self.frames.transform(self.start)
        self.assertIsNone(self.frames.transform(self.start))
        wrong = deepcopy(self.config); wrong["dialog"]["system_role"] = "wrong"
        self.assertIsNone(RealtimeContextFrames(self.lease).transform(frame(100, wrong)))

    def test_decompression_and_truncation_limits(self):
        self.frames.transform(self.start)
        oversized = frame(201, {"dialog": {"system_role": "x" * 70000}})
        self.assertIsNone(self.frames.transform(oversized))
        good = frame(201, {"dialog": {"system_role": self.grant()["envelope"]}})
        for truncated in (good[:8], good[:-1], good + b"trailing"):
            self.assertIsNone(self.frames.transform(truncated))

    def test_role_budget_whole_utf8_and_update_budget(self):
        for role, seq in (("中" * 3000, 1), ("ok", 129), ("ok", True)):
            with self.assertRaises(RealtimeContextError):
                issue_context_grant(self.lease, role=role, previous_hash=self.lease["providerContextHash"], sequence=seq)

    def test_second_update_uses_previous_applied_hash(self):
        self.frames.transform(self.start)
        first = self.grant()
        self.frames.transform(frame(201, {"dialog": {"system_role": first["envelope"]}}))
        second = issue_context_grant(self.lease, role="new topic", previous_hash=first["providerContextHash"], sequence=2, now=99)
        self.assertIsNotNone(self.frames.transform(frame(201, {"dialog": {"system_role": second["envelope"]}}, compressed=False)))
        self.assertEqual(self.frames.next_sequence, 3)
