"""G0 tests for default-deny Visitor answer safety."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationShareGrant,
    PublicationShareGrantState,
    PublicationVisitorIdentity,
    PublicationVisitorRelationshipOrigin,
    PublicationVisitorSessionProposal,
)
from app.domain.publication.visitor_answer_safety import (
    PublicationVisitorAnswerRequest,
    PublicationVisitorAnswerSafetyDisposition,
    PublicationVisitorExitChannel,
    PublicationVisitorInteractionKind,
    PublicationVisitorPromptRisk,
    PublicationVisitorPublicationState,
    PublicationVisitorPublicContextSource,
    PublicationVisitorQuestionClass,
    PublicationVisitorRateLimitState,
    PublicationVisitorReportKind,
    PublicationVisitorRiskState,
    evaluate_publication_visitor_answer_safety,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0054_publication_visitor_answer_safety.sql"
MIGRATION_JSON = ROOT / "db/migrations/0054_publication_visitor_answer_safety.json"
NOW = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
GRANT_ID = "650e859f-7de6-4bc7-bbb8-5f0ce6765b3f"
PUBLICATION_ID = "3d5d4c5c-4801-4981-9873-1a1f248f6ce5"
VERSION_ID = "39e06808-a93d-453d-9c30-e533dfc90f65"
SESSION_ID = "b55e318d-4bca-4e23-8feb-bc13740b972e"
REQUEST_ID = "725c32f8-d086-4e3d-9887-65e59e186902"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _grant(**overrides: object) -> PublicationShareGrant:
    values: dict[str, object] = {
        "grant_id": GRANT_ID,
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "vault_id": "vault-publication-owner",
        "owner_subject_hash": _digest("owner"),
        "grantee_subject_hash": _digest("visitor"),
        "grant_credential_hash": _digest("grant-credential"),
        "state": PublicationShareGrantState.ACTIVE,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(days=1),
        "use_limit": 3,
        "use_count": 0,
        "policy_hash": _digest("publication-policy"),
    }
    values.update(overrides)
    return PublicationShareGrant(**values)  # type: ignore[arg-type]


def _visitor(**overrides: object) -> PublicationVisitorIdentity:
    values: dict[str, object] = {
        "subject_hash": _digest("visitor"),
        "adult_verification": PublicationAdultVerificationState.VERIFIED,
        "relationship_origin": PublicationVisitorRelationshipOrigin.DIRECT,
        "emergency_contact_ref_hash": _digest("contact-ref"),
    }
    values.update(overrides)
    return PublicationVisitorIdentity(**values)  # type: ignore[arg-type]


def _session(**overrides: object) -> PublicationVisitorSessionProposal:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "grant_id": GRANT_ID,
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "visitor_subject_hash": _digest("visitor"),
        "session_credential_hash": _digest("session-credential"),
        "issued_at": NOW,
        "expires_at": NOW + timedelta(hours=4),
        "expected_grant_use_count": 0,
    }
    values.update(overrides)
    return PublicationVisitorSessionProposal(**values)  # type: ignore[arg-type]


def _request(**overrides: object) -> PublicationVisitorAnswerRequest:
    values: dict[str, object] = {
        "request_id": REQUEST_ID,
        "vault_id": "vault-publication-owner",
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "visitor_session_id": SESSION_ID,
        "request_hash": _digest("visitor-request"),
        "policy_hash": _digest("visitor-policy"),
        "publication_state": PublicationVisitorPublicationState.PUBLISHED,
        "current_public_version": True,
        "context_source": PublicationVisitorPublicContextSource.PUBLICATION_VERSION,
        "public_version_hash": _digest("public-version"),
        "public_citation_hashes": (_digest("public-citation"),),
        "continuous_use_started_at": NOW,
        "interaction_kind": PublicationVisitorInteractionKind.ANSWER,
        "question_class": PublicationVisitorQuestionClass.GENERAL,
        "prompt_risk": PublicationVisitorPromptRisk.CLEAR,
        "risk_state": PublicationVisitorRiskState.NONE,
        "rate_limit_state": PublicationVisitorRateLimitState.ALLOWED,
        "exit_channel": PublicationVisitorExitChannel.NONE,
        "report_kind": PublicationVisitorReportKind.NONE,
    }
    values.update(overrides)
    return PublicationVisitorAnswerRequest(**values)  # type: ignore[arg-type]


def _evaluate(**overrides: object):
    values: dict[str, object] = {
        "grant": _grant(),
        "visitor": _visitor(),
        "session": _session(),
        "request": _request(),
        "now": NOW + timedelta(minutes=1),
        "enabled": True,
    }
    values.update(overrides)
    return evaluate_publication_visitor_answer_safety(**values)


class PublicationVisitorAnswerSafetyTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_inputs(self) -> None:
        result = evaluate_publication_visitor_answer_safety(
            grant=object(), visitor=object(), session=object(), request=object()
        )
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.SHADOW_DISABLED)
        self.assertTrue(result.ai_disclosure_required)
        self.assertFalse(result.answer_allowed)
        self.assertFalse(result.public_query_allowed)

    def test_adult_family_grant_and_session_scope_denials(self) -> None:
        result = _evaluate(
            visitor=_visitor(adult_verification=PublicationAdultVerificationState.MINOR)
        )
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.ADULT_VERIFICATION_DENIED)

        result = _evaluate(
            visitor=_visitor(relationship_origin=PublicationVisitorRelationshipOrigin.FAMILY_DERIVED)
        )
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.FAMILY_AUTO_GRANT_DENIED)

        result = _evaluate(request=_request(vault_id="vault-other"))
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.GRANT_SCOPE_DENIED)

        result = _evaluate(session=_session(visitor_subject_hash=_digest("other-visitor")))
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.SESSION_SCOPE_DENIED)

    def test_grant_and_session_lifecycle_denials(self) -> None:
        result = _evaluate(grant=_grant(state=PublicationShareGrantState.REVOKED))
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.GRANT_INACTIVE)

        result = _evaluate(grant=_grant(expires_at=NOW + timedelta(seconds=1)), now=NOW + timedelta(minutes=1))
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.GRANT_EXPIRED)

        result = _evaluate(session=_session(expected_grant_use_count=1))
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.SESSION_EXPIRED)

    def test_exit_and_report_are_deterministic_but_not_effectful(self) -> None:
        for channel in (
            PublicationVisitorExitChannel.UI,
            PublicationVisitorExitChannel.VOICE,
            PublicationVisitorExitChannel.KEYWORD,
        ):
            with self.subTest(channel=channel):
                result = _evaluate(request=_request(exit_channel=channel))
                self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.EXIT_REQUESTED)
                self.assertTrue(result.requires_deterministic_exit)
                self.assertFalse(result.session_closed)

        result = _evaluate(
            request=_request(
                interaction_kind=PublicationVisitorInteractionKind.REPORT,
                report_kind=PublicationVisitorReportKind.SAFETY_REPORT,
            )
        )
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.REPORT_RECEIPT_REQUIRED)
        self.assertTrue(result.report_receipt_required)
        self.assertFalse(result.feedback_persisted)

    def test_crisis_prompt_risk_rate_limit_and_high_stakes_are_blocked(self) -> None:
        cases = (
            (
                _request(risk_state=PublicationVisitorRiskState.CRISIS),
                PublicationVisitorAnswerSafetyDisposition.CRISIS_SAFETY_ASSISTANT_REQUIRED,
            ),
            (
                _request(prompt_risk=PublicationVisitorPromptRisk.PRIVATE_EXTRACTION),
                PublicationVisitorAnswerSafetyDisposition.PROMPT_INJECTION_BLOCKED,
            ),
            (
                _request(rate_limit_state=PublicationVisitorRateLimitState.LIMIT_REACHED),
                PublicationVisitorAnswerSafetyDisposition.RATE_LIMIT_DENIED,
            ),
            (
                _request(question_class=PublicationVisitorQuestionClass.HIGH_STAKES_DECISION),
                PublicationVisitorAnswerSafetyDisposition.PERSONA_DECISION_DENIED,
            ),
        )
        for request, disposition in cases:
            with self.subTest(disposition=disposition):
                result = _evaluate(request=request)
                self.assertEqual(result.disposition, disposition)
                self.assertFalse(result.owner_persona_allowed)
                self.assertFalse(result.voice_or_digital_human_allowed)

    def test_publication_state_private_context_and_missing_evidence_fail_closed(self) -> None:
        cases = (
            (
                _request(publication_state=PublicationVisitorPublicationState.WITHDRAWN),
                PublicationVisitorAnswerSafetyDisposition.PUBLICATION_INACCESSIBLE,
            ),
            (
                _request(context_source=PublicationVisitorPublicContextSource.PRIVATE_KBLITE),
                PublicationVisitorAnswerSafetyDisposition.PRIVATE_CONTEXT_REJECTED,
            ),
            (
                _request(public_citation_hashes=()),
                PublicationVisitorAnswerSafetyDisposition.PUBLIC_EVIDENCE_REQUIRED,
            ),
        )
        for request, disposition in cases:
            with self.subTest(disposition=disposition):
                result = _evaluate(request=request)
                self.assertEqual(result.disposition, disposition)
                self.assertFalse(result.owner_memory_read_allowed)
                self.assertFalse(result.provider_call_allowed)

    def test_two_hour_boundary_requires_reminder(self) -> None:
        result = _evaluate(now=NOW + timedelta(hours=2))
        self.assertEqual(
            result.disposition,
            PublicationVisitorAnswerSafetyDisposition.CONTINUOUS_USE_REMINDER_REQUIRED,
        )
        self.assertTrue(result.requires_continuous_use_reminder)
        self.assertEqual(result.session_elapsed_seconds, 7200)

    def test_valid_public_only_answer_remains_policy_disabled(self) -> None:
        result = _evaluate()
        self.assertEqual(result.disposition, PublicationVisitorAnswerSafetyDisposition.POLICY_DISABLED)
        self.assertTrue(result.ai_disclosure_required)
        self.assertFalse(result.answer_allowed)
        self.assertFalse(result.public_query_allowed)
        self.assertFalse(result.provider_call_allowed)
        self.assertFalse(result.feedback_persisted)

    def test_value_free_summary_does_not_leak_private_markers(self) -> None:
        visitor = _visitor(subject_hash=_digest("visitor-private-marker"))
        grant = _grant(grantee_subject_hash=visitor.subject_hash, vault_id="vault-private-marker")
        session = _session(visitor_subject_hash=visitor.subject_hash)
        request = _request(vault_id=grant.vault_id)
        result = _evaluate(grant=grant, visitor=visitor, session=session, request=request)
        serialized = json.dumps(result.value_free_summary(), sort_keys=True)
        self.assertNotIn("vault-private-marker", serialized)
        self.assertNotIn(visitor.subject_hash, serialized)
        self.assertNotIn(grant.grant_credential_hash, serialized)

    def test_additive_migration_and_manifest_are_default_off(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0054")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "visitorCrisisFallbackV1": False,
                "visitorDurationGuardV1": False,
                "visitorReportV1": False,
                "visitorTextQAV1": False,
            },
        )
        self.assertIn("create table publication.visitor_answer_safety_receipts", sql)
        self.assertIn("revoke all on table publication.visitor_answer_safety_receipts from public;", sql)
        for forbidden in (
            "question_body",
            "answer_body",
            "raw_prompt",
            "raw_message",
            "source_payload",
            "object_url",
            "private_memory",
        ):
            self.assertNotIn(forbidden, sql)

    def test_migration_loader_and_domain_import_boundary(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        metadata = next(item for item in migrations if item.version == "0054")
        self.assertEqual(metadata.name, "publication_visitor_answer_safety")

        source = (ROOT / "app/domain/publication/visitor_answer_safety.py").read_text(encoding="utf-8")
        for forbidden in (
            "app.main", "app.services", "app.async_effects", "app.domain.owner_truth", "requests", "httpx", "psycopg", "boto3"
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
