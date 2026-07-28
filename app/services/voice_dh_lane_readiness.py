"""Value-free readiness observations for the Voice and Digital Human lanes.

This is deliberately an observation-only service.  It does not open a release
lane, call a provider, persist a decision, or inspect user/provider values.
The output is machine-safe aggregate readiness evidence for the existing
machine-only operations endpoint.  A human approval and acceptance evidence
remain mandatory even if every supplied input is synthetically positive.
"""

from __future__ import annotations

from typing import Any, Mapping


class VoiceDigitalHumanLaneReadinessService:
    """Summarize M1/M2/M3 readiness without granting a promotion."""

    SCHEMA_VERSION = 1
    POLICY_VERSION = "voiceDigitalHumanLaneReadiness-v1"

    def evaluate(
        self,
        *,
        capability_snapshots: Mapping[str, Any],
        provider_cost_evidence: Mapping[str, Any],
        incident_lifecycle: Mapping[str, Any],
        evidence_manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        voice = self._capability(capability_snapshots, "voiceCloneShell")
        digital_human = self._capability(
            capability_snapshots,
            "digitalHumanLivePanel",
        )
        cost = self._cost(provider_cost_evidence)
        incident = self._incident(incident_lifecycle)
        evidence = self._evidence(evidence_manifest)

        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "policyVersion": self.POLICY_VERSION,
            "promotionAllowed": False,
            "promotionMode": "manualOnly",
            "lanes": {
                "M1SelfVoice": self._voice_lane(
                    capability=voice,
                    cost=cost,
                    incident=incident,
                    evidence=evidence,
                ),
                "M2LivingDigitalHuman": self._digital_human_lane(
                    capability=digital_human,
                    cost=cost,
                    incident=incident,
                    evidence=evidence,
                ),
                "M3AdultMemorialPilot": self._memorial_lane(
                    cost=cost,
                    incident=incident,
                    evidence=evidence,
                ),
            },
        }

    def _voice_lane(
        self,
        *,
        capability: Mapping[str, Any],
        cost: Mapping[str, Any],
        incident: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        blockers = self._capability_blockers(
            capability=capability,
            prefix="voiceClone",
        )
        blockers.extend(self._shared_blockers(cost=cost, incident=incident, evidence=evidence))
        blockers.extend(
            (
                "voiceProviderExitReceiptRequired",
                "trueDeviceAcceptanceRequired",
                "manualPromotionRequired",
            )
        )
        return self._lane(
            release_stage="M1",
            feature="voiceCloneShell",
            blockers=blockers,
            capability=capability,
            cost=cost,
            incident=incident,
            evidence=evidence,
            provider_exit_requirement="voiceProviderExitReceiptRequired",
        )

    def _digital_human_lane(
        self,
        *,
        capability: Mapping[str, Any],
        cost: Mapping[str, Any],
        incident: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        blockers = self._capability_blockers(
            capability=capability,
            prefix="digitalHuman",
        )
        blockers.extend(self._shared_blockers(cost=cost, incident=incident, evidence=evidence))
        blockers.extend(
            (
                "digitalHumanSessionCleanupReceiptRequired",
                "trueDeviceAcceptanceRequired",
                "manualPromotionRequired",
            )
        )
        return self._lane(
            release_stage="M2",
            feature="digitalHumanLivePanel",
            blockers=blockers,
            capability=capability,
            cost=cost,
            incident=incident,
            evidence=evidence,
            provider_exit_requirement="digitalHumanSessionCleanupReceiptRequired",
        )

    def _memorial_lane(
        self,
        *,
        cost: Mapping[str, Any],
        incident: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        blockers = self._shared_blockers(cost=cost, incident=incident, evidence=evidence)
        blockers.extend(
            (
                "memorialPilotNotApproved",
                "adultMemorialPolicyEvidenceRequired",
                "rightsExitReceiptRequired",
                "trueDeviceAcceptanceRequired",
                "manualPromotionRequired",
            )
        )
        return self._lane(
            release_stage="M3",
            feature="adultMemorialPilot",
            blockers=blockers,
            capability={
                "implemented": False,
                "enabled": False,
                "providerReady": False,
                "externalVerified": False,
                "releaseVisible": False,
            },
            cost=cost,
            incident=incident,
            evidence=evidence,
            provider_exit_requirement="rightsExitReceiptRequired",
        )

    @staticmethod
    def _lane(
        *,
        release_stage: str,
        feature: str,
        blockers: list[str],
        capability: Mapping[str, Any],
        cost: Mapping[str, Any],
        incident: Mapping[str, Any],
        evidence: Mapping[str, Any],
        provider_exit_requirement: str,
    ) -> dict[str, Any]:
        return {
            "releaseStage": release_stage,
            "feature": feature,
            "status": "blocked",
            "promotionAllowed": False,
            "blockers": sorted(set(blockers)),
            "checks": {
                "runtimeCapability": {
                    "implemented": bool(capability["implemented"]),
                    "enabled": bool(capability["enabled"]),
                    "providerReady": bool(capability["providerReady"]),
                    "externalVerified": bool(capability["externalVerified"]),
                    "releaseVisible": bool(capability["releaseVisible"]),
                },
                "providerCost": {
                    "status": str(cost["status"]),
                    "costEvidenceComplete": bool(cost["costEvidenceComplete"]),
                    "costLimitEnforcementAllowed": bool(
                        cost["costLimitEnforcementAllowed"]
                    ),
                    "providerExpansionAllowed": bool(cost["providerExpansionAllowed"]),
                },
                "incident": {
                    "stopTheLine": bool(incident["stopTheLine"]),
                    "readinessStatus": str(incident["readinessStatus"]),
                },
                "evidenceManifest": {
                    "availability": str(evidence["availability"]),
                    "manifestCount": int(evidence["manifestCount"]),
                    "currentPassedCount": int(evidence["currentPassedCount"]),
                    "laneSpecificEvidenceReceipt": "required",
                },
                "providerExit": provider_exit_requirement,
                "qualityAcceptance": "profileLevelEvidenceRequired",
                "deviceAcceptance": "required",
                "promotion": "manualOnly",
            },
        }

    @staticmethod
    def _capability(
        snapshots: Mapping[str, Any],
        feature: str,
    ) -> dict[str, bool]:
        source = snapshots.get(feature)
        values = source if isinstance(source, Mapping) else {}
        return {
            "implemented": bool(values.get("implemented")),
            "enabled": bool(values.get("enabled")),
            "providerReady": bool(values.get("providerReady")),
            "externalVerified": bool(values.get("externalVerified")),
            "releaseVisible": bool(values.get("releaseVisible")),
        }

    @staticmethod
    def _cost(summary: Mapping[str, Any]) -> dict[str, Any]:
        readiness = summary.get("readiness")
        values = readiness if isinstance(readiness, Mapping) else {}
        status = str(values.get("status") or "notReady")
        return {
            "status": "ready" if status == "ready" else "notReady",
            "costEvidenceComplete": bool(values.get("costEvidenceComplete")),
            "costLimitEnforcementAllowed": bool(
                values.get("costLimitEnforcementAllowed")
            ),
            "providerExpansionAllowed": bool(values.get("providerExpansionAllowed")),
        }

    @staticmethod
    def _incident(summary: Mapping[str, Any]) -> dict[str, Any]:
        readiness = summary.get("readiness")
        values = readiness if isinstance(readiness, Mapping) else {}
        return {
            "stopTheLine": bool(summary.get("stopTheLine")),
            "readinessStatus": (
                "ready" if str(values.get("status") or "notReady") == "ready" else "notReady"
            ),
        }

    @staticmethod
    def _evidence(summary: Mapping[str, Any]) -> dict[str, Any]:
        availability = str(summary.get("availability") or "unavailable")
        return {
            "availability": "available" if availability == "available" else "unavailable",
            "manifestCount": max(0, int(summary.get("manifestCount") or 0)),
            "currentPassedCount": max(0, int(summary.get("currentPassedCount") or 0)),
        }

    @staticmethod
    def _capability_blockers(
        *,
        capability: Mapping[str, Any],
        prefix: str,
    ) -> list[str]:
        blockers: list[str] = []
        if not capability["implemented"]:
            blockers.append(f"{prefix}ImplementationMissing")
        if not capability["enabled"]:
            blockers.append(f"{prefix}RuntimeDisabled")
        if not capability["providerReady"]:
            blockers.append(f"{prefix}ProviderUnavailable")
        if not capability["externalVerified"]:
            blockers.append(f"{prefix}ExternalEvidenceMissing")
        if not capability["releaseVisible"]:
            blockers.append(f"{prefix}ReleaseNotApproved")
        return blockers

    @staticmethod
    def _shared_blockers(
        *,
        cost: Mapping[str, Any],
        incident: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if cost["status"] != "ready":
            blockers.append("providerCostNotReady")
        if incident["stopTheLine"]:
            blockers.append("incidentStopTheLine")
        if incident["readinessStatus"] != "ready":
            blockers.append("incidentReadinessNotReady")
        if evidence["availability"] != "available":
            blockers.append("evidenceManifestUnavailable")
        blockers.append("laneSpecificEvidenceReceiptRequired")
        return blockers
