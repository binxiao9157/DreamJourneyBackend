from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.family_visitor_v4_routing import (
    FamilyVisitorV4Route,
    FamilyVisitorV4RoutingPolicy,
)


class FamilyVisitorV4RoutingPolicyTests(unittest.TestCase):
    def test_owner_self_is_the_only_private_v4_route(self) -> None:
        decision = FamilyVisitorV4RoutingPolicy.evaluate(
            principal_subject_id="owner-a",
            target_owner_subject_id="owner-a",
            persona_scope="personal",
            relationship_accepted=False,
            visitor_session_owner_subject_id=None,
            visitor_session_active=False,
        )

        self.assertEqual(decision.route, FamilyVisitorV4Route.OWNER_PRIVATE)
        self.assertTrue(decision.private_context_allowed)
        self.assertFalse(decision.legacy_fallback_allowed)

    def test_family_relationship_without_share_grant_routes_to_contribution(self) -> None:
        decision = FamilyVisitorV4RoutingPolicy.evaluate(
            principal_subject_id="family-b",
            target_owner_subject_id="owner-a",
            persona_scope="family",
            relationship_accepted=True,
            visitor_session_owner_subject_id=None,
            visitor_session_active=False,
        )

        self.assertEqual(decision.route, FamilyVisitorV4Route.FAMILY_CONTRIBUTION)
        self.assertEqual(decision.reason, "shareGrantRequired")
        self.assertFalse(decision.private_context_allowed)
        self.assertFalse(decision.legacy_fallback_allowed)

    def test_matching_visitor_session_routes_only_to_public_projection(self) -> None:
        decision = FamilyVisitorV4RoutingPolicy.evaluate(
            principal_subject_id="visitor-b",
            target_owner_subject_id="owner-a",
            persona_scope="family",
            relationship_accepted=True,
            visitor_session_owner_subject_id="owner-a",
            visitor_session_active=True,
        )

        self.assertEqual(decision.route, FamilyVisitorV4Route.VISITOR_PUBLIC)
        self.assertEqual(decision.required_authority, "visitorSession")
        self.assertFalse(decision.private_context_allowed)
        self.assertFalse(decision.legacy_fallback_allowed)

    def test_session_for_another_owner_never_authorizes_selected_family(self) -> None:
        decision = FamilyVisitorV4RoutingPolicy.evaluate(
            principal_subject_id="visitor-b",
            target_owner_subject_id="owner-a",
            persona_scope="family",
            relationship_accepted=True,
            visitor_session_owner_subject_id="owner-c",
            visitor_session_active=True,
        )

        self.assertEqual(decision.route, FamilyVisitorV4Route.FAMILY_CONTRIBUTION)
        self.assertEqual(decision.reason, "visitorSessionOwnerMismatch")
        self.assertFalse(decision.private_context_allowed)


class FamilyVisitorV4PrivateEchoBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def _family_payload(self) -> dict[str, str]:
        return {
            "userId": "family-v4-route-owner",
            "intent": "echo_chat",
            "query": "讲讲家人的故事",
            "personaScope": "family",
            "digitalHumanId": "family-digital-human",
            "viewerFamilyMemberID": "family-relationship-id",
        }

    def test_context_build_rejects_family_private_and_legacy_fallback(self) -> None:
        response = self.client.post("/context/build", json=self._family_payload())

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "familyPrivateContextDenied")
        self.assertEqual(detail["route"], "familyContribution")
        self.assertFalse(detail["privateContextAllowed"])
        self.assertFalse(detail["legacyFallbackAllowed"])
        self.assertEqual(detail["requiredAuthority"], "shareGrantOrContributionGrant")

    def test_echo_answer_rejects_family_private_before_generation(self) -> None:
        response = self.client.post("/echo/answers", json=self._family_payload())

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "familyPrivateContextDenied")
        self.assertFalse(detail["legacyFallbackAllowed"])


if __name__ == "__main__":
    unittest.main()
