"""G0 tests for hidden Publication ViewState and release-guard contracts."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.lifecycle_propagation import PublicationLifecycleState
from app.domain.publication.release_guard_viewstate import (
    PublicationAggregateMetrics,
    PublicationReleaseGuardDisposition,
    PublicationReleasePolicySnapshot,
    PublicationViewAudience,
    PublicationViewStateRequest,
    evaluate_publication_release_guard_viewstate,
)
from app.domain.publication.schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationVisitorIdentity,
    PublicationVisitorRelationshipOrigin,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0056_publication_release_guard_viewstate.sql"
MIGRATION_JSON = ROOT / "db/migrations/0056_publication_release_guard_viewstate.json"
PUBLICATION_ID = "7f031c35-0a59-430f-b0bd-2f78969f7d60"
VERSION_ID = "84ce6f2c-e4f5-4b4f-a0f1-d9615cbf2d85"


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


def _owner_principal(**overrides: object) -> PublicationAuthorizationPrincipal:
    context = _context()
    values: dict[str, object] = {
        "kind": PublicationPrincipalKind.OWNER,
        "vault_id": context.vault_id,
        "subject_hash": context.owner_subject_hash,
    }
    values.update(overrides)
    return PublicationAuthorizationPrincipal(**values)  # type: ignore[arg-type]


def _visitor(**overrides: object) -> PublicationVisitorIdentity:
    values: dict[str, object] = {
        "subject_hash": _digest("visitor"),
        "adult_verification": PublicationAdultVerificationState.VERIFIED,
        "relationship_origin": PublicationVisitorRelationshipOrigin.DIRECT,
    }
    values.update(overrides)
    return PublicationVisitorIdentity(**values)  # type: ignore[arg-type]


def _visitor_principal(**overrides: object) -> PublicationAuthorizationPrincipal:
    visitor = _visitor()
    values: dict[str, object] = {
        "kind": PublicationPrincipalKind.VISITOR,
        "vault_id": None,
        "subject_hash": visitor.subject_hash,
    }
    values.update(overrides)
    return PublicationAuthorizationPrincipal(**values)  # type: ignore[arg-type]


def _metrics(**overrides: object) -> PublicationAggregateMetrics:
    values: dict[str, object] = {
        "grant_count": 4,
        "session_count": 8,
        "feedback_count": 3,
        "report_count": 1,
        "receipt_count": 6,
        "minimum_sample_size": 3,
    }
    values.update(overrides)
    return PublicationAggregateMetrics(**values)  # type: ignore[arg-type]


def _policy(**overrides: object) -> PublicationReleasePolicySnapshot:
    values: dict[str, object] = {
        "server_publication_switch_enabled": False,
        "visitor_feature_switch_enabled": False,
        "policy_ttl_valid": False,
        "minimum_client_satisfied": False,
        "cohort_approved": False,
        "offline": False,
    }
    values.update(overrides)
    return PublicationReleasePolicySnapshot(**values)  # type: ignore[arg-type]


def _request(**overrides: object) -> PublicationViewStateRequest:
    context = _context()
    values: dict[str, object] = {
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "vault_id": context.vault_id,
        "owner_subject_hash": context.owner_subject_hash,
        "authority_epoch": context.authority_epoch,
        "policy_hash": _digest("publication-policy"),
        "lifecycle_state": PublicationLifecycleState.PUBLISHED,
        "audience": PublicationViewAudience.OWNER,
        "aggregate_metrics": _metrics(),
        "release_policy": _policy(),
    }
    values.update(overrides)
    return PublicationViewStateRequest(**values)  # type: ignore[arg-type]


def _evaluate(**overrides: object):
    values: dict[str, object] = {
        "context": _context(),
        "principal": _owner_principal(),
        "request": _request(),
        "visitor": None,
        "enabled": True,
    }
    values.update(overrides)
    return evaluate_publication_release_guard_viewstate(**values)


class PublicationReleaseGuardViewStateTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_invalid_inputs(self) -> None:
        result = evaluate_publication_release_guard_viewstate(
            context=object(), principal=object(), request=object()
        )
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.SHADOW_DISABLED)
        self.assertFalse(result.owner_management_visible)
        self.assertFalse(result.visitor_feature_visible)
        self.assertFalse(result.public_route_registered)

    def test_invalid_or_cross_owner_context_fails_closed(self) -> None:
        result = _evaluate(context=object())
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.INVALID_CONTEXT)

        result = _evaluate(request=_request(vault_id="vault-other"))
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.OWNER_SCOPE_DENIED)

        result = _evaluate(principal=_owner_principal(vault_id="vault-other"))
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.OWNER_SCOPE_DENIED)

    def test_owner_aggregate_metrics_require_privacy_threshold_and_stay_hidden(self) -> None:
        result = _evaluate(request=_request(aggregate_metrics=_metrics(session_count=2)))
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.PRIVACY_THRESHOLD_REQUIRED)
        self.assertEqual(
            result.owner_aggregate_metrics,
            {
                "metricsSuppressed": True,
                "minimumSampleSize": 3,
                "privacyThresholdMet": False,
            },
        )

        result = _evaluate()
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.POLICY_DISABLED)
        self.assertEqual(result.owner_aggregate_metrics["sessionCount"], 8)
        self.assertFalse(result.owner_management_visible)
        self.assertFalse(result.aggregate_metrics_query_allowed)

    def test_owner_summary_is_allowlisted_and_value_minimized(self) -> None:
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
                "aggregateMetricsQueryAllowed",
                "audience",
                "lifecycleState",
                "ownerAggregateMetrics",
                "ownerManagementVisible",
                "publicRouteRegistered",
                "reasonCodes",
                "releaseVisible",
                "schemaVersion",
                "scopeHash",
                "status",
                "visitorFeatureVisible",
                "visitorSessionAccepted",
            },
        )
        serialized = json.dumps(summary, sort_keys=True)
        self.assertNotIn("vault-private-marker", serialized)
        self.assertNotIn(context.owner_subject_hash, serialized)
        self.assertNotIn(PUBLICATION_ID, serialized)
        self.assertNotIn(VERSION_ID, serialized)

    def test_visitor_never_inherits_owner_or_family_access(self) -> None:
        request = _request(audience=PublicationViewAudience.VISITOR)
        result = _evaluate(
            request=request,
            principal=_owner_principal(),
            visitor=_visitor(),
        )
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.VISITOR_SCOPE_DENIED)

        result = _evaluate(
            request=request,
            principal=_visitor_principal(),
            visitor=None,
        )
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.VISITOR_SESSION_REQUIRED)

        result = _evaluate(
            request=request,
            principal=_visitor_principal(),
            visitor=_visitor(relationship_origin=PublicationVisitorRelationshipOrigin.FAMILY_DERIVED),
        )
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.FAMILY_AUTO_GRANT_DENIED)

    def test_visitor_requires_adult_verification_and_all_release_guards(self) -> None:
        request = _request(audience=PublicationViewAudience.VISITOR)
        result = _evaluate(
            request=request,
            principal=_visitor_principal(),
            visitor=_visitor(adult_verification=PublicationAdultVerificationState.UNKNOWN),
        )
        self.assertEqual(
            result.disposition,
            PublicationReleaseGuardDisposition.VISITOR_ADULT_VERIFICATION_REQUIRED,
        )

        result = _evaluate(
            request=request,
            principal=_visitor_principal(),
            visitor=_visitor(),
        )
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.RELEASE_GUARD_REQUIRED)

        result = _evaluate(
            request=_request(
                audience=PublicationViewAudience.VISITOR,
                release_policy=_policy(offline=True),
            ),
            principal=_visitor_principal(),
            visitor=_visitor(),
        )
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.OFFLINE_DENIED)

    def test_all_candidate_visitor_prerequisites_still_do_not_open_g0_release(self) -> None:
        result = _evaluate(
            request=_request(
                audience=PublicationViewAudience.VISITOR,
                release_policy=_policy(
                    server_publication_switch_enabled=True,
                    visitor_feature_switch_enabled=True,
                    policy_ttl_valid=True,
                    minimum_client_satisfied=True,
                    cohort_approved=True,
                ),
            ),
            principal=_visitor_principal(),
            visitor=_visitor(),
        )
        self.assertEqual(result.disposition, PublicationReleaseGuardDisposition.POLICY_DISABLED)
        self.assertFalse(result.visitor_feature_visible)
        self.assertFalse(result.visitor_session_accepted)
        self.assertFalse(result.public_route_registered)

    def test_migration_manifest_is_additive_and_default_off(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0056")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "publicationOwnerViewStateV1": False,
                "publicationReleaseGuardV1": False,
                "publicationVisitorFeatureV1": False,
            },
        )
        for table in ("aggregate_metric_snapshots", "release_guard_candidates"):
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
        metadata = next(item for item in migrations if item.version == "0056")
        self.assertEqual(metadata.name, "publication_release_guard_viewstate")

        source = (ROOT / "app/domain/publication/release_guard_viewstate.py").read_text(encoding="utf-8")
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
