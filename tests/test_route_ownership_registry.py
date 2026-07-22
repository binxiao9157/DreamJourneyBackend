import ast
import inspect
import unittest

from app.main import app
from app.services.route_ownership import (
    RouteOwnershipCategory,
    RouteOwnershipRegistry,
)


class RouteOwnershipRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RouteOwnershipRegistry()

    @staticmethod
    def business_routes():
        excluded = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
        routes = set()
        for route in app.routes:
            path = str(getattr(route, "path", ""))
            if path in excluded:
                continue
            for method in getattr(route, "methods", set()) or set():
                routes.add((str(method).upper(), path))
        return routes

    def test_registry_covers_every_fastapi_business_route_exactly_once(self):
        app_routes = self.business_routes()
        registry_routes = {(rule.method, rule.path_template) for rule in self.registry.rules}

        self.assertEqual(len(app_routes), 108)
        self.assertEqual(len(self.registry.rules), len(registry_routes))
        self.assertEqual(registry_routes, app_routes)

    def test_concrete_path_match_extracts_owner_from_path_or_body(self):
        archive = self.registry.match("GET", "/archive/items/user_123")
        create = self.registry.match("POST", "/archive/items")

        self.assertIsNotNone(archive)
        self.assertEqual(archive.rule.category, RouteOwnershipCategory.OWNER_PATH)
        self.assertEqual(archive.owner_user_id(payload={}), "user_123")
        self.assertIsNotNone(create)
        self.assertEqual(create.rule.category, RouteOwnershipCategory.OWNER_BODY)
        self.assertEqual(create.owner_user_id(payload={"userId": "user_456"}), "user_456")

    def test_high_risk_routes_have_non_service_classification(self):
        expected = {
            ("POST", "/auth/purge-expired-deletions"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("POST", "/auth/data-export"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/mailbox/letters"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("POST", "/echo/delayed-replies/dispatch-due"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("POST", "/archive/time-letters/dispatch-due"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("GET", "/ops/release-policy/observations"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("POST", "/ops/evidence-manifests"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("GET", "/ops/evidence-manifests"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("GET", "/ops/data-rights/requests/{request_id}/evidence"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("GET", "/mailbox/letters/{user_id}"): RouteOwnershipCategory.OWNER_PATH,
            ("GET", "/echo/delayed-replies/{user_id}/{delayed_reply_id}/answer"): RouteOwnershipCategory.OWNER_PATH,
            ("GET", "/kb/source-ref-audit/{user_id}"): RouteOwnershipCategory.OWNER_PATH,
            ("POST", "/voice/synthesis"): RouteOwnershipCategory.OWNER_BODY,
            ("POST", "/kb/governance/actions"): RouteOwnershipCategory.OWNER_BODY,
            ("GET", "/archive/time-letters/{owner_user_id}/{item_id}/detail"): RouteOwnershipCategory.DELEGATED,
            ("GET", "/care/snapshots/latest/{user_id}"): RouteOwnershipCategory.DELEGATED,
            ("POST", "/family/access-grants"): RouteOwnershipCategory.OWNER_BODY,
            ("GET", "/family/access-grants/{user_id}"): RouteOwnershipCategory.OWNER_PATH,
            ("POST", "/family/access-grants/{user_id}/{grant_id}/revoke"): RouteOwnershipCategory.OWNER_PATH,
            ("POST", "/family/relationships/{user_id}/{relationship_id}/lifecycle"): RouteOwnershipCategory.OWNER_PATH,
            ("GET", "/v2/vaults/{vault_id}/candidates"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions"): RouteOwnershipCategory.USER_SESSION,
            ("GET", "/v2/vaults/{vault_id}/interview-sessions/{session_id}/state"): RouteOwnershipCategory.USER_SESSION,
            ("GET", "/v2/vaults/{vault_id}/interview-sessions/{session_id}/presentation"): RouteOwnershipCategory.USER_SESSION,
            ("GET", "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/confirmation"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/confirmation/batch-accept"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/interview-sessions"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/interview-sessions/{session_id}/boundary"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/interview-sessions/{session_id}/restore-do-not-ask"): RouteOwnershipCategory.USER_SESSION,
            ("GET", "/v2/vaults/{vault_id}/memory-projection"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/memory-projection/rebuild"): RouteOwnershipCategory.USER_SESSION,
            ("GET", "/v2/vaults/{vault_id}/kblite-compatibility"): RouteOwnershipCategory.USER_SESSION,
            ("GET", "/v2/vaults/{vault_id}/kblite-compatibility/read-envelope"): RouteOwnershipCategory.USER_SESSION,
            ("GET", "/v2/vaults/{vault_id}/context-shadow"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/context-shadow/build"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/answer-citation-receipts"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/memory-versions/{memory_version_id}/knowledge-dimension-confirmations"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/knowledge-recommendations/read"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/knowledge-recommendations/plan"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/interview-sessions/{session_id}/saved-continuation-cues"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/correction-requests/{correction_request_id}/resolve"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/legacy-migration/inventory"): RouteOwnershipCategory.USER_SESSION,
            ("POST", "/v2/vaults/{vault_id}/legacy-migration/shadow-parity"): RouteOwnershipCategory.USER_SESSION,
        }

        for key, category in expected.items():
            with self.subTest(route=key):
                self.assertEqual(self.registry.rule_for_template(*key).category, category)

    def test_audit_summary_contains_only_templates_and_counts(self):
        summary = self.registry.audit_summary()
        serialized = str(summary)

        self.assertEqual(summary["routeCount"], 108)
        self.assertEqual(sum(summary["categoryCounts"].values()), 108)
        self.assertEqual(summary["unclassifiedCount"], 0)
        self.assertNotIn("user_123", serialized)
        self.assertIn("/archive/items/{user_id}", serialized)

    def test_child_resource_routes_declare_typed_resolvers(self):
        expected = {
            ("POST", "/digital-human/sessions/{session_id}/heartbeat"): "digitalHumanSession",
            ("POST", "/voice/synthesis"): "voiceProfile",
            ("POST", "/archive/media/upload-intent"): "archiveItem",
            ("POST", "/archive/image-analysis"): "archiveItem",
            ("DELETE", "/archive/items/{user_id}/{item_id}"): "archiveItem",
            ("POST", "/mailbox/letters/{user_id}/{letter_id}/read"): "mailboxLetter",
            ("POST", "/family/members/{user_id}/{member_id}/revoke"): "familyMember",
        }

        for key, resource_type in expected.items():
            with self.subTest(route=key):
                rule = self.registry.rule_for_template(*key)
                self.assertEqual(rule.resource_type, resource_type)
                self.assertTrue(rule.requires_existing_resource)
                self.assertIsNotNone(rule.resource_operation)

    def test_every_owner_body_endpoint_canonicalizes_payload_from_principal(self):
        endpoints = {}
        for route in app.routes:
            path = str(getattr(route, "path", ""))
            endpoint = getattr(route, "endpoint", None)
            for method in getattr(route, "methods", set()) or set():
                endpoints[(str(method).upper(), path)] = endpoint

        for rule in self.registry.rules:
            if rule.category != RouteOwnershipCategory.OWNER_BODY:
                continue
            endpoint = endpoints[(rule.method, rule.path_template)]
            function = ast.parse(inspect.getsource(endpoint)).body[0]
            helper_calls = [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_principal_owned_payload"
            ]
            with self.subTest(route=(rule.method, rule.path_template)):
                self.assertTrue(helper_calls)

    def test_every_owner_path_endpoint_treats_path_owner_as_an_assertion(self):
        endpoints = {}
        for route in app.routes:
            path = str(getattr(route, "path", ""))
            endpoint = getattr(route, "endpoint", None)
            for method in getattr(route, "methods", set()) or set():
                endpoints[(str(method).upper(), path)] = endpoint

        for rule in self.registry.rules:
            if rule.category != RouteOwnershipCategory.OWNER_PATH:
                continue
            endpoint = endpoints[(rule.method, rule.path_template)]
            function = ast.parse(inspect.getsource(endpoint)).body[0]
            helper_calls = [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_principal_path_owner"
            ]
            with self.subTest(route=(rule.method, rule.path_template)):
                self.assertTrue(helper_calls)


if __name__ == "__main__":
    unittest.main()
