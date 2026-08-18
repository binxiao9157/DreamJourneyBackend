from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.auth_sessions import AuthSessionError, AuthSessionService
from app.services.identity_bindings import (
    IdentityBindingService,
    IdentityChallengeVerificationFailed,
    SyntheticIdentityChallengeAdapter,
)
from app.services.in_memory_store import InMemoryStore
from app.services.password_authentication import (
    PasswordAuthenticationError,
    PasswordAuthenticationService,
)
from app.services.runtime_config import RuntimeConfigService


NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
HMAC_KEY = "password-authentication-v2-test-key-" + ("x" * 40)
OTP_CODE = "246810"
PHONE = "+86 138-0013-8000"


class PasswordAuthenticationV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.sessions = AuthSessionService(
            self.store,
            access_ttl_seconds=300,
            refresh_ttl_seconds=3600,
        )
        self.identity = IdentityBindingService(
            self.store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=SyntheticIdentityChallengeAdapter(OTP_CODE),
            challenge_ttl_seconds=300,
            max_attempts=3,
            retry_after_seconds=1,
            auth_session_service=self.sessions,
            environment="test",
        )
        self.passwords = PasswordAuthenticationService(
            self.store,
            identity_binding_service=self.identity,
            auth_session_service=self.sessions,
            token_hmac_key=HMAC_KEY,
            max_attempts=3,
            lockout_seconds=60,
            action_ttl_seconds=300,
            event_sink=self.store.append_evidence_event,
            environment="test",
        )

    def _verify_challenge(self, purpose: str, phone: str = PHONE, *, offset: int = 0):
        now = NOW + timedelta(seconds=offset)
        created = self.identity.create_challenge(
            identity_type="phone",
            target=phone,
            purpose=purpose,
            now=now,
        )
        return self.identity.verify_challenge(
            created["challenge"]["challengeId"],
            OTP_CODE,
            now=now + timedelta(seconds=1),
        )

    def _configured_subject(self):
        login = self._verify_challenge("login")
        subject_id = login["subject"]["subjectId"]
        verification = self._verify_challenge("sensitiveOperation", offset=2)
        grant = self.passwords.issue_action_grant(
            verification=verification,
            purpose="sensitiveoperation",
            now=NOW + timedelta(seconds=4),
        )
        configured = self.passwords.setup_password(
            principal_user_id=subject_id,
            new_password="initial-password",
            reauth_token=grant["actionToken"],
            now=NOW + timedelta(seconds=5),
        )
        return subject_id, login, configured

    def test_password_and_otp_issue_the_same_session_contract(self):
        subject_id, otp_login, configured = self._configured_subject()

        password_login = self.passwords.login(
            identity_type="phone",
            target=PHONE,
            password="initial-password",
            now=NOW + timedelta(seconds=6),
        )

        self.assertEqual(password_login["user"]["id"], subject_id)
        self.assertEqual(password_login["auth"]["contractVersion"], 2)
        self.assertEqual(otp_login["auth"]["contractVersion"], 2)
        self.assertEqual(configured["password"]["passwordRevision"], 1)

    def test_unknown_and_known_wrong_password_share_failure_then_lock(self):
        self._configured_subject()

        with self.assertRaises(PasswordAuthenticationError) as known:
            self.passwords.login(
                identity_type="phone",
                target=PHONE,
                password="wrong-password",
                now=NOW + timedelta(seconds=10),
            )
        with self.assertRaises(PasswordAuthenticationError) as unknown:
            self.passwords.login(
                identity_type="phone",
                target="+86 139-0013-8000",
                password="wrong-password",
                now=NOW + timedelta(seconds=10),
            )
        self.assertEqual(known.exception.code, "password_authentication_failed")
        self.assertEqual(unknown.exception.code, "password_authentication_failed")

        for attempt in range(2):
            with self.assertRaises(PasswordAuthenticationError) as failure:
                self.passwords.login(
                    identity_type="phone",
                    target=PHONE,
                    password="wrong-password",
                    now=NOW + timedelta(seconds=11 + attempt),
                )
        self.assertEqual(failure.exception.code, "password_temporarily_locked")
        self.assertGreater(failure.exception.retry_after_seconds or 0, 0)

    def test_change_revokes_old_family_and_issues_replacement_session(self):
        subject_id, _, configured = self._configured_subject()
        old_access = configured["auth"]["accessToken"]
        old_refresh = configured["auth"]["refreshToken"]

        changed = self.passwords.change_password(
            principal_user_id=subject_id,
            current_password="initial-password",
            new_password="changed-password",
            now=NOW + timedelta(seconds=10),
        )

        self.assertEqual(changed["password"]["passwordRevision"], 2)
        self.assertNotEqual(changed["auth"]["accessToken"], old_access)
        self.assertIsNone(
            self.sessions.resolve_access_token(
                old_access,
                now=NOW + timedelta(seconds=11),
            )
        )
        self.assertEqual(
            self.sessions.resolve_access_token(
                changed["auth"]["accessToken"],
                now=NOW + timedelta(seconds=11),
            )["userId"],
            subject_id,
        )
        with self.assertRaises(AuthSessionError):
            self.sessions.refresh(old_refresh, now=NOW + timedelta(seconds=11))

    def test_password_reset_grant_is_single_use_and_revokes_sessions(self):
        subject_id, _, configured = self._configured_subject()
        reset_verification = self._verify_challenge("passwordReset", offset=10)
        action = self.passwords.issue_action_grant(
            verification=reset_verification,
            purpose="passwordreset",
            now=NOW + timedelta(seconds=12),
        )

        reset = self.passwords.reset_password(
            reset_token=action["actionToken"],
            new_password="reset-password",
            now=NOW + timedelta(seconds=13),
        )
        self.assertEqual(reset["status"], "resetAccepted")
        self.assertIsNone(
            self.sessions.resolve_access_token(
                configured["auth"]["accessToken"],
                now=NOW + timedelta(seconds=14),
            )
        )
        with self.assertRaises(PasswordAuthenticationError) as replay:
            self.passwords.reset_password(
                reset_token=action["actionToken"],
                new_password="another-password",
                now=NOW + timedelta(seconds=14),
            )
        self.assertEqual(
            replay.exception.code,
            "password_reset_token_invalid_or_expired",
        )
        with self.assertRaises(AuthSessionError):
            self.sessions.refresh(
                configured["auth"]["refreshToken"],
                now=NOW + timedelta(seconds=14),
            )
        authenticated = self.passwords.login(
            identity_type="phone",
            target=PHONE,
            password="reset-password",
            now=NOW + timedelta(seconds=15),
        )
        self.assertEqual(authenticated["user"]["id"], subject_id)

    def test_reset_for_unbound_phone_does_not_create_subject_or_leak_at_reset(self):
        unknown_phone = "+86 137-0013-8000"
        verification = self._verify_challenge(
            "passwordReset",
            phone=unknown_phone,
        )
        self.assertNotIn("subject", verification)
        action = self.passwords.issue_action_grant(
            verification=verification,
            purpose="passwordreset",
            now=NOW + timedelta(seconds=2),
        )
        result = self.passwords.reset_password(
            reset_token=action["actionToken"],
            new_password="unknown-password",
            now=NOW + timedelta(seconds=3),
        )
        self.assertEqual(result["status"], "resetAccepted")
        self.assertIsNone(
            self.identity.subject_for_target(
                identity_type="phone",
                target=unknown_phone,
            )
        )

    def test_otp_challenge_cannot_be_replayed(self):
        created = self.identity.create_challenge(
            identity_type="phone",
            target=PHONE,
            purpose="login",
            now=NOW,
        )
        challenge_id = created["challenge"]["challengeId"]
        self.identity.verify_challenge(challenge_id, OTP_CODE, now=NOW)
        with self.assertRaises(IdentityChallengeVerificationFailed):
            self.identity.verify_challenge(
                challenge_id,
                OTP_CODE,
                now=NOW + timedelta(seconds=1),
            )


class PasswordAuthenticationV2EndpointTests(unittest.TestCase):
    def test_dual_login_setup_reset_and_replay_contract(self):
        store = InMemoryStore()
        sessions = AuthSessionService(
            store,
            access_ttl_seconds=300,
            refresh_ttl_seconds=3600,
        )
        identity = IdentityBindingService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=SyntheticIdentityChallengeAdapter(OTP_CODE),
            challenge_ttl_seconds=300,
            max_attempts=3,
            retry_after_seconds=1,
            auth_session_service=sessions,
            environment="test",
        )
        passwords = PasswordAuthenticationService(
            store,
            identity_binding_service=identity,
            auth_session_service=sessions,
            token_hmac_key=HMAC_KEY,
            event_sink=store.append_evidence_event,
            environment="test",
        )

        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", "configured-system-token"),
            patch.object(main_module, "_identity_binding_service", return_value=identity),
            patch.object(
                main_module,
                "_password_authentication_service",
                return_value=passwords,
            ),
        ):
            client = TestClient(app)
            login_challenge = client.post(
                "/v2/auth/challenges",
                json={"identityType": "phone", "target": PHONE, "purpose": "login"},
            )
            otp_login = client.post(
                f"/v2/auth/challenges/{login_challenge.json()['challenge']['challengeId']}/verify",
                json={"code": OTP_CODE},
            )
            access_token = otp_login.json()["auth"]["accessToken"]

            reauth_challenge = client.post(
                "/v2/auth/challenges",
                json={
                    "identityType": "phone",
                    "target": PHONE,
                    "purpose": "sensitiveOperation",
                },
            )
            reauth = client.post(
                f"/v2/auth/challenges/{reauth_challenge.json()['challenge']['challengeId']}/verify",
                json={"code": OTP_CODE},
            )
            setup = client.post(
                "/v2/auth/password/setup",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "newPassword": "endpoint-password",
                    "reauthToken": reauth.json()["actionToken"],
                },
            )
            password_login = client.post(
                "/v2/auth/password/login",
                json={
                    "identityType": "phone",
                    "target": PHONE,
                    "password": "endpoint-password",
                },
            )
            reset_challenge = client.post(
                "/v2/auth/challenges",
                json={
                    "identityType": "phone",
                    "target": PHONE,
                    "purpose": "passwordReset",
                },
            )
            reset_action = client.post(
                f"/v2/auth/challenges/{reset_challenge.json()['challenge']['challengeId']}/verify",
                json={"code": OTP_CODE},
            )
            reset = client.post(
                "/v2/auth/password/reset",
                json={
                    "resetToken": reset_action.json()["actionToken"],
                    "newPassword": "reset-endpoint-password",
                },
            )
            replay = client.post(
                "/v2/auth/password/reset",
                json={
                    "resetToken": reset_action.json()["actionToken"],
                    "newPassword": "replayed-endpoint-password",
                },
            )

        self.assertEqual(otp_login.status_code, 200)
        self.assertEqual(set(reauth.json()), {"status", "action", "actionToken", "expiresAt", "contractVersion"})
        self.assertEqual(reauth.json()["action"], "sensitiveOperation")
        self.assertEqual(setup.status_code, 200)
        self.assertEqual(password_login.status_code, 200)
        self.assertEqual(
            password_login.json()["user"]["id"],
            otp_login.json()["user"]["id"],
        )
        self.assertEqual(reset.status_code, 202)
        self.assertEqual(reset.json()["status"], "resetAccepted")
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(
            replay.json()["detail"]["code"],
            "password_reset_token_invalid_or_expired",
        )

    def test_runtime_capability_is_typed_and_fail_closed(self):
        ready = RuntimeConfigService(
            Settings(identity_binding_hmac_key=HMAC_KEY)
        ).public_config()["auth"]["passwordAuthentication"]
        disabled = RuntimeConfigService(
            Settings(
                identity_binding_hmac_key=HMAC_KEY,
                password_authentication_enabled=False,
            )
        ).public_config()["auth"]["passwordAuthentication"]

        self.assertTrue(ready["ready"])
        self.assertTrue(ready["loginReady"])
        self.assertTrue(ready["changeReady"])
        self.assertFalse(ready["setupReady"])
        self.assertFalse(ready["resetReady"])
        self.assertFalse(ready["reauthReady"])
        self.assertEqual(
            ready["recoveryReason"],
            "identityChallengeUnavailable",
        )
        self.assertEqual(ready["loginEndpoint"], "/v2/auth/password/login")
        self.assertEqual(ready["resetChallengePurpose"], "passwordReset")
        self.assertFalse(disabled["enabled"])
        self.assertNotEqual(disabled["reason"], "ready")


if __name__ == "__main__":
    unittest.main()
