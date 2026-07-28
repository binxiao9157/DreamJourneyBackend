"""Tests for the value-free Voice/Digital Human lane-readiness observation."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.voice_dh_lane_readiness import VoiceDigitalHumanLaneReadinessService


def _capability(
    *,
    enabled: bool = False,
    provider_ready: bool = False,
    external_verified: bool = False,
    release_visible: bool = False,
    private_marker: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "implemented": True,
        "enabled": enabled,
        "providerReady": provider_ready,
        "externalVerified": external_verified,
        "releaseVisible": release_visible,
        "provider": "provider-private-value-must-not-leak",
        "reason": "provider-private-reason-must-not-leak",
    }
    if private_marker is not None:
        payload["privateMarker"] = private_marker
    return payload


def _inputs(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "capability_snapshots": {
            "voiceCloneShell": _capability(),
            "digitalHumanLivePanel": _capability(),
        },
        "provider_cost_evidence": {
            "readiness": {
                "status": "notReady",
                "costEvidenceComplete": False,
                "costLimitEnforcementAllowed": False,
                "providerExpansionAllowed": False,
                "reason": "commercialDecisionDeferred",
            }
        },
        "incident_lifecycle": {
            "stopTheLine": False,
            "readiness": {"status": "ready"},
        },
        "evidence_manifest": {
            "availability": "available",
            "manifestCount": 0,
            "currentPassedCount": 0,
        },
    }
    values.update(changes)
    return values


class VoiceDigitalHumanLaneReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = VoiceDigitalHumanLaneReadinessService()

    def test_default_runtime_is_blocked_with_runtime_cost_and_evidence_reasons(self) -> None:
        result = self.service.evaluate(**_inputs())

        self.assertEqual(result["schemaVersion"], 1)
        self.assertFalse(result["promotionAllowed"])
        self.assertEqual(set(result["lanes"]), {"M1SelfVoice", "M2LivingDigitalHuman", "M3AdultMemorialPilot"})
        for lane in result["lanes"].values():
            self.assertEqual(lane["status"], "blocked")
            self.assertFalse(lane["promotionAllowed"])
            self.assertIn("manualPromotionRequired", lane["blockers"])

        m1 = result["lanes"]["M1SelfVoice"]
        self.assertIn("voiceCloneRuntimeDisabled", m1["blockers"])
        self.assertIn("voiceCloneProviderUnavailable", m1["blockers"])
        self.assertIn("voiceCloneExternalEvidenceMissing", m1["blockers"])
        self.assertIn("voiceCloneReleaseNotApproved", m1["blockers"])
        self.assertIn("providerCostNotReady", m1["blockers"])
        self.assertIn("laneSpecificEvidenceReceiptRequired", m1["blockers"])
        self.assertIn("trueDeviceAcceptanceRequired", m1["blockers"])
        self.assertIn("voiceProviderExitReceiptRequired", m1["blockers"])

        m3 = result["lanes"]["M3AdultMemorialPilot"]
        self.assertIn("memorialPilotNotApproved", m3["blockers"])

    def test_manifests_and_synthetic_positive_inputs_never_auto_promote(self) -> None:
        result = self.service.evaluate(
            **_inputs(
                capability_snapshots={
                    "voiceCloneShell": _capability(
                        enabled=True,
                        provider_ready=True,
                        external_verified=True,
                        release_visible=True,
                    ),
                    "digitalHumanLivePanel": _capability(
                        enabled=True,
                        provider_ready=True,
                        external_verified=True,
                        release_visible=True,
                    ),
                },
                provider_cost_evidence={
                    "readiness": {
                        "status": "ready",
                        "costEvidenceComplete": True,
                        "costLimitEnforcementAllowed": True,
                        "providerExpansionAllowed": True,
                    }
                },
                evidence_manifest={
                    "availability": "available",
                    "manifestCount": 8,
                    "currentPassedCount": 8,
                },
            )
        )

        self.assertFalse(result["promotionAllowed"])
        for lane in result["lanes"].values():
            self.assertEqual(lane["status"], "blocked")
            self.assertFalse(lane["promotionAllowed"])
            self.assertIn("manualPromotionRequired", lane["blockers"])
        self.assertIn(
            "memorialPilotNotApproved",
            result["lanes"]["M3AdultMemorialPilot"]["blockers"],
        )

    def test_stop_the_line_incident_blocks_voice_and_digital_human_lanes(self) -> None:
        result = self.service.evaluate(
            **_inputs(
                incident_lifecycle={
                    "stopTheLine": True,
                    "readiness": {"status": "notReady"},
                    "privateIncidentId": "incident-private-value-must-not-leak",
                }
            )
        )

        self.assertIn("incidentStopTheLine", result["lanes"]["M1SelfVoice"]["blockers"])
        self.assertIn(
            "incidentStopTheLine",
            result["lanes"]["M2LivingDigitalHuman"]["blockers"],
        )

    def test_private_input_values_never_enter_readiness_summary(self) -> None:
        private_marker = "private-voice-dh-marker-must-not-leak"
        result = self.service.evaluate(
            **_inputs(
                capability_snapshots={
                    "voiceCloneShell": _capability(private_marker=private_marker),
                    "digitalHumanLivePanel": _capability(private_marker=private_marker),
                },
                provider_cost_evidence={
                    "readiness": {"status": "notReady", "private": private_marker}
                },
                evidence_manifest={
                    "availability": "available",
                    "manifestCount": 1,
                    "currentPassedCount": 1,
                    "private": private_marker,
                },
            )
        )

        self.assertNotIn(private_marker, repr(result))
        self.assertNotIn("provider-private-value-must-not-leak", repr(result))


class VoiceDigitalHumanLaneReadinessAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_token = main_module.BACKEND_API_TOKEN
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = "voice-dh-readiness-machine-token"
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer voice-dh-readiness-machine-token"}

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_token

    def test_machine_observation_exposes_only_blocked_value_free_lane_summary(self) -> None:
        response = self.client.get("/ops/release-policy/observations", headers=self.headers)
        anonymous = self.client.get("/ops/release-policy/observations")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(anonymous.status_code, 401)
        payload = response.json()["voiceDigitalHumanReadiness"]
        self.assertFalse(payload["promotionAllowed"])
        self.assertEqual(payload["lanes"]["M1SelfVoice"]["status"], "blocked")
        self.assertEqual(payload["lanes"]["M2LivingDigitalHuman"]["status"], "blocked")
        self.assertEqual(payload["lanes"]["M3AdultMemorialPilot"]["status"], "blocked")
        self.assertNotIn("voice-dh-readiness-machine-token", response.text)

