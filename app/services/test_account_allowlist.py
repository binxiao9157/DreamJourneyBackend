from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from app.observability.events import hash_evidence_identifier


TEST_ACCOUNT_ALLOWLIST_CONTRACT_VERSION = 1
TEST_ACCOUNT_PROVIDER_MODE = "testAllowlist"
SYNTHETIC_TEST_PHONE_PATTERN = re.compile(r"^86100[0-9]{8}$")


class TestAccountAllowlistError(RuntimeError):
    pass


class TestAccountAllowlistDisabled(TestAccountAllowlistError):
    pass


class TestAccountAllowlistValidationError(TestAccountAllowlistError):
    pass


class TestAccountAllowlistConflict(TestAccountAllowlistError):
    pass


class TestAccountAllowlistNotFound(TestAccountAllowlistError):
    pass


def normalize_test_phone_target(value: Any) -> str:
    raw_value = str(value or "").strip()
    if not raw_value or re.fullmatch(r"\+?[0-9()\-\s]+", raw_value) is None:
        raise TestAccountAllowlistValidationError("invalid test account phone")
    digits = "".join(character for character in raw_value if character.isdigit())
    if digits.startswith("0086"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("1"):
        digits = f"86{digits}"
    if len(digits) < 7 or len(digits) > 15:
        raise TestAccountAllowlistValidationError("invalid test account phone")
    return digits


def configured_test_phone_prefixes(value: Any) -> tuple[str, ...]:
    prefixes = []
    for item in str(value or "").split(","):
        candidate = "".join(character for character in item.strip() if character.isdigit())
        if not candidate:
            continue
        if len(candidate) < 10 or len(candidate) > 13:
            return ()
        prefixes.append(candidate)
    return tuple(sorted(set(prefixes)))


def test_account_allowlist_configured(settings: Any) -> bool:
    key = str(getattr(settings, "identity_binding_hmac_key", "") or "").encode(
        "utf-8"
    )
    prefixes = configured_test_phone_prefixes(
        getattr(settings, "test_account_allowed_phone_prefixes", None)
    )
    return bool(
        getattr(settings, "test_account_allowlist_enabled", False)
        and len(key) >= 32
        and prefixes
    )


class TestAccountAllowlistService:
    def __init__(
        self,
        store: Any,
        *,
        hmac_key: str,
        hmac_key_version: str,
        enabled: bool,
        allowed_phone_prefixes: Iterable[str],
        event_sink: Optional[Any] = None,
        environment: str = "unknown",
    ) -> None:
        self.store = store
        self._hmac_key = str(hmac_key or "").encode("utf-8")
        self.hmac_key_version = str(hmac_key_version or "v1").strip() or "v1"
        self.enabled = bool(enabled)
        self.allowed_phone_prefixes = tuple(sorted(set(allowed_phone_prefixes)))
        self.event_sink = event_sink
        self.environment = self._machine_code(environment, fallback="unknown")

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and len(self._hmac_key) >= 32
            and self.allowed_phone_prefixes
            and all(10 <= len(prefix) <= 13 for prefix in self.allowed_phone_prefixes)
        )

    def create(
        self,
        *,
        target: str,
        label: str,
        actor_id: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        self._require_configured()
        created_at = self._utc(now)
        normalized_target = normalize_test_phone_target(target)
        self._require_allowed_target(normalized_target)
        normalized_label = self._label(label)
        account_id = secrets.token_hex(16)
        code = self._new_code()
        code_version = 1
        record = {
            "accountId": account_id,
            "identityType": "phone",
            "targetHashKeyVersion": self.hmac_key_version,
            "targetHash": self.target_hash("phone", normalized_target),
            "targetHint": self._target_hint(normalized_target),
            "codeHashKeyVersion": self.hmac_key_version,
            "codeHash": self._code_hash(account_id, code_version, code),
            "codeVersion": code_version,
            "label": normalized_label,
            "status": "active",
            "subjectId": None,
            "expiresAt": None,
            "createdByHash": hash_evidence_identifier(actor_id),
            "createdAt": created_at.isoformat(),
            "updatedAt": created_at.isoformat(),
            "lastUsedAt": None,
            "useCount": 0,
            "contractVersion": TEST_ACCOUNT_ALLOWLIST_CONTRACT_VERSION,
        }
        result = self.store.create_test_account_allowlist(record)
        if result.get("outcome") != "created":
            raise TestAccountAllowlistConflict("test account target already exists")
        self._record_event(
            account_id=account_id,
            actor_id=actor_id,
            action="created",
            route="POST /ops/test-accounts",
            occurred_at=created_at,
        )
        return {
            "status": "created",
            "testAccount": {
                **self._public_record(result["account"]),
                "loginTarget": self._login_target(normalized_target),
                "verificationCode": code,
                "credentialDisclosure": "oneTime",
            },
        }

    def list(self, *, include_disabled: bool = True) -> Dict[str, Any]:
        self._require_configured()
        records = self.store.list_test_account_allowlist(
            include_disabled=include_disabled
        )
        return {
            "status": "available",
            "testAccounts": [self._public_record(item) for item in records],
            "contractVersion": TEST_ACCOUNT_ALLOWLIST_CONTRACT_VERSION,
        }

    def rotate_code(
        self,
        account_id: str,
        *,
        actor_id: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        self._require_configured()
        rotated_at = self._utc(now)
        account = self._account(account_id)
        code = self._new_code()
        code_version = int(account.get("codeVersion") or 0) + 1
        result = self.store.rotate_test_account_allowlist_code(
            str(account["accountId"]),
            code_hash=self._code_hash(str(account["accountId"]), code_version, code),
            code_hash_key_version=self.hmac_key_version,
            code_version=code_version,
            updated_at_iso=rotated_at.isoformat(),
        )
        if result is None:
            raise TestAccountAllowlistNotFound("test account not found")
        self._record_event(
            account_id=str(account["accountId"]),
            actor_id=actor_id,
            action="codeRotated",
            route="POST /ops/test-accounts/{account_id}/rotate-code",
            occurred_at=rotated_at,
        )
        return {
            "status": "rotated",
            "testAccount": {
                **self._public_record(result),
                "verificationCode": code,
                "credentialDisclosure": "oneTime",
            },
        }

    def disable(
        self,
        account_id: str,
        *,
        actor_id: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        return self._set_status(
            account_id,
            status="disabled",
            actor_id=actor_id,
            action="disabled",
            route="POST /ops/test-accounts/{account_id}/disable",
            now=now,
        )

    def enable(
        self,
        account_id: str,
        *,
        actor_id: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        observed_at = self._utc(now)
        self._account(account_id)
        return self._set_status(
            account_id,
            status="active",
            actor_id=actor_id,
            action="enabled",
            route="POST /ops/test-accounts/{account_id}/enable",
            now=observed_at,
        )

    def renew(
        self,
        account_id: str,
        *,
        actor_id: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        self._require_configured()
        renewed_at = self._utc(now)
        self._account(account_id)
        result = self.store.update_test_account_allowlist_status(
            str(account_id or "").strip(),
            status="active",
            expires_at_iso=None,
            clear_expiration=True,
            updated_at_iso=renewed_at.isoformat(),
        )
        if result is None:
            raise TestAccountAllowlistNotFound("test account not found")
        self._record_event(
            account_id=str(result["accountId"]),
            actor_id=actor_id,
            action="renewed",
            route="POST /ops/test-accounts/{account_id}/renew",
            occurred_at=renewed_at,
        )
        return {"status": "renewed", "testAccount": self._public_record(result)}

    def active_account_for_target_hash(
        self,
        *,
        identity_type: str,
        target_hash_key_version: str,
        target_hash: str,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.configured:
            return None
        return self.store.get_active_test_account_allowlist_by_target_hash(
            identity_type=str(identity_type or "").strip(),
            target_hash_key_version=str(target_hash_key_version or "").strip(),
            target_hash=str(target_hash or "").strip(),
            observed_at_iso=self._utc(now).isoformat(),
        )

    def verify_code(self, account: Dict[str, Any], code: str) -> bool:
        if not self.configured:
            return False
        candidate = str(code or "").strip()
        if not re.fullmatch(r"[0-9]{6}", candidate):
            return False
        if str(account.get("codeHashKeyVersion") or "") != self.hmac_key_version:
            return False
        expected = str(account.get("codeHash") or "")
        actual = self._code_hash(
            str(account.get("accountId") or ""),
            int(account.get("codeVersion") or 0),
            candidate,
        )
        return bool(expected and hmac.compare_digest(expected, actual))

    def challenge_code_hash(
        self,
        *,
        account: Dict[str, Any],
        challenge_id: str,
        keyed_hash: Any,
    ) -> str:
        return keyed_hash(
            "test-account-challenge:v1:"
            f"{challenge_id}:{account.get('accountId')}:{account.get('codeVersion')}"
        )

    def record_successful_login(
        self,
        account_id: str,
        *,
        subject_id: str,
        now: Optional[datetime] = None,
    ) -> bool:
        if not self.configured:
            return False
        used_at = self._utc(now)
        result = self.store.record_test_account_allowlist_use(
            str(account_id or "").strip(),
            subject_id=str(subject_id or "").strip(),
            used_at_iso=used_at.isoformat(),
        )
        if result is None:
            return False
        self._record_event(
            account_id=str(account_id),
            actor_id=str(subject_id),
            action="loginVerified",
            route="POST /v2/auth/challenges/{challenge_id}/verify",
            occurred_at=used_at,
        )
        return True

    def target_hash(self, identity_type: str, normalized_target: str) -> str:
        return self._keyed_hash(
            f"target:{self.hmac_key_version}:{identity_type}:{normalized_target}"
        )

    def _set_status(
        self,
        account_id: str,
        *,
        status: str,
        actor_id: str,
        action: str,
        route: str,
        now: Optional[datetime],
    ) -> Dict[str, Any]:
        self._require_configured()
        changed_at = self._utc(now)
        self._account(account_id)
        result = self.store.update_test_account_allowlist_status(
            str(account_id or "").strip(),
            status=status,
            expires_at_iso=None,
            clear_expiration=False,
            updated_at_iso=changed_at.isoformat(),
        )
        if result is None:
            raise TestAccountAllowlistNotFound("test account not found")
        self._record_event(
            account_id=str(result["accountId"]),
            actor_id=actor_id,
            action=action,
            route=route,
            occurred_at=changed_at,
        )
        return {"status": status, "testAccount": self._public_record(result)}

    def _account(self, account_id: str) -> Dict[str, Any]:
        normalized = str(account_id or "").strip()
        if not normalized or len(normalized) > 64:
            raise TestAccountAllowlistNotFound("test account not found")
        account = self.store.get_test_account_allowlist(normalized)
        if account is None:
            raise TestAccountAllowlistNotFound("test account not found")
        return account

    def _require_configured(self) -> None:
        if not self.configured:
            raise TestAccountAllowlistDisabled(
                "test account allowlist is disabled or incomplete"
            )

    def _require_allowed_target(self, normalized_target: str) -> None:
        if SYNTHETIC_TEST_PHONE_PATTERN.fullmatch(normalized_target) is None:
            raise TestAccountAllowlistValidationError(
                "test account phone must use the reserved 100xxxxxxxx range"
            )
        if not any(
            normalized_target.startswith(prefix)
            for prefix in self.allowed_phone_prefixes
        ):
            raise TestAccountAllowlistValidationError(
                "test account phone is outside the configured synthetic range"
            )

    @staticmethod
    def _label(value: Any) -> str:
        candidate = str(value or "").strip()
        if not candidate or len(candidate) > 80:
            raise TestAccountAllowlistValidationError(
                "test account label must contain 1 to 80 characters"
            )
        return candidate

    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def _code_hash(self, account_id: str, code_version: int, code: str) -> str:
        return self._keyed_hash(
            f"test-account-code:v1:{account_id}:{int(code_version)}:{code}"
        )

    def _keyed_hash(self, value: str) -> str:
        if len(self._hmac_key) < 32:
            raise TestAccountAllowlistDisabled("test account HMAC key is unavailable")
        return hmac.new(
            self._hmac_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _target_hint(normalized_target: str) -> str:
        if len(normalized_target) <= 7:
            return f"***{normalized_target[-3:]}"
        return f"+{normalized_target[:4]}***{normalized_target[-4:]}"

    @staticmethod
    def _login_target(normalized_target: str) -> str:
        if normalized_target.startswith("86") and len(normalized_target) == 13:
            return normalized_target[2:]
        return normalized_target

    @staticmethod
    def _public_record(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "accountId": str(record.get("accountId") or ""),
            "identityType": str(record.get("identityType") or "phone"),
            "targetHint": str(record.get("targetHint") or ""),
            "label": str(record.get("label") or ""),
            "status": str(record.get("status") or "disabled"),
            "subjectId": record.get("subjectId"),
            "codeVersion": int(record.get("codeVersion") or 0),
            "expiresAt": record.get("expiresAt"),
            "validity": "permanent" if record.get("expiresAt") is None else "limited",
            "createdAt": str(record.get("createdAt") or ""),
            "updatedAt": str(record.get("updatedAt") or ""),
            "lastUsedAt": record.get("lastUsedAt"),
            "useCount": int(record.get("useCount") or 0),
            "contractVersion": TEST_ACCOUNT_ALLOWLIST_CONTRACT_VERSION,
        }

    def _record_event(
        self,
        *,
        account_id: str,
        actor_id: str,
        action: str,
        route: str,
        occurred_at: datetime,
    ) -> None:
        if not callable(self.event_sink):
            return
        event_nonce = secrets.token_hex(16)
        self.event_sink(
            {
                "type": "operation",
                "eventId": f"evt-{event_nonce}",
                "schemaVersion": 1,
                "operationId": f"test-account-{action}-{event_nonce}",
                "correlationId": None,
                "principalHash": hash_evidence_identifier(actor_id),
                "resourceType": "testAccountAllowlist",
                "resourceIdHash": hash_evidence_identifier(account_id),
                "state": "succeeded",
                "reason": action,
                "attempt": 1,
                "occurredAt": occurred_at.isoformat(),
                "env": self.environment,
                "build": "backend",
                "redactionVersion": 1,
                "operation": "testAccountAllowlist",
                "route": route,
                "feature": "testAccountAccess",
                "decision": action,
            },
            retention_class="operationalTemporary",
            expires_at_iso=(occurred_at + timedelta(days=30)).isoformat(),
        )

    @staticmethod
    def _utc(value: Optional[datetime]) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            return result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    @staticmethod
    def _utc_from_text(value: Any) -> datetime:
        candidate = str(value or "").strip()
        if not candidate:
            return datetime.min.replace(tzinfo=timezone.utc)
        return TestAccountAllowlistService._utc(
            datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        )

    @staticmethod
    def _machine_code(value: Any, *, fallback: str) -> str:
        candidate = re.sub(r"[^A-Za-z0-9.:\-]", "-", str(value or "").strip())
        candidate = candidate[:128].strip("-.")
        return candidate if candidate and candidate[0].isalnum() else fallback


def make_test_account_allowlist_service(
    store: Any,
    settings: Any,
) -> TestAccountAllowlistService:
    return TestAccountAllowlistService(
        store,
        hmac_key=str(getattr(settings, "identity_binding_hmac_key", "") or ""),
        hmac_key_version=str(
            getattr(settings, "identity_binding_hmac_key_version", "v1") or "v1"
        ),
        enabled=bool(getattr(settings, "test_account_allowlist_enabled", False)),
        allowed_phone_prefixes=configured_test_phone_prefixes(
            getattr(settings, "test_account_allowed_phone_prefixes", None)
        ),
        event_sink=getattr(store, "append_evidence_event", None),
        environment=str(getattr(settings, "environment", "unknown") or "unknown"),
    )
