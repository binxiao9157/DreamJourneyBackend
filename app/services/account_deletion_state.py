"""Fail-closed lifecycle rules for account deletion, restore, and purge.

The stores own persistence and locking. This module owns only the deterministic
state checks shared by both the in-memory and Postgres implementations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional


ACTIVE_DELETION_STATE = "active"
SOFT_DELETED_STATE = "softDeleted"
PURGED_STATE = "purged"
DEFAULT_RESTORE_LIMIT = 1
RELEASED_RETENTION_HOLD_STATES = frozenset({"released"})


class AccountDeletionStateError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def guard_account_upsert(existing_account: Optional[Mapping[str, Any]]) -> None:
    """Prevent generic upsert callers from reviving a deletion lifecycle."""

    if not existing_account:
        return
    state = str(existing_account.get("deletionState") or ACTIVE_DELETION_STATE).strip()
    if state != ACTIVE_DELETION_STATE:
        raise AccountDeletionStateError("accountLifecycleUpsertBlocked")


def account_restore_block_reason(
    account: Mapping[str, Any],
    restored_at: Any,
) -> Optional[str]:
    """Return a stable reason when a persisted account cannot be restored."""

    if str(account.get("deletionState") or "").strip() != SOFT_DELETED_STATE:
        return "accountNotSoftDeleted"

    restore_count = _nonnegative_int(account.get("restoreCount"), default=0)
    if restore_count is None:
        return "restoreCountInvalid"
    restore_limit = _nonnegative_int(
        account.get("restoreLimit"),
        default=DEFAULT_RESTORE_LIMIT,
    )
    if restore_limit is None or restore_limit < 1:
        return "restoreLimitInvalid"
    if restore_count >= restore_limit:
        return "restoreLimitReached"

    deadline = _utc_datetime(account.get("restoreDeadline") or account.get("purgeAfter"))
    if deadline is None:
        return "restoreDeadlineInvalid"
    candidate = _utc_datetime(restored_at)
    if candidate is None:
        return "restoreTimestampInvalid"
    # The documented boundary is inclusive: a restore at the exact deadline is
    # still eligible; anything after it is terminal.
    if candidate > deadline:
        return "restoreDeadlineExpired"
    return None


def account_purge_block_reason(
    account: Mapping[str, Any],
    cutoff: Any,
) -> Optional[str]:
    """Return a stable reason when physical purge must not run yet."""

    if str(account.get("deletionState") or "").strip() != SOFT_DELETED_STATE:
        return "accountNotSoftDeleted"
    deadline = _utc_datetime(account.get("restoreDeadline") or account.get("purgeAfter"))
    if deadline is None:
        return "purgeDeadlineInvalid"
    candidate = _utc_datetime(cutoff)
    if candidate is None:
        return "purgeCutoffInvalid"
    if candidate < deadline:
        return "purgeNotDue"

    retention_holds = account.get("retentionHolds")
    if retention_holds is None:
        return None
    if not isinstance(retention_holds, (list, tuple)):
        return "retentionHoldInvalid"
    for hold in retention_holds:
        if not isinstance(hold, Mapping):
            return "retentionHoldInvalid"
        state = str(hold.get("state") or "").strip().lower()
        if not state:
            return "retentionHoldInvalid"
        if state not in RELEASED_RETENTION_HOLD_STATES:
            return "retentionHoldActive"
    return None


def _nonnegative_int(value: Any, *, default: int) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _utc_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)
