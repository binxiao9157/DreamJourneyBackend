"""Server-only StartSession search injection; leave all audio frames opaque."""
from __future__ import annotations

import gzip
import hashlib
import json
import struct
import zlib

from app.services.echo_public_search import LIVE_SEARCH_RULE, live_search_configured


class RealtimeSearchContractError(ValueError):
    pass


def _invalid():
    return RealtimeSearchContractError('realtimeSearchStartSessionInvalid')


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


class RealtimeSearchFrames:
    max_payload = 65536

    def __init__(self, settings, lease):
        self.settings = settings
        self.lease = lease or {}
        self.session_id = None

    def transform(self, frame: bytes) -> bytes:
        if not live_search_configured(self.settings):
            return frame
        # Legacy callers with no bound role do not acquire a new capability.
        role_hash = self.lease.get('providerContextHash', '')
        if not role_hash:
            return frame
        if len(frame) < 8 or frame[1] >> 4 != 1 or not (frame[1] & 4):
            return frame
        header_size = (frame[0] & 15) * 4
        offset = header_size + (4 if (frame[1] & 3) in (1, 3) else 0)
        if offset + 4 > len(frame):
            raise _invalid()
        event = struct.unpack_from('>I', frame, offset)[0]
        if event != 100:
            return frame
        if frame[0] >> 4 != 1 or header_size < 4 or frame[2] >> 4 != 1 or frame[2] & 15 not in (0, 1):
            raise _invalid()
        offset += 4
        try:
            size = struct.unpack_from('>I', frame, offset)[0]; offset += 4
            if not 1 <= size <= 128 or offset + size + 4 > len(frame):
                raise _invalid()
            session = frame[offset:offset + size].decode('utf-8'); offset += size
            if any(ord(c) < 32 for c in session) or (self.session_id is not None and session != self.session_id):
                raise _invalid()
            length_offset = offset
            size = struct.unpack_from('>I', frame, offset)[0]; offset += 4
            if size > self.max_payload or offset + size != len(frame):
                raise _invalid()
            payload = frame[offset:]
            compressed = frame[2] & 15 == 1
            if compressed:
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                payload = decoder.decompress(payload, self.max_payload + 1)
                if len(payload) > self.max_payload or not decoder.eof or decoder.unused_data:
                    raise _invalid()
            config = json.loads(payload, object_pairs_hook=_json_object)
            if not isinstance(config, dict) or not isinstance(config.get('dialog'), dict):
                raise _invalid()
            dialog = config['dialog']
            role = dialog.get('system_role')
            if not isinstance(role, str) or 'sha256:' + hashlib.sha256(role.encode()).hexdigest() != role_hash:
                raise _invalid()
            # Old tickets issued while search was off remain valid ordinary Live.
            if LIVE_SEARCH_RULE not in role:
                return frame
            extra = dialog.get('extra', {})
            if not isinstance(extra, dict) or extra.get('model') != '1.2.1.1':
                raise _invalid()
            extra = dict(extra)
            for key in list(extra):
                if key.startswith('volc_websearch_') or key == 'enable_volc_websearch':
                    del extra[key]
            extra.update(enable_volc_websearch=True,
                         volc_websearch_api_key=self.settings.volcengine_websearch_api_key,
                         volc_websearch_type='web_summary',
                         volc_websearch_no_result_message='暂时查不到可靠的实时信息，请稍后再试。')
            dialog['extra'] = extra
            wire = json.dumps(config, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
            if len(wire) > self.max_payload:
                raise _invalid()
            if compressed:
                wire = gzip.compress(wire, mtime=0)
            self.session_id = session
            return frame[:length_offset] + struct.pack('>I', len(wire)) + wire
        except (UnicodeError, struct.error, zlib.error, ValueError, TypeError, RecursionError):
            # No serialized payload or key can appear in a propagated error.
            raise _invalid() from None
