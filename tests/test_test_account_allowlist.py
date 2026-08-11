import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.identity_bindings import (
    IdentityBindingService,
    IdentityChallengeConfigurationError,
    IdentityChallengeVerificationFailed,
    UnavailableIdentityChallengeAdapter,
)
from app.services.in_memory_store import InMemoryStore
from app.services.route_ownership import (
    RouteAuthenticationMode,
    RouteOwnershipRegistry,
)
from app.services.runtime_config import RuntimeConfigService
from app.services.test_account_allowlist import (
    TEST_ACCOUNT_PROVIDER_MODE,
    TestAccountAllowlistService,
    TestAccountAllowlistValidationError,
    configured_test_phone_prefixes,
)


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)
HMAC_KEY = "test-account-allowlist-key-" + ("x" * 40)
TARGET = "10000000001"
NORMALIZED_TARGET = "8610000000001"
TARGET_PREFIX = "8610000000"
MACHINE_TOKEN = "test-machine-token"


def make_allowlist(store: InMemoryStore) -> TestAccountAllowlistService:
    return TestAccountAllowlistService(
        store,
        hmac_key=HMAC_KEY,
        hmac_key_version="v1",
        enabled=True,
        allowed_phone_prefixes=(TARGET_PREFIX,),
        default_ttl_days=7,
        max_ttl_days=30,
        event_sink=store.append_evidence_event,
        environment="test",
    )


class TestAccountAllowlistServiceTests(unittest.TestCase):
    def test_credentials_are_one_time_and_never_persisted_raw(self):
        store = InMemoryStore()
        service = make_allowlist(store)

        created = service.create(
            target=TARGET,
            label="iPhone QA",
            actor_id="backend-service-v1",
            now=NOW,
        )
        account = created["testAccount"]
        account_id = account["accountId"]
        code = account["verificationCode"]
        persisted = store.get_test_account_allowlist(account_id)

        self.assertRegex(code, r"^[0-9]{6}$")
        self.assertEqual(account["loginTarget"], TARGET)
        self.assertNotIn("verificationCode", persisted)
        self.assertNotIn("target", persisted)
        serialized = json.dumps(store._test_account_allowlist, sort_keys=True)
        self.assertNotIn(NORMALIZED_TARGET, serialized)
        self.assertNotIn(code, serialized)
        listed = service.list()["testAccounts"][0]
        self.assertNotIn("verificationCode", listed)
        self.assertNotIn("loginTarget", listed)
        self.assertTrue(service.verify_code(persisted, code))

    def test_rotate_disable_renew_and_subject_binding(self):
        store = InMemoryStore()
        service = make_allowlist(store)
        created = service.create(
            target=TARGET,
            label="Lifecycle QA",
            actor_id="backend-service-v1",
            now=NOW,
        )["testAccount"]
        account_id = created["accountId"]
        old_code = created["verificationCode"]

        rotated = service.rotate_code(
            account_id,
            actor_id="backend-service-v1",
            now=NOW + timedelta(minutes=1),
        )["testAccount"]
        persisted = store.get_test_account_allowlist(account_id)
        self.assertFalse(service.verify_code(persisted, old_code))
        self.assertTrue(service.verify_code(persisted, rotated["verificationCode"]))
        self.assertTrue(
            service.record_successful_login(
                account_id,
                subject_id="sub_test_account",
                now=NOW + timedelta(minutes=2),
            )
        )
        disabled = service.disable(
            account_id,
            actor_id="backend-service-v1",
            now=NOW + timedelta(minutes=3),
        )
        self.assertEqual(disabled["testAccount"]["status"], "disabled")
        target_hash = service.target_hash("phone", NORMALIZED_TARGET)
        self.assertIsNone(
            service.active_account_for_target_hash(
                identity_type="phone",
                target_hash_key_version="v1",
                target_hash=target_hash,
                now=NOW + timedelta(minutes=4),
            )
        )
        renewed = service.renew(
            account_id,
            actor_id="backend-service-v1",
            ttl_days=2,
            now=NOW + timedelta(days=40),
        )
        self.assertEqual(renewed["testAccount"]["status"], "active")
        self.assertEqual(renewed["testAccount"]["subjectId"], "sub_test_account")
        service.disable(
            account_id,
            actor_id="backend-service-v1",
            now=NOW + timedelta(days=40, minutes=1),
        )
        enabled = service.enable(
            account_id,
            actor_id="backend-service-v1",
            now=NOW + timedelta(days=40, minutes=2),
        )
        self.assertEqual(enabled["testAccount"]["status"], "active")
        self.assertEqual(
            {
                event["payload"]["route"]
                for event in store.list_evidence_events()
            },
            {
                "POST /ops/test-accounts",
                "POST /ops/test-accounts/{account_id}/rotate-code",
                "POST /ops/test-accounts/{account_id}/disable",
                "POST /ops/test-accounts/{account_id}/enable",
                "POST /ops/test-accounts/{account_id}/renew",
                "POST /v2/auth/challenges/{challenge_id}/verify",
            },
        )

    def test_target_must_match_explicit_synthetic_prefix(self):
        service = make_allowlist(InMemoryStore())
        with self.assertRaises(TestAccountAllowlistValidationError):
            service.create(
                target="13800138000",
                label="Real-looking phone",
                actor_id="backend-service-v1",
                now=NOW,
            )

    def test_prefix_configuration_rejects_broad_ranges(self):
        self.assertEqual(configured_test_phone_prefixes("86100"), ())
        self.assertEqual(
            configured_test_phone_prefixes(TARGET_PREFIX),
            (TARGET_PREFIX,),
        )


class TestAccountIdentityFlowTests(unittest.TestCase):
    def test_allowlisted_target_can_login_without_sms_provider(self):
        store = InMemoryStore()
        allowlist = make_allowlist(store)
        credential = allowlist.create(
            target=TARGET,
            label="Identity flow",
            actor_id="backend-service-v1",
            now=NOW,
        )["testAccount"]
        service = IdentityBindingService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=UnavailableIdentityChallengeAdapter(),
            challenge_ttl_seconds=300,
            max_attempts=3,
            test_account_allowlist_service=allowlist,
        )

        challenge = service.create_challenge(
            identity_type="phone",
            target=TARGET,
            purpose="login",
            now=NOW,
        )
        challenge_id = challenge["challenge"]["challengeId"]
        self.assertEqual(
            store.get_auth_challenge(challenge_id)["providerMode"],
            TEST_ACCOUNT_PROVIDER_MODE,
        )
        with self.assertRaises(IdentityChallengeVerificationFailed):
            service.verify_challenge(
                challenge_id,
                "000000",
                now=NOW + timedelta(seconds=1),
            )
        verified = service.verify_challenge(
            challenge_id,
            credential["verificationCode"],
            nickname="测试账号",
            now=NOW + timedelta(seconds=2),
        )
        persisted = store.get_test_account_allowlist(credential["accountId"])
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(persisted["subjectId"], verified["subject"]["subjectId"])
        self.assertEqual(persisted["useCount"], 1)

    def test_non_allowlisted_target_stays_closed_without_sms_provider(self):
        store = InMemoryStore()
        service = IdentityBindingService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=UnavailableIdentityChallengeAdapter(),
            challenge_ttl_seconds=300,
            max_attempts=3,
            test_account_allowlist_service=make_allowlist(store),
        )
        with self.assertRaises(IdentityChallengeConfigurationError):
            service.create_challenge(
                identity_type="phone",
                target="13800138000",
                purpose="login",
                now=NOW,
            )


class TestAccountEndpointAndContractTests(unittest.TestCase):
    def test_management_api_is_machine_only_and_returns_one_time_code(self):
        store = InMemoryStore()
        service = make_allowlist(store)
        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", MACHINE_TOKEN),
            patch.object(
                main_module,
                "_test_account_allowlist_service",
                return_value=service,
            ),
        ):
            client = TestClient(app)
            denied = client.post(
                "/ops/test-accounts",
                json={"target": TARGET, "label": "Denied"},
            )
            created = client.post(
                "/ops/test-accounts",
                headers={"X-DreamJourney-Api-Token": MACHINE_TOKEN},
                json={"target": TARGET, "label": "API QA", "ttlDays": 3},
            )
            listed = client.get(
                "/ops/test-accounts",
                headers={"X-DreamJourney-Api-Token": MACHINE_TOKEN},
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertIn("verificationCode", created.json()["testAccount"])
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("verificationCode", listed.text)
        self.assertNotIn(NORMALIZED_TARGET, listed.text)

    def test_runtime_exposes_restricted_test_lane_without_claiming_production_sms(self):
        settings = Settings(
            environment="production",
            identity_binding_hmac_key=HMAC_KEY,
            identity_binding_hmac_key_version="v1",
            identity_challenge_adapter="disabled",
            test_account_allowlist_enabled=True,
            test_account_allowed_phone_prefixes=TARGET_PREFIX,
        )
        descriptor = RuntimeConfigService(settings).public_config()["auth"][
            "identityChallenge"
        ]
        self.assertTrue(descriptor["enabled"])
        self.assertTrue(descriptor["clientFlowEnabled"])
        self.assertTrue(descriptor["internalVerificationEnabled"])
        self.assertTrue(descriptor["testAccountFlowEnabled"])
        self.assertTrue(descriptor["testAccountTargetRestricted"])
        self.assertFalse(descriptor["productionReady"])
        self.assertEqual(descriptor["providerMode"], TEST_ACCOUNT_PROVIDER_MODE)

    def test_management_routes_require_dedicated_machine_scope(self):
        registry = RouteOwnershipRegistry()
        paths = (
            ("POST", "/ops/test-accounts"),
            ("GET", "/ops/test-accounts"),
            ("POST", "/ops/test-accounts/abc/rotate-code"),
            ("POST", "/ops/test-accounts/abc/disable"),
            ("POST", "/ops/test-accounts/abc/enable"),
            ("POST", "/ops/test-accounts/abc/renew"),
        )
        for method, path in paths:
            with self.subTest(method=method, path=path):
                match = registry.match(method, path)
                self.assertIsNotNone(match)
                self.assertEqual(match.rule.auth_mode, RouteAuthenticationMode.MACHINE)
                self.assertEqual(match.rule.required_scopes, ("testAccount:manage",))
        self.assertEqual(registry.audit_summary()["unclassifiedCount"], 0)

    def test_0089_migration_is_additive_and_has_no_raw_credentials(self):
        migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
        sql = (migrations / "0089_test_account_allowlist.sql").read_text(
            encoding="utf-8"
        )
        metadata = json.loads(
            (migrations / "0089_test_account_allowlist.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["version"], "0089")
        self.assertEqual(metadata["phase"], "expand")
        self.assertEqual(metadata["compatibility"], "additive")
        self.assertIn("target_hash", sql)
        self.assertIn("code_hash", sql)
        self.assertNotIn("phone_number", sql)
        self.assertNotIn("verification_code TEXT", sql)
        self.assertNotIn("ON DELETE CASCADE", sql)


if __name__ == "__main__":
    unittest.main()
