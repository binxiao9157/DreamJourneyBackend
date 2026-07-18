"""Immutable, value-free manifests for QA and release acceptance evidence.

The evidence sink stores only manifest metadata and SHA-256 artifact hashes.
It never receives report bodies, provider payloads, user content, local paths,
or direct account identifiers. A manifest is current only while its explicit
TTL is valid, even when a legal retention hold keeps its append-only row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Optional

from app.observability.events import EvidenceManifestEvent, validate_evidence_event


class EvidenceManifestError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_STATUS_REASON = {
    "passed": "manifestPassed",
    "failed": "manifestFailed",
    "blocked": "manifestBlocked",
    "notRun": "manifestNotRun",
    "legacyUnverified": "manifestLegacyUnverified",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evidence manifest time must include a timezone")
    return value.astimezone(timezone.utc)


def _as_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _utc(parsed)
    raise ValueError(f"{field} must be an ISO timestamp")


def _as_machine_sequence(value: Iterable[str], *, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array")
    return tuple(str(item).strip() for item in value)


class EvidenceManifestService:
    """Issue, query, and validate current evidence manifests."""

    policy_version = "evidenceManifest-v1"

    def __init__(
        self,
        *,
        environment: str,
        build: str,
        event_sink: Optional[Callable[..., Mapping[str, Any]]],
        event_source: Optional[Callable[..., Iterable[Mapping[str, Any]]]],
        retention_days: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.environment = str(environment or "runtime").strip() or "runtime"
        self.build = str(build or "backend").strip() or "backend"
        self._event_sink = event_sink
        self._event_source = event_source
        self._retention_days = max(1, int(retention_days))
        self._clock = clock

    def issue(
        self,
        *,
        manifest_type: str,
        source_commit: str,
        command_id: str,
        sample_count: int,
        sample_set_hash: str,
        exclusion_codes: Iterable[str],
        source_schema_versions: Iterable[str],
        artifact_hashes: Iterable[str],
        window_started_at: object,
        window_ended_at: object,
        issuer: str,
        manifest_status: str,
        build: Optional[str] = None,
        owner_lease_hash: Optional[str] = None,
        issued_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
    ) -> dict[str, Any]:
        if not callable(self._event_sink):
            raise EvidenceManifestError("evidenceManifestSinkUnavailable")

        issued = _utc(issued_at or self._clock())
        expires = _utc(expires_at) if expires_at is not None else issued + timedelta(
            days=self._retention_days
        )
        started = _as_datetime(window_started_at, field="windowStartedAt")
        ended = _as_datetime(window_ended_at, field="windowEndedAt")
        normalized_exclusions = _as_machine_sequence(
            exclusion_codes,
            field="exclusionCodes",
        )
        normalized_schemas = _as_machine_sequence(
            source_schema_versions,
            field="sourceSchemaVersions",
        )
        normalized_artifacts = _as_machine_sequence(
            artifact_hashes,
            field="artifactHashes",
        )
        event_seed = {
            "artifactHashes": normalized_artifacts,
            "build": str(build or self.build).strip(),
            "commandId": str(command_id).strip(),
            "expiresAt": expires.isoformat(),
            "issuedAt": issued.isoformat(),
            "manifestStatus": str(manifest_status).strip(),
            "manifestType": str(manifest_type).strip(),
            "sampleSetHash": str(sample_set_hash).strip(),
            "sourceCommit": str(source_commit).strip(),
        }
        event_fingerprint = hashlib.sha256(
            json.dumps(event_seed, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        event = EvidenceManifestEvent(
            eventId=f"evm-{event_fingerprint[:32]}",
            operationId="evidenceManifestIssue",
            correlationId=None,
            principalHash=None,
            resourceType="evidenceManifest",
            resourceIdHash=hashlib.sha256(
                f"evidence-manifest-v1|{event_fingerprint}".encode("utf-8")
            ).hexdigest(),
            state={
                "passed": "succeeded",
                "failed": "failed",
                "blocked": "denied",
                "notRun": "unknown",
                "legacyUnverified": "unknown",
            }.get(str(manifest_status).strip(), "unknown"),
            reason=_STATUS_REASON.get(str(manifest_status).strip(), "manifestUnknown"),
            occurredAt=issued,
            env=self.environment,
            build=str(build or self.build).strip(),
            manifestType=str(manifest_type).strip(),
            sourceCommit=str(source_commit).strip(),
            commandId=str(command_id).strip(),
            sampleCount=max(0, int(sample_count)),
            sampleSetHash=str(sample_set_hash).strip(),
            exclusionCodes=normalized_exclusions,
            sourceSchemaVersions=normalized_schemas,
            artifactHashes=normalized_artifacts,
            windowStartedAt=started,
            windowEndedAt=ended,
            issuedAt=issued,
            expiresAt=expires,
            issuer=str(issuer).strip(),
            manifestStatus=str(manifest_status).strip(),
            ownerLeaseHash=(str(owner_lease_hash).strip() if owner_lease_hash else None),
        )
        try:
            receipt = self._event_sink(
                event.model_dump(mode="json"),
                retention_class="verificationManifest",
                expires_at_iso=event.expiresAt.isoformat(),
                legal_hold=False,
            )
        except Exception as exc:
            raise EvidenceManifestError("evidenceManifestAppendFailed") from exc
        result = self._summary_for_event(event, now=issued)
        result.update(
            {
                "outcome": str(receipt.get("outcome") or "appended"),
                "payloadHash": str(receipt.get("payloadHash") or ""),
                "retentionClass": str(receipt.get("retentionClass") or "verificationManifest"),
            }
        )
        return result

    def list_manifests(
        self,
        *,
        now: Optional[datetime] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not callable(self._event_source):
            raise EvidenceManifestError("evidenceManifestQueryUnavailable")
        instant = _utc(now or self._clock())
        try:
            try:
                candidates = self._event_source(
                    event_type="evidenceManifest",
                    include_expired=True,
                    event_limit=max(1, min(int(limit), 500)),
                )
            except TypeError:
                candidates = self._event_source(event_type="evidenceManifest")
        except Exception as exc:
            raise EvidenceManifestError("evidenceManifestQueryFailed") from exc

        manifests: list[dict[str, Any]] = []
        for candidate in candidates:
            payload = candidate.get("payload") if isinstance(candidate, Mapping) else None
            decoded = validate_evidence_event(dict(payload or candidate))
            if isinstance(decoded, EvidenceManifestEvent):
                manifests.append(self._summary_for_event(decoded, now=instant))
        manifests.sort(key=lambda item: (str(item["issuedAt"]), str(item["evidenceId"])))
        status_counts: dict[str, int] = {}
        validity_counts: dict[str, int] = {}
        for manifest in manifests:
            status = str(manifest["manifestStatus"])
            validity = str(manifest["validity"])
            status_counts[status] = status_counts.get(status, 0) + 1
            validity_counts[validity] = validity_counts.get(validity, 0) + 1
        return {
            "schemaVersion": 1,
            "policyVersion": self.policy_version,
            "manifestCount": len(manifests),
            "currentPassedCount": sum(
                1
                for item in manifests
                if item["validity"] == "current" and item["manifestStatus"] == "passed"
            ),
            "statusCounts": dict(sorted(status_counts.items())),
            "validityCounts": dict(sorted(validity_counts.items())),
            "manifests": manifests[-max(1, min(int(limit), 500)):],
        }

    def verify_artifacts(
        self,
        *,
        evidence_id: str,
        artifact_hashes: Iterable[str],
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        manifests = self.list_manifests(now=now, limit=500).get("manifests") or []
        selected = next(
            (item for item in manifests if item.get("evidenceId") == str(evidence_id)),
            None,
        )
        if selected is None:
            return {
                "schemaVersion": 1,
                "evidenceId": str(evidence_id),
                "valid": False,
                "reason": "evidenceManifestMissing",
            }
        supplied = tuple(sorted(str(item).strip() for item in artifact_hashes))
        expected = tuple(sorted(str(item) for item in selected["artifactHashes"]))
        if selected["validity"] != "current":
            reason = "evidenceManifestExpired"
        elif selected["manifestStatus"] != "passed":
            reason = "evidenceManifestNotPassed"
        elif supplied != expected:
            reason = "artifactHashMismatch"
        else:
            reason = "verified"
        return {
            "schemaVersion": 1,
            "evidenceId": selected["evidenceId"],
            "valid": reason == "verified",
            "reason": reason,
            "manifestStatus": selected["manifestStatus"],
            "validity": selected["validity"],
        }

    @staticmethod
    def _summary_for_event(
        event: EvidenceManifestEvent,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        validity = "current" if event.expiresAt > now else "expired"
        return {
            "schemaVersion": event.schemaVersion,
            "manifestVersion": event.manifestVersion,
            "evidenceId": event.eventId,
            "manifestType": event.manifestType,
            "sourceCommit": event.sourceCommit,
            "build": event.build,
            "env": event.env,
            "commandId": event.commandId,
            "sampleCount": event.sampleCount,
            "sampleSetHash": event.sampleSetHash,
            "exclusionCodes": list(event.exclusionCodes),
            "sourceSchemaVersions": list(event.sourceSchemaVersions),
            "redactionVersion": event.redactionVersion,
            "artifactHashes": list(event.artifactHashes),
            "windowStartedAt": event.windowStartedAt.isoformat(),
            "windowEndedAt": event.windowEndedAt.isoformat(),
            "issuedAt": event.issuedAt.isoformat(),
            "expiresAt": event.expiresAt.isoformat(),
            "issuer": event.issuer,
            "manifestStatus": event.manifestStatus,
            "ownerLeasePresent": event.ownerLeaseHash is not None,
            "validity": validity,
        }
