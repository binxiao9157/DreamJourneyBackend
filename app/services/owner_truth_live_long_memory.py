"""Durable, bounded coordination for long Live memory organization.

This module stores private intermediate reasoning state under the same
owner/vault/authority fence as the final Source.  It never publishes a
Candidate by itself; publication remains an atomic Owner Truth extraction
transaction after a frozen manifest has passed mechanical validation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import ceil
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID, uuid5

LIVE_LONG_MEMORY_PIPELINE_VERSION = "owner-truth-live-long-memory-v3"
LIVE_LONG_MEMORY_BUDGET_POLICY_VERSION = "live-long-memory-budget-v1"
LIVE_LONG_MEMORY_MANIFEST_VERSION = "owner-truth-live-publication-manifest-v2"
_RUN_NAMESPACE = UUID("fb3c0c8b-2177-4f37-9c86-ff71cd1934b1")
_UNIT_NAMESPACE = UUID("fa3d4166-8fef-407d-a6fd-5cc43a282c6f")
_ATTEMPT_NAMESPACE = UUID("84d10ea9-acde-4a88-95b4-e9de4eb955c0")
_ATOM_NAMESPACE = UUID("406b9983-c8ad-420d-863e-ecb2210d669b")
_MANIFEST_NAMESPACE = UUID("7bdcb632-bb26-431c-9e6a-d6f879f67ecb")


class LiveLongMemoryError(RuntimeError):
    pass


class LiveLongMemoryConflict(LiveLongMemoryError):
    pass


class LiveLongMemoryBudgetExhausted(LiveLongMemoryError):
    pass


class LiveLongMemoryManifestIncomplete(LiveLongMemoryError):
    pass


def _assert_run_deadline(
    started_at: Any,
    last_progress_at: Any,
    policy: LiveLongMemoryBudgetPolicy,
    now: datetime,
) -> None:
    if started_at is None or last_progress_at is None:
        raise LiveLongMemoryBudgetExhausted("Live memory run deadline coordinates are unavailable")
    started = datetime.fromisoformat(started_at) if isinstance(started_at, str) else started_at
    progressed = datetime.fromisoformat(last_progress_at) if isinstance(last_progress_at, str) else last_progress_at
    if (
        now >= started + timedelta(seconds=policy.absolute_deadline_seconds)
        or now >= progressed + timedelta(seconds=policy.inactivity_deadline_seconds)
    ):
        raise LiveLongMemoryBudgetExhausted("Live memory run deadline exhausted")


def _assert_request_deadline(started_at: Any, policy: LiveLongMemoryBudgetPolicy, now: datetime) -> None:
    if started_at is None:
        raise LiveLongMemoryBudgetExhausted("Live memory request deadline coordinate is unavailable")
    started = datetime.fromisoformat(started_at) if isinstance(started_at, str) else started_at
    if now >= started + timedelta(seconds=policy.request_deadline_seconds):
        raise LiveLongMemoryBudgetExhausted("Live memory request deadline exhausted")


def _run_can_claim_unit(run: Mapping[str, Any], policy: LiveLongMemoryBudgetPolicy, now: datetime) -> bool:
    started = run.get("organizationStartedAt")
    progressed = run.get("lastProgressAt")
    if started is None and progressed is None:
        return True  # Live preorganization has not bound its final Source yet.
    try:
        _assert_run_deadline(started, progressed, policy, now)
    except LiveLongMemoryBudgetExhausted:
        return False
    return True


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def conservative_token_estimate(value: Any) -> int:
    """Conservative byte-aware estimate; never treats characters as tokens."""

    encoded = _canonical_json(value).encode("utf-8")
    return max(1, len(encoded))


@dataclass(frozen=True)
class LiveLongMemoryBudgetPolicy:
    maximum_work_units: int = 2_048
    maximum_provider_requests: int = 2_048
    maximum_reserved_input_tokens: int = 32_000_000
    maximum_reserved_output_tokens: int = 8_000_000
    maximum_recovery_requests: int = 32
    maximum_unit_extra_requests: int = 1
    maximum_provider_concurrency: int = 2
    request_deadline_seconds: int = 90
    inactivity_deadline_seconds: int = 600
    absolute_deadline_seconds: int = 3_600
    planner_version: str = "live-request-planner-v1"
    contract_version: str = "live-memory-contract-v1"

    def snapshot(self) -> dict[str, Any]:
        return {"version": LIVE_LONG_MEMORY_BUDGET_POLICY_VERSION, **asdict(self)}

    @classmethod
    def from_snapshot(cls, value: Any, expected_hash: str | None) -> "LiveLongMemoryBudgetPolicy":
        if not isinstance(value, Mapping) or expected_hash is None:
            raise LiveLongMemoryConflict("Live memory run budget snapshot is unavailable")
        snapshot = dict(value)
        expected_keys = {"version", *(field.name for field in fields(cls))}
        if set(snapshot) != expected_keys or snapshot["version"] != LIVE_LONG_MEMORY_BUDGET_POLICY_VERSION:
            raise LiveLongMemoryConflict("Live memory run budget snapshot is unsupported")
        if _digest(snapshot) != expected_hash:
            raise LiveLongMemoryConflict("Live memory run budget snapshot changed")
        numbers = [field.name for field in fields(cls) if field.type in (int, "int")]
        if any(type(snapshot[name]) is not int or snapshot[name] <= 0 for name in numbers):
            raise LiveLongMemoryConflict("Live memory run budget snapshot is invalid")
        strings = [field.name for field in fields(cls) if field.name not in numbers]
        if any(type(snapshot[name]) is not str or not snapshot[name].strip() for name in strings):
            raise LiveLongMemoryConflict("Live memory run budget snapshot is invalid")
        return cls(**{field.name: snapshot[field.name] for field in fields(cls)})


@dataclass(frozen=True)
class LiveLongMemoryRunIdentity:
    owner_subject_id: str
    vault_id: str
    product_session_id: str
    capture_generation: int
    authority_epoch: int
    pipeline_version: str = LIVE_LONG_MEMORY_PIPELINE_VERSION

    def __post_init__(self) -> None:
        for field in ("owner_subject_id", "vault_id", "product_session_id", "pipeline_version"):
            if not str(getattr(self, field) or "").strip():
                raise LiveLongMemoryError(f"{field} is required")
        if (
            isinstance(self.capture_generation, bool)
            or not isinstance(self.capture_generation, int)
            or self.capture_generation < 1
        ):
            raise LiveLongMemoryError("capture_generation must be positive")
        if (
            isinstance(self.authority_epoch, bool)
            or not isinstance(self.authority_epoch, int)
            or self.authority_epoch < 0
        ):
            raise LiveLongMemoryError("authority_epoch must be non-negative")

    @property
    def run_id(self) -> str:
        return str(
            uuid5(
                _RUN_NAMESPACE,
                "|".join(
                    (
                        self.owner_subject_id,
                        self.vault_id,
                        self.product_session_id,
                        str(self.capture_generation),
                        self.pipeline_version,
                    )
                ),
            )
        )


@dataclass(frozen=True)
class LiveLongMemoryUnitPlan:
    run_id: str
    ordinal: int
    kind: str
    generation: int
    ownership: tuple[Mapping[str, Any], ...]
    context: tuple[Mapping[str, Any], ...] = ()
    parent_unit_id: str | None = None

    @property
    def input_hash(self) -> str:
        return _digest(
            {
                "context": list(self.context),
                "generation": self.generation,
                "kind": self.kind,
                "ownership": list(self.ownership),
                "parentUnitId": self.parent_unit_id,
            }
        )

    @property
    def unit_id(self) -> str:
        return str(uuid5(_UNIT_NAMESPACE, f"{self.run_id}:{self.kind}:{self.ordinal}:{self.input_hash}"))

    @property
    def budget_lineage_key(self) -> str:
        return self.parent_unit_id or self.unit_id


@dataclass(frozen=True)
class LiveLongMemoryProviderReservation:
    attempt_id: str
    run_id: str
    unit_id: str
    stage: str
    request_hash: str
    reserved_input_tokens: int
    reserved_output_tokens: int
    ordinal: int


@dataclass(frozen=True)
class LiveLongMemoryUnitLease:
    plan: LiveLongMemoryUnitPlan
    turns: tuple[Mapping[str, Any], ...]
    lease_owner: str
    lease_generation: int


@dataclass(frozen=True)
class LiveLongMemoryAtomRecord:
    atom_id: str
    run_id: str
    unit_id: str
    memory: Mapping[str, Any]
    source_turn_indices: tuple[int, ...]
    evidence_ranges: tuple[Mapping[str, Any], ...]
    evidence_hash: str
    state: str = "active"
    replacement_atom_id: str | None = None

    @classmethod
    def make(
        cls,
        *,
        run_id: str,
        unit_id: str,
        memory: Mapping[str, Any],
    ) -> "LiveLongMemoryAtomRecord":
        indices = tuple(dict.fromkeys(int(value) for value in memory.get("sourceTurnIndices") or ()))
        if not indices:
            raise LiveLongMemoryError("atom requires user evidence")
        ranges = tuple(
            deepcopy(dict(value))
            for value in memory.get("_sourceEvidenceRanges") or ()
        )
        if not ranges:
            raise LiveLongMemoryError("atom requires source evidence ranges")
        range_indices = {
            int(value.get("turnIndex"))
            for value in ranges
            if value.get("turnIndex") is not None
        }
        if set(indices) != range_indices:
            raise LiveLongMemoryError("atom evidence ranges do not match source turns")
        for value in ranges:
            start = value.get("start")
            end = value.get("end")
            text_hash = str(value.get("textHash") or "")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or not text_hash
            ):
                raise LiveLongMemoryError("atom evidence range is invalid")
        evidence_hash = _digest({"indices": indices, "ranges": ranges, "memory": dict(memory)})
        return cls(
            atom_id=str(uuid5(_ATOM_NAMESPACE, f"{run_id}:{unit_id}:{evidence_hash}")),
            run_id=run_id,
            unit_id=unit_id,
            memory=deepcopy(dict(memory)),
            source_turn_indices=indices,
            evidence_ranges=ranges,
            evidence_hash=evidence_hash,
        )


@dataclass(frozen=True)
class LiveLongMemoryManifest:
    manifest_id: str
    run_id: str
    source_id: str
    source_version: int
    source_content_hash: str
    generation: int
    items: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    manifest_hash: str


class LiveLongMemoryRepository(Protocol):
    def begin_or_load(
        self,
        identity: LiveLongMemoryRunIdentity,
        policy: LiveLongMemoryBudgetPolicy,
    ) -> Mapping[str, Any]: ...

    def bind_source(
        self,
        *,
        run_id: str,
        authority_epoch: int,
        source_id: str,
        source_version: int,
        source_content_hash: str,
        final_watermark: int,
    ) -> Mapping[str, Any]: ...

    def record_unit(self, plan: LiveLongMemoryUnitPlan) -> Mapping[str, Any]: ...

    def register_segment(
        self,
        *,
        identity: LiveLongMemoryRunIdentity,
        message_id: str,
        sequence: int,
        role: str,
        text: str,
        text_hash: str,
        policy: LiveLongMemoryBudgetPolicy,
    ) -> Mapping[str, Any]: ...

    def finalize_open_unit(
        self,
        *,
        run_id: str,
        policy: LiveLongMemoryBudgetPolicy,
    ) -> Mapping[str, Any] | None: ...

    def claim_planned_unit(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        maximum_concurrency: int = 2,
    ) -> LiveLongMemoryUnitLease | None: ...

    def reserve_provider_attempt(
        self,
        *,
        run_id: str,
        unit_id: str,
        stage: str,
        request_hash: str,
        reserved_input_tokens: int,
        reserved_output_tokens: int,
        recovery: bool,
        policy: LiveLongMemoryBudgetPolicy,
    ) -> LiveLongMemoryProviderReservation: ...

    def complete_provider_attempt(
        self,
        reservation: LiveLongMemoryProviderReservation,
        *,
        exposure_state: str,
        model: str,
        finish_reason: str | None,
        usage: Mapping[str, Any] | None,
        response_hash: str | None,
    ) -> None: ...

    def record_unit_result(
        self,
        *,
        plan: LiveLongMemoryUnitPlan,
        atoms: Sequence[LiveLongMemoryAtomRecord],
        output_hash: str,
        coverage: Mapping[str, Any],
        lease_owner: str | None = None,
        lease_generation: int | None = None,
    ) -> None: ...

    def record_unit_failure(
        self,
        *,
        plan: LiveLongMemoryUnitPlan,
        failure_code: str,
        terminal: bool,
        lease_owner: str | None = None,
        lease_generation: int | None = None,
    ) -> None: ...

    def freeze_manifest(self, manifest: LiveLongMemoryManifest) -> Mapping[str, Any]: ...

    def mark_published(
        self,
        *,
        run_id: str,
        manifest_hash: str,
        extraction_id: str,
        candidate_ids: Sequence[str],
    ) -> None: ...

    def snapshot(self, run_id: str) -> Mapping[str, Any] | None: ...


class InMemoryLiveLongMemoryRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._units: dict[str, dict[str, Any]] = {}
        self._attempts: dict[str, dict[str, Any]] = {}
        self._atoms: dict[str, dict[str, Any]] = {}
        self._manifests: dict[str, dict[str, Any]] = {}
        self._segments: dict[str, dict[str, Any]] = {}

    def begin_or_load(
        self,
        identity: LiveLongMemoryRunIdentity,
        policy: LiveLongMemoryBudgetPolicy,
    ) -> Mapping[str, Any]:
        with self._lock:
            existing = self._runs.get(identity.run_id)
            immutable = {
                "ownerSubjectId": identity.owner_subject_id,
                "vaultId": identity.vault_id,
                "productSessionId": identity.product_session_id,
                "captureGeneration": identity.capture_generation,
                "authorityEpoch": identity.authority_epoch,
                "pipelineVersion": identity.pipeline_version,
                "budgetPolicyVersion": LIVE_LONG_MEMORY_BUDGET_POLICY_VERSION,
            }
            if existing is not None:
                if any(existing[key] != value for key, value in immutable.items()):
                    raise LiveLongMemoryConflict("Live memory run identity changed")
                return deepcopy(existing)
            now = datetime.now(timezone.utc).isoformat()
            run = {
                "runId": identity.run_id,
                **immutable,
                "budgetPolicySnapshot": policy.snapshot(),
                "budgetPolicyHash": _digest(policy.snapshot()),
                "state": "collecting",
                "sourceId": None,
                "sourceVersion": None,
                "sourceContentHash": None,
                "finalWatermark": None,
                "plannedUnitCount": 0,
                "providerRequestCount": 0,
                "reservedInputTokens": 0,
                "reservedOutputTokens": 0,
                "recoveryRequestCount": 0,
                "manifestHash": None,
                "failureCode": None,
                "organizationStartedAt": None,
                "lastProgressAt": None,
                "createdAt": now,
                "updatedAt": now,
            }
            self._runs[identity.run_id] = run
            return deepcopy(run)

    def bind_source(self, **values: Any) -> Mapping[str, Any]:
        with self._lock:
            run = self._require_run(str(values["run_id"]))
            if int(run["authorityEpoch"]) != int(values["authority_epoch"]):
                raise LiveLongMemoryConflict("Live memory Source authority changed")
            binding = (
                str(values["source_id"]),
                int(values["source_version"]),
                str(values["source_content_hash"]),
                int(values["final_watermark"]),
            )
            current = (
                run.get("sourceId"),
                run.get("sourceVersion"),
                run.get("sourceContentHash"),
                run.get("finalWatermark"),
            )
            if current[0] is not None and current != binding:
                raise LiveLongMemoryConflict("Live memory run is bound to another Source")
            next_state = "failed" if run.get("state") == "failed" else "organizing"
            now = datetime.now(timezone.utc).isoformat()
            run.update(
                sourceId=binding[0],
                sourceVersion=binding[1],
                sourceContentHash=binding[2],
                finalWatermark=binding[3],
                state=next_state,
                organizationStartedAt=run.get("organizationStartedAt") or now,
                lastProgressAt=run.get("lastProgressAt") or now,
                updatedAt=now,
            )
            return deepcopy(run)

    def record_unit(self, plan: LiveLongMemoryUnitPlan) -> Mapping[str, Any]:
        with self._lock:
            run = self._require_run(plan.run_id)
            existing = self._units.get(plan.unit_id)
            immutable = {
                "runId": plan.run_id,
                "ordinal": plan.ordinal,
                "kind": plan.kind,
                "generation": plan.generation,
                "inputHash": plan.input_hash,
                "budgetLineageKey": plan.budget_lineage_key,
                "ownership": deepcopy(list(plan.ownership)),
                "context": deepcopy(list(plan.context)),
                "parentUnitId": plan.parent_unit_id,
            }
            if existing is not None:
                if any(existing[key] != value for key, value in immutable.items()):
                    raise LiveLongMemoryConflict("stable Live memory unit changed")
                return deepcopy(existing)
            unit = {
                "unitId": plan.unit_id,
                **immutable,
                "state": "planned",
                "outputHash": None,
                "coverage": None,
                "extraRequestCount": 0,
                "failureCode": None,
            }
            self._units[plan.unit_id] = unit
            run["plannedUnitCount"] += 1
            run["updatedAt"] = datetime.now(timezone.utc).isoformat()
            return deepcopy(unit)

    def register_segment(self, **values: Any) -> Mapping[str, Any]:
        identity: LiveLongMemoryRunIdentity = values["identity"]
        policy: LiveLongMemoryBudgetPolicy = values["policy"]
        self.begin_or_load(identity, policy)
        with self._lock:
            message_id = str(values["message_id"])
            existing = self._segments.get(message_id)
            segment = {
                "messageId": message_id,
                "runId": identity.run_id,
                "sequence": int(values["sequence"]),
                "role": str(values["role"]),
                "text": str(values["text"]),
                "textHash": str(values["text_hash"]),
                "unitId": None,
            }
            if existing is not None:
                if existing != segment:
                    raise LiveLongMemoryConflict("stable Live segment changed")
                return deepcopy(existing)
            self._segments[message_id] = segment
            if segment["role"] == "user":
                unassigned = self._unassigned_segments(identity.run_id)
                if sum(item["role"] == "user" for item in unassigned) > 8:
                    self._plan_segments(identity.run_id, unassigned[:-1], policy)
            return deepcopy(segment)

    def finalize_open_unit(self, **values: Any) -> Mapping[str, Any] | None:
        with self._lock:
            segments = self._unassigned_segments(str(values["run_id"]))
            if not any(item["role"] == "user" for item in segments):
                return None
            return self._plan_segments(
                str(values["run_id"]), segments, values["policy"]
            )

    def claim_planned_unit(self, **values: Any) -> LiveLongMemoryUnitLease | None:
        with self._lock:
            now = datetime.now(timezone.utc)
            maximum_concurrency = max(1, int(values.get("maximum_concurrency") or 2))
            planned = sorted(
                (
                    item
                    for item in self._units.values()
                    if (
                        (
                            item["state"] == "planned"
                            and (
                                item.get("leaseExpiresAt") is None
                                or datetime.fromisoformat(str(item["leaseExpiresAt"])) <= now
                            )
                        )
                        or (
                            item["state"] == "running"
                            and datetime.fromisoformat(str(item["leaseExpiresAt"])) <= now
                        )
                    )
                    and self._runs[item["runId"]].get("state") not in {
                        "failed", "published", "readyToPublish"
                    }
                    and _run_can_claim_unit(
                        self._runs[item["runId"]],
                        self._policy_for_run(self._runs[item["runId"]]),
                        now,
                    )
                    and sum(
                        candidate["runId"] == item["runId"]
                        and candidate["state"] == "running"
                        and datetime.fromisoformat(str(candidate["leaseExpiresAt"])) > now
                        for candidate in self._units.values()
                    ) < min(
                        maximum_concurrency,
                        self._policy_for_run(self._runs[item["runId"]]).maximum_provider_concurrency,
                    )
                ),
                key=lambda item: (item["runId"], item["ordinal"], item["unitId"]),
            )
            if not planned:
                return None
            unit = planned[0]
            unit["state"] = "running"
            unit["leaseOwner"] = str(values["worker_id"])
            unit["leaseGeneration"] = int(unit.get("leaseGeneration") or 0) + 1
            unit["leaseExpiresAt"] = (
                now + timedelta(seconds=int(values["lease_seconds"]))
            ).isoformat()
            segments = sorted(
                (item for item in self._segments.values() if item.get("unitId") == unit["unitId"]),
                key=lambda item: item["sequence"],
            )
            plan = LiveLongMemoryUnitPlan(
                run_id=unit["runId"], ordinal=unit["ordinal"], kind=unit["kind"],
                generation=unit["generation"], ownership=tuple(unit["ownership"]),
                context=tuple(unit["context"]), parent_unit_id=unit["parentUnitId"],
            )
            return LiveLongMemoryUnitLease(
                plan=plan,
                turns=tuple(
                    {
                        "index": item["sequence"], "role": item["role"],
                        "text": item["text"], "captureMode": "live",
                    }
                    for item in segments
                ),
                lease_owner=unit["leaseOwner"],
                lease_generation=unit["leaseGeneration"],
            )

    def _unassigned_segments(self, run_id: str) -> list[dict[str, Any]]:
        return sorted(
            (
                item for item in self._segments.values()
                if item["runId"] == run_id and item.get("unitId") is None
            ),
            key=lambda item: item["sequence"],
        )

    def _plan_segments(
        self,
        run_id: str,
        segments: Sequence[Mapping[str, Any]],
        policy: LiveLongMemoryBudgetPolicy,
    ) -> Mapping[str, Any]:
        if not segments or not any(item["role"] == "user" for item in segments):
            raise LiveLongMemoryConflict("Live unit requires user evidence")
        ordinal = sum(
            item["runId"] == run_id and item["kind"] == "atomExtraction"
            for item in self._units.values()
        )
        plan = LiveLongMemoryUnitPlan(
            run_id=run_id, ordinal=ordinal, kind="atomExtraction", generation=1,
            ownership=tuple(
                {"index": item["sequence"], "role": "user", "textHash": item["textHash"]}
                for item in segments if item["role"] == "user"
            ),
            context=tuple(
                {"index": item["sequence"], "role": "assistant", "textHash": item["textHash"]}
                for item in segments if item["role"] == "assistant"
            ),
        )
        run_unit_count = sum(
            item["runId"] == run_id for item in self._units.values()
        )
        frozen = self._policy_for_run(self._require_run(run_id))
        if run_unit_count >= frozen.maximum_work_units:
            raise LiveLongMemoryBudgetExhausted("Live memory run work-unit budget exhausted")
        unit = self.record_unit(plan)
        for item in segments:
            self._segments[item["messageId"]]["unitId"] = plan.unit_id
        return unit

    def reserve_provider_attempt(self, **values: Any) -> LiveLongMemoryProviderReservation:
        with self._lock:
            run = self._require_run(str(values["run_id"]))
            unit = self._units.get(str(values["unit_id"]))
            if unit is None or unit["runId"] != run["runId"]:
                raise LiveLongMemoryConflict("provider attempt unit is unavailable")
            policy = self._policy_for_run(run)
            if run.get("sourceId") is not None:
                _assert_run_deadline(run.get("organizationStartedAt"), run.get("lastProgressAt"), policy, datetime.now(timezone.utc))
            next_requests = int(run["providerRequestCount"]) + 1
            next_input = int(run["reservedInputTokens"]) + int(values["reserved_input_tokens"])
            next_output = int(run["reservedOutputTokens"]) + int(values["reserved_output_tokens"])
            next_recovery = int(run["recoveryRequestCount"]) + int(bool(values["recovery"]))
            if (
                int(run["plannedUnitCount"]) > policy.maximum_work_units
                or next_requests > policy.maximum_provider_requests
                or next_input > policy.maximum_reserved_input_tokens
                or next_output > policy.maximum_reserved_output_tokens
                or next_recovery > policy.maximum_recovery_requests
            ):
                raise LiveLongMemoryBudgetExhausted("Live memory run budget exhausted")
            if values["recovery"]:
                if int(unit["extraRequestCount"]) >= policy.maximum_unit_extra_requests:
                    raise LiveLongMemoryBudgetExhausted("Live memory unit retry budget exhausted")
                unit["extraRequestCount"] += 1
            ordinal = next_requests
            attempt_id = str(uuid5(_ATTEMPT_NAMESPACE, f"{run['runId']}:{ordinal}"))
            reservation = LiveLongMemoryProviderReservation(
                attempt_id=attempt_id,
                run_id=run["runId"],
                unit_id=unit["unitId"],
                stage=str(values["stage"]),
                request_hash=str(values["request_hash"]),
                reserved_input_tokens=int(values["reserved_input_tokens"]),
                reserved_output_tokens=int(values["reserved_output_tokens"]),
                ordinal=ordinal,
            )
            self._attempts[attempt_id] = {
                "attemptId": attempt_id,
                "runId": run["runId"],
                "unitId": unit["unitId"],
                "stage": reservation.stage,
                "requestHash": reservation.request_hash,
                "exposureState": "reserved",
                "reservedInputTokens": reservation.reserved_input_tokens,
                "reservedOutputTokens": reservation.reserved_output_tokens,
                "ordinal": ordinal,
                "reservedAt": datetime.now(timezone.utc).isoformat(),
            }
            run.update(
                providerRequestCount=next_requests,
                reservedInputTokens=next_input,
                reservedOutputTokens=next_output,
                recoveryRequestCount=next_recovery,
                updatedAt=datetime.now(timezone.utc).isoformat(),
            )
            return reservation

    def complete_provider_attempt(self, reservation: LiveLongMemoryProviderReservation, **values: Any) -> None:
        with self._lock:
            attempt = self._attempts.get(reservation.attempt_id)
            if attempt is None or attempt["requestHash"] != reservation.request_hash:
                raise LiveLongMemoryConflict("provider reservation is no longer current")
            attempt.update(
                exposureState=str(values["exposure_state"]),
                model=str(values["model"]),
                finishReason=values["finish_reason"],
                usage=deepcopy(dict(values["usage"] or {})),
                responseHash=values["response_hash"],
            )

    def record_unit_result(self, **values: Any) -> None:
        plan: LiveLongMemoryUnitPlan = values["plan"]
        with self._lock:
            unit = self._units.get(plan.unit_id)
            if unit is None or unit["inputHash"] != plan.input_hash:
                raise LiveLongMemoryConflict("Live memory unit input changed")
            run = self._require_run(plan.run_id)
            policy = self._policy_for_run(run)
            now = datetime.now(timezone.utc)
            if run.get("sourceId") is not None:
                _assert_run_deadline(run.get("organizationStartedAt"), run.get("lastProgressAt"), policy, now)
            attempts = [item for item in self._attempts.values() if item["unitId"] == plan.unit_id]
            if attempts:
                _assert_request_deadline(max(attempts, key=lambda item: item["ordinal"])["reservedAt"], policy, now)
            lease_owner = values.get("lease_owner")
            lease_generation = values.get("lease_generation")
            if lease_owner is not None or lease_generation is not None:
                if (
                    unit.get("state") != "running"
                    or unit.get("leaseOwner") != lease_owner
                    or int(unit.get("leaseGeneration") or 0)
                    != int(lease_generation or 0)
                ):
                    raise LiveLongMemoryConflict("Live memory unit lease is no longer current")
            output_hash = str(values["output_hash"])
            coverage = deepcopy(dict(values["coverage"]))
            if unit["state"] == "completed":
                if unit["outputHash"] != output_hash:
                    raise LiveLongMemoryConflict("completed Live memory unit output changed")
                if unit.get("coverage") != coverage:
                    raise LiveLongMemoryConflict("completed Live memory unit coverage changed")
                return
            atoms: Sequence[LiveLongMemoryAtomRecord] = values["atoms"]
            for atom in atoms:
                existing = self._atoms.get(atom.atom_id)
                payload = {
                    "atomId": atom.atom_id,
                    "runId": atom.run_id,
                    "unitId": atom.unit_id,
                    "memory": deepcopy(dict(atom.memory)),
                    "sourceTurnIndices": list(atom.source_turn_indices),
                    "evidenceRanges": deepcopy(list(atom.evidence_ranges)),
                    "evidenceHash": atom.evidence_hash,
                    "state": atom.state,
                    "replacementAtomId": atom.replacement_atom_id,
                }
                if existing is not None and existing != payload:
                    raise LiveLongMemoryConflict("stable Live memory atom changed")
                self._atoms[atom.atom_id] = payload
            unit.update(state="completed", outputHash=output_hash, coverage=coverage)
            if run.get("sourceId") is not None:
                run["lastProgressAt"] = now.isoformat()

    def record_unit_failure(self, **values: Any) -> None:
        plan: LiveLongMemoryUnitPlan = values["plan"]
        with self._lock:
            unit = self._units.get(plan.unit_id)
            if unit is None or unit["inputHash"] != plan.input_hash:
                raise LiveLongMemoryConflict("Live memory unit input changed")
            lease_owner = values.get("lease_owner")
            lease_generation = values.get("lease_generation")
            if lease_owner is not None or lease_generation is not None:
                if (
                    unit.get("state") != "running"
                    or unit.get("leaseOwner") != lease_owner
                    or int(unit.get("leaseGeneration") or 0)
                    != int(lease_generation or 0)
                ):
                    raise LiveLongMemoryConflict("Live memory unit lease is no longer current")
            failure_code = str(values["failure_code"])
            terminal = bool(values["terminal"])
            retry_seconds = max(0, int(values.get("retry_seconds") or 0))
            unit.update(
                state="failed" if terminal else "planned",
                failureCode=failure_code,
                leaseOwner=None,
                leaseExpiresAt=(
                    datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
                ).isoformat() if not terminal and retry_seconds else None,
            )
            run = self._require_run(plan.run_id)
            if terminal:
                run.update(state="failed", failureCode=failure_code)
            run["updatedAt"] = datetime.now(timezone.utc).isoformat()

    def freeze_manifest(self, manifest: LiveLongMemoryManifest) -> Mapping[str, Any]:
        with self._lock:
            run = self._require_run(manifest.run_id)
            if (
                run.get("sourceId") != manifest.source_id
                or run.get("sourceVersion") != manifest.source_version
                or run.get("sourceContentHash") != manifest.source_content_hash
            ):
                raise LiveLongMemoryConflict("manifest Source binding changed")
            existing = self._manifests.get(manifest.manifest_id)
            payload = {
                "manifestId": manifest.manifest_id,
                "runId": manifest.run_id,
                "sourceId": manifest.source_id,
                "sourceVersion": manifest.source_version,
                "sourceContentHash": manifest.source_content_hash,
                "generation": manifest.generation,
                "items": deepcopy(list(manifest.items)),
                "coverage": deepcopy(dict(manifest.coverage)),
                "manifestHash": manifest.manifest_hash,
                "state": "readyToPublish",
                "extractionId": None,
                "candidateIds": [],
            }
            if existing is not None and existing != payload:
                raise LiveLongMemoryConflict("frozen Live memory manifest changed")
            self._manifests[manifest.manifest_id] = payload
            run.update(state="readyToPublish", manifestHash=manifest.manifest_hash)
            return deepcopy(payload)

    def mark_published(self, **values: Any) -> None:
        with self._lock:
            run = self._require_run(str(values["run_id"]))
            if run.get("manifestHash") != str(values["manifest_hash"]):
                raise LiveLongMemoryConflict("published manifest is not current")
            manifest = next(
                (item for item in self._manifests.values() if item["runId"] == run["runId"]),
                None,
            )
            if manifest is None:
                raise LiveLongMemoryConflict("publication manifest is missing")
            candidate_ids = list(values["candidate_ids"])
            if manifest["state"] == "published":
                if (
                    manifest["extractionId"] != str(values["extraction_id"])
                    or manifest["candidateIds"] != candidate_ids
                ):
                    raise LiveLongMemoryConflict("published result changed")
                return
            manifest.update(
                state="published",
                extractionId=str(values["extraction_id"]),
                candidateIds=candidate_ids,
            )
            run["state"] = "published"

    def snapshot(self, run_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return {
                **deepcopy(run),
                "units": deepcopy(
                    sorted(
                        (unit for unit in self._units.values() if unit["runId"] == run_id),
                        key=lambda unit: (unit["ordinal"], unit["unitId"]),
                    )
                ),
                "attempts": deepcopy(
                    sorted(
                        (item for item in self._attempts.values() if item["runId"] == run_id),
                        key=lambda item: item["ordinal"],
                    )
                ),
                "atoms": deepcopy(
                    sorted(
                        (atom for atom in self._atoms.values() if atom["runId"] == run_id),
                        key=lambda atom: atom["atomId"],
                    )
                ),
                "manifests": deepcopy(
                    [item for item in self._manifests.values() if item["runId"] == run_id]
                ),
            }

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise LiveLongMemoryConflict("Live memory run is unavailable")
        return run

    @staticmethod
    def _policy_for_run(run: Mapping[str, Any]) -> LiveLongMemoryBudgetPolicy:
        return LiveLongMemoryBudgetPolicy.from_snapshot(
            run.get("budgetPolicySnapshot"), run.get("budgetPolicyHash")
        )


class PostgresLiveLongMemoryRepository:
    """Persist one Live organization run inside the caller's active UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    @staticmethod
    def _policy_for_run(row: Mapping[str, Any]) -> LiveLongMemoryBudgetPolicy:
        return LiveLongMemoryBudgetPolicy.from_snapshot(
            row.get("budget_policy_snapshot"), row.get("budget_policy_hash")
        )

    def begin_or_load(
        self,
        identity: LiveLongMemoryRunIdentity,
        policy: LiveLongMemoryBudgetPolicy,
    ) -> Mapping[str, Any]:
        snapshot = policy.snapshot()
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.live_memory_runs (
                    id, owner_subject_id, vault_id, product_session_id,
                    capture_generation, authority_epoch, pipeline_version,
                    budget_policy_version, budget_policy_snapshot, budget_policy_hash, state
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'collecting')
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    identity.run_id,
                    identity.owner_subject_id,
                    identity.vault_id,
                    identity.product_session_id,
                    identity.capture_generation,
                    identity.authority_epoch,
                    identity.pipeline_version,
                    LIVE_LONG_MEMORY_BUDGET_POLICY_VERSION,
                    self._json(snapshot), _digest(snapshot),
                ),
            )
            row = self._run_row(cursor, identity.run_id, lock=True)
            expected = (
                identity.owner_subject_id,
                identity.vault_id,
                identity.product_session_id,
                identity.capture_generation,
                identity.authority_epoch,
                identity.pipeline_version,
                LIVE_LONG_MEMORY_BUDGET_POLICY_VERSION,
            )
            actual = (
                str(row["owner_subject_id"]),
                str(row["vault_id"]),
                str(row["product_session_id"]),
                int(row["capture_generation"]),
                int(row["authority_epoch"]),
                str(row["pipeline_version"]),
                str(row["budget_policy_version"]),
            )
            if actual != expected:
                raise LiveLongMemoryConflict("Live memory run identity changed")
            self._policy_for_run(row)
            return self._run_payload(row)

    def bind_source(self, **values: Any) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            row = self._run_row(cursor, str(values["run_id"]), lock=True)
            if int(row["authority_epoch"]) != int(values["authority_epoch"]):
                raise LiveLongMemoryConflict("Live memory Source authority changed")
            binding = (
                str(values["source_id"]),
                int(values["source_version"]),
                str(values["source_content_hash"]),
                int(values["final_watermark"]),
            )
            current = (
                str(row["source_id"]) if row["source_id"] is not None else None,
                int(row["source_version"]) if row["source_version"] is not None else None,
                str(row["source_content_hash"]) if row["source_content_hash"] is not None else None,
                int(row["final_watermark"]) if row["final_watermark"] is not None else None,
            )
            if current[0] is not None and current != binding:
                raise LiveLongMemoryConflict("Live memory run is bound to another Source")
            cursor.execute(
                """
                UPDATE owner_truth.live_memory_runs
                   SET source_id = %s, source_version = %s, source_content_hash = %s,
                       final_watermark = %s,
                       organization_started_at = COALESCE(organization_started_at, NOW()),
                       last_progress_at = COALESCE(last_progress_at, NOW()),
                       state = CASE WHEN state = 'failed' THEN state ELSE 'organizing' END,
                       updated_at = NOW()
                 WHERE id = %s
                RETURNING *
                """,
                (*binding, values["run_id"]),
            )
            return self._run_payload(cursor.fetchone())

    def record_unit(self, plan: LiveLongMemoryUnitPlan) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            self._run_row(cursor, plan.run_id, lock=True)
            cursor.execute(
                """
                INSERT INTO owner_truth.live_memory_work_units (
                    id, run_id, ordinal, kind, generation, input_hash,
                    budget_lineage_key, ownership, context, parent_unit_id, state
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'planned')
                ON CONFLICT (id) DO NOTHING
                RETURNING *
                """,
                (
                    plan.unit_id, plan.run_id, plan.ordinal, plan.kind, plan.generation,
                    plan.input_hash, plan.budget_lineage_key,
                    self._json(list(plan.ownership)), self._json(list(plan.context)),
                    plan.parent_unit_id,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    """UPDATE owner_truth.live_memory_runs
                          SET planned_unit_count = planned_unit_count + 1, updated_at = NOW()
                        WHERE id = %s""",
                    (plan.run_id,),
                )
            else:
                cursor.execute(
                    "SELECT * FROM owner_truth.live_memory_work_units WHERE id = %s FOR UPDATE",
                    (plan.unit_id,),
                )
                row = cursor.fetchone()
            expected = (
                plan.run_id, plan.ordinal, plan.kind, plan.generation, plan.input_hash,
                plan.budget_lineage_key, list(plan.ownership), list(plan.context), plan.parent_unit_id,
            )
            actual = (
                str(row["run_id"]), int(row["ordinal"]), str(row["kind"]),
                int(row["generation"]), str(row["input_hash"]),
                str(row["budget_lineage_key"]), list(row["ownership"] or []),
                list(row["context"] or []),
                str(row["parent_unit_id"]) if row["parent_unit_id"] is not None else None,
            )
            if actual != expected:
                raise LiveLongMemoryConflict("stable Live memory unit changed")
            return self._unit_payload(row)

    def register_segment(self, **values: Any) -> Mapping[str, Any]:
        identity: LiveLongMemoryRunIdentity = values["identity"]
        policy: LiveLongMemoryBudgetPolicy = values["policy"]
        self.begin_or_load(identity, policy)
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.live_memory_segments (
                    message_id, run_id, sequence_number, role, text_hash
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING *
                """,
                (
                    values["message_id"], identity.run_id, values["sequence"],
                    values["role"], values["text_hash"],
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT * FROM owner_truth.live_memory_segments WHERE message_id = %s FOR UPDATE",
                    (values["message_id"],),
                )
                row = cursor.fetchone()
            expected = (
                identity.run_id, int(values["sequence"]), str(values["role"]),
                str(values["text_hash"]),
            )
            actual = (
                str(row["run_id"]), int(row["sequence_number"]), str(row["role"]),
                str(row["text_hash"]),
            )
            if actual != expected:
                raise LiveLongMemoryConflict("stable Live segment changed")
            if str(values["role"]) == "user":
                segments = self._unassigned_segment_rows(cursor, identity.run_id)
                if sum(str(item["role"]) == "user" for item in segments) > 8:
                    self._plan_segment_rows(cursor, identity.run_id, segments[:-1], policy)
            return self._segment_payload(row)

    def finalize_open_unit(self, **values: Any) -> Mapping[str, Any] | None:
        with self._cursor() as cursor:
            self._run_row(cursor, str(values["run_id"]), lock=True)
            segments = self._unassigned_segment_rows(cursor, str(values["run_id"]))
            if not any(str(item["role"]) == "user" for item in segments):
                return None
            return self._plan_segment_rows(
                cursor, str(values["run_id"]), segments, values["policy"]
            )

    def claim_planned_unit(self, **values: Any) -> LiveLongMemoryUnitLease | None:
        with self._cursor() as cursor:
            maximum_concurrency = max(1, int(values.get("maximum_concurrency") or 2))
            cursor.execute(
                """
                SELECT unit.*
                FROM owner_truth.live_memory_work_units AS unit
                JOIN owner_truth.live_memory_runs AS run_row
                  ON run_row.id = unit.run_id
                WHERE (
                    (unit.state = 'planned' AND (unit.lease_expires_at IS NULL OR unit.lease_expires_at <= NOW()))
                    OR (unit.state = 'running' AND unit.lease_expires_at < NOW())
                )
                  AND run_row.state NOT IN ('failed', 'published', 'readyToPublish')
                  AND (
                    run_row.source_id IS NULL
                    OR (
                      run_row.organization_started_at IS NOT NULL
                      AND run_row.last_progress_at IS NOT NULL
                      AND NOW() < run_row.organization_started_at
                          + ((run_row.budget_policy_snapshot ->> 'absolute_deadline_seconds')::integer * INTERVAL '1 second')
                      AND NOW() < run_row.last_progress_at
                          + ((run_row.budget_policy_snapshot ->> 'inactivity_deadline_seconds')::integer * INTERVAL '1 second')
                    )
                  )
                  AND (
                    SELECT COUNT(*)
                    FROM owner_truth.live_memory_work_units AS active
                    WHERE active.run_id = unit.run_id
                      AND active.state = 'running'
                      AND active.lease_expires_at >= NOW()
                  ) < LEAST(
                      %s,
                      (run_row.budget_policy_snapshot ->> 'maximum_provider_concurrency')::integer
                  )
                ORDER BY unit.created_at ASC, unit.run_id ASC, unit.ordinal ASC
                FOR UPDATE OF run_row, unit SKIP LOCKED
                LIMIT 1
                """,
                (maximum_concurrency,),
            )
            unit = cursor.fetchone()
            if unit is None:
                return None
            cursor.execute(
                """
                UPDATE owner_truth.live_memory_work_units
                   SET state = 'running', lease_owner = %s,
                       lease_generation = lease_generation + 1,
                       lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                       updated_at = NOW()
                 WHERE id = %s
                RETURNING *
                """,
                (values["worker_id"], int(values["lease_seconds"]), unit["id"]),
            )
            leased = cursor.fetchone()
            cursor.execute(
                """
                SELECT segment.sequence_number, segment.role, segment.text_hash,
                       message.content_payload ->> 'text' AS text,
                       message.content_payload ->> 'captureMode' AS capture_mode
                FROM owner_truth.live_memory_segments AS segment
                JOIN owner_truth.conversation_messages AS message
                  ON message.id = segment.message_id
                WHERE segment.unit_id = %s
                ORDER BY segment.sequence_number ASC
                """,
                (leased["id"],),
            )
            rows = cursor.fetchall()
            if not rows or any(str(row.get("text") or "").strip() == "" for row in rows):
                raise LiveLongMemoryConflict("planned Live unit transcript is unavailable")
            plan = LiveLongMemoryUnitPlan(
                run_id=str(leased["run_id"]), ordinal=int(leased["ordinal"]),
                kind=str(leased["kind"]), generation=int(leased["generation"]),
                ownership=tuple(leased["ownership"] or ()), context=tuple(leased["context"] or ()),
                parent_unit_id=str(leased["parent_unit_id"]) if leased["parent_unit_id"] else None,
            )
            return LiveLongMemoryUnitLease(
                plan=plan,
                turns=tuple(
                    {
                        "index": int(row["sequence_number"]),
                        "role": str(row["role"]),
                        "text": str(row["text"]),
                        "captureMode": str(row.get("capture_mode") or "live"),
                    }
                    for row in rows
                ),
                lease_owner=str(leased["lease_owner"]),
                lease_generation=int(leased["lease_generation"]),
            )

    @staticmethod
    def _unassigned_segment_rows(cursor: Any, run_id: str) -> list[Mapping[str, Any]]:
        cursor.execute(
            "SELECT * FROM owner_truth.live_memory_segments WHERE run_id = %s AND unit_id IS NULL ORDER BY sequence_number FOR UPDATE",
            (run_id,),
        )
        return list(cursor.fetchall())

    def _plan_segment_rows(
        self,
        cursor: Any,
        run_id: str,
        segments: Sequence[Mapping[str, Any]],
        policy: LiveLongMemoryBudgetPolicy,
    ) -> Mapping[str, Any]:
        if not segments or not any(str(item["role"]) == "user" for item in segments):
            raise LiveLongMemoryConflict("Live unit requires user evidence")
        cursor.execute(
            "SELECT COUNT(*) AS count FROM owner_truth.live_memory_work_units WHERE run_id = %s AND kind = 'atomExtraction'",
            (run_id,),
        )
        ordinal = int(cursor.fetchone()["count"])
        policy = self._policy_for_run(self._run_row(cursor, run_id, lock=True))
        if ordinal >= policy.maximum_work_units:
            raise LiveLongMemoryBudgetExhausted("Live memory run work-unit budget exhausted")
        plan = LiveLongMemoryUnitPlan(
            run_id=run_id, ordinal=ordinal, kind="atomExtraction", generation=1,
            ownership=tuple(
                {"index": int(item["sequence_number"]), "role": "user", "textHash": str(item["text_hash"])}
                for item in segments if str(item["role"]) == "user"
            ),
            context=tuple(
                {"index": int(item["sequence_number"]), "role": "assistant", "textHash": str(item["text_hash"])}
                for item in segments if str(item["role"]) == "assistant"
            ),
        )
        unit = self.record_unit(plan)
        cursor.execute(
            "UPDATE owner_truth.live_memory_segments SET unit_id = %s WHERE message_id = ANY(%s)",
            (plan.unit_id, [item["message_id"] for item in segments]),
        )
        return unit

    def reserve_provider_attempt(self, **values: Any) -> LiveLongMemoryProviderReservation:
        with self._cursor() as cursor:
            run = self._run_row(cursor, str(values["run_id"]), lock=True)
            policy = self._policy_for_run(run)
            if run["source_id"] is not None:
                _assert_run_deadline(
                    run.get("organization_started_at"), run.get("last_progress_at"),
                    policy, datetime.now(timezone.utc),
                )
            cursor.execute(
                "SELECT * FROM owner_truth.live_memory_work_units WHERE id = %s FOR UPDATE",
                (values["unit_id"],),
            )
            unit = cursor.fetchone()
            if unit is None or str(unit["run_id"]) != str(run["id"]):
                raise LiveLongMemoryConflict("provider attempt unit is unavailable")
            next_requests = int(run["provider_request_count"]) + 1
            next_input = int(run["reserved_input_tokens"]) + int(values["reserved_input_tokens"])
            next_output = int(run["reserved_output_tokens"]) + int(values["reserved_output_tokens"])
            next_recovery = int(run["recovery_request_count"]) + int(bool(values["recovery"]))
            if (
                int(run["planned_unit_count"]) > policy.maximum_work_units
                or next_requests > policy.maximum_provider_requests
                or next_input > policy.maximum_reserved_input_tokens
                or next_output > policy.maximum_reserved_output_tokens
                or next_recovery > policy.maximum_recovery_requests
            ):
                raise LiveLongMemoryBudgetExhausted("Live memory run budget exhausted")
            if values["recovery"] and int(unit["extra_request_count"]) >= policy.maximum_unit_extra_requests:
                raise LiveLongMemoryBudgetExhausted("Live memory unit retry budget exhausted")
            ordinal = next_requests
            attempt_id = str(uuid5(_ATTEMPT_NAMESPACE, f"{run['id']}:{ordinal}"))
            cursor.execute(
                """
                INSERT INTO owner_truth.live_memory_provider_attempts (
                    id, run_id, unit_id, stage, request_hash, exposure_state,
                    reserved_input_tokens, reserved_output_tokens, ordinal, recovery
                ) VALUES (%s, %s, %s, %s, %s, 'reserved', %s, %s, %s, %s)
                """,
                (
                    attempt_id, run["id"], unit["id"], values["stage"],
                    values["request_hash"], values["reserved_input_tokens"],
                    values["reserved_output_tokens"], ordinal, bool(values["recovery"]),
                ),
            )
            cursor.execute(
                """
                UPDATE owner_truth.live_memory_runs
                   SET provider_request_count = %s, reserved_input_tokens = %s,
                       reserved_output_tokens = %s, recovery_request_count = %s,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (next_requests, next_input, next_output, next_recovery, run["id"]),
            )
            if values["recovery"]:
                cursor.execute(
                    "UPDATE owner_truth.live_memory_work_units SET extra_request_count = extra_request_count + 1 WHERE id = %s",
                    (unit["id"],),
                )
            return LiveLongMemoryProviderReservation(
                attempt_id=attempt_id, run_id=str(run["id"]), unit_id=str(unit["id"]),
                stage=str(values["stage"]), request_hash=str(values["request_hash"]),
                reserved_input_tokens=int(values["reserved_input_tokens"]),
                reserved_output_tokens=int(values["reserved_output_tokens"]), ordinal=ordinal,
            )

    def complete_provider_attempt(self, reservation: LiveLongMemoryProviderReservation, **values: Any) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE owner_truth.live_memory_provider_attempts
                   SET exposure_state = %s, model = %s, finish_reason = %s,
                       usage = %s, response_hash = %s, completed_at = NOW()
                 WHERE id = %s AND request_hash = %s
                RETURNING id
                """,
                (
                    values["exposure_state"], values["model"], values["finish_reason"],
                    self._json(dict(values["usage"] or {})), values["response_hash"],
                    reservation.attempt_id, reservation.request_hash,
                ),
            )
            if cursor.fetchone() is None:
                raise LiveLongMemoryConflict("provider reservation is no longer current")

    def record_unit_result(self, **values: Any) -> None:
        plan: LiveLongMemoryUnitPlan = values["plan"]
        atoms: Sequence[LiveLongMemoryAtomRecord] = values["atoms"]
        output_hash = str(values["output_hash"])
        coverage = dict(values["coverage"])
        with self._cursor() as cursor:
            run = self._run_row(cursor, plan.run_id, lock=True)
            policy = self._policy_for_run(run)
            now = datetime.now(timezone.utc)
            if run["source_id"] is not None:
                _assert_run_deadline(
                    run.get("organization_started_at"), run.get("last_progress_at"), policy, now,
                )
            cursor.execute(
                "SELECT * FROM owner_truth.live_memory_work_units WHERE id = %s FOR UPDATE",
                (plan.unit_id,),
            )
            unit = cursor.fetchone()
            if unit is None or str(unit["input_hash"]) != plan.input_hash:
                raise LiveLongMemoryConflict("Live memory unit input changed")
            cursor.execute(
                "SELECT created_at FROM owner_truth.live_memory_provider_attempts WHERE unit_id = %s ORDER BY ordinal DESC LIMIT 1",
                (plan.unit_id,),
            )
            latest_attempt = cursor.fetchone()
            if latest_attempt is not None:
                _assert_request_deadline(latest_attempt["created_at"], policy, now)
            lease_owner = values.get("lease_owner")
            lease_generation = values.get("lease_generation")
            if lease_owner is not None or lease_generation is not None:
                if (
                    str(unit["state"]) != "running"
                    or str(unit["lease_owner"] or "") != str(lease_owner or "")
                    or int(unit["lease_generation"] or 0)
                    != int(lease_generation or 0)
                ):
                    raise LiveLongMemoryConflict("Live memory unit lease is no longer current")
            if str(unit["state"]) == "completed":
                if str(unit["output_hash"]) != output_hash:
                    raise LiveLongMemoryConflict("completed Live memory unit output changed")
                if dict(unit["output_coverage"] or {}) != coverage:
                    raise LiveLongMemoryConflict("completed Live memory unit coverage changed")
                return
            for atom in atoms:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.live_memory_atoms (
                        id, run_id, unit_id, memory, source_turn_indices,
                        evidence_hash, state, replacement_atom_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        atom.atom_id, atom.run_id, atom.unit_id, self._json(dict(atom.memory)),
                        self._json(list(atom.source_turn_indices)), atom.evidence_hash,
                        atom.state, atom.replacement_atom_id,
                    ),
                )
                cursor.execute("SELECT * FROM owner_truth.live_memory_atoms WHERE id = %s", (atom.atom_id,))
                row = cursor.fetchone()
                if (
                    str(row["run_id"]) != atom.run_id
                    or str(row["unit_id"]) != atom.unit_id
                    or dict(row["memory"] or {}) != dict(atom.memory)
                    or list(row["source_turn_indices"] or []) != list(atom.source_turn_indices)
                    or str(row["evidence_hash"]) != atom.evidence_hash
                ):
                    raise LiveLongMemoryConflict("stable Live memory atom changed")
            cursor.execute(
                "UPDATE owner_truth.live_memory_work_units SET state = 'completed', output_hash = %s, output_coverage = %s, updated_at = NOW() WHERE id = %s",
                (output_hash, self._json(coverage), plan.unit_id),
            )
            if run["source_id"] is not None:
                cursor.execute(
                    "UPDATE owner_truth.live_memory_runs SET last_progress_at = NOW() WHERE id = %s",
                    (plan.run_id,),
                )

    def record_unit_failure(self, **values: Any) -> None:
        plan: LiveLongMemoryUnitPlan = values["plan"]
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM owner_truth.live_memory_work_units WHERE id = %s FOR UPDATE",
                (plan.unit_id,),
            )
            unit = cursor.fetchone()
            if unit is None or str(unit["input_hash"]) != plan.input_hash:
                raise LiveLongMemoryConflict("Live memory unit input changed")
            lease_owner = values.get("lease_owner")
            lease_generation = values.get("lease_generation")
            if lease_owner is not None or lease_generation is not None:
                if (
                    str(unit["state"]) != "running"
                    or str(unit["lease_owner"] or "") != str(lease_owner or "")
                    or int(unit["lease_generation"] or 0)
                    != int(lease_generation or 0)
                ):
                    raise LiveLongMemoryConflict("Live memory unit lease is no longer current")
            failure_code = str(values["failure_code"])
            terminal = bool(values["terminal"])
            retry_seconds = max(0, int(values.get("retry_seconds") or 0))
            cursor.execute(
                """
                UPDATE owner_truth.live_memory_work_units
                   SET state = %s, failure_code = %s, lease_owner = NULL,
                       lease_expires_at = CASE WHEN %s > 0 THEN NOW() + (%s * INTERVAL '1 second') ELSE NULL END,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (
                    "failed" if terminal else "planned", failure_code,
                    0 if terminal else retry_seconds, 0 if terminal else retry_seconds,
                    plan.unit_id,
                ),
            )
            if terminal:
                cursor.execute(
                    "UPDATE owner_truth.live_memory_runs SET state = 'failed', failure_code = %s, updated_at = NOW() WHERE id = %s",
                    (failure_code, plan.run_id),
                )

    def freeze_manifest(self, manifest: LiveLongMemoryManifest) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            run = self._run_row(cursor, manifest.run_id, lock=True)
            if (
                str(run["source_id"]) != manifest.source_id
                or int(run["source_version"]) != manifest.source_version
                or str(run["source_content_hash"]) != manifest.source_content_hash
            ):
                raise LiveLongMemoryConflict("manifest Source binding changed")
            cursor.execute(
                """
                INSERT INTO owner_truth.live_memory_publication_manifests (
                    id, run_id, source_id, source_version, source_content_hash,
                    generation, items, coverage, manifest_hash, state
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'readyToPublish')
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    manifest.manifest_id, manifest.run_id, manifest.source_id,
                    manifest.source_version, manifest.source_content_hash, manifest.generation,
                    self._json(list(manifest.items)), self._json(dict(manifest.coverage)),
                    manifest.manifest_hash,
                ),
            )
            cursor.execute("SELECT * FROM owner_truth.live_memory_publication_manifests WHERE id = %s FOR UPDATE", (manifest.manifest_id,))
            row = cursor.fetchone()
            if (
                str(row["run_id"]) != manifest.run_id
                or str(row["manifest_hash"]) != manifest.manifest_hash
                or list(row["items"] or []) != list(manifest.items)
                or dict(row["coverage"] or {}) != dict(manifest.coverage)
            ):
                raise LiveLongMemoryConflict("frozen Live memory manifest changed")
            cursor.execute(
                "UPDATE owner_truth.live_memory_runs SET state = 'readyToPublish', manifest_hash = %s, updated_at = NOW() WHERE id = %s",
                (manifest.manifest_hash, manifest.run_id),
            )
            return self._manifest_payload(row)

    def mark_published(self, **values: Any) -> None:
        with self._cursor() as cursor:
            run = self._run_row(cursor, str(values["run_id"]), lock=True)
            if str(run["manifest_hash"] or "") != str(values["manifest_hash"]):
                raise LiveLongMemoryConflict("published manifest is not current")
            cursor.execute(
                "SELECT * FROM owner_truth.live_memory_publication_manifests WHERE run_id = %s FOR UPDATE",
                (run["id"],),
            )
            row = cursor.fetchone()
            if row is None:
                raise LiveLongMemoryConflict("publication manifest is missing")
            candidate_ids = list(values["candidate_ids"])
            if str(row["state"]) == "published":
                if str(row["extraction_id"]) != str(values["extraction_id"]) or list(row["candidate_ids"] or []) != candidate_ids:
                    raise LiveLongMemoryConflict("published result changed")
                return
            cursor.execute(
                """
                UPDATE owner_truth.live_memory_publication_manifests
                   SET state = 'published', extraction_id = %s, candidate_ids = %s,
                       published_at = NOW(), updated_at = NOW()
                 WHERE id = %s
                """,
                (values["extraction_id"], self._json(candidate_ids), row["id"]),
            )
            cursor.execute(
                "UPDATE owner_truth.live_memory_runs SET state = 'published', updated_at = NOW() WHERE id = %s",
                (run["id"],),
            )

    def snapshot(self, run_id: str) -> Mapping[str, Any] | None:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM owner_truth.live_memory_runs WHERE id = %s", (run_id,))
            run = cursor.fetchone()
            if run is None:
                return None
            cursor.execute("SELECT * FROM owner_truth.live_memory_work_units WHERE run_id = %s ORDER BY ordinal, id", (run_id,))
            units = [self._unit_payload(row) for row in cursor.fetchall()]
            cursor.execute("SELECT * FROM owner_truth.live_memory_provider_attempts WHERE run_id = %s ORDER BY ordinal", (run_id,))
            attempts = [self._attempt_payload(row) for row in cursor.fetchall()]
            cursor.execute("SELECT * FROM owner_truth.live_memory_atoms WHERE run_id = %s ORDER BY id", (run_id,))
            atoms = [self._atom_payload(row) for row in cursor.fetchall()]
            cursor.execute("SELECT * FROM owner_truth.live_memory_publication_manifests WHERE run_id = %s ORDER BY generation", (run_id,))
            manifests = [self._manifest_payload(row) for row in cursor.fetchall()]
            return {**self._run_payload(run), "units": units, "attempts": attempts, "atoms": atoms, "manifests": manifests}

    def _run_row(self, cursor: Any, run_id: str, *, lock: bool) -> Mapping[str, Any]:
        cursor.execute(
            f"SELECT * FROM owner_truth.live_memory_runs WHERE id = %s{' FOR UPDATE' if lock else ''}",
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LiveLongMemoryConflict("Live memory run is unavailable")
        return row

    @staticmethod
    def _run_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "runId": str(row["id"]), "ownerSubjectId": str(row["owner_subject_id"]),
            "vaultId": str(row["vault_id"]), "productSessionId": str(row["product_session_id"]),
            "captureGeneration": int(row["capture_generation"]), "authorityEpoch": int(row["authority_epoch"]),
            "pipelineVersion": str(row["pipeline_version"]), "budgetPolicyVersion": str(row["budget_policy_version"]),
            "budgetPolicySnapshot": dict(row["budget_policy_snapshot"]) if row.get("budget_policy_snapshot") is not None else None,
            "budgetPolicyHash": str(row["budget_policy_hash"]) if row.get("budget_policy_hash") else None,
            "state": str(row["state"]), "sourceId": str(row["source_id"]) if row["source_id"] is not None else None,
            "sourceVersion": int(row["source_version"]) if row["source_version"] is not None else None,
            "sourceContentHash": str(row["source_content_hash"]) if row["source_content_hash"] is not None else None,
            "finalWatermark": int(row["final_watermark"]) if row["final_watermark"] is not None else None,
            "plannedUnitCount": int(row["planned_unit_count"]), "providerRequestCount": int(row["provider_request_count"]),
            "reservedInputTokens": int(row["reserved_input_tokens"]), "reservedOutputTokens": int(row["reserved_output_tokens"]),
            "recoveryRequestCount": int(row["recovery_request_count"]),
            "manifestHash": str(row["manifest_hash"]) if row["manifest_hash"] is not None else None,
            "failureCode": str(row["failure_code"]) if row.get("failure_code") else None,
            "organizationStartedAt": row["organization_started_at"].isoformat() if row.get("organization_started_at") else None,
            "lastProgressAt": row["last_progress_at"].isoformat() if row.get("last_progress_at") else None,
        }

    @staticmethod
    def _unit_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "unitId": str(row["id"]), "runId": str(row["run_id"]), "ordinal": int(row["ordinal"]),
            "kind": str(row["kind"]), "generation": int(row["generation"]), "inputHash": str(row["input_hash"]),
            "budgetLineageKey": str(row["budget_lineage_key"]), "ownership": list(row["ownership"] or []),
            "context": list(row["context"] or []), "parentUnitId": str(row["parent_unit_id"]) if row["parent_unit_id"] else None,
            "state": str(row["state"]), "outputHash": str(row["output_hash"]) if row["output_hash"] else None,
            "extraRequestCount": int(row["extra_request_count"]),
            "failureCode": str(row["failure_code"]) if row.get("failure_code") else None,
            "coverage": dict(row["output_coverage"] or {}) if row["output_coverage"] is not None else None,
        }

    @staticmethod
    def _attempt_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "attemptId": str(row["id"]), "runId": str(row["run_id"]), "unitId": str(row["unit_id"]),
            "stage": str(row["stage"]), "requestHash": str(row["request_hash"]),
            "exposureState": str(row["exposure_state"]), "reservedInputTokens": int(row["reserved_input_tokens"]),
            "reservedOutputTokens": int(row["reserved_output_tokens"]), "ordinal": int(row["ordinal"]),
            "model": str(row["model"]) if row["model"] is not None else None,
            "finishReason": str(row["finish_reason"]) if row["finish_reason"] is not None else None,
            "usage": dict(row["usage"] or {}), "responseHash": str(row["response_hash"]) if row["response_hash"] else None,
        }

    @staticmethod
    def _atom_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "atomId": str(row["id"]), "runId": str(row["run_id"]), "unitId": str(row["unit_id"]),
            "memory": dict(row["memory"] or {}), "sourceTurnIndices": list(row["source_turn_indices"] or []),
            "evidenceRanges": list((row["memory"] or {}).get("_sourceEvidenceRanges") or []),
            "evidenceHash": str(row["evidence_hash"]), "state": str(row["state"]),
            "replacementAtomId": str(row["replacement_atom_id"]) if row["replacement_atom_id"] else None,
        }

    @staticmethod
    def _manifest_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "manifestId": str(row["id"]), "runId": str(row["run_id"]), "sourceId": str(row["source_id"]),
            "sourceVersion": int(row["source_version"]), "sourceContentHash": str(row["source_content_hash"]),
            "generation": int(row["generation"]), "items": list(row["items"] or []),
            "coverage": dict(row["coverage"] or {}), "manifestHash": str(row["manifest_hash"]),
            "state": str(row["state"]), "extractionId": str(row["extraction_id"]) if row["extraction_id"] else None,
            "candidateIds": list(row["candidate_ids"] or []),
        }

    @staticmethod
    def _segment_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "messageId": str(row["message_id"]), "runId": str(row["run_id"]),
            "sequence": int(row["sequence_number"]), "role": str(row["role"]),
            "textHash": str(row["text_hash"]),
            "unitId": str(row["unit_id"]) if row["unit_id"] else None,
        }

    @staticmethod
    def _json(value: Any) -> Any:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover
            return _canonical_json(value)
        return Jsonb(value)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class StoreBackedLiveLongMemoryRepository:
    """Give provider-side work short durable transactions without leaking a connection."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        correlation = kwargs.get("run_id")
        if correlation is None and args:
            correlation = getattr(args[0], "run_id", None)
        with self._store.request_unit_of_work(
            correlation_id=f"live-long-memory-{correlation or 'coordination'}",
            command_id=f"liveLongMemory:{name}:{correlation or 'coordination'}",
        ):
            repository = self._store.owner_truth_live_long_memory_repository()
            return getattr(repository, name)(*args, **kwargs)

    def begin_or_load(self, identity: LiveLongMemoryRunIdentity, policy: LiveLongMemoryBudgetPolicy) -> Mapping[str, Any]:
        return self._call("begin_or_load", identity, policy)

    def bind_source(self, **values: Any) -> Mapping[str, Any]: return self._call("bind_source", **values)
    def record_unit(self, plan: LiveLongMemoryUnitPlan) -> Mapping[str, Any]: return self._call("record_unit", plan)
    def register_segment(self, **values: Any) -> Mapping[str, Any]: return self._call("register_segment", **values)
    def finalize_open_unit(self, **values: Any) -> Mapping[str, Any] | None: return self._call("finalize_open_unit", **values)
    def claim_planned_unit(self, **values: Any) -> LiveLongMemoryUnitLease | None: return self._call("claim_planned_unit", **values)
    def reserve_provider_attempt(self, **values: Any) -> LiveLongMemoryProviderReservation: return self._call("reserve_provider_attempt", **values)
    def complete_provider_attempt(self, reservation: LiveLongMemoryProviderReservation, **values: Any) -> None: self._call("complete_provider_attempt", reservation, **values)
    def record_unit_result(self, **values: Any) -> None: self._call("record_unit_result", **values)
    def record_unit_failure(self, **values: Any) -> None: self._call("record_unit_failure", **values)
    def freeze_manifest(self, manifest: LiveLongMemoryManifest) -> Mapping[str, Any]: return self._call("freeze_manifest", manifest)
    def mark_published(self, **values: Any) -> None: self._call("mark_published", **values)
    def snapshot(self, run_id: str) -> Mapping[str, Any] | None: return self._call("snapshot", run_id)


def build_publication_manifest(
    *,
    run_snapshot: Mapping[str, Any],
    source_id: str,
    source_version: int,
    source_content_hash: str,
    generation: int,
    memories: Sequence[Mapping[str, Any]],
    required_user_turn_indices: Sequence[int],
    excluded_user_turn_indices: Sequence[int],
    retracted_atom_ids: Sequence[str] = (),
) -> LiveLongMemoryManifest:
    if run_snapshot.get("sourceId") != source_id or run_snapshot.get("sourceContentHash") != source_content_hash:
        raise LiveLongMemoryManifestIncomplete("manifest does not match the frozen Source")
    atoms = list(run_snapshot.get("atoms") or [])
    atom_by_id = {
        str(atom.get("atomId")): atom
        for atom in atoms
        if str(atom.get("atomId") or "")
    }
    if len(atom_by_id) != len(atoms):
        raise LiveLongMemoryManifestIncomplete("atom ledger contains duplicate or missing identifiers")
    covered = {
        int(index)
        for atom in atoms
        if atom.get("state") in {"active", "merged", "superseded", "retracted"}
        for index in atom.get("sourceTurnIndices") or []
    }
    required = {int(value) for value in required_user_turn_indices}
    excluded = {int(value) for value in excluded_user_turn_indices}
    if required - covered:
        raise LiveLongMemoryManifestIncomplete("fact-bearing Source turns are not covered")
    if required & excluded:
        raise LiveLongMemoryManifestIncomplete("fact-bearing Source turn cannot be excluded")
    if any(atom.get("state") == "unresolved" for atom in atoms):
        raise LiveLongMemoryManifestIncomplete("unresolved atoms block publication")

    replacement_by_atom: dict[str, str] = {}
    for atom_id, atom in atom_by_id.items():
        state = str(atom.get("state") or "active")
        replacement = str(atom.get("replacementAtomId") or "")
        if state in {"merged", "superseded"}:
            if not replacement or replacement == atom_id or replacement not in atom_by_id:
                raise LiveLongMemoryManifestIncomplete("atom replacement binding is invalid")
            replacement_by_atom[atom_id] = replacement
        elif replacement:
            raise LiveLongMemoryManifestIncomplete("active atom cannot carry a replacement")

    def terminal_replacement(atom_id: str) -> str:
        visited: set[str] = set()
        current = atom_id
        while current in replacement_by_atom:
            if current in visited:
                raise LiveLongMemoryManifestIncomplete("atom replacement cycle blocks publication")
            visited.add(current)
            current = replacement_by_atom[current]
        return current

    retired = {str(value) for value in retracted_atom_ids}
    retired.update(
        atom_id
        for atom_id, atom in atom_by_id.items()
        if str(atom.get("state") or "") == "retracted"
    )
    if retired - set(atom_by_id):
        raise LiveLongMemoryManifestIncomplete("retracted atom is not in the run ledger")

    assigned: dict[str, str] = {}
    item_payloads: list[Mapping[str, Any]] = []
    for memory in memories:
        private_memory = deepcopy(dict(memory))
        atom_ids = tuple(dict.fromkeys(str(value) for value in private_memory.pop("_atomIds", ()) if value))
        ranges = tuple(private_memory.pop("_sourceEvidenceRanges", ()) or ())
        support_hash = str(private_memory.pop("_supportProofHash", "") or "")
        if not atom_ids or not ranges or not support_hash:
            raise LiveLongMemoryManifestIncomplete("publication item lacks current atom support binding")
        unknown = set(atom_ids) - set(atom_by_id)
        if unknown:
            raise LiveLongMemoryManifestIncomplete("publication item references an unknown atom")
        for atom_id in atom_ids:
            terminal = terminal_replacement(atom_id)
            if terminal in retired:
                raise LiveLongMemoryManifestIncomplete("retracted atom cannot be published")
            if atom_id in assigned:
                raise LiveLongMemoryManifestIncomplete("atom is assigned to more than one publication item")
            assigned[atom_id] = support_hash
        item_payloads.append(
            {
                "itemId": _digest(
                    {
                        "runId": run_snapshot["runId"],
                        "memory": private_memory,
                        "atomIds": atom_ids,
                        "supportProofHash": support_hash,
                    }
                ),
                "memory": private_memory,
                "atomIds": list(atom_ids),
                "sourceEvidenceRanges": deepcopy(list(ranges)),
                "supportProofHash": support_hash,
            }
        )

    missing: list[str] = []
    for atom_id in atom_by_id:
        terminal = terminal_replacement(atom_id)
        if atom_id in retired or terminal in retired:
            continue
        if atom_id in assigned or terminal in assigned:
            continue
        missing.append(atom_id)
    if missing:
        raise LiveLongMemoryManifestIncomplete("recognized atoms do not all have a final disposition")
    items = tuple(item_payloads)
    coverage = {
        "requiredUserTurnIndices": sorted(required),
        "excludedUserTurnIndices": sorted(excluded),
        "coveredUserTurnIndices": sorted(covered),
        "assignedAtomIds": sorted(assigned),
        "retractedAtomIds": sorted(retired),
        "recognizedAtomCount": len(atom_by_id),
        "unitHashes": sorted(
            str(unit.get("outputHash") or "")
            for unit in run_snapshot.get("units") or []
            if unit.get("state") == "completed"
        ),
    }
    body = {
        "schemaVersion": LIVE_LONG_MEMORY_MANIFEST_VERSION,
        "runId": run_snapshot["runId"],
        "sourceId": source_id,
        "sourceVersion": source_version,
        "sourceContentHash": source_content_hash,
        "generation": generation,
        "items": items,
        "coverage": coverage,
    }
    manifest_hash = _digest(body)
    return LiveLongMemoryManifest(
        manifest_id=str(uuid5(_MANIFEST_NAMESPACE, f"{run_snapshot['runId']}:{generation}:{manifest_hash}")),
        run_id=str(run_snapshot["runId"]),
        source_id=source_id,
        source_version=source_version,
        source_content_hash=source_content_hash,
        generation=generation,
        items=items,
        coverage=coverage,
        manifest_hash=manifest_hash,
    )


__all__ = [
    "InMemoryLiveLongMemoryRepository",
    "LIVE_LONG_MEMORY_BUDGET_POLICY_VERSION",
    "LIVE_LONG_MEMORY_MANIFEST_VERSION",
    "LIVE_LONG_MEMORY_PIPELINE_VERSION",
    "LiveLongMemoryAtomRecord",
    "LiveLongMemoryBudgetExhausted",
    "LiveLongMemoryBudgetPolicy",
    "LiveLongMemoryConflict",
    "LiveLongMemoryError",
    "LiveLongMemoryManifest",
    "LiveLongMemoryManifestIncomplete",
    "LiveLongMemoryProviderReservation",
    "PostgresLiveLongMemoryRepository",
    "LiveLongMemoryRepository",
    "LiveLongMemoryRunIdentity",
    "LiveLongMemoryUnitPlan",
    "LiveLongMemoryUnitLease",
    "StoreBackedLiveLongMemoryRepository",
    "build_publication_manifest",
    "conservative_token_estimate",
]
