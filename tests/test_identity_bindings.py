import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.auth_sessions import AuthSessionService
from app.services.identity_bindings import (
    IdentityBindingService,
    IdentityChallengeConfigurationError,
    IdentityChallengeRateLimited,
    IdentityChallengeValidationError,
    IdentityChallengeVerificationFailed,
    SyntheticIdentityChallengeAdapter,
    UnavailableIdentityChallengeAdapter,
)
from app.services.in_memory_store import InMemoryStore
from app.services.postgres_store import PostgresStore
from app.services.runtime_config import RuntimeConfigService
from app.services.route_ownership import (
    RouteOwnershipCategory,
    RouteOwnershipRegistry,
)


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
HMAC_KEY = "identity-binding-test-key-" + ("x" * 40)
SYNTHETIC_CODE = "246810"
TARGET = "+86 138-0013-8000"


class IdentityPostgresCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.connection.statements.append((normalized, params))
        self.result = None
        if normalized.startswith("SELECT version, key_fingerprint, status"):
            self.result = list(self.connection.hash_key_versions.values())
        elif normalized.startswith("INSERT INTO identity_hash_key_versions"):
            self.connection.hash_key_versions[params[0]] = {
                "version": params[0],
                "key_fingerprint": params[1],
                "status": "active",
            }
        elif normalized.startswith("INSERT INTO auth_challenges"):
            row = {
                "id": params[0],
                "identity_type": params[1],
                "target_hash_key_version": params[2],
                "target_hash": params[3],
                "code_hash": params[4],
                "provider_mode": params[5],
                "purpose": params[6],
                "status": params[7],
                "attempts": params[8],
                "max_attempts": params[9],
                "internal_verification_enabled": params[10],
                "expires_at": params[11],
                "created_at": params[12],
                "updated_at": params[13],
                "consumed_at": None,
            }
            self.connection.challenges[row["id"]] = row
            self.result = deepcopy(row)
        elif (
            normalized.startswith("SELECT id, identity_type")
            and "FROM auth_challenges" in normalized
            and "WHERE identity_type = %s" in normalized
        ):
            identity_type, target_hash_key_version, target_hash, purpose = params
            matches = [
                row
                for row in self.connection.challenges.values()
                if row["identity_type"] == identity_type
                and row["target_hash_key_version"] == target_hash_key_version
                and row["target_hash"] == target_hash
                and row["purpose"] == purpose
            ]
            self.result = None if not matches else deepcopy(
                max(matches, key=lambda item: item["created_at"])
            )
        elif (
            normalized.startswith("SELECT id, identity_type")
            and "FROM auth_challenges" in normalized
        ):
            row = self.connection.challenges.get(params[0])
            self.result = None if row is None else deepcopy(row)
        elif normalized.startswith("UPDATE auth_challenges SET status = 'expired'"):
            row = self.connection.challenges[params[1]]
            row.update(status="expired", updated_at=params[0])
        elif (
            normalized.startswith("UPDATE auth_challenges SET attempts = %s")
            and "status = %s" in normalized
        ):
            row = self.connection.challenges[params[3]]
            row.update(attempts=params[0], status=params[1], updated_at=params[2])
        elif normalized.startswith("SELECT pg_advisory_xact_lock"):
            self.result = {"locked": True}
        elif normalized.startswith("SELECT b.id, b.subject_id"):
            identity_type, target_hash_key_version, target_hash = params
            binding_id = self.connection.binding_ids_by_target.get(
                (identity_type, target_hash_key_version, target_hash)
            )
            binding = self.connection.bindings.get(binding_id)
            self.result = None if binding is None else {
                "id": binding["id"],
                "subject_id": binding["subject_id"],
                "binding_status": binding["status"],
                "subject_status": self.connection.subjects[
                    binding["subject_id"]
                ]["status"],
            }
        elif normalized.startswith("INSERT INTO subjects"):
            self.connection.subjects[params[0]] = {
                "id": params[0],
                "status": "active",
                "created_at": params[1],
                "updated_at": params[2],
            }
        elif normalized.startswith("INSERT INTO identity_bindings"):
            binding = {
                "id": params[0],
                "subject_id": params[1],
                "identity_type": params[2],
                "target_hash_key_version": params[3],
                "target_hash": params[4],
                "provider_mode": params[5],
                "status": "active",
                "verified_at": params[6],
                "created_at": params[7],
                "updated_at": params[8],
            }
            self.connection.bindings[binding["id"]] = binding
            self.connection.binding_ids_by_target[
                (
                    binding["identity_type"],
                    binding["target_hash_key_version"],
                    binding["target_hash"],
                )
            ] = binding["id"]
        elif normalized.startswith("UPDATE identity_bindings"):
            binding = self.connection.bindings[params[3]]
            binding.update(
                provider_mode=params[0],
                status="active",
                verified_at=params[1],
                updated_at=params[2],
            )
        elif normalized.startswith("INSERT INTO identity_proofs"):
            self.connection.proofs[params[0]] = {
                "id": params[0],
                "challenge_id": params[1],
                "binding_id": params[2],
                "subject_id": params[3],
                "provider_mode": params[4],
                "verified_at": params[5],
                "contract_version": 1,
                "created_at": params[6],
            }
        elif (
            normalized.startswith("UPDATE auth_challenges SET attempts = %s")
            and "status = 'consumed'" in normalized
        ):
            row = self.connection.challenges[params[3]]
            row.update(
                attempts=params[0],
                status="consumed",
                consumed_at=params[1],
                updated_at=params[2],
            )
        else:
            raise AssertionError(f"unexpected identity SQL: {normalized}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        if self.result is None:
            return []
        return self.result if isinstance(self.result, list) else [self.result]


class IdentityPostgresConnection:
    def __init__(self):
        self.hash_key_versions = {}
        self.challenges = {}
        self.subjects = {}
        self.bindings = {}
        self.binding_ids_by_target = {}
        self.proofs = {}
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self, row_factory=None):
        return IdentityPostgresCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class IdentityBindingServiceTests(unittest.TestCase):
    def make_service(
        self,
        store=None,
        *,
        ttl_seconds=60,
        max_attempts=3,
        issue_session=True,
        hmac_key=HMAC_KEY,
        hmac_key_version="v1",
        adapter=None,
    ):
        candidate = store or InMemoryStore()
        auth_sessions = None
        if issue_session:
            auth_sessions = AuthSessionService(
                candidate,
                access_ttl_seconds=900,
                refresh_ttl_seconds=2592000,
            )
        return candidate, IdentityBindingService(
            candidate,
            hmac_key=hmac_key,
            hmac_key_version=hmac_key_version,
            adapter=adapter or SyntheticIdentityChallengeAdapter(SYNTHETIC_CODE),
            challenge_ttl_seconds=ttl_seconds,
            max_attempts=max_attempts,
            auth_session_service=auth_sessions,
        )

    def create(self, service, target=TARGET, *, now=NOW):
        return service.create_challenge(
            identity_type="phone",
            target=target,
            purpose="login",
            now=now,
        )

    @staticmethod
    def challenge_id(response):
        return response["challenge"]["challengeId"]

    def test_missing_wrong_expired_replayed_and_exhausted_are_one_failure_contract(self):
        _, service = self.make_service()
        failures = []

        with self.assertRaises(IdentityChallengeVerificationFailed) as missing:
            service.verify_challenge("ach_missing", SYNTHETIC_CODE, now=NOW)
        failures.append(str(missing.exception))

        wrong = self.create(service)
        with self.assertRaises(IdentityChallengeVerificationFailed) as mismatch:
            service.verify_challenge(self.challenge_id(wrong), "000000", now=NOW)
        failures.append(str(mismatch.exception))

        expired = self.create(service, now=NOW + timedelta(seconds=31))
        with self.assertRaises(IdentityChallengeVerificationFailed) as expiry:
            service.verify_challenge(
                self.challenge_id(expired),
                SYNTHETIC_CODE,
                now=NOW + timedelta(seconds=92),
            )
        failures.append(str(expiry.exception))

        replayed = self.create(service, now=NOW + timedelta(seconds=62))
        service.verify_challenge(
            self.challenge_id(replayed),
            SYNTHETIC_CODE,
            now=NOW + timedelta(seconds=62),
        )
        with self.assertRaises(IdentityChallengeVerificationFailed) as replay:
            service.verify_challenge(
                self.challenge_id(replayed),
                SYNTHETIC_CODE,
                now=NOW + timedelta(seconds=62),
            )
        failures.append(str(replay.exception))

        exhausted = self.create(service, now=NOW + timedelta(seconds=93))
        for _ in range(3):
            with self.assertRaises(IdentityChallengeVerificationFailed) as brute_force:
                service.verify_challenge(
                    self.challenge_id(exhausted),
                    "111111",
                    now=NOW + timedelta(seconds=93),
                )
            failures.append(str(brute_force.exception))
        with self.assertRaises(IdentityChallengeVerificationFailed) as locked:
            service.verify_challenge(
                self.challenge_id(exhausted),
                SYNTHETIC_CODE,
                now=NOW + timedelta(seconds=93),
            )
        failures.append(str(locked.exception))

        self.assertEqual(set(failures), {"challenge could not be verified"})

    def test_same_binding_reuses_random_subject_and_issues_user_session(self):
        store, service = self.make_service()
        first_challenge = self.create(service)
        first = service.verify_challenge(
            self.challenge_id(first_challenge),
            SYNTHETIC_CODE,
            now=NOW,
        )
        second_challenge = self.create(
            service,
            "+8613800138000",
            now=NOW + timedelta(seconds=31),
        )
        second = service.verify_challenge(
            self.challenge_id(second_challenge),
            SYNTHETIC_CODE,
            now=NOW + timedelta(seconds=31),
        )

        first_subject = first["subject"]
        second_subject = second["subject"]
        self.assertRegex(first_subject["subjectId"], r"^sub_[A-Za-z0-9_-]{24,}$")
        self.assertNotIn("13800138000", first_subject["subjectId"])
        self.assertEqual(second_subject["subjectId"], first_subject["subjectId"])
        self.assertEqual(second_subject["bindingId"], first_subject["bindingId"])
        self.assertNotEqual(second_subject["proofReceiptId"], first_subject["proofReceiptId"])
        self.assertEqual(first["user"], {"id": first_subject["subjectId"], "nickname": ""})
        self.assertEqual(first["auth"]["userId"], first_subject["subjectId"])
        self.assertIsNotNone(
            AuthSessionService(
                store,
                access_ttl_seconds=900,
                refresh_ttl_seconds=2592000,
            ).resolve_access_token(first["auth"]["accessToken"], now=NOW)
        )

    def test_mainland_phone_formats_resolve_to_one_binding(self):
        _, service = self.make_service()
        local = self.create(service, "13800138000", now=NOW)
        local_verified = service.verify_challenge(
            self.challenge_id(local),
            SYNTHETIC_CODE,
            now=NOW,
        )

        e164 = self.create(service, "+86 138-0013-8000", now=NOW + timedelta(seconds=31))
        e164_verified = service.verify_challenge(
            self.challenge_id(e164),
            SYNTHETIC_CODE,
            now=NOW + timedelta(seconds=31),
        )

        self.assertEqual(
            local_verified["subject"]["subjectId"],
            e164_verified["subject"]["subjectId"],
        )
        self.assertEqual(
            local_verified["subject"]["bindingId"],
            e164_verified["subject"]["bindingId"],
        )

    def test_phone_target_rejects_embedded_letters(self):
        _, service = self.make_service()

        with self.assertRaises(IdentityChallengeValidationError):
            self.create(service, "account-13800138000")

    def test_challenge_response_is_neutral_for_existing_and_unknown_binding(self):
        _, service = self.make_service()
        seed = self.create(service)
        service.verify_challenge(self.challenge_id(seed), SYNTHETIC_CODE, now=NOW)

        existing = self.create(service, "+8613800138000", now=NOW + timedelta(seconds=31))
        unknown = self.create(service, "+8613900000000", now=NOW + timedelta(seconds=31))

        self.assertEqual(set(existing), {"status", "challenge"})
        self.assertEqual(set(unknown), {"status", "challenge"})
        expected_challenge_keys = {
            "challengeId",
            "purpose",
            "deliveryMode",
            "expiresAt",
            "retryAfterSeconds",
            "productionReady",
            "contractVersion",
        }
        self.assertEqual(set(existing["challenge"]), expected_challenge_keys)
        self.assertEqual(set(unknown["challenge"]), expected_challenge_keys)
        for key in expected_challenge_keys - {"challengeId"}:
            self.assertEqual(existing["challenge"][key], unknown["challenge"][key])
        self.assertEqual(existing["status"], "accepted")
        self.assertNotIn("sent", json.dumps(existing).lower())

    def test_repeated_challenge_is_rate_limited_by_keyed_target(self):
        _, service = self.make_service()
        self.create(service, now=NOW)

        with self.assertRaises(IdentityChallengeRateLimited) as limited:
            self.create(service, now=NOW + timedelta(seconds=5))

        self.assertEqual(limited.exception.retry_after_seconds, 25)
        different_target = self.create(
            service,
            "+8613900000000",
            now=NOW + timedelta(seconds=5),
        )
        after_window = self.create(service, now=NOW + timedelta(seconds=30))
        self.assertEqual(different_target["status"], "accepted")
        self.assertEqual(after_window["status"], "accepted")

    def test_unavailable_provider_does_not_persist_an_unverifiable_challenge(self):
        store = InMemoryStore()
        service = IdentityBindingService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=UnavailableIdentityChallengeAdapter(),
            challenge_ttl_seconds=60,
            max_attempts=3,
        )

        with self.assertRaises(Exception) as unavailable:
            self.create(service)

        self.assertEqual(
            type(unavailable.exception).__name__,
            "IdentityChallengeConfigurationError",
        )
        self.assertEqual(store._auth_challenges, {})

    def test_disabled_adapter_cannot_verify_a_persisted_synthetic_challenge(self):
        store, enabled = self.make_service()
        challenge = self.create(enabled)
        _, disabled = self.make_service(
            store,
            adapter=UnavailableIdentityChallengeAdapter(),
        )

        with self.assertRaises(IdentityChallengeConfigurationError):
            disabled.verify_challenge(
                self.challenge_id(challenge),
                SYNTHETIC_CODE,
                now=NOW,
            )

        self.assertEqual(
            store.get_auth_challenge(self.challenge_id(challenge))["status"],
            "active",
        )
        self.assertEqual(store._auth_sessions, {})

    def test_suspended_subject_and_revoked_binding_cannot_be_reactivated(self):
        for disabled_record in ("subject", "binding"):
            with self.subTest(disabled_record=disabled_record):
                store, service = self.make_service()
                first = self.create(service)
                verified = service.verify_challenge(
                    self.challenge_id(first),
                    SYNTHETIC_CODE,
                    now=NOW,
                )
                subject_id = verified["subject"]["subjectId"]
                binding_id = verified["subject"]["bindingId"]
                if disabled_record == "subject":
                    store._subjects[subject_id]["status"] = "suspended"
                else:
                    store._identity_bindings[binding_id]["status"] = "revoked"

                retry = self.create(service, now=NOW + timedelta(seconds=31))
                with self.assertRaises(IdentityChallengeVerificationFailed):
                    service.verify_challenge(
                        self.challenge_id(retry),
                        SYNTHETIC_CODE,
                        now=NOW + timedelta(seconds=31),
                    )

                self.assertEqual(
                    store._subjects[subject_id]["status"],
                    "suspended" if disabled_record == "subject" else "active",
                )
                self.assertEqual(
                    store._identity_bindings[binding_id]["status"],
                    "revoked" if disabled_record == "binding" else "active",
                )
                self.assertEqual(len(store._auth_sessions), 1)

    def test_hmac_key_or_version_drift_fails_closed_before_new_challenge(self):
        store, service = self.make_service()
        self.create(service)
        original_count = len(store._auth_challenges)

        _, changed_key = self.make_service(
            store,
            hmac_key="different-identity-binding-key-" + ("y" * 40),
        )
        _, changed_version = self.make_service(
            store,
            hmac_key_version="v2",
        )

        with self.assertRaises(IdentityChallengeConfigurationError):
            self.create(changed_key, target="+8613900000001", now=NOW + timedelta(seconds=31))
        with self.assertRaises(IdentityChallengeConfigurationError):
            self.create(changed_version, target="+8613900000002", now=NOW + timedelta(seconds=31))
        self.assertEqual(len(store._auth_challenges), original_count)

    def test_raw_target_and_code_never_reach_persistence_or_responses(self):
        store, service = self.make_service()
        challenge = self.create(service)
        verified = service.verify_challenge(
            self.challenge_id(challenge),
            SYNTHETIC_CODE,
            now=NOW,
        )

        persisted = json.dumps(
            {
                "challenges": store._auth_challenges,
                "subjects": store._subjects,
                "bindings": store._identity_bindings,
                "proofs": store._identity_proofs,
                "sessions": store._auth_sessions,
            },
            sort_keys=True,
        )
        response_text = json.dumps({"challenge": challenge, "verified": verified}, sort_keys=True)
        normalized_target = "8613800138000"
        for raw_value in (TARGET, normalized_target, SYNTHETIC_CODE):
            self.assertNotIn(raw_value, persisted)
            self.assertNotIn(raw_value, response_text)
        record = store.get_auth_challenge(self.challenge_id(challenge))
        self.assertEqual(len(record["targetHash"]), 64)
        self.assertEqual(len(record["codeHash"]), 64)
        self.assertNotIn("target", record)
        self.assertNotIn("code", record)

    def test_challenge_attempts_emit_redacted_audit_events(self):
        store = InMemoryStore()
        service = IdentityBindingService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=SyntheticIdentityChallengeAdapter(SYNTHETIC_CODE),
            challenge_ttl_seconds=60,
            max_attempts=3,
            event_sink=store.append_evidence_event,
            environment="test",
        )
        challenge = self.create(service)
        with self.assertRaises(IdentityChallengeVerificationFailed):
            service.verify_challenge(
                self.challenge_id(challenge),
                "000000",
                now=NOW,
            )
        verified = service.verify_challenge(
            self.challenge_id(challenge),
            SYNTHETIC_CODE,
            now=NOW + timedelta(seconds=1),
        )

        summary = store.summarize_evidence_events(
            operation="identityChallenge",
            now_iso=(NOW + timedelta(seconds=2)).isoformat(),
        )
        self.assertEqual(summary["eventCount"], 3)
        self.assertEqual(
            summary["decisionCounts"],
            {
                "createAccepted": 1,
                "verifyAccepted": 1,
                "verifyDenied": 1,
            },
        )
        serialized = json.dumps(summary, sort_keys=True)
        self.assertNotIn(TARGET, serialized)
        self.assertNotIn("8613800138000", serialized)
        self.assertNotIn(SYNTHETIC_CODE, serialized)
        self.assertNotIn(verified["subject"]["subjectId"], serialized)

    def test_postgres_flow_persists_attempt_and_reuses_one_subject(self):
        connection = IdentityPostgresConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        _, service = self.make_service(store, issue_session=False)

        first_challenge = self.create(service)
        with self.assertRaises(IdentityChallengeVerificationFailed):
            service.verify_challenge(
                self.challenge_id(first_challenge),
                "000000",
                now=NOW,
            )
        first = service.verify_challenge(
            self.challenge_id(first_challenge),
            SYNTHETIC_CODE,
            now=NOW + timedelta(seconds=1),
        )
        with self.assertRaises(IdentityChallengeVerificationFailed):
            service.verify_challenge(
                self.challenge_id(first_challenge),
                SYNTHETIC_CODE,
                now=NOW + timedelta(seconds=2),
            )

        second_challenge = self.create(
            service,
            "+8613800138000",
            now=NOW + timedelta(seconds=31),
        )
        second = service.verify_challenge(
            self.challenge_id(second_challenge),
            SYNTHETIC_CODE,
            now=NOW + timedelta(seconds=32),
        )

        first_record = connection.challenges[self.challenge_id(first_challenge)]
        self.assertEqual(first_record["attempts"], 2)
        self.assertEqual(first_record["status"], "consumed")
        self.assertEqual(first["subject"]["subjectId"], second["subject"]["subjectId"])
        self.assertEqual(first["subject"]["bindingId"], second["subject"]["bindingId"])
        self.assertEqual(len(connection.subjects), 1)
        self.assertEqual(len(connection.bindings), 1)
        self.assertEqual(len(connection.proofs), 2)
        self.assertGreaterEqual(connection.commits, 5)
        serialized = json.dumps(
            {
                "challenges": connection.challenges,
                "subjects": connection.subjects,
                "bindings": connection.bindings,
                "proofs": connection.proofs,
            },
            sort_keys=True,
        )
        self.assertNotIn("8613800138000", serialized)
        self.assertNotIn(SYNTHETIC_CODE, serialized)

    def test_postgres_rate_lookup_serializes_same_target_inside_request_uow(self):
        connection = IdentityPostgresConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        with store.request_unit_of_work(
            correlation_id="identity-rate-test",
            command_id="createIdentityChallenge",
        ):
            latest = store.get_latest_auth_challenge(
                identity_type="phone",
                target_hash_key_version="v1",
                target_hash="a" * 64,
                purpose="login",
            )

        self.assertIsNone(latest)
        statements = [statement for statement, _ in connection.statements]
        lock_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("SELECT pg_advisory_xact_lock")
        )
        lookup_index = next(
            index
            for index, statement in enumerate(statements)
            if "FROM auth_challenges" in statement
            and "WHERE identity_type = %s" in statement
        )
        self.assertLess(lock_index, lookup_index)


class IdentityBindingEndpointTests(unittest.TestCase):
    def test_legacy_phone_login_and_restore_are_retired_by_default(self):
        store = InMemoryStore()
        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", ""),
            patch.object(main_module, "AUTH_LEGACY_PHONE_LOGIN_ENABLED", False),
        ):
            client = TestClient(app)
            login = client.post(
                "/auth/login",
                json={"phone": TARGET, "password": "password123"},
            )
            restore = client.post("/auth/restore", json={"phone": TARGET})

        self.assertEqual(login.status_code, 410)
        self.assertEqual(restore.status_code, 410)
        self.assertEqual(login.json(), restore.json())
        self.assertEqual(login.json()["detail"]["code"], "legacy_identity_flow_retired")
        self.assertEqual(store._users, {})
        self.assertNotIn(TARGET, login.text)

    def test_typed_endpoint_contract_and_neutral_failure(self):
        store = InMemoryStore()
        service = IdentityBindingService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=SyntheticIdentityChallengeAdapter(SYNTHETIC_CODE),
            challenge_ttl_seconds=60,
            max_attempts=3,
            auth_session_service=AuthSessionService(
                store,
                access_ttl_seconds=900,
                refresh_ttl_seconds=2592000,
            ),
        )
        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", "configured-system-token"),
            patch.object(main_module, "_identity_binding_service", return_value=service),
        ):
            client = TestClient(app)
            challenge = client.post(
                "/v2/auth/challenges",
                json={"identityType": "phone", "target": TARGET, "purpose": "login"},
            )
            wrong = client.post(
                f"/v2/auth/challenges/{challenge.json()['challenge']['challengeId']}/verify",
                json={"code": "000000"},
            )
            verified = client.post(
                f"/v2/auth/challenges/{challenge.json()['challenge']['challengeId']}/verify",
                json={"code": SYNTHETIC_CODE},
            )
            replay = client.post(
                f"/v2/auth/challenges/{challenge.json()['challenge']['challengeId']}/verify",
                json={"code": SYNTHETIC_CODE},
            )
            missing = client.post(
                "/v2/auth/challenges/ach_missing/verify",
                json={"code": SYNTHETIC_CODE},
            )
            unrelated = client.post(
                "/v2/auth/challenges/ach_missing/unrelated",
                json={"code": SYNTHETIC_CODE},
            )
            rate_limited = client.post(
                "/v2/auth/challenges",
                json={"identityType": "phone", "target": TARGET, "purpose": "login"},
            )

        self.assertEqual(challenge.status_code, 202)
        self.assertEqual(set(challenge.json()), {"status", "challenge"})
        self.assertEqual(
            set(challenge.json()["challenge"]),
            {
                "challengeId",
                "purpose",
                "deliveryMode",
                "expiresAt",
                "retryAfterSeconds",
                "productionReady",
                "contractVersion",
            },
        )
        self.assertEqual(challenge.headers["cache-control"], "no-store")
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(
            set(verified.json()),
            {"status", "subject", "user", "auth", "contractVersion"},
        )
        self.assertEqual(
            set(verified.json()["subject"]),
            {"subjectId", "bindingId", "proofReceiptId", "contractVersion"},
        )
        self.assertEqual(
            verified.json()["user"],
            {"id": verified.json()["subject"]["subjectId"], "nickname": ""},
        )
        self.assertEqual(
            verified.json()["auth"]["userId"],
            verified.json()["subject"]["subjectId"],
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.json(), replay.json())
        self.assertEqual(replay.json(), missing.json())
        self.assertEqual(unrelated.status_code, 401)
        self.assertEqual(rate_limited.status_code, 429)
        self.assertGreater(int(rate_limited.headers["retry-after"]), 0)
        self.assertEqual(
            rate_limited.json()["detail"]["code"],
            "identity_challenge_rate_limited",
        )
        for response in (challenge, wrong, verified, replay, missing):
            self.assertNotIn(TARGET, response.text)
            self.assertNotIn(SYNTHETIC_CODE, response.text)
            self.assertNotIn("sent", response.text.lower())


class IdentityBindingRuntimeAndSchemaTests(unittest.TestCase):
    def test_production_without_real_provider_is_not_ready_even_if_synthetic_requested(self):
        settings = Settings(
            environment="production",
            identity_binding_hmac_key=HMAC_KEY,
            identity_binding_hmac_key_version="v1",
            identity_challenge_adapter="synthetic",
            identity_challenge_synthetic_code=SYNTHETIC_CODE,
            auth_legacy_phone_login_enabled=True,
        )

        identity = RuntimeConfigService(settings).public_config()["auth"]["identityChallenge"]

        self.assertFalse(identity["productionReady"])
        self.assertFalse(identity["enabled"])
        self.assertFalse(identity["clientFlowEnabled"])
        self.assertFalse(identity["internalVerificationEnabled"])
        self.assertEqual(identity["providerMode"], "unavailable")
        self.assertEqual(identity["deliverySemantics"], "acceptedOnly")
        self.assertFalse(identity["legacyPhoneLoginEnabled"])

    def test_dynamic_challenge_routes_are_registered_as_public(self):
        registry = RouteOwnershipRegistry()
        create = registry.match("POST", "/v2/auth/challenges")
        verify = registry.match(
            "POST",
            "/v2/auth/challenges/ach_random/verify",
        )

        self.assertIsNotNone(create)
        self.assertIsNotNone(verify)
        self.assertEqual(create.rule.category, RouteOwnershipCategory.PUBLIC)
        self.assertEqual(verify.rule.category, RouteOwnershipCategory.PUBLIC)
        self.assertEqual(verify.path_parameters, {"challenge_id": "ach_random"})
        self.assertEqual(registry.audit_summary()["unclassifiedCount"], 0)

    def test_0002_schema_is_additive_and_has_no_raw_target_or_code_columns(self):
        migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
        sql = (migrations / "0002_identity_bindings.sql").read_text(encoding="utf-8")
        metadata = json.loads(
            (migrations / "0002_identity_bindings.json").read_text(encoding="utf-8")
        )

        self.assertEqual(metadata["version"], "0002")
        self.assertEqual(metadata["phase"], "expand")
        self.assertEqual(metadata["compatibility"], "additive")
        for table in ("subjects", "identity_bindings", "auth_challenges", "identity_proofs"):
            self.assertIn(f"CREATE TABLE {table}", sql)
        self.assertIn("CREATE TABLE identity_hash_key_versions", sql)
        self.assertIn("target_hash_key_version", sql)
        self.assertIn("UNIQUE (id, subject_id)", sql)
        self.assertIn(
            "FOREIGN KEY (binding_id, subject_id) REFERENCES identity_bindings(id, subject_id)",
            sql,
        )
        self.assertIn("BEFORE UPDATE OR DELETE ON identity_proofs", sql)
        self.assertNotRegex(sql.lower(), r"\bphone\s+text")
        self.assertNotRegex(sql.lower(), r"\btarget\s+text")
        self.assertNotRegex(sql.lower(), r"\bcode\s+text")
        for destructive in ("DROP TABLE", "TRUNCATE ", "DELETE FROM"):
            self.assertNotIn(destructive, sql.upper())


if __name__ == "__main__":
    unittest.main()
