"""P2-S2a service-level ShareGrant and Visitor admission tests.

The closed-beta lane remains service-only here: the tests exercise the
default-off implementation through its in-memory repository without creating
public routes, deep links, or reader behavior.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from uuid import uuid4

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationVisitorRelationshipOrigin,
)
from app.services.publication_visitor_access import (
    InMemoryPublicationVisitorAccessRepository,
    PublicationGrantIssueCommand,
    PublicationGrantRevokeCommand,
    PublicationGrantScope,
    PublicationVisitorAccessDenied,
    PublicationVisitorAccessService,
    PublicationVisitorAccessUnavailable,
    PublicationVisitorAdultVerificationRequired,
    PublicationVisitorEligibility,
    PublicationVisitorSessionCommand,
    StaticPublicationVisitorEligibilityResolver,
)


NOW = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class PublicationVisitorAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_subject_id = "owner-publication-access"
        self.visitor_subject_id = "visitor-publication-access"
        self.uninvited_subject_id = "visitor-not-invited"
        self.context = OwnerTruthCommandContext(
            vault_id="vault-publication-access",
            owner_subject_id=self.owner_subject_id,
            actor_subject_id=self.owner_subject_id,
        )
        self.scope = PublicationGrantScope(
            vault_id=self.context.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            publication_id=str(uuid4()),
            publication_version_id=str(uuid4()),
            projection_state="active",
        )
        self.repository = InMemoryPublicationVisitorAccessRepository()
        self.repository.seed_scope(self.scope)
        self.service = self._service(
            {
                self.visitor_subject_id: self._eligibility(),
                self.uninvited_subject_id: self._eligibility(),
            }
        )

    @staticmethod
    def _eligibility(
        adult_verification: PublicationAdultVerificationState = PublicationAdultVerificationState.VERIFIED,
        relationship_origin: PublicationVisitorRelationshipOrigin = PublicationVisitorRelationshipOrigin.DIRECT,
    ) -> PublicationVisitorEligibility:
        return PublicationVisitorEligibility(
            adult_verification=adult_verification,
            relationship_origin=relationship_origin,
        )

    def _service(
        self,
        eligibility: dict[str, PublicationVisitorEligibility],
    ) -> PublicationVisitorAccessService:
        return PublicationVisitorAccessService(
            self.repository,
            enabled=True,
            eligibility_resolver=StaticPublicationVisitorEligibilityResolver(eligibility),
        )

    def _issue_command(self, *, use_limit: int = 2) -> PublicationGrantIssueCommand:
        return PublicationGrantIssueCommand(
            command_id=str(uuid4()),
            publication_id=self.scope.publication_id,
            publication_version_id=self.scope.publication_version_id,
            grantee_subject_id=self.visitor_subject_id,
            expires_at=NOW + timedelta(days=1),
            use_limit=use_limit,
        )

    @staticmethod
    def _session_command(
        *,
        grant_credential: str,
        suffix: str,
    ) -> PublicationVisitorSessionCommand:
        return PublicationVisitorSessionCommand(
            command_id=str(uuid4()),
            grant_credential=grant_credential,
            session_credential=f"session-{suffix}-" + "s" * 32,
        )

    def _issue(self, *, use_limit: int = 2):
        return self.service.issue_grant(
            context=self.context,
            command=self._issue_command(use_limit=use_limit),
            now=NOW,
        )

    def _admit(
        self,
        *,
        grant_id: str,
        grant_credential: str,
        suffix: str,
        visitor_subject_id: str | None = None,
    ):
        return self.service.admit_visitor(
            visitor_subject_id=visitor_subject_id or self.visitor_subject_id,
            grant_id=grant_id,
            command=self._session_command(
                grant_credential=grant_credential,
                suffix=suffix,
            ),
            now=NOW,
        )

    def test_owner_issue_returns_raw_credential_once_and_replay_omits_it(self) -> None:
        command = replace(
            self._issue_command(),
            grantee_display_label="手机号尾号 9512",
        )

        first = self.service.issue_grant(context=self.context, command=command, now=NOW)

        self.assertEqual(first.outcome, "created")
        self.assertIsNotNone(first.grant_credential)
        assert first.grant_credential is not None
        self.assertGreaterEqual(len(first.grant_credential), 24)
        snapshot = self.repository.grant_snapshot(first.grant_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertIn("grantCredentialHash", snapshot)
        self.assertNotIn(first.grant_credential, json.dumps(snapshot, default=str))
        self.assertEqual(snapshot["granteeDisplayLabel"], "手机号尾号 9512")

        replay = self.service.issue_grant(context=self.context, command=command, now=NOW)

        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.grant_id, first.grant_id)
        self.assertIsNone(replay.grant_credential)
        self.assertEqual(replay.grantee_display_label, "手机号尾号 9512")

    def test_verified_visitor_admission_obeys_use_limit_and_replay(self) -> None:
        issued = self._issue(use_limit=2)
        assert issued.grant_credential is not None
        first_command = self._session_command(
            grant_credential=issued.grant_credential,
            suffix="first",
        )

        first = self.service.admit_visitor(
            visitor_subject_id=self.visitor_subject_id,
            grant_id=issued.grant_id,
            command=first_command,
            now=NOW,
        )
        replay = self.service.admit_visitor(
            visitor_subject_id=self.visitor_subject_id,
            grant_id=issued.grant_id,
            command=first_command,
            now=NOW,
        )
        second = self._admit(
            grant_id=issued.grant_id,
            grant_credential=issued.grant_credential,
            suffix="second",
        )

        self.assertEqual(first.outcome, "created")
        self.assertEqual(first.use_remaining, 1)
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.session_id, first.session_id)
        self.assertEqual(replay.use_remaining, 1)
        self.assertEqual(second.outcome, "created")
        self.assertEqual(second.use_remaining, 0)
        snapshot = self.repository.grant_snapshot(issued.grant_id)
        self.assertEqual(snapshot["useCount"], 2)

        with self.assertRaises(PublicationVisitorAccessUnavailable):
            self._admit(
                grant_id=issued.grant_id,
                grant_credential=issued.grant_credential,
                suffix="over-limit",
            )

    def test_wrong_grant_secret_or_uninvited_visitor_is_denied(self) -> None:
        issued = self._issue()
        assert issued.grant_credential is not None

        with self.assertRaises(PublicationVisitorAccessDenied):
            self._admit(
                grant_id=issued.grant_id,
                grant_credential="invalid-grant-credential-" + "x" * 32,
                suffix="bad-secret",
            )

        with self.assertRaises(PublicationVisitorAccessDenied):
            self._admit(
                grant_id=issued.grant_id,
                grant_credential=issued.grant_credential,
                suffix="wrong-visitor",
                visitor_subject_id=self.uninvited_subject_id,
            )

        snapshot = self.repository.grant_snapshot(issued.grant_id)
        self.assertEqual(snapshot["useCount"], 0)

    def test_unknown_minor_or_family_derived_visitor_is_rejected_before_admission(self) -> None:
        issued = self._issue()
        assert issued.grant_credential is not None
        denied_cases = (
            (
                "unknown",
                self._eligibility(PublicationAdultVerificationState.UNKNOWN),
            ),
            (
                "minor",
                self._eligibility(PublicationAdultVerificationState.MINOR),
            ),
            (
                "family-derived",
                self._eligibility(
                    relationship_origin=PublicationVisitorRelationshipOrigin.FAMILY_DERIVED,
                ),
            ),
        )

        for label, eligibility in denied_cases:
            with self.subTest(label=label):
                service = self._service({self.visitor_subject_id: eligibility})
                with self.assertRaises(PublicationVisitorAdultVerificationRequired):
                    service.admit_visitor(
                        visitor_subject_id=self.visitor_subject_id,
                        grant_id=issued.grant_id,
                        command=self._session_command(
                            grant_credential=issued.grant_credential,
                            suffix=label,
                        ),
                        now=NOW,
                    )

        snapshot = self.repository.grant_snapshot(issued.grant_id)
        self.assertEqual(snapshot["useCount"], 0)

    def test_revoke_closes_active_sessions_and_blocks_new_admission(self) -> None:
        issued = self._issue()
        assert issued.grant_credential is not None
        admitted = self._admit(
            grant_id=issued.grant_id,
            grant_credential=issued.grant_credential,
            suffix="before-revoke",
        )

        result = self.service.revoke_grant(
            context=self.context,
            command=PublicationGrantRevokeCommand(
                command_id=str(uuid4()),
                grant_id=issued.grant_id,
            ),
            now=NOW,
        )

        self.assertEqual(result.outcome, "revoked")
        self.assertEqual(result.revoked_session_count, 1)
        session = self.repository.session_snapshot(admitted.session_id)
        self.assertEqual(session["state"], "revoked")

        with self.assertRaises(PublicationVisitorAccessUnavailable):
            self._admit(
                grant_id=issued.grant_id,
                grant_credential=issued.grant_credential,
                suffix="after-revoke",
            )

    def test_blocked_projection_or_authority_change_rejects_existing_grant(self) -> None:
        issued = self._issue()
        assert issued.grant_credential is not None

        self.repository.seed_scope(replace(self.scope, projection_state="blocked"))
        with self.assertRaisesRegex(PublicationVisitorAccessUnavailable, "projection is not active"):
            self._admit(
                grant_id=issued.grant_id,
                grant_credential=issued.grant_credential,
                suffix="projection-blocked",
            )

        self.repository.seed_scope(replace(self.scope, authority_epoch=self.scope.authority_epoch + 1))
        with self.assertRaisesRegex(PublicationVisitorAccessUnavailable, "authority changed"):
            self._admit(
                grant_id=issued.grant_id,
                grant_credential=issued.grant_credential,
                suffix="authority-changed",
            )

    def test_authority_epoch_migration_uses_a_non_reserved_share_grant_alias(self) -> None:
        migration = (
            ROOT
            / "db/migrations/0081_publication_share_grant_authority_epoch.sql"
        ).read_text(encoding="utf-8")
        service = (ROOT / "app/services/publication_visitor_access.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("UPDATE publication.share_grants AS share_grant", migration)
        self.assertNotIn("AS grant\n", migration)
        self.assertNotIn("publication.share_grants AS grant", service)
        self.assertNotIn("FOR UPDATE OF grant", service)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
