"""Redacted, append-only receipts for terminal account purge.

The account row is intentionally reduced to a tombstone after purge.  This
module preserves only the minimum non-identifying evidence needed to prove
that the terminal transition happened without retaining a phone number or a
raw user identifier.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


ACCOUNT_PURGE_RECEIPT_SCHEMA_VERSION = 1


def account_purge_subject_hash(user_id: str) -> str:
    """Return the redacted stable subject key used by purge receipts."""

    return hashlib.sha256(
        f"dreamjourney-account-purge-subject-v1|{str(user_id)}".encode("utf-8")
    ).hexdigest()


def build_account_purge_receipt(
    *,
    user_id: str,
    account: Mapping[str, Any],
    purged_at: Any,
) -> Dict[str, Any]:
    """Build a deterministic, value-free receipt for one terminal purge."""

    subject_hash = account_purge_subject_hash(user_id)
    request_id = str(account.get("deletionRequestId") or "").strip()
    request_hash = (
        None
        if not request_id
        else hashlib.sha256(
            f"dreamjourney-account-deletion-request-v1|{request_id}".encode("utf-8")
        ).hexdigest()
    )
    payload = {
        "schemaVersion": ACCOUNT_PURGE_RECEIPT_SCHEMA_VERSION,
        "subjectHash": subject_hash,
        "deletionRequestIdHash": request_hash,
        "deletedAt": _timestamp_or_none(account.get("deletedAt")),
        "purgeAfter": _timestamp_or_none(
            account.get("purgeAfter") or account.get("restoreDeadline")
        ),
        "purgedAt": _required_timestamp(purged_at),
        "restoreCount": _nonnegative_int(account.get("restoreCount")),
        "terminalState": "purged",
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    receipt_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "id": f"apr_{receipt_hash[:32]}",
        **payload,
        "receiptHash": receipt_hash,
    }


def _timestamp_or_none(value: Any) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    return _required_timestamp(value)


def _required_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("account purge receipt timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("account purge receipt timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("account purge receipt restore count must be an integer")
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("account purge receipt restore count must be an integer") from exc
    if parsed < 0:
        raise ValueError("account purge receipt restore count must be non-negative")
    return parsed


__all__ = [
    "ACCOUNT_PURGE_RECEIPT_SCHEMA_VERSION",
    "account_purge_subject_hash",
    "build_account_purge_receipt",
]
