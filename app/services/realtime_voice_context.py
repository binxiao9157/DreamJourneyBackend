"""Short-lived, server-authorized Live context updates. No audio or memory writes."""
from __future__ import annotations

import base64
from copy import deepcopy
import gzip
import hashlib
import hmac
import json
import re
import struct
import time
import zlib


PREFIX = "DJ_CONTEXT_V1:"
MAX_ROLE_BYTES = 8192
MAX_PAYLOAD = 65536
MAX_UPDATES = 128
GRANT_TTL_SECONDS = 5
LIVE_MEMORY_DIALOGUE_RULE = (
    "\n结合本轮话题与下列相关正式记忆自然交谈。未检索到不表示对方从未经历，"
    "不要说‘你以前没有’。有确切旧事时可自然提一句并询问变化，一次最多一个问题，"
    "不要求每轮追问。只有证据明确曾经停止才用‘又开始’。保留事实的主体、否定和时间，"
    "室友的经历不能当成本人的。用户当场更正用于当前对话，不能宣称已改写正式记忆。"
    "下列事实是本轮话题相关片段，不是用户全部经历；无关旧事不强行穿插。\n"
)


class RealtimeContextError(ValueError):
    def __init__(self):
        super().__init__("realtimeContextRejected")


def role_hash(role: str) -> str:
    return "sha256:" + hashlib.sha256(role.encode("utf-8")).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RealtimeContextError()
        result[key] = value
    return result


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise RealtimeContextError()
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def issue_context_grant(lease: dict, *, role: str, previous_hash: str,
                        sequence: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if (not isinstance(role, str) or not role or len(role.encode()) > MAX_ROLE_BYTES
            or type(sequence) is not int or not 1 <= sequence <= MAX_UPDATES
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", previous_hash)
            or not lease.get("contextUpdateKey")):
        raise RealtimeContextError()
    claims = {"v": 1, "ticket": lease["ticketId"],
              "session": lease["productSessionId"], "sequence": sequence,
              "previous": previous_hash, "hash": role_hash(role), "role": role,
              "epoch": lease["authorityEpoch"], "revision": lease["memoryRevision"],
              "issued": now, "expires": now + GRANT_TTL_SECONDS}
    body = _canonical(claims)
    signature = hmac.new(bytes.fromhex(lease["contextUpdateKey"]), body, hashlib.sha256).digest()
    return {"version": 1, "sequence": sequence, "previousHash": previous_hash,
            "providerContextHash": claims["hash"], "expiresAtUnix": claims["expires"],
            "envelope": PREFIX + _b64(body) + "." + _b64(signature)}


def verify_context_grant(envelope: str, lease: dict, *, previous_hash: str,
                         sequence: int, now: float) -> dict:
    try:
        if not isinstance(envelope, str) or not envelope.startswith(PREFIX) or len(envelope) > 20000:
            raise RealtimeContextError()
        body_part, signature_part = envelope[len(PREFIX):].split(".")
        body, signature = _decode(body_part), _decode(signature_part)
        expected = hmac.new(bytes.fromhex(lease["contextUpdateKey"]), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise RealtimeContextError()
        claims = json.loads(body, object_pairs_hook=_object)
        if (claims["v"] != 1 or claims["ticket"] != lease["ticketId"]
                or claims["session"] != lease["productSessionId"]
                or claims["epoch"] != lease["authorityEpoch"]
                or claims["revision"] != lease["memoryRevision"]
                or type(claims["sequence"]) is not int or claims["sequence"] != sequence
                or not 1 <= sequence <= MAX_UPDATES or claims["previous"] != previous_hash
                or not claims["issued"] <= now < claims["expires"]
                or claims["expires"] - claims["issued"] != GRANT_TTL_SECONDS
                or not isinstance(claims["role"], str) or not claims["role"]
                or len(claims["role"].encode()) > MAX_ROLE_BYTES
                or claims["hash"] != role_hash(claims["role"])):
            raise RealtimeContextError()
        return claims
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise RealtimeContextError() from None


def _parse_config_frame(frame: bytes):
    """Return only session configuration events; audio remains byte opaque."""
    if len(frame) < 8 or frame[1] >> 4 != 1 or not frame[1] & 4:
        return None
    header = (frame[0] & 15) * 4
    offset = header + (4 if (frame[1] & 3) in (1, 3) else 0)
    if offset + 4 > len(frame):
        raise RealtimeContextError()
    event = struct.unpack_from(">I", frame, offset)[0]
    if event not in (100, 201):
        return None
    if frame[0] >> 4 != 1 or header < 4 or frame[2] >> 4 != 1 or frame[2] & 15 not in (0, 1):
        raise RealtimeContextError()
    offset += 4
    size = struct.unpack_from(">I", frame, offset)[0]; offset += 4
    if not 1 <= size <= 128 or offset + size + 4 > len(frame):
        raise RealtimeContextError()
    session = frame[offset:offset + size].decode("utf-8"); offset += size
    if any(ord(c) < 32 for c in session):
        raise RealtimeContextError()
    length_offset = offset
    size = struct.unpack_from(">I", frame, offset)[0]; offset += 4
    if size > MAX_PAYLOAD or offset + size != len(frame):
        raise RealtimeContextError()
    data = frame[offset:]
    compressed = frame[2] & 15 == 1
    if compressed:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = decoder.decompress(data, MAX_PAYLOAD + 1)
        if len(data) > MAX_PAYLOAD or not decoder.eof or decoder.unused_data:
            raise RealtimeContextError()
    config = json.loads(data, object_pairs_hook=_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(RealtimeContextError()))
    if not isinstance(config, dict) or not isinstance(config.get("dialog"), dict):
        raise RealtimeContextError()
    return event, session, length_offset, compressed, config


class RealtimeContextFrames:
    """Connection-local replay fence. Invalid updates leave the audio stream alive."""
    def __init__(self, lease: dict | None, *, clock=time.time):
        self.lease = lease or {}
        self.enabled = bool(self.lease.get("contextUpdateKey"))
        self.clock = clock
        self.previous_hash = self.lease.get("providerContextHash")
        self.next_sequence = 1
        self.start_config = None
        self.session_id = None

    def transform(self, frame: bytes) -> bytes | None:
        if not self.enabled:
            return frame
        try:
            parsed = _parse_config_frame(frame)
            if parsed is None:
                return frame
            event, session, offset, compressed, config = parsed
            if event == 100:
                if self.start_config is not None or role_hash(config["dialog"]["system_role"]) != self.previous_hash:
                    raise RealtimeContextError()
                self.start_config, self.session_id = deepcopy(config), session
                return frame
            if self.start_config is None or session != self.session_id:
                raise RealtimeContextError()
            # No other client-controlled configuration may accompany a grant.
            if set(config) != {"dialog"} or set(config["dialog"]) != {"system_role"}:
                raise RealtimeContextError()
            claims = verify_context_grant(config["dialog"]["system_role"], self.lease,
                                          previous_hash=self.previous_hash,
                                          sequence=self.next_sequence, now=self.clock())
            # Provider updates replace config. Retain original voice/style/search.
            updated = deepcopy(self.start_config)
            updated["dialog"]["system_role"] = claims["role"]
            wire = _canonical(updated)
            if len(wire) > MAX_PAYLOAD:
                raise RealtimeContextError()
            if compressed:
                wire = gzip.compress(wire, mtime=0)
            result = frame[:offset] + struct.pack(">I", len(wire)) + wire
            self.previous_hash = claims["hash"]
            self.next_sequence += 1
            return result
        except (ValueError, KeyError, TypeError, UnicodeError, struct.error, zlib.error, RecursionError):
            return None
