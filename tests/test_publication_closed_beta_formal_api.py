from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class PublicationClosedBetaFormalAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_authority_qa_enabled = main_module.PUBLICATION_AUTHORITY_QA_ENABLED
        self.previous_visitor_qa_enabled = main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = False
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = False

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_closed_pilot_owner_ids
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = self.previous_authority_qa_enabled
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = self.previous_visitor_qa_enabled

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "发布闭测测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return str(payload["user"]["id"]), {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-Client-Build": "9001",
        }

    def test_formal_owner_route_is_registered_but_d0_denied_even_with_qa_header(self) -> None:
        owner_id, headers = self._login("13800139881")
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({owner_id})

        response = client.post(
            f"/v2/vaults/{owner_id}/publication-drafts",
            headers={**headers, "X-DreamJourney-QA-Publication": "1"},
            json={
                "commandId": str(uuid4()),
                "memoryVersionId": str(uuid4()),
                "publicTitle": "闭测发布副本",
                "publicBody": "这是一份独立脱敏副本。",
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "release_policy_denied",
                "feature": "publication",
                "reason": "publicationVisitorNotApproved",
                "policyRevision": main_module.RELEASE_POLICY_SERVICE.policy_revision,
                "minimumClientBuild": main_module.RELEASE_POLICY_SERVICE.min_client_build,
                "accessMode": "deny",
                "retryable": False,
            },
        )

    def test_formal_visitor_route_is_registered_but_d0_denied_and_not_family_inherited(self) -> None:
        visitor_id, headers = self._login("13800139882")
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({visitor_id})

        response = client.post(
            f"/v2/publication-grants/{uuid4()}/sessions",
            headers={
                **headers,
                "X-DreamJourney-Policy-Audience": "family",
                "X-DreamJourney-QA-Visitor-Access": "1",
            },
            json={
                "commandId": str(uuid4()),
                "grantCredential": "grant-credential-" + "g" * 32,
                "sessionCredential": "visitor-session-" + "s" * 32,
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["feature"], "visitorAccess")
        self.assertEqual(detail["reason"], "publicationVisitorNotApproved")
        self.assertEqual(detail["accessMode"], "deny")

    def test_formal_routes_are_user_authenticated_hidden_schema_contracts(self) -> None:
        route_cases = (
            ("GET", "/v2/vaults/vault-closed-beta/publications"),
            (
                "GET",
                f"/v2/vaults/vault-closed-beta/publications/{uuid4()}/versions",
            ),
            ("POST", "/v2/vaults/vault-closed-beta/publication-drafts"),
            (
                "POST",
                f"/v2/vaults/vault-closed-beta/publication-drafts/{uuid4()}/confirm/{uuid4()}",
            ),
            (
                "POST",
                f"/v2/vaults/vault-closed-beta/publications/{uuid4()}/withdraw",
            ),
            (
                "POST",
                f"/v2/vaults/vault-closed-beta/publications/{uuid4()}/suspend",
            ),
            ("GET", "/v2/vaults/vault-closed-beta/publication-grants"),
            ("POST", "/v2/vaults/vault-closed-beta/publication-grants"),
            (
                "POST",
                f"/v2/vaults/vault-closed-beta/publication-grants/{uuid4()}/revoke",
            ),
            ("POST", f"/v2/publication-grants/{uuid4()}/sessions"),
            ("POST", f"/v2/publication-sessions/{uuid4()}/projection"),
            ("POST", f"/v2/publication-sessions/{uuid4()}/answers"),
        )

        openapi_paths = client.get("/openapi.json").json().get("paths", {})
        registered_routes = {
            (method, route.path): route
            for route in main_module.app.routes
            for method in getattr(route, "methods", set())
        }
        for method, path in route_cases:
            with self.subTest(method=method, path=path):
                match = main_module.ROUTE_AUTHENTICATION_POLICY.registry.match(method, path)
                self.assertIsNotNone(match)
                self.assertEqual(match.rule.auth_mode.value, "user")

        formal_templates = {
            "/v2/vaults/{vault_id}/publications",
            "/v2/vaults/{vault_id}/publications/{publication_id}/versions",
            "/v2/vaults/{vault_id}/publication-drafts",
            "/v2/vaults/{vault_id}/publication-drafts/{draft_id}/confirm/{publication_id}",
            "/v2/vaults/{vault_id}/publications/{publication_id}/withdraw",
            "/v2/vaults/{vault_id}/publications/{publication_id}/suspend",
            "/v2/vaults/{vault_id}/publication-grants",
            "/v2/vaults/{vault_id}/publication-grants/{grant_id}/revoke",
            "/v2/publication-grants/{grant_id}/sessions",
            "/v2/publication-sessions/{session_id}/projection",
            "/v2/publication-sessions/{session_id}/answers",
        }
        for template in formal_templates:
            with self.subTest(template=template):
                self.assertNotIn(template, openapi_paths)
                matching = [
                    route
                    for (method, path), route in registered_routes.items()
                    if path == template and method in {"GET", "POST"}
                ]
                self.assertTrue(matching)
                self.assertTrue(all(not route.include_in_schema for route in matching))

    def test_formal_route_labels_redact_resource_identifiers(self) -> None:
        gate = main_module.RELEASE_POLICY_COMMAND_GATE
        version_path = "/v2/vaults/owner-secret/publications/publication-secret/versions"
        self.assertEqual(gate.feature_for_request("GET", version_path, {}), "publication")
        self.assertEqual(
            gate.route_label_for_request("GET", version_path, {}),
            "GET /v2/vaults/*/publications/*/versions",
        )
        cases = {
            "/v2/vaults/owner-secret/publication-drafts/draft-secret/confirm/publication-secret": (
                "publication",
                "POST /v2/vaults/*/publication-drafts/*/confirm/*",
            ),
            "/v2/vaults/owner-secret/publication-grants/grant-secret/revoke": (
                "visitorAccess",
                "POST /v2/vaults/*/publication-grants/*/revoke",
            ),
            "/v2/publication-grants/grant-secret/sessions": (
                "visitorAccess",
                "POST /v2/publication-grants/*/sessions",
            ),
            "/v2/publication-sessions/session-secret/projection": (
                "visitorAccess",
                "POST /v2/publication-sessions/*/projection",
            ),
        }
        for path, (feature, label) in cases.items():
            with self.subTest(path=path):
                self.assertEqual(gate.feature_for_request("POST", path, {}), feature)
                self.assertEqual(gate.route_label_for_request("POST", path, {}), label)
                self.assertNotIn("secret", label)

    def test_qa_routes_remain_separate_and_hidden_when_their_qa_gate_is_off(self) -> None:
        owner_id, headers = self._login("13800139883")
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({owner_id})

        response = client.post(
            f"/v2/internal/owner-authority/vaults/{owner_id}/drafts",
            headers=headers,
            json={},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "publicationAuthorityUnavailable")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
