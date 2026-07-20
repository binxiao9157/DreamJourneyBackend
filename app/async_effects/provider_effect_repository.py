"""Durable, value-free Provider effect receipt and reconciliation repository.

Provider requests are still disabled.  This repository only persists stable
request coordinates plus append-only observations.  A timeout leaves the base
effect in ``unknown`` permanently; later query/callback observations are
projected into an effective state without rewriting that historical fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping

from app.async_effects.provider_effects import (
    ProviderEffectConflict,
    ProviderEffectContractError,
    ProviderEffectIntent,
    ProviderEffectReconciliation,
    ProviderEffectReceipt,
    ProviderEffectState,
    assert_same_provider_request,
)


_INITIAL_STATES = {ProviderEffectState.ACCEPTED, ProviderEffectState.UNKNOWN}
_TERMINAL_RECONCILIATION_STATES = {
    ProviderEffectState.COMPLETED,
    ProviderEffectState.FAILED,
}


@dataclass(frozen=True)
class ProviderEffectPersistenceSummary:
    """Value-free result of recording or reconciling one Provider effect."""

    outcome: str
    intent: ProviderEffectIntent
    effect_state: ProviderEffectState
    effective_state: ProviderEffectState
    receipt_hash: str
    reconciliation_status: str
    reconciliation_id: str | None = None
    requires_manual_review: bool = False
    reissue_allowed: bool = False

    def value_free_summary(self) -> dict[str, object]:
        return {
            "effectId": self.intent.provider_effect_id,
            "effectState": self.effect_state.value,
            "effectiveState": self.effective_state.value,
            "outcome": self.outcome,
            "provider": self.intent.provider,
            "providerEffectKey": self.intent.provider_effect_key,
            "receiptHash": self.receipt_hash,
            "reconciliationId": self.reconciliation_id,
            "reconciliationStatus": self.reconciliation_status,
            "reissueAllowed": self.reissue_allowed,
            "requiresManualReview": self.requires_manual_review,
            "requestHash": self.intent.request_hash,
            "schemaVersion": self.intent.contract_version,
        }


def _assert_same_intent(existing: ProviderEffectIntent, candidate: ProviderEffectIntent) -> None:
    if existing.effect_intent.immutable_fingerprint() != candidate.effect_intent.immutable_fingerprint():
        raise ProviderEffectConflict(
            "a stable provider effect key cannot be reused with a different operation payload"
        )
    assert_same_provider_request(existing, candidate)


def _require_initial_receipt(receipt: ProviderEffectReceipt) -> None:
    if receipt.state not in _INITIAL_STATES:
        raise ProviderEffectContractError(
            "Provider effect persistence accepts only accepted or unknown initial receipts"
        )


def _projection(
    effect_state: ProviderEffectState,
    query_receipts: tuple[ProviderEffectReceipt, ...],
) -> tuple[ProviderEffectState, str, bool]:
    """Project append-only query evidence without rewriting an unknown fact."""

    if effect_state is not ProviderEffectState.UNKNOWN:
        return effect_state, "notReconciled", False
    terminal_states = {receipt.state for receipt in query_receipts if receipt.state in _TERMINAL_RECONCILIATION_STATES}
    if terminal_states == _TERMINAL_RECONCILIATION_STATES:
        return ProviderEffectState.UNKNOWN, "reconciliationConflict", True
    if terminal_states == {ProviderEffectState.COMPLETED}:
        return ProviderEffectState.COMPLETED, "reconciledCompleted", False
    if terminal_states == {ProviderEffectState.FAILED}:
        return ProviderEffectState.FAILED, "reconciledFailed", False
    if query_receipts:
        return ProviderEffectState.UNKNOWN, "manualReview", True
    return ProviderEffectState.UNKNOWN, "pendingReconcile", True


class InMemoryProviderEffectRepository:
    """Thread-safe semantic double for Provider effect persistence tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._effects: dict[str, dict[str, object]] = {}

    def record(self, receipt: ProviderEffectReceipt) -> ProviderEffectPersistenceSummary:
        _require_initial_receipt(receipt)
        effect_id = receipt.intent.provider_effect_id
        with self._lock:
            record = self._effects.get(effect_id)
            if record is None:
                record = {
                    "intent": receipt.intent,
                    "state": receipt.state,
                    "receipts": {receipt.storage_receipt_hash: receipt},
                    "queryReceiptHashes": [],
                }
                self._effects[effect_id] = record
                return self._summary("accepted", record=record, receipt=receipt)

            existing_intent = self._record_intent(record)
            _assert_same_intent(existing_intent, receipt.intent)
            receipts = self._record_receipts(record)
            existing_receipt = receipts.get(receipt.storage_receipt_hash)
            if existing_receipt is not None:
                self._assert_same_receipt(existing_receipt, receipt)
                return self._summary("deduplicated", record=record, receipt=receipt)

            state = self._record_state(record)
            if receipt.state is ProviderEffectState.ACCEPTED:
                if state is not ProviderEffectState.ACCEPTED:
                    raise ProviderEffectConflict("an unknown provider effect cannot be reopened as accepted")
                receipts[receipt.storage_receipt_hash] = receipt
                return self._summary("acceptedObservation", record=record, receipt=receipt)
            if state is ProviderEffectState.ACCEPTED:
                record["state"] = ProviderEffectState.UNKNOWN
                receipts[receipt.storage_receipt_hash] = receipt
                return self._summary("unknownRecorded", record=record, receipt=receipt)
            if state is ProviderEffectState.UNKNOWN:
                receipts[receipt.storage_receipt_hash] = receipt
                return self._summary("unknownObserved", record=record, receipt=receipt)
            raise ProviderEffectConflict("a terminal provider effect cannot record a new initial receipt")

    def reconcile(self, reconciliation: ProviderEffectReconciliation) -> ProviderEffectPersistenceSummary:
        prior_unknown = reconciliation.prior_unknown
        with self._lock:
            record = self._require_record(prior_unknown.intent)
            if self._record_state(record) is not ProviderEffectState.UNKNOWN:
                raise ProviderEffectContractError("only a durably unknown provider effect may be reconciled")
            prior = self._record_receipts(record).get(prior_unknown.storage_receipt_hash)
            if prior is None or prior.state is not ProviderEffectState.UNKNOWN:
                raise ProviderEffectContractError("the prior unknown receipt is not durably recorded")
            self._assert_same_receipt(prior, prior_unknown)

            terminal_receipt = reconciliation.terminal_receipt()
            receipts = self._record_receipts(record)
            existing = receipts.get(terminal_receipt.storage_receipt_hash)
            if existing is not None:
                self._assert_same_receipt(existing, terminal_receipt)
                return self._summary(
                    "deduplicated",
                    record=record,
                    receipt=terminal_receipt,
                    reconciliation=reconciliation,
                )
            receipts[terminal_receipt.storage_receipt_hash] = terminal_receipt
            self._query_receipt_hashes(record).append(terminal_receipt.storage_receipt_hash)
            return self._summary(
                "reconciled",
                record=record,
                receipt=terminal_receipt,
                reconciliation=reconciliation,
            )

    def effect_state(self, intent: ProviderEffectIntent) -> ProviderEffectState:
        with self._lock:
            return self._record_state(self._require_record(intent))

    def effective_state(self, intent: ProviderEffectIntent) -> ProviderEffectState:
        with self._lock:
            return self._project_record(self._require_record(intent))[0]

    def _require_record(self, intent: ProviderEffectIntent) -> dict[str, object]:
        record = self._effects.get(intent.provider_effect_id)
        if record is None:
            raise ProviderEffectContractError("provider effect is not durably recorded")
        _assert_same_intent(self._record_intent(record), intent)
        return record

    @staticmethod
    def _record_intent(record: Mapping[str, object]) -> ProviderEffectIntent:
        intent = record.get("intent")
        if not isinstance(intent, ProviderEffectIntent):
            raise RuntimeError("provider effect record lacks its immutable intent")
        return intent

    @staticmethod
    def _record_state(record: Mapping[str, object]) -> ProviderEffectState:
        state = record.get("state")
        if not isinstance(state, ProviderEffectState):
            raise RuntimeError("provider effect record has an invalid state")
        return state

    @staticmethod
    def _record_receipts(record: Mapping[str, object]) -> dict[str, ProviderEffectReceipt]:
        receipts = record.get("receipts")
        if not isinstance(receipts, dict):
            raise RuntimeError("provider effect record lacks receipts")
        return receipts

    @staticmethod
    def _query_receipt_hashes(record: Mapping[str, object]) -> list[str]:
        hashes = record.get("queryReceiptHashes")
        if not isinstance(hashes, list):
            raise RuntimeError("provider effect record lacks query receipt coordinates")
        return hashes

    @staticmethod
    def _assert_same_receipt(existing: ProviderEffectReceipt, candidate: ProviderEffectReceipt) -> None:
        if existing.value_free_summary() != candidate.value_free_summary():
            raise ProviderEffectConflict("provider receipt hash is bound to different immutable evidence")

    def _project_record(self, record: Mapping[str, object]) -> tuple[ProviderEffectState, str, bool]:
        receipts = self._record_receipts(record)
        query_receipts = tuple(
            receipts[receipt_hash]
            for receipt_hash in self._query_receipt_hashes(record)
            if receipt_hash in receipts
        )
        return _projection(self._record_state(record), query_receipts)

    def _summary(
        self,
        outcome: str,
        *,
        record: Mapping[str, object],
        receipt: ProviderEffectReceipt,
        reconciliation: ProviderEffectReconciliation | None = None,
    ) -> ProviderEffectPersistenceSummary:
        effective_state, reconciliation_status, manual_review = self._project_record(record)
        return ProviderEffectPersistenceSummary(
            outcome=outcome,
            intent=self._record_intent(record),
            effect_state=self._record_state(record),
            effective_state=effective_state,
            receipt_hash=receipt.storage_receipt_hash,
            reconciliation_status=reconciliation_status,
            reconciliation_id=None if reconciliation is None else reconciliation.reconciliation_id,
            requires_manual_review=manual_review,
            reissue_allowed=False if reconciliation is None else reconciliation.reissue_allowed,
        )


class PostgresProviderEffectRepository:
    """Postgres Provider evidence writer bound to an active Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(self, receipt: ProviderEffectReceipt) -> ProviderEffectPersistenceSummary:
        _require_initial_receipt(receipt)
        with self._cursor() as cursor:
            effect, effect_inserted = self._insert_or_lock_effect(
                cursor,
                receipt.intent,
                initial_state=receipt.state,
                attempt=receipt.attempt,
            )
            state = ProviderEffectState(str(effect["state"]))
            outcome = "accepted" if effect_inserted else "acceptedObservation"

            # A replay of the original accepted observation must remain a
            # dedupe after a later timeout made the base effect ``unknown``.
            # Check receipt identity before applying the state-transition
            # guard so idempotent callers are never treated as a resend.
            receipt_inserted = self._insert_or_assert_receipt(cursor, receipt)
            if not receipt_inserted:
                return self._summary_from_projection(
                    cursor,
                    outcome="deduplicated",
                    intent=receipt.intent,
                    receipt_hash=receipt.storage_receipt_hash,
                )

            if receipt.state is ProviderEffectState.UNKNOWN:
                if state is ProviderEffectState.ACCEPTED:
                    cursor.execute(
                        """
                        UPDATE async_effects.provider_effects
                        SET state = 'unknown', attempt = GREATEST(attempt, %s), updated_at = NOW()
                        WHERE effect_id = %s AND state = 'accepted'
                        RETURNING state
                        """,
                        (receipt.attempt, receipt.intent.provider_effect_id),
                    )
                    if cursor.fetchone() is None:
                        raise ProviderEffectConflict("provider effect state changed while recording unknown")
                    outcome = "unknownRecorded"
                elif state is ProviderEffectState.UNKNOWN:
                    outcome = "unknownRecorded" if effect_inserted else "unknownObserved"
                else:
                    raise ProviderEffectConflict("a terminal provider effect cannot record unknown evidence")
            elif state is not ProviderEffectState.ACCEPTED:
                raise ProviderEffectConflict("an unknown provider effect cannot be reopened as accepted")

            return self._summary_from_projection(
                cursor,
                outcome=outcome,
                intent=receipt.intent,
                receipt_hash=receipt.storage_receipt_hash,
            )

    def reconcile(self, reconciliation: ProviderEffectReconciliation) -> ProviderEffectPersistenceSummary:
        prior_unknown = reconciliation.prior_unknown
        with self._cursor() as cursor:
            effect = self._lock_effect(cursor, prior_unknown.intent)
            if ProviderEffectState(str(effect["state"])) is not ProviderEffectState.UNKNOWN:
                raise ProviderEffectContractError("only a durably unknown provider effect may be reconciled")
            self._assert_prior_unknown_receipt(cursor, prior_unknown)
            terminal_receipt = reconciliation.terminal_receipt()
            inserted = self._insert_or_assert_receipt(cursor, terminal_receipt)
            return self._summary_from_projection(
                cursor,
                outcome="reconciled" if inserted else "deduplicated",
                intent=prior_unknown.intent,
                receipt_hash=terminal_receipt.storage_receipt_hash,
                reconciliation_id=reconciliation.reconciliation_id,
            )

    def effective_state(self, intent: ProviderEffectIntent) -> ProviderEffectState:
        with self._cursor() as cursor:
            self._lock_effect(cursor, intent)
            summary = self._summary_from_projection(
                cursor,
                outcome="observed",
                intent=intent,
                receipt_hash="",
            )
            return summary.effective_state

    def _cursor(self) -> Any:
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - psycopg is a production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    def _insert_or_lock_effect(
        self,
        cursor: Any,
        intent: ProviderEffectIntent,
        *,
        initial_state: ProviderEffectState,
        attempt: int,
    ) -> tuple[dict[str, object], bool]:
        target = intent.effect_intent.target
        cursor.execute(
            """
            INSERT INTO async_effects.provider_effects (
                effect_id, operation_id, owner_subject_id, vault_id, resource_type,
                resource_id, resource_version, purpose, authority_epoch, stable_key,
                provider_name, provider_effect_key_hash, request_hash, capability,
                contract_version, provider_request_id_hash, state, attempt, accepted_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, CASE WHEN %s = 'accepted' THEN NOW() ELSE NULL END)
            ON CONFLICT (provider_name, provider_effect_key_hash) DO NOTHING
            RETURNING effect_id, operation_id, owner_subject_id, vault_id, resource_type,
                resource_id, resource_version, purpose, authority_epoch, stable_key,
                provider_name, provider_effect_key_hash, request_hash, capability,
                contract_version, provider_request_id_hash, state, attempt
            """,
            (
                intent.provider_effect_id,
                intent.effect_intent.operation_id,
                target.owner_subject_id,
                target.vault_id,
                target.resource_type,
                target.resource_id,
                target.resource_version,
                target.purpose,
                target.authority_epoch,
                intent.effect_intent.stable_key,
                intent.provider,
                intent.provider_effect_key,
                intent.request_hash,
                intent.capability,
                intent.contract_version,
                intent.provider_request_id_hash,
                initial_state.value,
                attempt,
                initial_state.value,
            ),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return dict(inserted), True
        return self._lock_effect(cursor, intent), False

    def _lock_effect(self, cursor: Any, intent: ProviderEffectIntent) -> dict[str, object]:
        cursor.execute(
            """
            SELECT effect_id, operation_id, owner_subject_id, vault_id, resource_type,
                resource_id, resource_version, purpose, authority_epoch, stable_key,
                provider_name, provider_effect_key_hash, request_hash, capability,
                contract_version, provider_request_id_hash, state, attempt
            FROM async_effects.provider_effects
            WHERE provider_name = %s AND provider_effect_key_hash = %s
            FOR UPDATE
            """,
            (intent.provider, intent.provider_effect_key),
        )
        row = cursor.fetchone()
        if row is None:
            raise ProviderEffectContractError("provider effect is not durably recorded")
        result = dict(row)
        self._assert_effect_row(result, intent)
        return result

    @staticmethod
    def _assert_effect_row(row: Mapping[str, object], intent: ProviderEffectIntent) -> None:
        target = intent.effect_intent.target
        expected = {
            "effect_id": intent.provider_effect_id,
            "operation_id": intent.effect_intent.operation_id,
            "owner_subject_id": target.owner_subject_id,
            "vault_id": target.vault_id,
            "resource_type": target.resource_type,
            "resource_id": target.resource_id,
            "resource_version": target.resource_version,
            "purpose": target.purpose,
            "authority_epoch": target.authority_epoch,
            "stable_key": intent.effect_intent.stable_key,
            "provider_name": intent.provider,
            "provider_effect_key_hash": intent.provider_effect_key,
            "request_hash": intent.request_hash,
            "capability": intent.capability,
            "contract_version": intent.contract_version,
            "provider_request_id_hash": intent.provider_request_id_hash,
        }
        if any(str(row.get(key)) != str(value) for key, value in expected.items()):
            raise ProviderEffectConflict("provider effect stable key is bound to different immutable evidence")

    def _insert_or_assert_receipt(self, cursor: Any, receipt: ProviderEffectReceipt) -> bool:
        target = receipt.intent.effect_intent.target
        cursor.execute(
            """
            INSERT INTO async_effects.provider_receipts (
                provider_receipt_id, effect_id, operation_id, owner_subject_id, vault_id,
                resource_type, resource_id, resource_version, purpose, authority_epoch,
                stable_key, provider_name, provider_receipt_hash, state, attempt,
                reason_code, observation_origin
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider_name, provider_receipt_hash) DO NOTHING
            RETURNING provider_receipt_id, effect_id, state, attempt, reason_code, observation_origin
            """,
            (
                receipt.provider_receipt_id,
                receipt.intent.provider_effect_id,
                receipt.intent.effect_intent.operation_id,
                target.owner_subject_id,
                target.vault_id,
                target.resource_type,
                target.resource_id,
                target.resource_version,
                target.purpose,
                target.authority_epoch,
                receipt.intent.effect_intent.stable_key,
                receipt.intent.provider,
                receipt.storage_receipt_hash,
                receipt.state.value,
                receipt.attempt,
                receipt.reason_code,
                receipt.observation_origin,
            ),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return True
        cursor.execute(
            """
            SELECT provider_receipt_id, effect_id, state, attempt, reason_code, observation_origin
            FROM async_effects.provider_receipts
            WHERE provider_name = %s AND provider_receipt_hash = %s
            FOR UPDATE
            """,
            (receipt.intent.provider, receipt.storage_receipt_hash),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise RuntimeError("provider receipt insert did not produce a row")
        self._assert_receipt_row(dict(existing), receipt)
        return False

    @staticmethod
    def _assert_receipt_row(row: Mapping[str, object], receipt: ProviderEffectReceipt) -> None:
        expected = {
            "provider_receipt_id": receipt.provider_receipt_id,
            "effect_id": receipt.intent.provider_effect_id,
            "state": receipt.state.value,
            "attempt": receipt.attempt,
            "reason_code": receipt.reason_code,
            "observation_origin": receipt.observation_origin,
        }
        if any(str(row.get(key)) != str(value) for key, value in expected.items()):
            raise ProviderEffectConflict("provider receipt hash is bound to different immutable evidence")

    def _assert_prior_unknown_receipt(self, cursor: Any, prior_unknown: ProviderEffectReceipt) -> None:
        cursor.execute(
            """
            SELECT provider_receipt_id, effect_id, state, attempt, reason_code, observation_origin
            FROM async_effects.provider_receipts
            WHERE effect_id = %s AND provider_name = %s AND provider_receipt_hash = %s
            FOR UPDATE
            """,
            (
                prior_unknown.intent.provider_effect_id,
                prior_unknown.intent.provider,
                prior_unknown.storage_receipt_hash,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise ProviderEffectContractError("the prior unknown receipt is not durably recorded")
        self._assert_receipt_row(dict(row), prior_unknown)
        if prior_unknown.state is not ProviderEffectState.UNKNOWN:
            raise ProviderEffectContractError("the prior receipt must be unknown")

    def _summary_from_projection(
        self,
        cursor: Any,
        *,
        outcome: str,
        intent: ProviderEffectIntent,
        receipt_hash: str,
        reconciliation_id: str | None = None,
    ) -> ProviderEffectPersistenceSummary:
        cursor.execute(
            """
            SELECT recorded_state, effective_state, reconciliation_status, requires_manual_review
            FROM async_effects.provider_effect_reconciliation_projection
            WHERE effect_id = %s
            """,
            (intent.provider_effect_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("provider effect reconciliation projection is missing")
        return ProviderEffectPersistenceSummary(
            outcome=outcome,
            intent=intent,
            effect_state=ProviderEffectState(str(row["recorded_state"])),
            effective_state=ProviderEffectState(str(row["effective_state"])),
            receipt_hash=receipt_hash,
            reconciliation_status=str(row["reconciliation_status"]),
            reconciliation_id=reconciliation_id,
            requires_manual_review=bool(row["requires_manual_review"]),
            reissue_allowed=False,
        )
