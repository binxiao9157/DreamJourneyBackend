from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional

from app.observability.events import (
    IncidentEvidenceEvent,
    hash_evidence_identifier,
    normalize_machine_code,
    validate_evidence_event,
)


INCIDENT_OPERATION = "incidentLifecycle"
INCIDENT_AUDIT_RETENTION_CLASS = "incidentAudit"
_ACTIVE_STATES = {"open", "acknowledged", "fenced"}
_LIFECYCLE_EVENT_STATES = {
    "open": "started",
    "ack": "observed",
    "fence": "observed",
    "resolve": "succeeded",
    "reopen": "started",
}


class IncidentLifecycleError(ValueError):
    """Machine-safe command failure; no untrusted incident content is retained."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentLifecycleService:
    """Replay and mutate the minimal V4 incident lifecycle via append-only evidence.

    This intentionally does not create a second mutable incident aggregate. The
    current state is reconstructed from audited incident events, while one
    incident-scoped store lock serializes transition checks and writes.
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        store: Any,
        environment: str,
        build: str,
        ack_timeout_seconds: int = 900,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.store = store
        self.environment = self._machine_code(environment, "incidentEnvironment")
        self.build = self._machine_code(build, "incidentBuild")
        self.ack_timeout_seconds = max(60, min(int(ack_timeout_seconds), 86_400))
        self.clock = clock
        self._last_event_at: Optional[datetime] = None

    def open(
        self,
        *,
        incident_id: str,
        category: str,
        severity: str,
        owner: str,
        runbook_id: str,
        reason: str,
        required_fence_actions: Iterable[str],
        command_id: str,
        surface: str = "operations",
    ) -> Dict[str, Any]:
        normalized_id = self._machine_code(incident_id, "incidentId")
        normalized_category = self._machine_code(category, "incidentCategory")
        normalized_owner = self._machine_code(owner, "incidentOwner")
        normalized_runbook_id = self._machine_code(runbook_id, "incidentRunbookId")
        normalized_reason = self._machine_code(reason, "incidentReason")
        normalized_surface = self._machine_code(surface, "incidentSurface")
        normalized_command_id = self._machine_code(command_id, "incidentCommandId")
        normalized_severity = self._severity(severity)
        fences = self._fence_actions(required_fence_actions)
        if normalized_severity == "critical" and not fences:
            raise IncidentLifecycleError("criticalIncidentFencePlanRequired")

        with self._operation_scope(normalized_id):
            events = self._events()
            event_id = self._event_id(normalized_id, "open", normalized_command_id)
            replayed = self._deduplicated_response(event_id, events, normalized_id)
            if replayed is not None:
                return replayed
            incidents = self._replay(events)
            if normalized_id in incidents:
                raise IncidentLifecycleError("incidentAlreadyExists")
            occurred_at = self._event_now()
            event = self._make_event(
                incident_id=normalized_id,
                category=normalized_category,
                severity=normalized_severity,
                owner=normalized_owner,
                runbook_id=normalized_runbook_id,
                reason=normalized_reason,
                action="open",
                command_id=normalized_command_id,
                surface=normalized_surface,
                required_fence_actions=fences,
                ack_by_at=occurred_at + timedelta(seconds=self.ack_timeout_seconds),
                occurred_at=occurred_at,
            )
            return self._append(event, normalized_id)

    def acknowledge(
        self,
        *,
        incident_id: str,
        reason: str,
        command_id: str,
    ) -> Dict[str, Any]:
        normalized_id = self._machine_code(incident_id, "incidentId")
        normalized_reason = self._machine_code(reason, "incidentReason")
        normalized_command_id = self._machine_code(command_id, "incidentCommandId")
        with self._operation_scope(normalized_id):
            events = self._events()
            event_id = self._event_id(normalized_id, "ack", normalized_command_id)
            replayed = self._deduplicated_response(event_id, events, normalized_id)
            if replayed is not None:
                return replayed
            incident = self._incident_or_error(events, normalized_id)
            if incident["state"] != "open":
                raise IncidentLifecycleError("incidentAcknowledgementInvalidState")
            event = self._event_from_incident(
                incident,
                action="ack",
                reason=normalized_reason,
                command_id=normalized_command_id,
                occurred_at=self._event_now(),
            )
            return self._append(event, normalized_id)

    def fence(
        self,
        *,
        incident_id: str,
        reason: str,
        fence_actions: Iterable[str],
        command_id: str,
    ) -> Dict[str, Any]:
        normalized_id = self._machine_code(incident_id, "incidentId")
        normalized_reason = self._machine_code(reason, "incidentReason")
        normalized_command_id = self._machine_code(command_id, "incidentCommandId")
        requested_actions = self._fence_actions(fence_actions)
        if not requested_actions:
            raise IncidentLifecycleError("incidentFenceActionsRequired")
        with self._operation_scope(normalized_id):
            events = self._events()
            event_id = self._event_id(normalized_id, "fence", normalized_command_id)
            replayed = self._deduplicated_response(event_id, events, normalized_id)
            if replayed is not None:
                return replayed
            incident = self._incident_or_error(events, normalized_id)
            if incident["state"] not in {"acknowledged", "fenced"}:
                raise IncidentLifecycleError("incidentAcknowledgementRequired")
            required_actions = set(incident["requiredFenceActions"])
            existing_actions = set(incident["fenceActions"])
            if not set(requested_actions).issubset(required_actions):
                raise IncidentLifecycleError("incidentFenceActionNotPlanned")
            if set(requested_actions).issubset(existing_actions):
                raise IncidentLifecycleError("incidentFenceActionAlreadyRecorded")
            event = self._event_from_incident(
                incident,
                action="fence",
                reason=normalized_reason,
                command_id=normalized_command_id,
                fence_actions=requested_actions,
                occurred_at=self._event_now(),
            )
            return self._append(event, normalized_id)

    def resolve(
        self,
        *,
        incident_id: str,
        reason: str,
        evidence_ids: Iterable[str],
        command_id: str,
    ) -> Dict[str, Any]:
        normalized_id = self._machine_code(incident_id, "incidentId")
        normalized_reason = self._machine_code(reason, "incidentReason")
        normalized_command_id = self._machine_code(command_id, "incidentCommandId")
        evidence_hashes = self._evidence_hashes(evidence_ids)
        if not evidence_hashes:
            raise IncidentLifecycleError("incidentResolutionEvidenceRequired")
        with self._operation_scope(normalized_id):
            events = self._events()
            event_id = self._event_id(normalized_id, "resolve", normalized_command_id)
            replayed = self._deduplicated_response(event_id, events, normalized_id)
            if replayed is not None:
                return replayed
            incident = self._incident_or_error(events, normalized_id)
            if incident["state"] == "open":
                raise IncidentLifecycleError("incidentAcknowledgementRequired")
            if incident["state"] == "resolved":
                raise IncidentLifecycleError("incidentAlreadyResolved")
            if incident["fenceStatus"] != "complete" and incident["fenceStatus"] != "notRequired":
                raise IncidentLifecycleError("incidentFenceIncomplete")
            event = self._event_from_incident(
                incident,
                action="resolve",
                reason=normalized_reason,
                command_id=normalized_command_id,
                evidence_id_hashes=evidence_hashes,
                occurred_at=self._event_now(),
            )
            return self._append(event, normalized_id)

    def reopen(
        self,
        *,
        incident_id: str,
        new_incident_id: str,
        owner: str,
        reason: str,
        runbook_id: str,
        required_fence_actions: Iterable[str],
        command_id: str,
    ) -> Dict[str, Any]:
        source_id = self._machine_code(incident_id, "incidentId")
        normalized_id = self._machine_code(new_incident_id, "newIncidentId")
        normalized_owner = self._machine_code(owner, "incidentOwner")
        normalized_reason = self._machine_code(reason, "incidentReason")
        normalized_runbook_id = self._machine_code(runbook_id, "incidentRunbookId")
        normalized_command_id = self._machine_code(command_id, "incidentCommandId")
        fences = self._fence_actions(required_fence_actions)
        if source_id == normalized_id:
            raise IncidentLifecycleError("incidentReopenRequiresNewId")

        # The source and reopened incidents are both observed before the new
        # event is written. The new-id lock keeps independently reopened copies
        # from racing; the source must already be terminal.
        with self._operation_scope(normalized_id):
            events = self._events()
            event_id = self._event_id(normalized_id, "reopen", normalized_command_id)
            replayed = self._deduplicated_response(event_id, events, normalized_id)
            if replayed is not None:
                return replayed
            incidents = self._replay(events)
            source = incidents.get(source_id)
            if source is None:
                raise IncidentLifecycleError("incidentNotFound")
            if source["state"] != "resolved":
                raise IncidentLifecycleError("incidentReopenRequiresResolvedSource")
            if normalized_id in incidents:
                raise IncidentLifecycleError("incidentAlreadyExists")
            if source["severity"] == "critical" and not fences:
                raise IncidentLifecycleError("criticalIncidentFencePlanRequired")
            occurred_at = self._event_now()
            event = self._make_event(
                incident_id=normalized_id,
                category=source["category"],
                severity=source["severity"],
                owner=normalized_owner,
                runbook_id=normalized_runbook_id,
                reason=normalized_reason,
                action="reopen",
                command_id=normalized_command_id,
                surface=source["surface"],
                required_fence_actions=fences,
                ack_by_at=occurred_at + timedelta(seconds=self.ack_timeout_seconds),
                reopened_from=source_id,
                occurred_at=occurred_at,
            )
            return self._append(event, normalized_id)

    def get(self, incident_id: str) -> Dict[str, Any]:
        normalized_id = self._machine_code(incident_id, "incidentId")
        return deepcopy(self._incident_or_error(self._events(), normalized_id))

    def summary(self) -> Dict[str, Any]:
        try:
            incidents = list(self._replay(self._events()).values())
        except IncidentLifecycleError:
            return {
                "schemaVersion": self.SCHEMA_VERSION,
                "evidenceSource": "persistent",
                "incidentCount": 0,
                "activeIncidentCount": 0,
                "criticalActiveIncidentCount": 0,
                "ackOverdueCount": 0,
                "stopTheLine": True,
                "blockedLanes": [],
                "readiness": {
                    "status": "notReady",
                    "reason": "incidentEvidenceInvalid",
                },
                "integrityState": "invalid",
            }
        incidents.sort(key=lambda item: (str(item["openedAt"]), str(item["incidentId"])))
        active = [item for item in incidents if item["state"] in _ACTIVE_STATES]
        critical_active = [item for item in active if item["severity"] == "critical"]
        ack_overdue = [item for item in critical_active if item["ackOverdue"]]
        blocked_lanes = sorted(
            {
                action
                for item in critical_active
                for action in item["requiredFenceActions"]
            }
        )
        if ack_overdue:
            readiness_reason = "criticalIncidentAckOverdue"
        elif critical_active:
            readiness_reason = "criticalIncidentOpen"
        else:
            readiness_reason = "noCriticalIncidentOpen"
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "evidenceSource": "persistent",
            "incidentCount": len(incidents),
            "activeIncidentCount": len(active),
            "criticalActiveIncidentCount": len(critical_active),
            "ackOverdueCount": len(ack_overdue),
            "stopTheLine": bool(critical_active),
            "blockedLanes": blocked_lanes,
            "readiness": {
                "status": "notReady" if critical_active else "ready",
                "reason": readiness_reason,
            },
            "incidents": [deepcopy(item) for item in incidents],
            "integrityState": "valid",
        }

    def readiness_component(self) -> Dict[str, str]:
        summary = self.summary()
        readiness = dict(summary["readiness"])
        return {
            "component": "incident",
            "status": str(readiness["status"]),
            "reason": str(readiness["reason"]),
            "evidenceTimestamp": self._now().isoformat(),
        }

    def release_policy_block(self, feature: str) -> Optional[Dict[str, str]]:
        normalized_feature = self._machine_code(feature, "releasePolicyFeature")
        target_action = f"releasePolicy.{normalized_feature}"
        summary = self.summary()
        if summary.get("integrityState") != "valid":
            return {
                "incidentId": "incidentEvidence",
                "reason": "incidentEvidenceInvalid",
                "state": "unknown",
            }
        for incident in summary.get("incidents", []):
            if incident["severity"] != "critical" or incident["state"] not in _ACTIVE_STATES:
                continue
            actions = set(incident["requiredFenceActions"]) | set(incident["fenceActions"])
            if "releasePolicy.all" not in actions and target_action not in actions:
                continue
            reason = (
                "incidentFenceIncomplete"
                if incident["fenceStatus"] in {"notStarted", "partial"}
                else "incidentFenced"
            )
            return {
                "incidentId": str(incident["incidentId"]),
                "reason": reason,
                "state": str(incident["state"]),
            }
        return None

    def _append(self, event: IncidentEvidenceEvent, incident_id: str) -> Dict[str, Any]:
        append = getattr(self.store, "append_evidence_event", None)
        if not callable(append):
            raise IncidentLifecycleError("incidentEvidenceSinkUnavailable")
        try:
            receipt = append(
                event.model_dump(mode="json"),
                retention_class=INCIDENT_AUDIT_RETENTION_CLASS,
                expires_at_iso=None,
                legal_hold=False,
            )
        except Exception as exc:
            raise IncidentLifecycleError("incidentEvidenceAppendFailed") from exc
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "eventOutcome": str(receipt.get("outcome") or "appended"),
            "incident": self.get(incident_id),
            "summary": self.summary(),
        }

    def _deduplicated_response(
        self,
        event_id: str,
        events: list[IncidentEvidenceEvent],
        incident_id: str,
    ) -> Optional[Dict[str, Any]]:
        if not any(event.eventId == event_id for event in events):
            return None
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "eventOutcome": "deduplicated",
            "incident": self.get(incident_id),
            "summary": self.summary(),
        }

    def _events(self) -> list[IncidentEvidenceEvent]:
        lister = getattr(self.store, "list_evidence_events", None)
        if not callable(lister):
            raise IncidentLifecycleError("incidentEvidenceQueryUnavailable")
        try:
            records = lister(event_type="incident", operation=INCIDENT_OPERATION)
        except Exception as exc:
            raise IncidentLifecycleError("incidentEvidenceQueryFailed") from exc
        events: list[IncidentEvidenceEvent] = []
        for record in records:
            payload = record.get("payload") if isinstance(record, Mapping) else None
            if not isinstance(payload, Mapping):
                raise IncidentLifecycleError("incidentEvidenceInvalid")
            try:
                parsed = validate_evidence_event(dict(payload))
            except Exception as exc:
                raise IncidentLifecycleError("incidentEvidenceInvalid") from exc
            if not isinstance(parsed, IncidentEvidenceEvent):
                raise IncidentLifecycleError("incidentEvidenceInvalid")
            if parsed.operation != INCIDENT_OPERATION or not parsed.incidentId:
                continue
            events.append(parsed)
        events.sort(key=lambda item: (item.occurredAt, item.eventId))
        return events

    def _incident_or_error(
        self,
        events: list[IncidentEvidenceEvent],
        incident_id: str,
    ) -> Dict[str, Any]:
        incident = self._replay(events).get(incident_id)
        if incident is None:
            raise IncidentLifecycleError("incidentNotFound")
        return incident

    def _replay(self, events: list[IncidentEvidenceEvent]) -> Dict[str, Dict[str, Any]]:
        incidents: Dict[str, Dict[str, Any]] = {}
        for event in events:
            incident_id = str(event.incidentId or "")
            if not incident_id:
                continue
            action = event.action
            if action in {"open", "reopen"}:
                if incident_id in incidents:
                    raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                if action == "reopen":
                    source = str(event.reopenedFrom or "")
                    if not source or incidents.get(source, {}).get("state") != "resolved":
                        raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                incidents[incident_id] = {
                    "incidentId": incident_id,
                    "category": event.incidentClass,
                    "severity": event.severity,
                    "owner": str(event.owner or ""),
                    "runbookId": str(event.runbookId or ""),
                    "surface": str(event.surface or "operations"),
                    "reason": event.reason,
                    "state": "open",
                    "openedAt": event.occurredAt.isoformat(),
                    "ackByAt": event.ackByAt.isoformat() if event.ackByAt else None,
                    "ackedAt": None,
                    "fenceActions": [],
                    "requiredFenceActions": list(event.requiredFenceActions),
                    "fenceStatus": "notStarted" if event.requiredFenceActions else "notRequired",
                    "evidenceIdHashes": [],
                    "resolvedAt": None,
                    "reopenedFrom": event.reopenedFrom,
                    "eventCount": 1,
                }
                continue

            incident = incidents.get(incident_id)
            if incident is None or incident["state"] == "resolved":
                raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
            if event.incidentClass != incident["category"] or event.severity != incident["severity"]:
                raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
            if str(event.owner or "") != incident["owner"]:
                raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
            incident["eventCount"] = int(incident["eventCount"]) + 1
            incident["reason"] = event.reason

            if action == "ack":
                if incident["state"] != "open":
                    raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                incident["ackedAt"] = event.occurredAt.isoformat()
                incident["state"] = "acknowledged"
                continue
            if action == "fence":
                if incident["state"] not in {"acknowledged", "fenced"}:
                    raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                requested_actions = set(event.fenceActions)
                required_actions = set(incident["requiredFenceActions"])
                if not requested_actions or not requested_actions.issubset(required_actions):
                    raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                existing_actions = set(incident["fenceActions"])
                if requested_actions.issubset(existing_actions):
                    raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                applied = sorted(existing_actions | requested_actions)
                incident["fenceActions"] = applied
                if set(applied) == required_actions:
                    incident["fenceStatus"] = "complete"
                    incident["state"] = "fenced"
                else:
                    incident["fenceStatus"] = "partial"
                continue
            if action == "resolve":
                if incident["state"] == "open":
                    raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                if incident["fenceStatus"] not in {"complete", "notRequired"}:
                    raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")
                incident["evidenceIdHashes"] = sorted(set(event.evidenceIdHashes))
                incident["resolvedAt"] = event.occurredAt.isoformat()
                incident["state"] = "resolved"
                continue
            raise IncidentLifecycleError("incidentEvidenceSequenceInvalid")

        now = self._now()
        for incident in incidents.values():
            ack_by = self._parse_timestamp(incident.get("ackByAt"))
            incident["ackOverdue"] = bool(
                incident["state"] == "open" and ack_by is not None and now > ack_by
            )
        return incidents

    def _event_from_incident(
        self,
        incident: Mapping[str, Any],
        *,
        action: str,
        reason: str,
        command_id: str,
        fence_actions: Iterable[str] = (),
        evidence_id_hashes: Iterable[str] = (),
        occurred_at: datetime,
    ) -> IncidentEvidenceEvent:
        return self._make_event(
            incident_id=str(incident["incidentId"]),
            category=str(incident["category"]),
            severity=str(incident["severity"]),
            owner=str(incident["owner"]),
            runbook_id=str(incident["runbookId"]),
            reason=reason,
            action=action,
            command_id=command_id,
            surface=str(incident["surface"]),
            required_fence_actions=tuple(incident["requiredFenceActions"]),
            fence_actions=tuple(fence_actions),
            evidence_id_hashes=tuple(evidence_id_hashes),
            ack_by_at=self._parse_timestamp(incident.get("ackByAt")),
            occurred_at=occurred_at,
        )

    def _make_event(
        self,
        *,
        incident_id: str,
        category: str,
        severity: str,
        owner: str,
        runbook_id: str,
        reason: str,
        action: str,
        command_id: str,
        surface: str,
        required_fence_actions: Iterable[str],
        occurred_at: datetime,
        fence_actions: Iterable[str] = (),
        evidence_id_hashes: Iterable[str] = (),
        ack_by_at: Optional[datetime] = None,
        reopened_from: Optional[str] = None,
    ) -> IncidentEvidenceEvent:
        normalized_action = self._machine_code(action, "incidentAction")
        event_state = _LIFECYCLE_EVENT_STATES.get(normalized_action)
        if event_state is None:
            raise IncidentLifecycleError("incidentActionUnsupported")
        lifecycle_state = {
            "open": "open",
            "ack": "acknowledged",
            "fence": "fenced",
            "resolve": "resolved",
            "reopen": "open",
        }[normalized_action]
        return IncidentEvidenceEvent(
            eventId=self._event_id(incident_id, normalized_action, command_id),
            operationId=self._operation_id(incident_id),
            correlationId=None,
            principalHash=None,
            resourceType="incident",
            resourceIdHash=hash_evidence_identifier(incident_id),
            state=event_state,
            reason=reason,
            attempt=self._event_attempt(incident_id),
            occurredAt=occurred_at,
            env=self.environment,
            build=self.build,
            type="incident",
            incidentClass=category,
            severity=severity,  # type: ignore[arg-type]
            action=normalized_action,
            surface=surface,
            operation=INCIDENT_OPERATION,
            incidentId=incident_id,
            incidentState=lifecycle_state,  # type: ignore[arg-type]
            owner=owner,
            runbookId=runbook_id,
            requiredFenceActions=tuple(required_fence_actions),
            fenceActions=tuple(fence_actions),
            evidenceIdHashes=tuple(evidence_id_hashes),
            ackByAt=ack_by_at,
            reopenedFrom=reopened_from,
        )

    def _event_attempt(self, incident_id: str) -> int:
        existing = self._replay(self._events()).get(incident_id)
        return min(1_000, int(existing["eventCount"]) + 1) if existing else 1

    def _event_id(self, incident_id: str, action: str, command_id: str) -> str:
        return f"evt_inc_{self._digest(f'{incident_id}|{action}|{command_id}')[:40]}"

    def _operation_id(self, incident_id: str) -> str:
        return f"op_inc_{self._digest(incident_id)[:40]}"

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(f"incident-lifecycle-v1|{value}".encode("utf-8")).hexdigest()

    def _operation_scope(self, incident_id: str) -> Iterator[None]:
        scope = getattr(self.store, "incident_operation", None)
        if not callable(scope):
            return nullcontext()
        return scope(incident_id)

    def _fence_actions(self, values: Iterable[str]) -> tuple[str, ...]:
        candidates = self._machine_code_collection(values, "incidentFenceActions")
        for action in candidates:
            if not self._supported_fence_action(action):
                raise IncidentLifecycleError("incidentFenceActionUnsupported")
        return candidates

    def _evidence_hashes(self, values: Iterable[str]) -> tuple[str, ...]:
        candidates = self._machine_code_collection(values, "incidentEvidenceIds")
        return tuple(sorted({hash_evidence_identifier(value) for value in candidates}))

    def _machine_code_collection(
        self,
        values: Iterable[str],
        field: str,
    ) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
            raise IncidentLifecycleError("incidentCommandInvalid")
        return tuple(sorted({self._machine_code(value, field) for value in values}))

    @staticmethod
    def _supported_fence_action(value: str) -> bool:
        if value.startswith("releasePolicy."):
            return value != "releasePolicy."
        if value.startswith("provider."):
            return value != "provider."
        return value in {
            "migration.goNoGo",
            "credentialRotation.stop",
            "readiness.degrade",
        }

    @staticmethod
    def _severity(value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in {"info", "warning", "critical"}:
            raise IncidentLifecycleError("incidentSeverityInvalid")
        return normalized

    @staticmethod
    def _machine_code(value: str, _field: str) -> str:
        try:
            return normalize_machine_code(str(value or "").strip())
        except Exception as exc:
            raise IncidentLifecycleError("incidentCommandInvalid") from exc

    def _now(self) -> datetime:
        instant = self.clock()
        if not isinstance(instant, datetime):
            raise IncidentLifecycleError("incidentClockInvalid")
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise IncidentLifecycleError("incidentClockInvalid")
        return instant.astimezone(timezone.utc)

    def _event_now(self) -> datetime:
        instant = self._now()
        if self._last_event_at is not None and instant <= self._last_event_at:
            instant = self._last_event_at + timedelta(microseconds=1)
        self._last_event_at = instant
        return instant

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise IncidentLifecycleError("incidentEvidenceInvalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise IncidentLifecycleError("incidentEvidenceInvalid")
        return parsed.astimezone(timezone.utc)
