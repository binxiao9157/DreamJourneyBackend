import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.observability.events import (
    IncidentEvidenceEvent,
    OperationEvidenceEvent,
    ProviderCostEvidenceEvent,
    RightsEvidenceEvent,
    map_release_policy_operation_event,
    validate_evidence_event,
)


class EvidenceEventSchemaTests(unittest.TestCase):
    def setUp(self):
        self.occurred_at = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        self.common = {
            "eventId": "evt_release_policy_01",
            "schemaVersion": 1,
            "operationId": "op_release_policy_01",
            "correlationId": "corr_release_policy_01",
            "principalHash": "a" * 64,
            "resourceType": "releasePolicy",
            "resourceIdHash": "b" * 64,
            "state": "succeeded",
            "reason": "policyAllowed",
            "attempt": 1,
            "occurredAt": self.occurred_at,
            "env": "test",
            "build": "backend-test",
            "redactionVersion": 1,
        }

    def test_four_event_types_accept_only_their_versioned_allowlists(self):
        operation = OperationEvidenceEvent(
            **self.common,
            type="operation",
            operation="releasePolicyDecision",
            route="POST /family/invite",
            latencyMs=12,
            policyVersion="release-policy-v1",
            clientBuild=42,
            feature="familyManagement",
            decision="allow",
        )
        rights = RightsEvidenceEvent(
            **{**self.common, "state": "denied", "reason": "grantMissing"},
            type="rights",
            right="memoryRead",
            action="deny",
            authority="shareGrant",
            receiptIdHash="c" * 64,
        )
        incident = IncidentEvidenceEvent(
            **{**self.common, "state": "failed", "reason": "providerUnavailable"},
            type="incident",
            incidentClass="providerAvailability",
            severity="warning",
            action="fallback",
            surface="echo",
        )
        provider_cost = ProviderCostEvidenceEvent(
            **self.common,
            type="providerCost",
            provider="tencentDigitalHuman",
            capability="digitalHumanSession",
            providerRequestHash="d" * 64,
            unitType="session",
            units=1,
            costMicros=0,
            latencyMs=180,
        )

        for event, expected_type in [
            (operation, "operation"),
            (rights, "rights"),
            (incident, "incident"),
            (provider_cost, "providerCost"),
        ]:
            decoded = validate_evidence_event(event.model_dump(mode="json"))
            self.assertEqual(decoded.type, expected_type)
            self.assertEqual(decoded.schemaVersion, 1)

    def test_content_secret_and_identity_fields_are_rejected(self):
        payload = {
            **self.common,
            "type": "operation",
            "operation": "releasePolicyDecision",
        }
        for forbidden_field in [
            "rawText",
            "prompt",
            "token",
            "phone",
            "mediaInput",
            "userId",
        ]:
            with self.subTest(forbidden_field=forbidden_field):
                with self.assertRaises(ValidationError):
                    validate_evidence_event({**payload, forbidden_field: "private-value"})

    def test_free_text_query_strings_raw_identifiers_and_naive_time_are_rejected(self):
        payload = {
            **self.common,
            "type": "operation",
            "operation": "releasePolicyDecision",
        }
        invalid_payloads = [
            {**payload, "reason": "the user said something private"},
            {**payload, "route": "GET /config/runtime?token=private"},
            {**payload, "principalHash": "raw-user-id"},
            {**payload, "occurredAt": datetime(2026, 7, 16, 12, 0)},
        ]
        for invalid in invalid_payloads:
            with self.assertRaises(ValidationError):
                validate_evidence_event(invalid)

    def test_release_policy_shadow_mapper_is_value_free_and_deterministic(self):
        event = map_release_policy_operation_event(
            feature="familyManagement",
            policy_version="release-policy-v1",
            client_build=42,
            decision="deny",
            reason="notApprovedForClosedPilot",
            route="POST /family/invite",
            occurred_at=self.occurred_at,
            environment="production",
        )
        repeated = map_release_policy_operation_event(
            feature="familyManagement",
            policy_version="release-policy-v1",
            client_build=42,
            decision="deny",
            reason="notApprovedForClosedPilot",
            route="POST /family/invite",
            occurred_at=self.occurred_at,
            environment="production",
        )

        self.assertEqual(event.eventId, repeated.eventId)
        self.assertEqual(event.operationId, repeated.operationId)
        self.assertEqual(event.state, "denied")
        self.assertIsNone(event.principalHash)
        self.assertIsNone(event.resourceIdHash)
        self.assertEqual(
            set(event.model_dump(mode="json")),
            {
                "eventId",
                "schemaVersion",
                "type",
                "operationId",
                "correlationId",
                "principalHash",
                "resourceType",
                "resourceIdHash",
                "state",
                "reason",
                "attempt",
                "occurredAt",
                "env",
                "build",
                "redactionVersion",
                "operation",
                "route",
                "latencyMs",
                "policyVersion",
                "clientBuild",
                "feature",
                "decision",
            },
        )
        serialized = str(event.model_dump(mode="json")).lower()
        for forbidden in ["userid", "phone", "prompt", "token", "media"]:
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
