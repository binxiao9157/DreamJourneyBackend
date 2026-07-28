"""G0 tests for default-blocked Publication canary and exit-readiness contracts."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.canary_exit_readiness import (
    PublicationCanaryDecision,
    PublicationCanaryEvidence,
    PublicationCanaryExitDisposition,
    PublicationCanaryExitRequest,
    PublicationCanaryStage,
    evaluate_publication_canary_exit_readiness,
)
from app.domain.publication.schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0057_publication_canary_exit_readiness.sql"
MIGRATION_JSON = ROOT / "db/migrations/0057_publication_canary_exit_readiness.json"
PUBLICATION_ID = "36546558-5fb6-46c6-bb2a-30d03aec4cd3"
VERSION_ID = "c3bb1af3-6fcd-4995-aa55-917c683a82e9"
DECISION_ID = "afd0d5c9-a765-4ad3-b845-0b7cc4d9917f"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**overrides: object) -> PublicationAuthorizationContext:
    values: dict[str, object] = {
        "vault_id": "vault-publication-owner",
        "owner_subject_hash": _digest("owner"),
        "authority_epoch": 7,
        "policy_version": "publication-visitor-policy-v1",
    }
    values.update(overrides)
    return PublicationAuthorizationContext(**values)  # type: ignore[arg-type]


def _principal(**overrides: object) -> PublicationAuthorizationPrincipal:
    context = _context()
    values: dict[str, object] = {
        "kind": PublicationPrincipalKind.OWNER,
        "vault_id": context.vault_id,
        "subject_hash": context.owner_subject_hash,
    }
    values.update(overrides)
    return PublicationAuthorizationPrincipal(**values)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> PublicationCanaryEvidence:
    values: dict[str, object] = {
        "synthetic_negative_corpus_passed": True,
        "internal_release_guard_passed": True,
        "withdrawal_receipt_candidate_present": True,
        "rights_exit_candidate_present": True,
        "incident_response_candidate_present": True,
        "private_leak_observed": False,
        "revoke_gap_observed": False,
        "unknown_required_effect_observed": False,
        "open_incident_observed": False,
        "legacy_guest_path_hit_count": 0,
    }
    values.update(overrides)
    return PublicationCanaryEvidence(**values)  # type: ignore[arg-type]


def _request(**overrides: object) -> PublicationCanaryExitRequest:
    context = _context()
    values: dict[str, object] = {
        "decision_id": DECISION_ID,
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "vault_id": context.vault_id,
        "owner_subject_hash": context.owner_subject_hash,
        "authority_epoch": context.authority_epoch,
        "stage": PublicationCanaryStage.ADULT_COHORT,
        "policy_hash": _digest("publication-policy"),
        "build_hash": _digest("build"),
        "schema_hash": _digest("schema"),
        "evidence_hashes": (_digest("evidence-1"),),
        "evidence": _evidence(),
        "external_g2_evidence_present": False,
        "external_g3_evidence_present": False,
        "external_g4_approval_present": False,
    }
    values.update(overrides)
    return PublicationCanaryExitRequest(**values)  # type: ignore[arg-type]


def _evaluate(**overrides: object):
    values: dict[str, object] = {
        "context": _context(),
        "principal": _principal(),
        "request": _request(),
        "enabled": True,
    }
    values.update(overrides)
    return evaluate_publication_canary_exit_readiness(**values)


class PublicationCanaryExitReadinessTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_invalid_inputs(self) -> None:
        result = evaluate_publication_canary_exit_readiness(
            context=object(), principal=object(), request=object()
        )
        self.assertEqual(result.disposition, PublicationCanaryExitDisposition.SHADOW_DISABLED)
        self.assertEqual(result.decision, PublicationCanaryDecision.NO_GO)
        self.assertFalse(result.cohort_enrolled)
        self.assertFalse(result.public_access_enabled)

    def test_invalid_or_cross_owner_context_fails_closed(self) -> None:
        result = _evaluate(context=object())
        self.assertEqual(result.disposition, PublicationCanaryExitDisposition.INVALID_CONTEXT)

        result = _evaluate(request=_request(vault_id="vault-other"))
        self.assertEqual(result.disposition, PublicationCanaryExitDisposition.OWNER_SCOPE_DENIED)

        result = _evaluate(principal=_principal(vault_id="vault-other"))
        self.assertEqual(result.disposition, PublicationCanaryExitDisposition.OWNER_SCOPE_DENIED)

    def test_any_private_leak_revoke_gap_unknown_effect_or_incident_pauses(self) -> None:
        for field_name in (
            "private_leak_observed",
            "revoke_gap_observed",
            "unknown_required_effect_observed",
            "open_incident_observed",
        ):
            with self.subTest(field_name=field_name):
                result = _evaluate(request=_request(evidence=_evidence(**{field_name: True})))
                self.assertEqual(result.disposition, PublicationCanaryExitDisposition.PAUSE_REQUIRED)
                self.assertEqual(result.decision, PublicationCanaryDecision.PAUSE)
                self.assertFalse(result.incident_dispatched)
                self.assertFalse(result.rights_exit_executed)

    def test_legacy_guest_traffic_and_incomplete_internal_evidence_are_no_go(self) -> None:
        result = _evaluate(
            request=_request(evidence=_evidence(legacy_guest_path_hit_count=1))
        )
        self.assertEqual(
            result.disposition,
            PublicationCanaryExitDisposition.LEGACY_PATH_RETIREMENT_REQUIRED,
        )
        self.assertEqual(result.decision, PublicationCanaryDecision.NO_GO)

        result = _evaluate(
            request=_request(evidence=_evidence(rights_exit_candidate_present=False))
        )
        self.assertEqual(result.disposition, PublicationCanaryExitDisposition.INTERNAL_EVIDENCE_REQUIRED)
        self.assertEqual(result.decision, PublicationCanaryDecision.NO_GO)

    def test_adult_cohort_requires_external_g2_g3_and_g4(self) -> None:
        result = _evaluate()
        self.assertEqual(result.disposition, PublicationCanaryExitDisposition.EXTERNAL_GATES_REQUIRED)
        self.assertIn("G2", result.required_gates)
        self.assertIn("G3", result.required_gates)
        self.assertIn("G4", result.required_gates)

    def test_even_all_positive_candidate_observations_do_not_promote(self) -> None:
        result = _evaluate(
            request=_request(
                external_g2_evidence_present=True,
                external_g3_evidence_present=True,
                external_g4_approval_present=True,
            )
        )
        self.assertEqual(result.disposition, PublicationCanaryExitDisposition.POLICY_DISABLED)
        self.assertEqual(result.decision, PublicationCanaryDecision.NO_GO)
        self.assertFalse(result.cohort_enrolled)
        self.assertFalse(result.public_access_enabled)
        self.assertFalse(result.regulatory_exit_approved)

    def test_synthetic_and_internal_stages_remain_manual_and_disabled(self) -> None:
        for stage in (PublicationCanaryStage.SYNTHETIC, PublicationCanaryStage.INTERNAL):
            with self.subTest(stage=stage):
                result = _evaluate(request=_request(stage=stage))
                self.assertEqual(result.disposition, PublicationCanaryExitDisposition.POLICY_DISABLED)
                self.assertEqual(result.decision, PublicationCanaryDecision.NO_GO)

    def test_value_free_summary_excludes_private_identifiers_and_has_fixed_fields(self) -> None:
        context = _context(vault_id="vault-private-marker", owner_subject_hash=_digest("owner-private-marker"))
        request = _request(vault_id=context.vault_id, owner_subject_hash=context.owner_subject_hash)
        principal = PublicationAuthorizationPrincipal(
            kind=PublicationPrincipalKind.OWNER,
            vault_id=context.vault_id,
            subject_hash=context.owner_subject_hash,
        )
        result = _evaluate(context=context, principal=principal, request=request)
        summary = result.value_free_summary()
        self.assertEqual(
            set(summary),
            {
                "cohortEnrolled",
                "decision",
                "incidentDispatched",
                "publicAccessEnabled",
                "reasonCodes",
                "regulatoryExitApproved",
                "releaseVisible",
                "requiredGates",
                "rightsExitExecuted",
                "schemaVersion",
                "scopeHash",
                "stage",
                "status",
            },
        )
        serialized = json.dumps(summary, sort_keys=True)
        self.assertNotIn("vault-private-marker", serialized)
        self.assertNotIn(context.owner_subject_hash, serialized)
        self.assertNotIn(PUBLICATION_ID, serialized)
        self.assertNotIn(VERSION_ID, serialized)

    def test_migration_manifest_is_additive_and_default_blocked(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0057")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "publicationAdultCanaryV1": False,
                "publicationRegulatoryExitV1": False,
                "publicationVisitorReleaseV1": False,
            },
        )
        for table in ("canary_decision_candidates", "incident_exit_candidates"):
            self.assertIn(f"create table publication.{table}", sql)
            self.assertIn(f"revoke all on table publication.{table} from public;", sql)
        for forbidden in (
            "content_body",
            "conversation_body",
            "source_payload",
            "object_url",
            "preview_url",
            "raw_identity",
            "search_text",
            "visitor_subject_hash",
        ):
            self.assertNotIn(forbidden, sql)

    def test_migration_loader_and_domain_remain_route_persistence_network_free(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        metadata = next(item for item in migrations if item.version == "0057")
        self.assertEqual(metadata.name, "publication_canary_exit_readiness")

        source = (ROOT / "app/domain/publication/canary_exit_readiness.py").read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services",
            "app.async_effects",
            "app.domain.owner_truth",
            "requests",
            "httpx",
            "psycopg",
            "boto3",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
