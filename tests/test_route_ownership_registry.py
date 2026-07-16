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

        self.assertEqual(len(app_routes), 60)
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
            ("POST", "/mailbox/letters"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("POST", "/echo/delayed-replies/dispatch-due"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("POST", "/archive/time-letters/dispatch-due"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("GET", "/ops/release-policy/observations"): RouteOwnershipCategory.SYSTEM_ONLY,
            ("GET", "/mailbox/letters/{user_id}"): RouteOwnershipCategory.OWNER_PATH,
            ("GET", "/kb/source-ref-audit/{user_id}"): RouteOwnershipCategory.OWNER_PATH,
            ("POST", "/voice/synthesis"): RouteOwnershipCategory.OWNER_BODY,
            ("POST", "/kb/governance/actions"): RouteOwnershipCategory.OWNER_BODY,
            ("GET", "/archive/time-letters/{owner_user_id}/{item_id}/detail"): RouteOwnershipCategory.DELEGATED,
            ("GET", "/care/snapshots/latest/{user_id}"): RouteOwnershipCategory.DELEGATED,
        }

        for key, category in expected.items():
            with self.subTest(route=key):
                self.assertEqual(self.registry.rule_for_template(*key).category, category)

    def test_audit_summary_contains_only_templates_and_counts(self):
        summary = self.registry.audit_summary()
        serialized = str(summary)

        self.assertEqual(summary["routeCount"], 60)
        self.assertEqual(sum(summary["categoryCounts"].values()), 60)
        self.assertEqual(summary["unclassifiedCount"], 0)
        self.assertNotIn("user_123", serialized)
        self.assertIn("/archive/items/{user_id}", serialized)


if __name__ == "__main__":
    unittest.main()
