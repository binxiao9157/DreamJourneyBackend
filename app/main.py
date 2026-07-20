import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from pydantic import ValidationError

try:
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover - exercised only without runtime deps
    raise RuntimeError("FastAPI is not installed. Run `pip install -r requirements.txt`.") from exc

from app.core.config import settings
from app.services.amap import AMapDistrictProxy
from app.services.auth_sessions import AuthSessionError, AuthSessionService
from app.services.authorization_policy import (
    CrossAccountAuthorizationPolicy,
    owner_authority_claims,
)
from app.services.data_rights_adapter import (
    completed_account_delete_execution,
    make_account_delete_request,
)
from app.services.data_rights_contract import (
    DataRightsCommandConflict,
    DataRightsContractError,
)
from app.services.data_rights_evidence_projection import (
    DataRightsEvidenceProjectionError,
    build_data_rights_evidence_projection,
)
from app.services.data_rights_module_inventory import build_module_owned_data_export
from app.services.client_compatibility import (
    ClientCompatibilityDecision,
    ClientCompatibilityDecisionRecorder,
    ClientCompatibilityPolicy,
    resolve_client_compatibility_mode,
)
from app.services.deepseek import ArchiveImageAnalysisProviderFactory
from app.services.echo_delayed_reply_effects import ECHO_DELAYED_REPLY_SCHEMA_VERSION
from app.services.digital_human_access import DigitalHumanAccessPolicy
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessError,
    DelegatedAccessService,
    GrantOperation,
    RelationshipLifecycleCommand,
    ResourceScopeType,
    RevokeAccessGrantCommand,
)
from app.services.identity_bindings import (
    IdentityChallengeConfigurationError,
    IdentityChallengeRateLimited,
    IdentityChallengeValidationError,
    IdentityChallengeVerificationFailed,
    legacy_phone_login_enabled,
    make_identity_binding_service,
)
from app.services.privacy import (
    filter_syncable_graph,
    sanitize_archive_item_payload,
    sanitize_care_snapshot_payload,
    sanitize_image_analysis_payload,
    sanitize_knowledge_extraction_payload,
    sanitize_mailbox_letter_payload,
)
from app.services.owner_truth_source import ArchiveOwnerTruthCompatibilityFacade
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewConflict,
    OwnerTruthCandidateReviewError,
    OwnerTruthCandidateReviewSourceInactive,
    OwnerTruthCandidateVersionConflict,
)
from app.domain.owner_truth.interview_candidate_batch_decision import (
    OwnerTruthInterviewCandidateBatchAcceptCommand,
    OwnerTruthInterviewCandidateBatchDecisionConflict,
    OwnerTruthInterviewCandidateBatchDecisionError,
    OwnerTruthInterviewCandidateBatchDecisionNotReady,
    OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired,
    OwnerTruthInterviewCandidateBatchSelection,
)
from app.domain.owner_truth.interview_candidate_review import (
    OwnerTruthInterviewCandidateReviewAccessDenied,
    OwnerTruthInterviewCandidateReviewConflict,
    OwnerTruthInterviewCandidateReviewError,
    OwnerTruthInterviewCandidateReviewSourceInactive,
)
from app.domain.owner_truth.interview_candidate_single_review import (
    OwnerTruthInterviewCandidateSingleReviewBatchRequired,
    OwnerTruthInterviewCandidateSingleReviewCommand,
    OwnerTruthInterviewCandidateSingleReviewConflict,
    OwnerTruthInterviewCandidateSingleReviewError,
    OwnerTruthInterviewCandidateSingleReviewNotReady,
)
from app.domain.owner_truth.contracts import OwnerTruthContractError
from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationConflict,
    OwnerTruthConversationError,
    OwnerTruthConversationVersionConflict,
    OwnerTruthInterviewSessionStateConflict,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
    projection_summary,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.knowledge_recommendations import RecommendationCandidate
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_interview_candidate_batch_decision import (
    OwnerTruthInterviewCandidateBatchDecisionService,
)
from app.services.owner_truth_interview_candidate_review import (
    OwnerTruthInterviewCandidateReviewReadService,
)
from app.services.owner_truth_interview_session_read import (
    OwnerTruthInterviewSessionReadService,
)
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_candidate_single_review import (
    OwnerTruthInterviewCandidateSingleReviewService,
)
from app.services.owner_truth_kblite_compatibility import (
    OwnerTruthKBLiteCompatibilityReadService,
    compatibility_read_envelope as kblite_compatibility_read_envelope,
    compatibility_summary as kblite_compatibility_summary,
)
from app.services.owner_truth_context_shadow import (
    OwnerTruthContextShadowReadService,
    context_shadow_summary,
)
from app.services.owner_truth_context_shadow_build import (
    OwnerTruthContextShadowBuildService,
    context_shadow_build_summary,
)
from app.services.owner_truth_answer_citation import (
    OwnerTruthAnswerCitationCommand,
    OwnerTruthAnswerCitationConflict,
    OwnerTruthAnswerCitationError,
    OwnerTruthAnswerCitationService,
    answer_citation_summary,
)
from app.services.owner_truth_knowledge_dimension_confirmation import (
    OwnerTruthKnowledgeDimensionConfirmationAccessDenied,
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationConflict,
    OwnerTruthKnowledgeDimensionConfirmationError,
    OwnerTruthKnowledgeDimensionConfirmationService,
    OwnerTruthKnowledgeDimensionConfirmationStaleMemory,
    confirmation_summary as knowledge_dimension_confirmation_summary,
)
from app.services.owner_truth_knowledge_recommendation_read import (
    OwnerTruthKnowledgeRecommendationReadError,
    OwnerTruthKnowledgeRecommendationReadService,
)
from app.services.owner_truth_correction_request import (
    OwnerTruthCorrectionRequestAccessDenied,
    OwnerTruthCorrectionRequestCommand,
    OwnerTruthCorrectionRequestConflict,
    OwnerTruthCorrectionRequestError,
    OwnerTruthCorrectionRequestService,
    OwnerTruthCorrectionRequestStaleCitation,
    OwnerTruthCorrectionResolutionCommand,
    OwnerTruthCorrectionResolutionConflict,
    OwnerTruthCorrectionResolutionStale,
    correction_resolution_summary,
    correction_request_summary,
)
from app.services.owner_truth_legacy_migration import (
    OwnerTruthLegacyMigrationAccessDenied,
    OwnerTruthLegacyMigrationConflict,
    OwnerTruthLegacyMigrationError,
    OwnerTruthLegacyMigrationInventoryService,
    OwnerTruthLegacyMigrationUnavailable,
    legacy_migration_summary,
)
from app.services.owner_truth_legacy_shadow_parity import (
    OwnerTruthLegacyShadowParityService,
    legacy_shadow_parity_summary,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.deepseek import DeepSeekKnowledgeExtractionProxy
from app.services.knowledge_store import (
    KB_OPERATION_GOVERNANCE,
    KB_OPERATION_MUTATION,
    KB_OPERATION_SYNC,
    KnowledgeMutationValidationError,
    KnowledgeOperationPayloadConflict,
    KnowledgeRevisionConflict,
)
from app.services.knowledge_extraction import (
    KnowledgeExtractionValidationError,
    empty_evidence_policy,
    filter_extraction_by_evidence,
    normalize_knowledge_extraction_input,
    sanitize_knowledge_extraction_context,
)
from app.services.knowledge_proposal import (
    KnowledgeProposalValidationError,
    build_knowledge_mutation_proposal,
)
from app.services.knowledge_source_ref_audit import audit_knowledge_source_refs
from app.services.knowledge_governance import (
    GOVERNANCE_SCHEMA_VERSION,
    KnowledgeGovernanceNotFound,
    KnowledgeGovernanceValidationError,
    build_knowledge_governance_mutation,
    normalize_knowledge_governance_action,
    summarize_knowledge_governance_mutation,
)
from app.services.passwords import make_password_credential, verify_password
from app.services.archive_store import (
    ArchiveItemDeletionForbidden,
    ArchiveItemNotFound,
    ArchiveItemOwnershipConflict,
    ResourceOwnershipConflict,
    ResourceVersionConflict,
)
from app.services.runtime_config import RuntimeConfigService
from app.services.safety_policy import (
    HighRiskCapability,
    SafetyPolicy,
    SubjectEligibilityDecision,
    SubjectEligibilityEvidence,
    SubjectEligibilityReason,
    evaluate_subject_eligibility,
)
from app.services.recovery_access import RecoveryAccessPolicy
from app.services.route_authentication import (
    MACHINE_API_AUDIENCE,
    MACHINE_SYSTEM_SCOPES,
    PrincipalKind,
    RequestPrincipal,
    RouteAuthenticationDecision,
    RouteAuthenticationDecisionRecorder,
    RouteAuthenticationPolicy,
    resolve_route_authentication_mode,
    validate_route_authentication_startup,
)
from app.services.readiness import ReadinessService, liveness_payload
from app.services.incident_lifecycle import (
    IncidentLifecycleError,
    IncidentLifecycleService,
)
from app.observability.operation_metrics import (
    OperationMetricRecorder,
    summarize_operation_metrics_for_observations,
)
from app.observability.provider_costs import (
    ProviderCostEvidenceRecorder,
    summarize_provider_cost_evidence_for_observations,
)
from app.observability.evidence_manifest import (
    EvidenceManifestError,
    EvidenceManifestService,
)
from app.observability.redaction import provider_error_detail
from app.services.release_policy import (
    ReleasePolicyCommandGate,
    ReleasePolicyDecisionRecorder,
    ReleasePolicyFeatureAccessDenied,
    ReleasePolicyService,
    ReleasePolicySnapshot,
    ReleasePolicyVersionDowngrade,
    normalize_release_policy_audience,
    parse_release_policy_feature_set,
)
from app.services.context_packet import ContextPacketBuilder
from app.db.pool import ConnectionPoolExhausted
from app.services.store_factory import close_store, init_store, make_store
from app.services.tokens import TokenService
from app.services.tts import TencentAudioDrivePCMAdapter, VolcTTSProxy, VoiceCloneTTSProviderFactory
from app.services.time_letters import (
    TimeLetterAccessError,
    dispatch_due_time_letters_for_store,
    time_letter_detail_for_viewer,
)
from app.services.voice_clone import (
    VoiceCloneProviderFactory,
    VoiceCloneProviderUnavailable,
    configured_voice_clone_speaker_ids,
    uses_voice_clone_speaker_pool,
)
from app.services.user_identity import stable_user_id


app = FastAPI(title=settings.app_name, version="0.1.0")
store = make_store(settings)
logger = logging.getLogger(__name__)


def _delegated_access_service() -> DelegatedAccessService:
    return DelegatedAccessService(store)


def _delegated_access_http_error(error: DelegatedAccessError) -> HTTPException:
    status_code = 404 if error.code in {"relationshipNotFound", "grantNotFound"} else 409
    if error.code in {"relationshipOwnerMismatch", "grantOwnerMismatch", "relationshipSubjectMismatch"}:
        status_code = 403
    return HTTPException(status_code=status_code, detail={"code": error.code})


def _require_delegated_access_contract_api() -> None:
    if not DELEGATED_ACCESS_CONTRACT_API_ENABLED:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "delegatedAccessContractDefaultOff",
                "retryable": False,
            },
        )


@app.exception_handler(ResourceOwnershipConflict)
async def resource_ownership_conflict_handler(
    _request: Request,
    _error: ResourceOwnershipConflict,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": {"code": "resourceOwnershipConflict"}},
    )

BACKEND_API_TOKEN = settings.backend_api_token or ""
DELEGATED_ACCESS_CONTRACT_API_ENABLED = bool(
    settings.delegated_access_contract_api_enabled
)
OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = bool(
    settings.owner_truth_candidate_review_qa_enabled
)
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = bool(
    settings.owner_truth_knowledge_dimension_confirmation_qa_enabled
)
OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = bool(
    settings.owner_truth_knowledge_recommendation_read_qa_enabled
)
AUTH_ACCESS_TTL_SECONDS = max(60, settings.auth_access_ttl_seconds)
AUTH_REFRESH_TTL_SECONDS = max(AUTH_ACCESS_TTL_SECONDS + 60, settings.auth_refresh_ttl_seconds)
AUTH_LEGACY_PHONE_LOGIN_ENABLED = legacy_phone_login_enabled(settings)
AUTH_ROUTE_MODE = resolve_route_authentication_mode(
    settings.environment,
    settings.auth_route_mode,
)
AUTH_OWNERSHIP_MODE = (
    settings.auth_ownership_mode
    if settings.auth_ownership_mode in {"shadow", "enforce"}
    else "shadow"
)


def _require_owner_truth_candidate_review_qa(request: Request) -> str:
    """Keep the V4 review lane unavailable outside an explicit QA session."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        # A stable not-found response avoids presenting this unfinished contract
        # as a released product capability.
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthCandidateReviewUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthCandidateReviewUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_memory_projection_qa(request: Request) -> str:
    """Keep the derived V4 Projection surface unavailable outside QA."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthMemoryProjectionUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthMemoryProjectionUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_kblite_compatibility_qa(request: Request) -> str:
    """Keep the derived KBLite compatibility surface unavailable outside QA."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthKBLiteCompatibilityUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthKBLiteCompatibilityUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_context_shadow_qa(request: Request) -> str:
    """Keep the citation-only Context shadow unavailable outside QA."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthContextShadowUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthContextShadowUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_answer_citation_qa(request: Request) -> str:
    """Keep Answer/Citation evidence unavailable outside explicit Owner QA."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthAnswerCitationUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthAnswerCitationUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_knowledge_dimension_confirmation_qa(request: Request) -> str:
    """Keep explicit Owner dimension confirmation unavailable outside QA."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or not OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthKnowledgeDimensionConfirmationUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthKnowledgeDimensionConfirmationUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_knowledge_recommendation_read_qa(request: Request) -> str:
    """Keep M0-B recommendation QA unavailable outside a separate gate."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or not OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED
        or not OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthKnowledgeRecommendationReadUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthKnowledgeRecommendationReadUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_correction_request_qa(request: Request) -> str:
    """Keep Answer/Citation correction requests unavailable outside Owner QA."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthCorrectionRequestUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthCorrectionRequestUserSessionRequired"},
        )
    return user_id


def _require_owner_truth_legacy_migration_qa(request: Request) -> str:
    """Keep hash-only legacy inventory unavailable outside explicit Owner QA."""

    if (
        not OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        or str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() != "1"
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "ownerTruthLegacyMigrationUnavailable"},
        )
    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "ownerTruthLegacyMigrationUserSessionRequired"},
        )
    return user_id


def _owner_truth_candidate_review_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_candidate_review_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_captured_release_policy_context(
    request: Request,
    *,
    vault_id: str,
    feature: str,
    route: str,
    user_session_required_code: str,
) -> OwnerTruthCommandContext:
    """Authorize one owner-only route from a captured release policy.

    Callers must carry a current server-authorized feature capture. This stays
    fail-closed even while the broader command-policy middleware observes
    other routes. QA routes intentionally use their own explicit helpers and
    must never inherit this authorization path.
    """
    principal = getattr(request.state, "auth_principal", None)
    owner_subject_id = _request_user_principal_id(request)
    if not isinstance(principal, RequestPrincipal) or owner_subject_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": user_session_required_code},
        )

    observed_client_build = _release_policy_int_header(
        request,
        "x-dreamjourney-client-build",
    ) or 1
    try:
        captured = RELEASE_POLICY_COMMAND_GATE.capture(
            feature=feature,
            audience=_release_policy_audience(request, principal),
            cohort=str(
                request.headers.get("x-dreamjourney-policy-cohort")
                or "closedPilotAdultSelf"
            ).strip(),
            client_build=observed_client_build,
            client_policy_version=str(
                request.headers.get("x-dreamjourney-policy-version") or ""
            ).strip() or None,
            client_policy_revision=_release_policy_int_header(
                request,
                "x-dreamjourney-policy-revision",
            ),
            client_account_generation=str(
                request.headers.get("x-dreamjourney-account-generation") or ""
            ).strip() or None,
            client_allowed=_release_policy_bool_header(
                request,
                "x-dreamjourney-feature-allowed",
            ),
            client_decision_id=str(
                request.headers.get("x-dreamjourney-feature-decision-id") or ""
            ).strip() or None,
            client_feature=str(
                request.headers.get("x-dreamjourney-feature") or ""
            ).strip() or None,
            expected_account_generation=_release_policy_account_generation(principal),
            require_client_capture=True,
        )
        RELEASE_POLICY_COMMAND_GATE.revalidate_effect(captured)
    except ReleasePolicyFeatureAccessDenied as error:
        RELEASE_POLICY_DECISION_RECORDER.record(
            feature=feature,
            policy_version=RELEASE_POLICY_SERVICE.POLICY_VERSION,
            client_build=observed_client_build,
            decision="deny",
            reason=error.reason,
            route=route,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "code": "release_policy_denied",
                "feature": error.feature,
                "reason": error.reason,
                "policyRevision": error.policy_revision,
                "retryable": False,
            },
        ) from error

    RELEASE_POLICY_DECISION_RECORDER.record(
        feature=feature,
        policy_version=captured.policy_version,
        client_build=observed_client_build,
        decision="allow",
        reason=captured.server_reason,
        route=route,
    )
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_interview_natural_input_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    """Authorize M0 natural input without opening review QA routes."""

    if str(request.headers.get("x-dreamjourney-qa-owner-truth") or "").strip() == "1":
        return _owner_truth_candidate_review_context(request, vault_id=vault_id)
    return _owner_truth_captured_release_policy_context(
        request,
        vault_id=vault_id,
        feature="echoTextInput",
        route=f"{request.method.upper()} /v2/vaults/*/interview-sessions",
        user_session_required_code="ownerTruthInterviewNaturalInputUserSessionRequired",
    )


def _owner_truth_interview_candidate_confirmation_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    """Authorize the default-off product confirmation read, never QA routes."""

    return _owner_truth_captured_release_policy_context(
        request,
        vault_id=vault_id,
        feature="ownerTruthCandidateReview",
        route=(
            f"{request.method.upper()} /v2/vaults/*/interview-review-batches/*/confirmation"
        ),
        user_session_required_code="ownerTruthInterviewCandidateConfirmationUserSessionRequired",
    )


def _owner_truth_interview_candidate_confirmation_write_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    """Authorize one product confirmation effect behind the captured policy.

    The formal confirmation action must stay separate from both the QA review
    routes and the read route. A QA header is never an authorization bypass.
    """

    return _owner_truth_captured_release_policy_context(
        request,
        vault_id=vault_id,
        feature="ownerTruthCandidateReview",
        route=(
            f"{request.method.upper()} /v2/vaults/*/interview-review-batches/*/"
            "confirmation/batch-accept"
        ),
        user_session_required_code="ownerTruthInterviewCandidateConfirmationUserSessionRequired",
    )


def _owner_truth_memory_projection_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_memory_projection_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_kblite_compatibility_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_kblite_compatibility_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_context_shadow_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_context_shadow_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_answer_citation_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_answer_citation_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_knowledge_dimension_confirmation_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_knowledge_dimension_confirmation_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_knowledge_recommendation_read_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_knowledge_recommendation_read_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_correction_request_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_correction_request_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_legacy_migration_context(
    request: Request,
    *,
    vault_id: str,
) -> OwnerTruthCommandContext:
    owner_subject_id = _require_owner_truth_legacy_migration_qa(request)
    return OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )


def _owner_truth_candidate_review_http_error(
    error: OwnerTruthContractError,
) -> HTTPException:
    if isinstance(error, OwnerTruthCandidateReviewAccessDenied):
        return HTTPException(status_code=403, detail={"code": "ownerTruthCandidateReviewDenied"})
    if isinstance(error, OwnerTruthCandidateVersionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "ownerTruthCandidateVersionConflict",
                "expectedCandidateVersion": error.expected_version,
                "currentCandidateVersion": error.current_version,
            },
        )
    if isinstance(error, OwnerTruthCandidateReviewSourceInactive):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthCandidateSourceInactive"},
        )
    if isinstance(error, OwnerTruthCandidateReviewConflict):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthCandidateReviewConflict"},
        )
    return HTTPException(
        status_code=400,
        detail={"code": "ownerTruthCandidateReviewInvalid"},
    )


def _owner_truth_interview_candidate_review_http_error(
    error: OwnerTruthContractError,
) -> HTTPException:
    """Map the M0-A non-activation review lane to stable QA-only errors."""

    if isinstance(
        error,
        (
            OwnerTruthInterviewCandidateReviewAccessDenied,
            OwnerTruthCandidateReviewAccessDenied,
        ),
    ):
        return HTTPException(
            status_code=403,
            detail={"code": "ownerTruthInterviewCandidateReviewDenied"},
        )
    if isinstance(error, OwnerTruthCandidateVersionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "ownerTruthInterviewCandidateVersionConflict",
                "expectedCandidateVersion": error.expected_version,
                "currentCandidateVersion": error.current_version,
            },
        )
    if isinstance(
        error,
        (
            OwnerTruthInterviewCandidateReviewSourceInactive,
            OwnerTruthCandidateReviewSourceInactive,
        ),
    ):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthInterviewCandidateSourceInactive"},
        )
    if isinstance(
        error,
        (
            OwnerTruthInterviewCandidateBatchDecisionNotReady,
            OwnerTruthInterviewCandidateSingleReviewNotReady,
        ),
    ):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthInterviewCandidateReviewNotReady"},
        )
    if isinstance(
        error,
        (
            OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired,
            OwnerTruthInterviewCandidateSingleReviewBatchRequired,
        ),
    ):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthInterviewCandidateSingleReviewRequired"},
        )
    if isinstance(
        error,
        (
            OwnerTruthInterviewCandidateReviewConflict,
            OwnerTruthInterviewCandidateBatchDecisionConflict,
            OwnerTruthInterviewCandidateSingleReviewConflict,
            OwnerTruthCandidateReviewConflict,
        ),
    ):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthInterviewCandidateReviewConflict"},
        )
    if isinstance(
        error,
        (
            OwnerTruthInterviewCandidateReviewError,
            OwnerTruthInterviewCandidateBatchDecisionError,
            OwnerTruthInterviewCandidateSingleReviewError,
            OwnerTruthCandidateReviewError,
        ),
    ):
        return HTTPException(
            status_code=400,
            detail={"code": "ownerTruthInterviewCandidateReviewInvalid"},
        )
    return HTTPException(
        status_code=400,
        detail={"code": "ownerTruthInterviewCandidateReviewInvalid"},
    )


def _owner_truth_interview_session_state_http_error(
    error: OwnerTruthContractError,
) -> HTTPException:
    """Keep the private session-state read behind stable QA-only errors."""

    if isinstance(error, OwnerTruthConversationAccessDenied):
        return HTTPException(
            status_code=403,
            detail={"code": "ownerTruthInterviewSessionDenied"},
        )
    if isinstance(
        error,
        (
            OwnerTruthConversationConflict,
            OwnerTruthConversationVersionConflict,
            OwnerTruthInterviewSessionStateConflict,
        ),
    ):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthInterviewSessionConflict"},
        )
    if isinstance(error, OwnerTruthConversationError):
        return HTTPException(
            status_code=400,
            detail={"code": "ownerTruthInterviewSessionInvalid"},
        )
    return HTTPException(
        status_code=400,
        detail={"code": "ownerTruthInterviewSessionInvalid"},
    )


def _owner_truth_memory_projection_http_error(
    error: OwnerTruthMemoryProjectionError,
) -> HTTPException:
    if isinstance(error, OwnerTruthMemoryProjectionAccessDenied):
        return HTTPException(
            status_code=403,
            detail={"code": "ownerTruthMemoryProjectionDenied"},
        )
    return HTTPException(
        status_code=409,
        detail={"code": "ownerTruthMemoryProjectionUnavailable"},
    )


def _owner_truth_answer_citation_http_error(
    error: OwnerTruthMemoryProjectionError,
) -> HTTPException:
    if isinstance(error, OwnerTruthMemoryProjectionAccessDenied):
        return HTTPException(status_code=403, detail={"code": "ownerTruthAnswerCitationDenied"})
    if isinstance(error, OwnerTruthAnswerCitationConflict):
        return HTTPException(status_code=409, detail={"code": "ownerTruthAnswerCitationConflict"})
    return HTTPException(status_code=400, detail={"code": "ownerTruthAnswerCitationInvalid"})


def _owner_truth_knowledge_dimension_confirmation_http_error(
    error: OwnerTruthKnowledgeDimensionConfirmationError,
) -> HTTPException:
    if isinstance(error, OwnerTruthKnowledgeDimensionConfirmationAccessDenied):
        return HTTPException(
            status_code=403,
            detail={"code": "ownerTruthKnowledgeDimensionConfirmationDenied"},
        )
    if isinstance(error, OwnerTruthKnowledgeDimensionConfirmationStaleMemory):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthKnowledgeDimensionConfirmationStaleMemory"},
        )
    if isinstance(error, OwnerTruthKnowledgeDimensionConfirmationConflict):
        return HTTPException(
            status_code=409,
            detail={"code": "ownerTruthKnowledgeDimensionConfirmationConflict"},
        )
    return HTTPException(
        status_code=400,
        detail={"code": "ownerTruthKnowledgeDimensionConfirmationInvalid"},
    )


def _owner_truth_knowledge_recommendation_read_http_error(
    error: OwnerTruthContractError,
) -> HTTPException:
    if isinstance(error, OwnerTruthMemoryProjectionAccessDenied):
        return HTTPException(
            status_code=403,
            detail={"code": "ownerTruthKnowledgeRecommendationReadDenied"},
        )
    if isinstance(error, OwnerTruthKnowledgeRecommendationReadError):
        return HTTPException(
            status_code=400,
            detail={"code": "ownerTruthKnowledgeRecommendationReadInvalid"},
        )
    return HTTPException(
        status_code=400,
        detail={"code": "ownerTruthKnowledgeRecommendationReadInvalid"},
    )


def _owner_truth_correction_request_http_error(
    error: OwnerTruthCorrectionRequestError,
) -> HTTPException:
    if isinstance(error, OwnerTruthCorrectionRequestAccessDenied):
        return HTTPException(status_code=403, detail={"code": "ownerTruthCorrectionRequestDenied"})
    if isinstance(error, OwnerTruthCorrectionRequestStaleCitation):
        return HTTPException(status_code=409, detail={"code": "ownerTruthCorrectionRequestStaleCitation"})
    if isinstance(error, OwnerTruthCorrectionRequestConflict):
        return HTTPException(status_code=409, detail={"code": "ownerTruthCorrectionRequestConflict"})
    if isinstance(error, OwnerTruthCorrectionResolutionStale):
        return HTTPException(status_code=409, detail={"code": "ownerTruthCorrectionResolutionStale"})
    if isinstance(error, OwnerTruthCorrectionResolutionConflict):
        return HTTPException(status_code=409, detail={"code": "ownerTruthCorrectionResolutionConflict"})
    return HTTPException(status_code=400, detail={"code": "ownerTruthCorrectionRequestInvalid"})


def _owner_truth_legacy_migration_http_error(
    error: OwnerTruthLegacyMigrationError,
) -> HTTPException:
    if isinstance(error, OwnerTruthLegacyMigrationAccessDenied):
        return HTTPException(status_code=403, detail={"code": "ownerTruthLegacyMigrationDenied"})
    if isinstance(error, OwnerTruthLegacyMigrationConflict):
        return HTTPException(status_code=409, detail={"code": "ownerTruthLegacyMigrationConflict"})
    if isinstance(error, OwnerTruthLegacyMigrationUnavailable):
        return HTTPException(status_code=404, detail={"code": "ownerTruthLegacyMigrationUnavailable"})
    return HTTPException(status_code=400, detail={"code": "ownerTruthLegacyMigrationInvalid"})


def _owner_truth_candidate_expected_version(payload: Dict[str, Any]) -> int:
    try:
        return int(payload.get("expectedCandidateVersion") or 0)
    except (TypeError, ValueError) as error:
        raise OwnerTruthCandidateReviewError(
            "expectedCandidateVersion must be a positive integer"
        ) from error


def _owner_truth_interview_candidate_batch_selections(
    payload: Dict[str, Any],
) -> tuple[OwnerTruthInterviewCandidateBatchSelection, ...]:
    raw_selections = payload.get("selections")
    if not isinstance(raw_selections, list):
        raise OwnerTruthInterviewCandidateBatchDecisionError(
            "selections must be a non-empty array"
        )
    selections: list[OwnerTruthInterviewCandidateBatchSelection] = []
    for raw_selection in raw_selections:
        if not isinstance(raw_selection, dict):
            raise OwnerTruthInterviewCandidateBatchDecisionError(
                "each selection must be an object"
            )
        try:
            expected_version = int(raw_selection.get("expectedCandidateVersion") or 0)
        except (TypeError, ValueError) as error:
            raise OwnerTruthInterviewCandidateBatchDecisionError(
                "expectedCandidateVersion must be a positive integer"
            ) from error
        selections.append(
            OwnerTruthInterviewCandidateBatchSelection(
                candidate_id=str(raw_selection.get("candidateId") or ""),
                expected_candidate_version=expected_version,
            )
        )
    return tuple(selections)


def _owner_truth_candidate_inbox_item_response(item: Any) -> Dict[str, Any]:
    return {
        "candidateId": item.candidate_id,
        "sourceId": item.source_id,
        "memoryKind": item.memory_kind,
        "perspectiveType": item.perspective_type,
        "epistemicStatus": item.epistemic_status,
        "sensitivity": item.sensitivity,
        "contentSchemaVersion": item.content_schema_version,
        "content": dict(item.content),
        "contentHash": item.content_hash,
        "sourceRefs": [dict(source_ref) for source_ref in item.source_refs],
        "reviewMode": item.review_mode,
        "candidateVersion": item.candidate_row_version,
        "createdAt": item.created_at,
    }


def _owner_truth_candidate_decision_response(result: Any) -> Dict[str, Any]:
    review = result.review
    activation = result.memory_activation
    return {
        "schemaVersion": "owner-truth-candidate-decision-memory-v1",
        "status": review.outcome,
        "receipt": {
            "receiptId": review.receipt_id,
            "candidateId": review.candidate_id,
            "decision": review.decision.value,
            "candidateVersion": review.candidate_row_version,
            "candidateBeforeHash": review.candidate_before_hash,
            "candidateAfterHash": review.candidate_after_hash,
            "correctedValueId": review.corrected_value_id,
        },
        "memoryActivation": {
            "status": activation.outcome,
            "memoryId": activation.memory_id,
            "memoryVersionId": activation.memory_version_id,
            "contentHash": activation.content_hash,
        },
    }


def _owner_truth_interview_candidate_review_item_response(item: Any) -> Dict[str, Any]:
    """Reuse the existing Owner Candidate preview without changing its authority."""

    response = _owner_truth_candidate_inbox_item_response(item.candidate)
    response.update(
        {
            "extractionId": item.review_item.extraction_id,
            "reviewPath": item.review_item.review_path.value,
        }
    )
    return response


def _owner_truth_interview_candidate_review_read_response(
    *,
    vault_id: str,
    result: Any,
) -> Dict[str, Any]:
    return {
        "schemaVersion": "owner-truth-interview-candidate-review-read-v1",
        "vaultId": vault_id,
        "review": result.composition.public_summary(),
        "batchCandidates": [
            _owner_truth_interview_candidate_review_item_response(item)
            for item in result.batch_candidates
        ],
        "singleCandidates": [
            _owner_truth_interview_candidate_review_item_response(item)
            for item in result.single_candidates
        ],
    }


def _owner_truth_interview_candidate_confirmation_read_response(
    *,
    vault_id: str,
    result: Any,
) -> Dict[str, Any]:
    """Return the same owner-scoped review material under product policy.

    The confirmation content is intentionally available only behind the
    dedicated release-policy feature. Natural-input presentation never calls
    this response builder and remains content-free.
    """

    return {
        "schemaVersion": "owner-truth-interview-candidate-confirmation-read-v1",
        "vaultId": vault_id,
        "confirmation": result.composition.public_summary(),
        "batchCandidates": [
            _owner_truth_interview_candidate_review_item_response(item)
            for item in result.batch_candidates
        ],
        "singleCandidates": [
            _owner_truth_interview_candidate_review_item_response(item)
            for item in result.single_candidates
        ],
    }


def _owner_truth_interview_session_state_read_response(
    *,
    vault_id: str,
    snapshot: Any,
) -> Dict[str, Any]:
    """Return only session lifecycle metadata needed by hidden QA diagnostics."""

    return {
        "schemaVersion": "owner-truth-interview-session-state-read-v1",
        "vaultId": vault_id,
        "session": {
            "state": snapshot.state.value,
            "boundary": snapshot.boundary.value,
            "rowVersion": snapshot.row_version,
            "threadVersion": snapshot.thread_version,
            "ownerTurnCount": snapshot.turn_count,
            "deepeningTurnCount": snapshot.deepening_turn_count,
            "candidateBatchTurnCount": snapshot.candidate_batch_turn_count,
            "fatigue": snapshot.fatigue.value,
            "hasPendingReviewBatch": snapshot.pending_review_batch_id is not None,
            "authorityEpoch": snapshot.authority_epoch,
        },
    }


def _owner_truth_interview_session_presentation_response(
    *,
    vault_id: str,
    snapshot: Any,
) -> Dict[str, Any]:
    """Project one private session into product-safe, value-minimized guidance.

    The product surface must not receive transcript text, candidate content,
    review identifiers, pacing counters, fatigue or internal authority values.
    It only needs enough state to explain whether the owner can continue now or
    has a confirmation boundary ahead.  iOS owns the natural-language copy.
    """

    if snapshot.pending_review_batch_id is not None:
        state = "reviewPending"
        can_continue = False
        can_continue_later = True
    elif snapshot.state.value == "active" and snapshot.boundary.value == "open":
        state = "readyForNarrative" if snapshot.turn_count == 0 else "narrativeRecorded"
        can_continue = True
        can_continue_later = True
    elif snapshot.state.value == "ended":
        state = "ended"
        can_continue = False
        can_continue_later = True
    else:
        state = "paused"
        can_continue = False
        can_continue_later = snapshot.boundary.value != "doNotAsk"

    return {
        "schemaVersion": "owner-truth-interview-session-presentation-v1",
        "vaultId": vault_id,
        "presentation": {
            "state": state,
            "canContinue": can_continue,
            "canContinueLater": can_continue_later,
        },
    }


def _owner_truth_interview_session_command_response(
    *,
    vault_id: str,
    result: Any,
) -> Dict[str, Any]:
    """Return a write receipt without echoing a private conversation message."""

    receipt = result.public_receipt()
    minimized_receipt = {
        key: receipt[key]
        for key in (
            "status",
            "threadId",
            "sessionId",
            "threadVersion",
            "sessionVersion",
            "state",
            "boundary",
            "messageId",
            "messageSequence",
        )
        if key in receipt
    }
    return {
        "schemaVersion": "owner-truth-interview-session-command-v1",
        "vaultId": vault_id,
        "receipt": minimized_receipt,
    }


def _owner_truth_interview_candidate_review_receipt_response(review: Any) -> Dict[str, Any]:
    return {
        "receiptId": review.receipt_id,
        "candidateId": review.candidate_id,
        "decision": review.decision.value,
        "candidateVersion": review.candidate_row_version,
        "correctedValueId": review.corrected_value_id,
    }


def _owner_truth_interview_candidate_batch_decision_response(result: Any) -> Dict[str, Any]:
    return {
        "schemaVersion": "owner-truth-interview-candidate-batch-decision-response-v1",
        "status": result.outcome,
        "batchDecisionId": result.batch_decision_id,
        "reviewBatchId": result.review_batch_id,
        "acceptedCandidateCount": result.accepted_candidate_count,
        "receipts": [
            _owner_truth_interview_candidate_review_receipt_response(item)
            for item in result.candidate_results
        ],
        # This QA-only M0-A route deliberately stops at an immutable receipt.
        "memoryActivation": {
            "status": "notApplicable",
            "memoryVersionCreated": False,
        },
    }


def _owner_truth_interview_candidate_confirmation_batch_decision_response(
    result: Any,
) -> Dict[str, Any]:
    """Return a product receipt without exposing the QA review envelope."""

    return {
        "schemaVersion": (
            "owner-truth-interview-candidate-confirmation-batch-decision-response-v1"
        ),
        "status": result.outcome,
        "batchDecisionId": result.batch_decision_id,
        "reviewBatchId": result.review_batch_id,
        "acceptedCandidateCount": result.accepted_candidate_count,
        "acceptedCandidateIds": [
            review.candidate_id for review in result.candidate_results
        ],
        # M0-A confirmation records receipts only. Memory activation remains a
        # distinct authority transition and is intentionally not reachable here.
        "memoryActivation": {
            "status": "notApplicable",
            "memoryVersionCreated": False,
        },
    }


def _owner_truth_interview_candidate_single_review_response(result: Any) -> Dict[str, Any]:
    return {
        "schemaVersion": "owner-truth-interview-candidate-single-review-response-v1",
        "status": result.outcome,
        "batchDecisionId": result.batch_decision_id,
        "reviewBatchId": result.review_batch_id,
        "receipt": _owner_truth_interview_candidate_review_receipt_response(result.review),
        # This QA-only M0-A route deliberately stops at an immutable receipt.
        "memoryActivation": {
            "status": "notApplicable",
            "memoryVersionCreated": False,
        },
    }
ROUTE_AUTHENTICATION_POLICY = RouteAuthenticationPolicy()
ROUTE_AUTHENTICATION_DECISION_RECORDER = RouteAuthenticationDecisionRecorder()
CLIENT_COMPATIBILITY_MODE = resolve_client_compatibility_mode(
    settings.client_compatibility_mode
)
CLIENT_COMPATIBILITY_POLICY = ClientCompatibilityPolicy(
    registry=ROUTE_AUTHENTICATION_POLICY.registry,
    minimum_client_build=settings.release_policy_min_client_build,
    mode=CLIENT_COMPATIBILITY_MODE,
)
CLIENT_COMPATIBILITY_DECISION_RECORDER = ClientCompatibilityDecisionRecorder()
RECOVERY_ACCESS_POLICY = RecoveryAccessPolicy(
    mode=settings.recovery_access_mode,
    authority_epoch=settings.authority_epoch,
)
RELEASE_POLICY_COMMAND_MODE = (
    settings.release_policy_command_mode
    if settings.release_policy_command_mode in {"observe", "enforce"}
    else "observe"
)
RELEASE_POLICY_SERVICE = ReleasePolicyService(
    policy_revision=settings.release_policy_revision,
    min_client_build=settings.release_policy_min_client_build,
    ttl_seconds=settings.release_policy_ttl_seconds,
    emergency_revision=settings.release_policy_emergency_revision,
    emergency_disabled_features=parse_release_policy_feature_set(
        settings.release_policy_emergency_disabled_features
    ),
    enforced_features=parse_release_policy_feature_set(
        settings.release_policy_enforced_features
    ),
    shadow_mode=RELEASE_POLICY_COMMAND_MODE != "enforce",
)
RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(RELEASE_POLICY_SERVICE)
_release_policy_event_sink = getattr(store, "append_evidence_event", None)
_release_policy_event_summary = getattr(store, "summarize_evidence_events", None)
RELEASE_POLICY_DECISION_RECORDER = ReleasePolicyDecisionRecorder(
    environment=settings.environment,
    event_sink=(
        _release_policy_event_sink
        if callable(_release_policy_event_sink)
        else None
    ),
    event_summary_source=(
        (
            lambda: _release_policy_event_summary(
                operation="releasePolicyDecision",
            )
        )
        if callable(_release_policy_event_summary)
        else None
    ),
    retention_days=settings.evidence_rollout_retention_days,
)
_operation_metric_event_sink = getattr(store, "append_evidence_event", None)
_operation_metric_summary = getattr(store, "summarize_operation_metrics", None)


def _operation_metric_expected_routes() -> set[str]:
    return {
        f"{rule.method} {rule.path_template}"
        for rule in ROUTE_AUTHENTICATION_POLICY.registry.rules
    }


OPERATION_METRIC_RECORDER = OperationMetricRecorder(
    environment=settings.environment,
    build=f"backend-{app.version}",
    event_sink=(
        _operation_metric_event_sink
        if callable(_operation_metric_event_sink)
        else None
    ),
    event_summary_source=(
        (
            lambda: _operation_metric_summary(
                expected_routes=_operation_metric_expected_routes(),
            )
        )
        if callable(_operation_metric_summary)
        else None
    ),
    retention_days=settings.evidence_rollout_retention_days,
    identifier_hmac_key=settings.operations_evidence_hmac_key,
)


def _append_provider_cost_evidence_event(
    event: Dict[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    sink = getattr(store, "append_evidence_event", None)
    if not callable(sink):
        raise RuntimeError("provider cost evidence sink is unavailable")
    return sink(event, **kwargs)


def _list_provider_cost_evidence_events() -> list[Dict[str, Any]]:
    source = getattr(store, "list_evidence_events", None)
    if not callable(source):
        raise RuntimeError("provider cost evidence source is unavailable")
    return source(event_type="providerCost")


PROVIDER_COST_EVIDENCE_RECORDER = ProviderCostEvidenceRecorder(
    environment=settings.environment,
    build=f"backend-{app.version}",
    event_sink=_append_provider_cost_evidence_event,
    event_source=_list_provider_cost_evidence_events,
    retention_days=settings.evidence_rollout_retention_days,
    identifier_hmac_key=settings.operations_evidence_hmac_key,
)


def _evidence_manifest_service() -> EvidenceManifestService:
    """Bind manifest persistence to the active store, including test stores."""
    sink = getattr(store, "append_evidence_event", None)
    source = getattr(store, "list_evidence_events", None)
    return EvidenceManifestService(
        environment=settings.environment,
        build=f"backend-{app.version}",
        event_sink=sink if callable(sink) else None,
        event_source=source if callable(source) else None,
        retention_days=settings.evidence_rollout_retention_days,
    )


INCIDENT_LIFECYCLE_SERVICE = IncidentLifecycleService(
    store=store,
    environment=settings.environment,
    build=f"backend-{app.version}",
    ack_timeout_seconds=settings.incident_ack_timeout_seconds,
)


def _incident_lifecycle_service() -> IncidentLifecycleService:
    # Tests and isolated maintenance scripts replace the module store. Keep
    # incident evidence bound to that active store rather than silently using
    # the process-startup store from a different database.
    if INCIDENT_LIFECYCLE_SERVICE.store is store:
        return INCIDENT_LIFECYCLE_SERVICE
    return IncidentLifecycleService(
        store=store,
        environment=settings.environment,
        build=f"backend-{app.version}",
        ack_timeout_seconds=settings.incident_ack_timeout_seconds,
    )

ARCHIVE_MEDIA_UPLOAD_PROVIDER = "mockObjectStorage"
ARCHIVE_MEDIA_UPLOAD_PROVIDER_DISPLAY_NAME = "Mock Object Storage"
ARCHIVE_MEDIA_UPLOAD_PROVIDER_MODE = "mock"
ARCHIVE_MEDIA_REQUIRES_CLIENT_UPLOAD = False
ARCHIVE_MEDIA_UPLOAD_URL_SCHEME = "mock"
ARCHIVE_MEDIA_REAL_PROVIDER_READY = False
ARCHIVE_MEDIA_PROVIDER_SWITCH_CONTRACT_VERSION = 1
ARCHIVE_MEDIA_CLIENT_UPLOAD_ACTION = "metadataOnly"
ARCHIVE_MEDIA_UPLOAD_TTL_SECONDS = 900
ARCHIVE_AUDIO_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024
ARCHIVE_VIDEO_UPLOAD_LIMIT_BYTES = 200 * 1024 * 1024
ARCHIVE_MEDIA_UPLOAD_LIMITS = {
    "audio": ARCHIVE_AUDIO_UPLOAD_LIMIT_BYTES,
    "video": ARCHIVE_VIDEO_UPLOAD_LIMIT_BYTES,
}
VOICE_CLONE_SAMPLE_STATUSES = {"notProvided", "pending", "ready", "disabled", "deleted", "failed"}
VOICE_CLONE_CONTRACT_VERSION = 2
VOICE_CLONE_PROVIDER_MODE = "mockContract"
VOICE_CLONE_AUTHORIZATION_COPY = (
    "声音克隆必须由用户主动授权，仅使用用户确认提交的声音样本；"
    "未完成授权、样本质量和合规验收前不会公开训练或合成功能。"
)
VOICE_CLONE_DISABLE_CONTRACT = (
    "disableVoiceProfile(profileId:) 应撤销该 voiceProfileId 的合成权限，"
    "当前后端仅保存 mock 禁用状态。"
)
VOICE_CLONE_DELETE_CONTRACT = (
    "deleteVoiceProfile(profileId:) 应删除样本、训练产物和关联授权记录，"
    "当前后端保存 deleted tombstone 以便验收生命周期。"
)
FAMILY_PERSONA_CONTRACT_VERSION = 1
FAMILY_PERSONA_CONTRACT_MODE = "mockFamilyPersona"
DIGITAL_HUMAN_MODE_LABELS = {
    "sunlight": "阳光",
    "star": "星辰",
    "silent": "静默",
}
ACCOUNT_DELETION_RETENTION_DAYS = 30
ACCOUNT_RESTORE_LIMIT = 1
ACCOUNT_DELETION_CONTRACT_VERSION = 1
DIGITAL_HUMAN_SESSION_LEASE_CONTRACT_VERSION = 1
DIGITAL_HUMAN_SESSION_TTL_SECONDS = max(60, settings.tencent_digital_human_session_ttl_seconds)
DIGITAL_HUMAN_SESSION_HEARTBEAT_INTERVAL_SECONDS = max(
    10,
    min(settings.tencent_digital_human_heartbeat_interval_seconds, DIGITAL_HUMAN_SESSION_TTL_SECONDS // 2),
)
DIGITAL_HUMAN_MAX_CONCURRENT_SESSIONS = max(1, settings.tencent_digital_human_max_concurrent_sessions)
SAFETY_POLICY = SafetyPolicy()


def _digital_human_session_lease_response(lease: Dict[str, Any], *, reused: bool) -> Dict[str, Any]:
    session_id = str(lease.get("sessionId") or "")
    return {
        "status": str(lease.get("status") or "active"),
        "reused": reused,
        "createdAt": lease.get("createdAt"),
        "heartbeatAt": lease.get("heartbeatAt"),
        "expiresAt": lease.get("expiresAt"),
        "heartbeatIntervalSeconds": DIGITAL_HUMAN_SESSION_HEARTBEAT_INTERVAL_SECONDS,
        "heartbeatEndpoint": f"/digital-human/sessions/{session_id}/heartbeat",
        "releaseEndpoint": f"/digital-human/sessions/{session_id}/release",
        "contractVersion": DIGITAL_HUMAN_SESSION_LEASE_CONTRACT_VERSION,
    }


def _subject_eligibility_hard_deny(
    capability: HighRiskCapability,
    reason: SubjectEligibilityReason,
) -> None:
    decision = SubjectEligibilityDecision(
        capability=capability,
        allowed=False,
        decision="hardDeny",
        reason=reason,
    )
    raise HTTPException(
        status_code=403,
        detail={
            "code": "subject_eligibility_hard_denied",
            "message": "subject is not eligible for the requested high-risk capability",
            "eligibilityDecision": decision.model_dump(mode="json"),
            "retryable": False,
        },
    )


def _evaluate_subject_eligibility_payload(
    payload: Dict[str, Any],
    capability: HighRiskCapability,
    *,
    required: bool = False,
) -> Optional[SubjectEligibilityDecision]:
    raw_evidence = payload.get("subjectEligibility")
    if raw_evidence is None:
        if required:
            _subject_eligibility_hard_deny(
                capability,
                SubjectEligibilityReason.AGE_VERIFICATION_MISSING,
            )
        return None
    if not isinstance(raw_evidence, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "subject_eligibility_evidence_invalid",
                "message": "subjectEligibility must be an object",
            },
        )
    try:
        evidence = SubjectEligibilityEvidence.model_validate(
            {**raw_evidence, "capability": capability.value}
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "subject_eligibility_evidence_invalid",
                "message": "subject eligibility evidence is incomplete",
            },
        ) from exc
    decision = evaluate_subject_eligibility(evidence)
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "subject_eligibility_hard_denied",
                "message": "subject is not eligible for the requested high-risk capability",
                "eligibilityDecision": decision.model_dump(mode="json"),
                "retryable": False,
            },
        )
    return decision


def _request_bearer_token(request: Request) -> str:
    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _request_backend_api_token(request: Request) -> str:
    return str(request.headers.get("x-dreamjourney-api-token") or "").strip()


def _auth_session_service() -> AuthSessionService:
    return AuthSessionService(
        store,
        access_ttl_seconds=AUTH_ACCESS_TTL_SECONDS,
        refresh_ttl_seconds=AUTH_REFRESH_TTL_SECONDS,
    )


def _identity_binding_service():
    return make_identity_binding_service(
        store,
        settings,
        auth_session_service=_auth_session_service(),
    )


def _tokens_match(left: str, right: str) -> bool:
    return bool(left and right and secrets.compare_digest(left, right))


def _configured_backend_api_token() -> str:
    return BACKEND_API_TOKEN or str(settings.backend_api_token or "")


def _configured_machine_principal() -> RequestPrincipal:
    return RequestPrincipal.machine(
        principal_id="backend-service-v1",
        audience=MACHINE_API_AUDIENCE,
        scopes=MACHINE_SYSTEM_SCOPES,
    )


def _ownership_path_user_id(path: str) -> str:
    patterns = (
        r"^/profile/([^/]+)$",
        r"^/voice/profiles/([^/]+)(?:/|$)",
        r"^/kb/snapshot/([^/]+)$",
        r"^/kb/changes/([^/]+)$",
        r"^/memories/([^/]+)$",
        r"^/archive/items/([^/]+)(?:/|$)",
        r"^/mailbox/letters/([^/]+)(?:/|$)",
        r"^/echo/delayed-replies/([^/]+)/[^/]+/answer$",
        r"^/echo/delayed-replies/([^/]+)$",
        r"^/family/members/([^/]+)(?:/|$)",
        r"^/care/snapshots/(?:latest/)?([^/]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, path)
        if match:
            return match.group(1)
    return ""


async def _ownership_claim_user_ids(request: Request) -> Tuple[set[str], str, Dict[str, Any]]:
    claims = {
        str(request.headers.get("x-dreamjourney-user-id") or "").strip(),
        str(request.query_params.get("userId") or "").strip(),
        str(request.query_params.get("viewerUserId") or "").strip(),
        _ownership_path_user_id(request.url.path),
    }
    payload_context = await _request_json_payload(request)
    claims.update(owner_authority_claims(payload_context))
    return {claim for claim in claims if claim}, "inspected", payload_context


async def _request_json_payload(request: Request) -> Dict[str, Any]:
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return {}
    try:
        payload = json.loads((await request.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _ownership_log_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _request_user_principal_id(request: Request) -> Optional[str]:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, RequestPrincipal) or principal.kind != PrincipalKind.USER:
        return None
    user_id = str(principal.principal_id or "").strip()
    return user_id or None


def _principal_owned_payload(
    request: Request,
    payload: Dict[str, Any],
    *,
    aliases: Tuple[str, ...] = (),
) -> Tuple[str, Dict[str, Any]]:
    principal_user_id = _request_user_principal_id(request)
    if principal_user_id is not None:
        conflicting_claims = owner_authority_claims(payload) - {principal_user_id}
        if conflicting_claims:
            raise HTTPException(status_code=403, detail="owner claim does not match authenticated user")
    elif AUTH_ROUTE_MODE == "enforce" or bool(_configured_backend_api_token()):
        raise HTTPException(status_code=401, detail="authenticated user is required")
    user_id = principal_user_id or str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    canonical = dict(payload)
    canonical["userId"] = user_id
    for alias in aliases:
        canonical[alias] = user_id
    return user_id, canonical


def _principal_path_owner(request: Request, asserted_user_id: str) -> str:
    principal_user_id = _request_user_principal_id(request)
    if principal_user_id is not None:
        if asserted_user_id != principal_user_id:
            raise HTTPException(status_code=403, detail="path owner does not match authenticated user")
        return principal_user_id
    if AUTH_ROUTE_MODE == "enforce" or bool(_configured_backend_api_token()):
        raise HTTPException(status_code=401, detail="authenticated user is required")
    return asserted_user_id


def _require_user_principal_identity(request: Request, expected_user_id: str, detail: str) -> None:
    principal_user_id = _request_user_principal_id(request)
    if principal_user_id is not None and principal_user_id != expected_user_id:
        raise HTTPException(status_code=403, detail=detail)


def _authorization_fallback_headers(ownership_decision: str) -> Dict[str, str]:
    if ownership_decision == "match":
        decision = "allowClaimMatch"
        reason = "principalClaimsMatch"
    elif ownership_decision == "mismatch":
        decision = "denyClaimMismatch"
        reason = "principalClaimsMismatch"
    elif ownership_decision == "unclaimed":
        decision = "observeUnclaimed"
        reason = "noOwnershipClaim"
    else:
        decision = "observeUninspected"
        reason = "requestBodyNotInspected"
    return {
        "policy": "ownershipFallback",
        "decision": decision,
        "reason": reason,
    }


def _set_auth_diagnostic_headers(
    response: Any,
    *,
    principal_kind: str,
    ownership_decision: str,
    authorization_headers: Dict[str, str],
    route_authentication_headers: Dict[str, str],
) -> Any:
    response.headers["X-DreamJourney-Auth-Principal"] = principal_kind
    response.headers["X-DreamJourney-Route-Auth-Mode"] = AUTH_ROUTE_MODE
    response.headers["X-DreamJourney-Route-Auth-Policy"] = route_authentication_headers["policy"]
    response.headers["X-DreamJourney-Route-Auth-Decision"] = route_authentication_headers["decision"]
    response.headers["X-DreamJourney-Route-Auth-Reason"] = route_authentication_headers["reason"]
    response.headers["X-DreamJourney-Ownership-Mode"] = AUTH_OWNERSHIP_MODE
    response.headers["X-DreamJourney-Ownership-Decision"] = ownership_decision
    response.headers["X-DreamJourney-Authorization-Policy"] = authorization_headers["policy"]
    response.headers["X-DreamJourney-Authorization-Decision"] = authorization_headers["decision"]
    response.headers["X-DreamJourney-Authorization-Reason"] = authorization_headers["reason"]
    if authorization_headers.get("grantId"):
        response.headers["X-DreamJourney-Authorization-Grant-Id"] = authorization_headers["grantId"]
    if authorization_headers.get("grantReceiptId"):
        response.headers["X-DreamJourney-Authorization-Grant-Receipt-Id"] = authorization_headers[
            "grantReceiptId"
        ]
    return response


def _request_client_build_header(request: Request) -> Optional[str]:
    value = request.headers.get("x-dreamjourney-client-build")
    return None if value is None else str(value)


def _set_client_compatibility_diagnostic_headers(
    response: Any,
    decision: ClientCompatibilityDecision,
) -> Any:
    values = decision.header_values()
    response.headers["X-DreamJourney-Client-Compatibility-Mode"] = values["mode"]
    response.headers["X-DreamJourney-Client-Compatibility-Decision"] = values[
        "decision"
    ]
    response.headers["X-DreamJourney-Client-Compatibility-Reason"] = values[
        "reason"
    ]
    response.headers["X-DreamJourney-Minimum-Client-Build"] = values[
        "minimumClientBuild"
    ]
    return response


def _upgrade_required_response(
    *,
    reason: str,
    minimum_client_build: int,
    extra_detail: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    detail: Dict[str, Any] = {
        "code": "upgrade_required",
        "reason": reason,
        "retryable": False,
        "reauthenticationRequired": False,
        "minimumClientBuild": max(1, int(minimum_client_build)),
        "accessMode": "readOnly",
    }
    detail.update(extra_detail or {})
    CLIENT_COMPATIBILITY_DECISION_RECORDER.record_upgrade_required_response()
    return JSONResponse(
        status_code=426,
        content={"detail": detail},
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _legacy_identity_upgrade_response(request: Request) -> JSONResponse:
    decision = CLIENT_COMPATIBILITY_POLICY.evaluate_legacy_identity_retirement(
        method=request.method,
        path=request.url.path,
        client_build_header=_request_client_build_header(request),
    )
    CLIENT_COMPATIBILITY_DECISION_RECORDER.record(decision)
    return _set_client_compatibility_diagnostic_headers(
        _upgrade_required_response(
            reason=decision.reason,
            minimum_client_build=decision.minimum_client_build,
        ),
        decision,
    )


def _release_policy_int_header(request: Request, name: str) -> Optional[int]:
    value = str(request.headers.get(name) or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _release_policy_bool_header(request: Request, name: str) -> Optional[bool]:
    value = str(request.headers.get(name) or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _release_policy_audience(request: Request, principal: RequestPrincipal) -> str:
    value = str(request.headers.get("x-dreamjourney-policy-audience") or "owner")
    return normalize_release_policy_audience(
        value,
        environment=settings.environment,
        principal_kind=str(principal.get("kind") or "anonymous"),
    )


def _set_release_policy_diagnostic_headers(response: Any, diagnostic: Dict[str, str]) -> Any:
    if not diagnostic:
        return response
    response.headers["X-DreamJourney-Release-Policy-Mode"] = diagnostic.get(
        "mode",
        RELEASE_POLICY_COMMAND_MODE,
    )
    response.headers["X-DreamJourney-Release-Policy-Feature"] = diagnostic.get("feature", "none")
    response.headers["X-DreamJourney-Release-Policy-Decision"] = diagnostic.get("decision", "notApplicable")
    response.headers["X-DreamJourney-Release-Policy-Decision-Id"] = diagnostic.get(
        "decisionId",
        "none",
    )
    response.headers["X-DreamJourney-Release-Policy-Reason"] = diagnostic.get("reason", "notApplicable")
    response.headers["X-DreamJourney-Release-Policy-Revision"] = diagnostic.get("policyRevision", "0")
    return response


def _release_policy_denied_response(error: ReleasePolicyFeatureAccessDenied) -> JSONResponse:
    client_upgrade_required = error.reason == "clientBelowMinimum"
    if client_upgrade_required:
        response = _upgrade_required_response(
            reason=error.reason,
            minimum_client_build=RELEASE_POLICY_SERVICE.min_client_build,
            extra_detail={
                "feature": error.feature,
                "policyRevision": error.policy_revision,
            },
        )
    else:
        response = JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "code": "release_policy_denied",
                    "feature": error.feature,
                    "reason": error.reason,
                    "policyRevision": error.policy_revision,
                    "minimumClientBuild": RELEASE_POLICY_SERVICE.min_client_build,
                    "accessMode": RELEASE_POLICY_SERVICE.minimum_client_access_mode(
                        error.feature
                    ),
                    "retryable": False,
                }
            },
        )
    return _set_release_policy_diagnostic_headers(
        response,
        {
            "feature": error.feature,
            "decision": "deny",
            "reason": error.reason,
            "policyRevision": str(error.policy_revision),
        },
    )


def _release_policy_account_generation(principal: RequestPrincipal) -> str:
    if principal.kind == PrincipalKind.MACHINE:
        return "machine"
    source = str(
        principal.get("sessionId")
        or principal.get("userId")
        or "anonymous"
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _evaluate_release_policy_command(
    request: Request,
    payload: Dict[str, Any],
    principal: RequestPrincipal,
) -> Tuple[Optional[JSONResponse], Dict[str, str]]:
    feature = RELEASE_POLICY_COMMAND_GATE.feature_for_request(
        request.method,
        request.url.path,
        payload,
    )
    if feature is None:
        return None, {}
    incident_block = _incident_lifecycle_service().release_policy_block(feature)
    if incident_block is not None:
        route_label = RELEASE_POLICY_COMMAND_GATE.route_label_for_request(
            request.method,
            request.url.path,
            payload,
        )
        observed_client_build = _release_policy_int_header(
            request,
            "x-dreamjourney-client-build",
        ) or 1
        RELEASE_POLICY_DECISION_RECORDER.record(
            feature=feature,
            policy_version=RELEASE_POLICY_SERVICE.POLICY_VERSION,
            client_build=observed_client_build,
            decision="deny",
            reason=str(incident_block["reason"]),
            route=route_label,
        )
        response = JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "incident_stop_the_line",
                    "feature": feature,
                    "reason": str(incident_block["reason"]),
                    "retryable": False,
                }
            },
        )
        response.headers["X-DreamJourney-Incident-Stop-Line"] = "true"
        response.headers["X-DreamJourney-Incident-Reason"] = str(
            incident_block["reason"]
        )
        return response, {}
    release_stage = RELEASE_POLICY_SERVICE.release_stage_for(feature)
    system_default_closed_bypass = (
        principal.kind == PrincipalKind.MACHINE
        and release_stage in {"M1", "M2", "M3", "M4"}
    )
    mode = (
        "observe"
        if system_default_closed_bypass
        else (
            "enforce"
            if RELEASE_POLICY_COMMAND_MODE == "enforce"
            else RELEASE_POLICY_SERVICE.command_mode_for(feature)
        )
    )
    observed_client_build = _release_policy_int_header(
        request,
        "x-dreamjourney-client-build",
    ) or 1
    read_only_request = request.method.upper() in {"GET", "HEAD"}
    evaluation_client_build = (
        max(RELEASE_POLICY_SERVICE.min_client_build, observed_client_build)
        if read_only_request
        else observed_client_build
    )
    route_label = RELEASE_POLICY_COMMAND_GATE.route_label_for_request(
        request.method,
        request.url.path,
        payload,
    )

    try:
        captured = RELEASE_POLICY_COMMAND_GATE.capture(
            feature=feature,
            audience=_release_policy_audience(request, principal),
            cohort=str(
                request.headers.get("x-dreamjourney-policy-cohort")
                or "closedPilotAdultSelf"
            ).strip(),
            client_build=evaluation_client_build,
            client_policy_version=str(
                request.headers.get("x-dreamjourney-policy-version") or ""
            ).strip() or None,
            client_policy_revision=_release_policy_int_header(
                request,
                "x-dreamjourney-policy-revision",
            ),
            client_account_generation=str(
                request.headers.get("x-dreamjourney-account-generation") or ""
            ).strip() or None,
            client_allowed=_release_policy_bool_header(
                request,
                "x-dreamjourney-feature-allowed",
            ),
            client_decision_id=str(
                request.headers.get("x-dreamjourney-feature-decision-id") or ""
            ).strip() or None,
            client_feature=str(
                request.headers.get("x-dreamjourney-feature") or ""
            ).strip() or None,
            expected_account_generation=_release_policy_account_generation(principal),
            require_client_capture=(
                principal.kind != PrincipalKind.MACHINE and not read_only_request
            ),
        )
        RELEASE_POLICY_COMMAND_GATE.revalidate_effect(captured)
        RELEASE_POLICY_DECISION_RECORDER.record(
            feature=feature,
            policy_version=captured.policy_version,
            client_build=observed_client_build,
            decision="allow",
            reason=captured.server_reason,
            route=route_label,
        )
        return None, {
            "feature": feature,
            "decision": "allow",
            "decisionId": captured.decision_id,
            "reason": captured.server_reason,
            "policyRevision": str(captured.policy_revision),
            "mode": mode,
        }
    except ReleasePolicyFeatureAccessDenied as error:
        logger.warning(
            "release_policy_command_denied mode=%s feature=%s reason=%s revision=%s",
            mode,
            error.feature,
            error.reason,
            error.policy_revision,
        )
        decision = "deny" if mode == "enforce" else "observeDeny"
        RELEASE_POLICY_DECISION_RECORDER.record(
            feature=error.feature,
            policy_version=RELEASE_POLICY_SERVICE.POLICY_VERSION,
            client_build=observed_client_build,
            decision=decision,
            reason=error.reason,
            route=route_label,
        )
        if mode == "enforce":
            response = _release_policy_denied_response(error)
            response.headers["X-DreamJourney-Release-Policy-Mode"] = mode
            return response, {}
        return None, {
            "feature": error.feature,
            "decision": decision,
            "reason": error.reason,
            "policyRevision": str(error.policy_revision),
            "mode": mode,
        }


NO_STORE_PATH_PREFIXES = (
    "/auth/",
    "/v2/auth/",
    "/v2/vaults/",
    "/voice/",
    "/digital-human/",
    "/ops/incidents",
)
NO_STORE_EXACT_PATHS = {
    "/health",
    "/live",
    "/ready",
    "/config/runtime",
    "/v2/release-policy",
    "/ops/release-policy/observations",
    "/ops/evidence-manifests",
    "/tts",
    "/archive/image-analysis",
}
INFRASTRUCTURE_PATHS = frozenset({"/health", "/live", "/ready"})
DATABASE_TRANSACTION_BYPASS_PATHS = INFRASTRUCTURE_PATHS | frozenset({"/config/runtime"})
ANONYMOUS_AUTH_PATHS = {
    "/auth/login",
    "/auth/refresh",
    "/v2/auth/challenges",
    "/config/runtime",
    "/v2/release-policy",
}
ANONYMOUS_AUTH_PATH_PATTERNS = (
    re.compile(r"^/v2/auth/challenges/[^/]+/verify$"),
)


def _requires_no_store(path: str) -> bool:
    return path in NO_STORE_EXACT_PATHS or path.startswith(NO_STORE_PATH_PREFIXES)


def _allows_anonymous_auth(path: str) -> bool:
    return path in ANONYMOUS_AUTH_PATHS or any(
        pattern.fullmatch(path) is not None
        for pattern in ANONYMOUS_AUTH_PATH_PATTERNS
    )


def _set_no_store_headers(response: Any) -> Any:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Vary"] = "Authorization, X-DreamJourney-Api-Token"
    return response


def _operation_metric_attempt(request: Request) -> int:
    raw = str(request.headers.get("x-dreamjourney-operation-attempt") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return value if 1 <= value <= 1000 else 1


def _operation_metric_feedback_state(request: Request) -> str:
    value = str(request.headers.get("x-dreamjourney-feedback-state") or "").strip()
    return value if value in {"received", "missing", "notApplicable"} else "notApplicable"


def _operation_metric_client_identifier(value: Optional[str]) -> Optional[str]:
    """Accept only opaque UUIDv4 values from callers; never hash arbitrary PII."""

    try:
        identifier = UUID(str(value or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return identifier.hex if identifier.version == 4 else None


def _operation_metric_outcome_for_status(
    status_code: int,
    feedback_state: str,
) -> str:
    if status_code == 499:
        return "cancelled"
    if status_code in {408, 504}:
        return "timedOut"
    if 200 <= status_code < 400:
        if feedback_state == "missing":
            return "feedbackMissing"
        return "succeeded"
    return "failed"


def _record_operation_metric_attempt(
    *,
    route: str,
    operation: str,
    request_key: str,
    operation_key: str,
    attempt: int,
    feedback_state: str,
    elapsed_ms: int,
    outcome: str,
    http_status: Optional[int],
    correlation_key: Optional[str],
) -> None:
    # Metrics are shadow-only. The recorder contains its own sink failure guard.
    OPERATION_METRIC_RECORDER.record_attempt(
        request_key=request_key,
        operation_key=operation_key,
        attempt=attempt,
        route=route,
        operation=operation,
        outcome=outcome,
        feedback_state=feedback_state,
        latency_ms=max(0, elapsed_ms),
        http_status=http_status,
        correlation_key=correlation_key,
    )


def _provider_cost_identifier(request: Request, header: str) -> str:
    return (
        _operation_metric_client_identifier(request.headers.get(header))
        or secrets.token_hex(16)
    )


def _record_provider_cost_attempt(
    request: Request,
    *,
    provider: str,
    capability: str,
    unit_type: str,
    units: int,
    state: str,
    reason: str,
    started_at: float,
    provider_request_key: Optional[str] = None,
) -> None:
    """Persist only value-free usage evidence; never alter a provider response."""

    principal = getattr(request.state, "auth_principal", None)
    principal_key = (
        str(principal.principal_id or "")
        if isinstance(principal, RequestPrincipal) and principal.kind == PrincipalKind.USER
        else None
    )
    request_key = _provider_cost_identifier(request, "x-dreamjourney-request-id")
    operation_key = _provider_cost_identifier(request, "x-dreamjourney-operation-id")
    correlation_value = _operation_metric_client_identifier(
        request.headers.get("x-dreamjourney-correlation-id")
    )
    try:
        PROVIDER_COST_EVIDENCE_RECORDER.record_attempt(
            request_key=request_key,
            operation_key=operation_key,
            provider=provider,
            capability=capability,
            unit_type=unit_type,
            units=max(0, int(units)),
            state=state,
            reason=reason,
            principal_key=principal_key,
            provider_request_key=provider_request_key,
            correlation_key=correlation_value,
            latency_ms=max(0, int((time.monotonic() - started_at) * 1000)),
        )
    except Exception:
        # Operational evidence is strictly best effort. A malformed or
        # unavailable evidence sink must never change a provider response.
        return


def _recovery_access_denied_response(request: Request) -> Optional[JSONResponse]:
    decision = RECOVERY_ACCESS_POLICY.evaluate(
        method=str(getattr(request, "method", "GET")),
        path=request.url.path,
    )
    if decision.allowed:
        return None
    response = JSONResponse(
        status_code=503,
        content={
            "detail": "service recovery maintenance",
            "code": decision.code,
            "recovery": RECOVERY_ACCESS_POLICY.public_descriptor(),
        },
    )
    response.headers["X-DreamJourney-Recovery-Mode"] = RECOVERY_ACCESS_POLICY.mode
    response.headers["X-DreamJourney-Authority-Epoch"] = RECOVERY_ACCESS_POLICY.authority_epoch
    return _set_no_store_headers(response)


@app.middleware("http")
async def require_backend_api_token(request: Request, call_next):
    recovery_response = _recovery_access_denied_response(request)
    if recovery_response is not None:
        return recovery_response

    bearer_token = _request_bearer_token(request)
    backend_header_token = _request_backend_api_token(request)
    configured_backend_token = _configured_backend_api_token()
    principal = RequestPrincipal.anonymous()

    if bearer_token and _tokens_match(bearer_token, configured_backend_token):
        principal = _configured_machine_principal()
    elif bearer_token:
        session = _auth_session_service().resolve_access_token(bearer_token)
        if session is None:
            return JSONResponse(status_code=401, content={"detail": "invalid or expired access token"})
        try:
            principal = RequestPrincipal.user(
                principal_id=str(session.get("userId") or ""),
                session_id=str(session.get("sessionId") or ""),
                token_family_id=str(session.get("tokenFamilyId") or ""),
                session_version=int(session.get("sessionVersion") or 0),
            )
        except (TypeError, ValueError):
            logger.error("auth_session_principal_contract_invalid")
            return JSONResponse(status_code=401, content={"detail": "invalid access token session"})
    elif backend_header_token:
        if not _tokens_match(backend_header_token, configured_backend_token):
            return JSONResponse(status_code=401, content={"detail": "invalid backend api token"})
        principal = _configured_machine_principal()

    request.state.auth_principal = principal
    try:
        route_authentication_decision = ROUTE_AUTHENTICATION_POLICY.evaluate(
            method=request.method,
            path=request.url.path,
            principal=principal,
        )
    except Exception:
        logger.exception("route_authentication_policy_evaluation_failed method=%s", request.method)
        route_authentication_decision = RouteAuthenticationDecision(
            policy_id="routeAuthentication",
            decision="deny",
            reason="policyEvaluationFailed",
            allowed=False,
            route_label=f"{request.method.upper()} {request.url.path}",
            principal_kind=principal.kind,
        )
    ROUTE_AUTHENTICATION_DECISION_RECORDER.record(route_authentication_decision)
    route_authentication_headers = route_authentication_decision.header_values()
    route_deny_is_terminal = (
        route_authentication_decision.reason == "policyEvaluationFailed"
        or route_authentication_decision.reason == "machinePrincipalRequired"
        or (
            principal.kind == PrincipalKind.MACHINE
            and route_authentication_decision.reason == "userPrincipalRequired"
        )
        or (
            principal.kind == PrincipalKind.ANONYMOUS
            and bool(configured_backend_token)
        )
    )
    if (
        not route_authentication_decision.allowed
        and AUTH_ROUTE_MODE != "enforce"
        and not route_deny_is_terminal
    ):
        route_authentication_headers["decision"] = "observeDeny"
    if (
        not route_authentication_decision.allowed
        and (AUTH_ROUTE_MODE == "enforce" or route_deny_is_terminal)
    ):
        if route_authentication_decision.reason == "policyEvaluationFailed":
            status_code = 503
        elif principal.kind == PrincipalKind.ANONYMOUS:
            status_code = 401
        elif route_authentication_decision.reason == "routeNotClassified":
            status_code = 503
        else:
            status_code = 403
        compatibility_reason = (
            "systemPrincipalRequired"
            if route_authentication_decision.reason == "machinePrincipalRequired"
            else route_authentication_decision.reason
        )
        return _set_auth_diagnostic_headers(
            JSONResponse(
                status_code=status_code,
                content={
                    "detail": {
                        "code": "route_authentication_denied",
                        "reason": route_authentication_decision.reason,
                    }
                },
            ),
            principal_kind=principal.kind.value,
            ownership_decision="notEvaluated",
            authorization_headers={
                "policy": route_authentication_decision.policy_id,
                "decision": "deny",
                "reason": compatibility_reason,
            },
            route_authentication_headers=route_authentication_headers,
        )

    if (
        not AUTH_LEGACY_PHONE_LOGIN_ENABLED
        and request.method.upper() == "POST"
        and request.url.path in {"/auth/login", "/auth/restore"}
    ):
        legacy_decision = (
            CLIENT_COMPATIBILITY_POLICY.evaluate_legacy_identity_retirement(
                method=request.method,
                path=request.url.path,
                client_build_header=_request_client_build_header(request),
            )
        )
        CLIENT_COMPATIBILITY_DECISION_RECORDER.record(legacy_decision)
        response = _set_auth_diagnostic_headers(
            _upgrade_required_response(
                reason=legacy_decision.reason,
                minimum_client_build=legacy_decision.minimum_client_build,
            ),
            principal_kind=principal.kind.value,
            ownership_decision="notEvaluated",
            authorization_headers={
                "policy": "clientCompatibility",
                "decision": "deny",
                "reason": legacy_decision.reason,
            },
            route_authentication_headers=route_authentication_headers,
        )
        return _set_client_compatibility_diagnostic_headers(
            response,
            legacy_decision,
        )

    client_compatibility_decision = CLIENT_COMPATIBILITY_POLICY.evaluate(
        method=request.method,
        path=request.url.path,
        client_build_header=_request_client_build_header(request),
    )
    CLIENT_COMPATIBILITY_DECISION_RECORDER.record(client_compatibility_decision)
    if client_compatibility_decision.blocked:
        response = _set_auth_diagnostic_headers(
            _upgrade_required_response(
                reason=client_compatibility_decision.reason,
                minimum_client_build=(
                    client_compatibility_decision.minimum_client_build
                ),
            ),
            principal_kind=principal.kind.value,
            ownership_decision="notEvaluated",
            authorization_headers={
                "policy": "clientCompatibility",
                "decision": "deny",
                "reason": client_compatibility_decision.reason,
            },
            route_authentication_headers=route_authentication_headers,
        )
        return _set_client_compatibility_diagnostic_headers(
            response,
            client_compatibility_decision,
        )

    ownership_decision = principal.kind.value
    authorization_headers = {
        "policy": principal.kind.value,
        "decision": "allowMachine" if principal.kind == PrincipalKind.MACHINE else "observeAnonymous",
        "reason": "machinePrincipal" if principal.kind == PrincipalKind.MACHINE else "noCredential",
    }
    payload_context: Dict[str, Any] = {}
    if principal.kind == PrincipalKind.USER:
        claims, inspection_decision, payload_context = await _ownership_claim_user_ids(request)
        principal_user_id = str(principal.principal_id or "")
        if inspection_decision != "inspected":
            ownership_decision = inspection_decision
        else:
            ownership_decision = "unclaimed" if not claims else (
                "match" if claims == {principal_user_id} else "mismatch"
            )
        try:
            policy_decision = CrossAccountAuthorizationPolicy(store).evaluate(
                method=request.method,
                path=request.url.path,
                principal_user_id=principal_user_id,
                query=dict(request.query_params),
                payload=payload_context,
            )
        except Exception:  # pragma: no cover - defensive fallback for external stores
            logger.exception("authorization_policy_evaluation_failed method=%s", request.method)
            policy_decision = None

        should_block = False
        if policy_decision is not None and policy_decision.decision != "fallback":
            authorization_headers = policy_decision.header_values()
            if policy_decision.allowed is True:
                ownership_decision = "delegated" if policy_decision.delegated else "match"
            elif policy_decision.allowed is False:
                ownership_decision = "mismatch"
                should_block = policy_decision.terminal
        else:
            authorization_headers = _authorization_fallback_headers(ownership_decision)
            should_block = ownership_decision == "mismatch"

        if authorization_headers["decision"] in {"deny", "denyClaimMismatch"}:
            logger.warning(
                "authorization_denied mode=%s policy=%s reason=%s principal=%s claims=%s method=%s",
                AUTH_OWNERSHIP_MODE,
                authorization_headers["policy"],
                authorization_headers["reason"],
                _ownership_log_hash(principal_user_id),
                sorted(_ownership_log_hash(claim) for claim in claims),
                request.method,
            )
        principal_bound = bool(policy_decision is not None and policy_decision.principal_bound)
        if should_block and (principal_bound or AUTH_OWNERSHIP_MODE == "enforce"):
            return _set_client_compatibility_diagnostic_headers(
                _set_auth_diagnostic_headers(
                    JSONResponse(
                        status_code=403,
                        content={"detail": "authorization denied"},
                    ),
                    principal_kind="user",
                    ownership_decision=ownership_decision,
                    authorization_headers=authorization_headers,
                    route_authentication_headers=route_authentication_headers,
                ),
                client_compatibility_decision,
            )

    if principal.kind != PrincipalKind.USER:
        payload_context = await _request_json_payload(request)

    if request.url.path == "/config/runtime":
        RELEASE_POLICY_DECISION_RECORDER.record_runtime_contract(
            client_build=_release_policy_int_header(
                request,
                "x-dreamjourney-client-build",
            ) or 0,
            contract_version=_release_policy_int_header(
                request,
                "x-dreamjourney-runtime-contract-version",
            ) or 0,
        )

    release_policy_response, release_policy_diagnostic = _evaluate_release_policy_command(
        request,
        payload_context,
        principal,
    )
    if release_policy_response is not None:
        return _set_client_compatibility_diagnostic_headers(
            _set_auth_diagnostic_headers(
                release_policy_response,
                principal_kind=principal.kind.value,
                ownership_decision=ownership_decision,
                authorization_headers=authorization_headers,
                route_authentication_headers=route_authentication_headers,
            ),
            client_compatibility_decision,
        )

    response = await call_next(request)
    response = _set_release_policy_diagnostic_headers(response, release_policy_diagnostic)
    return _set_client_compatibility_diagnostic_headers(
        _set_auth_diagnostic_headers(
            response,
            principal_kind=principal.kind.value,
            ownership_decision=ownership_decision,
            authorization_headers=authorization_headers,
            route_authentication_headers=route_authentication_headers,
        ),
        client_compatibility_decision,
    )


@app.middleware("http")
async def prevent_sensitive_response_caching(request: Request, call_next):
    response = await call_next(request)
    if _requires_no_store(request.url.path):
        return _set_no_store_headers(response)
    return response


@app.middleware("http")
async def database_request_unit_of_work(request: Request, call_next):
    recovery_response = _recovery_access_denied_response(request)
    if recovery_response is not None:
        return recovery_response
    unit_of_work_factory = getattr(store, "request_unit_of_work", None)
    if request.url.path in DATABASE_TRANSACTION_BYPASS_PATHS or not callable(unit_of_work_factory):
        return await call_next(request)

    correlation_id = secrets.token_hex(16)
    command_id = secrets.token_hex(16)
    try:
        with unit_of_work_factory(
            correlation_id=correlation_id,
            command_id=command_id,
        ) as unit_of_work:
            response = await call_next(request)
            request_state = getattr(request, "state", None)
            commit_security_attempt = bool(
                getattr(request_state, "commit_security_attempt", False)
            )
            if (
                int(getattr(response, "status_code", 500)) >= 400
                and not commit_security_attempt
            ):
                unit_of_work.mark_rollback("httpErrorResponse")
    except ConnectionPoolExhausted:
        logger.error("database_pool_exhausted correlation=%s", correlation_id)
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "database_pool_exhausted",
                    "message": "database capacity is temporarily unavailable",
                }
            },
            headers={
                "Cache-Control": "no-store",
                "Retry-After": "1",
                "X-DreamJourney-Correlation-Id": correlation_id,
            },
        )
    response.headers["X-DreamJourney-Correlation-Id"] = correlation_id
    return response


@app.middleware("http")
async def serve_head_as_read_only_get(request: Request, call_next):
    if request.method.upper() == "HEAD":
        request.scope["method"] = "GET"
    return await call_next(request)


@app.middleware("http")
async def shadow_operation_metric_attempt(request: Request, call_next):
    method = "GET" if request.method.upper() == "HEAD" else request.method.upper()
    route_match = ROUTE_AUTHENTICATION_POLICY.registry.match(method, request.url.path)
    if route_match is None:
        return await call_next(request)

    route = f"{route_match.rule.method} {route_match.rule.path_template}"
    operation = route_match.rule.policy_id
    request_key = _operation_metric_client_identifier(
        request.headers.get("x-dreamjourney-request-id")
    ) or secrets.token_hex(16)
    operation_key = _operation_metric_client_identifier(
        request.headers.get("x-dreamjourney-operation-id")
    ) or request_key
    attempt = _operation_metric_attempt(request)
    feedback_state = _operation_metric_feedback_state(request)
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except asyncio.CancelledError:
        _record_operation_metric_attempt(
            route=route,
            operation=operation,
            request_key=request_key,
            operation_key=operation_key,
            attempt=attempt,
            feedback_state=feedback_state,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            outcome="cancelled",
            http_status=None,
            correlation_key=None,
        )
        raise
    except Exception:
        _record_operation_metric_attempt(
            route=route,
            operation=operation,
            request_key=request_key,
            operation_key=operation_key,
            attempt=attempt,
            feedback_state=feedback_state,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            outcome="failed",
            http_status=500,
            correlation_key=None,
        )
        raise

    _record_operation_metric_attempt(
        route=route,
        operation=operation,
        request_key=request_key,
        operation_key=operation_key,
        attempt=attempt,
        feedback_state=feedback_state,
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        outcome=_operation_metric_outcome_for_status(
            int(response.status_code),
            feedback_state,
        ),
        http_status=int(response.status_code),
        correlation_key=str(response.headers.get("X-DreamJourney-Correlation-Id") or "") or None,
    )
    return response


@app.on_event("startup")
def startup() -> None:
    validate_route_authentication_startup(
        app,
        registry=ROUTE_AUTHENTICATION_POLICY.registry,
        environment=settings.environment,
        enforcement_mode=AUTH_ROUTE_MODE,
        machine_credential_configured=bool(_configured_backend_api_token()),
    )
    init_store(store)


@app.on_event("shutdown")
def shutdown() -> None:
    close_store(store)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "store": settings.store_backend,
        "deprecated": True,
        "livenessEndpoint": "/live",
        "readinessEndpoint": "/ready",
    }


@app.get("/live")
def live() -> Dict[str, str]:
    return liveness_payload()


@app.get("/ready")
def ready() -> JSONResponse:
    incident_component_source = (
        _incident_lifecycle_service().readiness_component
        if callable(getattr(store, "list_evidence_events", None))
        else None
    )
    payload = ReadinessService(
        settings=settings,
        store=store,
        incident_component_source=incident_component_source,
    ).evaluate()
    return JSONResponse(
        status_code=200 if payload["status"] == "ready" else 503,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/v2/release-policy", response_model=ReleasePolicySnapshot)
def release_policy(
    audience: str = Query(default="owner", pattern="^(owner|family|visitor|qa)$"),
    cohort: str = Query(default="closedPilotAdultSelf", min_length=1, max_length=80),
    clientBuild: int = Query(default=1, ge=0),
    knownPolicyRevision: int = Query(default=0, ge=0),
    feature: Optional[str] = Query(default=None, min_length=1, max_length=100),
) -> ReleasePolicySnapshot:
    try:
        return RELEASE_POLICY_SERVICE.build_snapshot(
            audience=audience,  # type: ignore[arg-type]
            cohort=cohort,
            client_build=clientBuild,
            known_policy_revision=knownPolicyRevision,
            requested_feature=feature,
        )
    except ReleasePolicyVersionDowngrade as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "release_policy_version_downgrade",
                "message": "server release policy revision is older than the client snapshot",
                "serverPolicyRevision": error.server_revision,
                "knownPolicyRevision": error.known_revision,
            },
        ) from error


@app.get(
    "/v2/vaults/{vault_id}/candidates",
    include_in_schema=False,
)
def owner_truth_candidate_inbox(
    request: Request,
    vault_id: str,
) -> Dict[str, Any]:
    """QA-only Owner Candidate Inbox; never a public M0 read surface."""

    try:
        context = _owner_truth_candidate_review_context(request, vault_id=vault_id)
        items = OwnerTruthCandidateReviewService(store).list_pending(context=context)
    except OwnerTruthContractError as error:
        raise _owner_truth_candidate_review_http_error(error) from error
    return {
        "schemaVersion": "owner-truth-candidate-inbox-v1",
        "vaultId": context.vault_id,
        "candidates": [
            _owner_truth_candidate_inbox_item_response(item) for item in items
        ],
    }


@app.post(
    "/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions",
    include_in_schema=False,
)
def review_owner_truth_candidate(
    request: Request,
    vault_id: str,
    candidate_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """QA-only review plus receipt-derived initial MemoryVersion activation."""

    try:
        context = _owner_truth_candidate_review_context(request, vault_id=vault_id)
        command = OwnerTruthCandidateReviewCommand(
            command_id=str(payload.get("commandId") or ""),
            candidate_id=candidate_id,
            expected_candidate_version=_owner_truth_candidate_expected_version(payload),
            action=str(payload.get("action") or ""),
            corrected_value=payload.get("correctedValue"),
            corrected_value_schema_version=str(
                payload.get("correctedValueSchemaVersion")
                or OWNER_TRUTH_SCHEMA_VERSION
            ),
            reason_code=str(payload.get("reasonCode") or "ownerReviewed"),
        )
        result = OwnerTruthCandidateReviewService(store).decide_and_activate(
            command=command,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_candidate_review_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.review.outcome == "created" else 200,
        content=_owner_truth_candidate_decision_response(result),
    )


@app.get(
    "/v2/vaults/{vault_id}/interview-sessions/{session_id}/state",
    include_in_schema=False,
)
def read_owner_truth_interview_session_state(
    request: Request,
    vault_id: str,
    session_id: str,
) -> JSONResponse:
    """Value-minimized natural-input session state; it exposes no content."""

    try:
        context = _owner_truth_interview_natural_input_context(request, vault_id=vault_id)
        snapshot = OwnerTruthInterviewSessionReadService(store).read(
            session_id=session_id,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_session_state_http_error(error) from error
    return JSONResponse(
        status_code=200,
        content=_owner_truth_interview_session_state_read_response(
            vault_id=context.vault_id,
            snapshot=snapshot,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.get(
    "/v2/vaults/{vault_id}/interview-sessions/{session_id}/presentation",
    include_in_schema=False,
)
def read_owner_truth_interview_session_presentation(
    request: Request,
    vault_id: str,
    session_id: str,
) -> JSONResponse:
    """Read product-safe continuation guidance for one private interview.

    This route follows the same captured ``echoTextInput`` policy boundary as
    the natural-input commands.  It deliberately does not promote or expose
    Candidate review content; it only makes the current continuation boundary
    renderable in the product sheet.
    """

    try:
        context = _owner_truth_interview_natural_input_context(request, vault_id=vault_id)
        snapshot = OwnerTruthInterviewSessionReadService(store).read(
            session_id=session_id,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_session_state_http_error(error) from error
    return JSONResponse(
        status_code=200,
        content=_owner_truth_interview_session_presentation_response(
            vault_id=context.vault_id,
            snapshot=snapshot,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/interview-sessions",
    include_in_schema=False,
)
def start_owner_truth_interview_session(
    request: Request,
    vault_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """Natural-input session bootstrap; it creates no memory artifact."""

    try:
        context = _owner_truth_interview_natural_input_context(request, vault_id=vault_id)
        command = StartInterviewSessionCommand(
            command_id=str(payload.get("commandId") or ""),
            thread_id=str(payload.get("threadId") or ""),
            session_id=str(payload.get("sessionId") or ""),
            expected_thread_version=0,
            entry_mode="naturalInput",
        )
        with store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-input-start:"
                f"{context.vault_id}:{command.session_id}"
            ),
            command_id=command.command_id,
        ):
            result = OwnerTruthConversationService(
                store.owner_truth_conversation_repository()
            ).start_session(
                command=command,
                context=context,
            )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_session_state_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content=_owner_truth_interview_session_command_response(
            vault_id=context.vault_id,
            result=result,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages",
    include_in_schema=False,
)
def append_owner_truth_interview_narrative(
    request: Request,
    vault_id: str,
    session_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """Owner narrative append; the response remains content-free."""

    try:
        context = _owner_truth_interview_natural_input_context(request, vault_id=vault_id)
        command = AppendInterviewMessageCommand(
            command_id=str(payload.get("commandId") or ""),
            thread_id=str(payload.get("threadId") or ""),
            session_id=session_id,
            message_id=str(payload.get("messageId") or ""),
            expected_thread_version=int(payload.get("expectedThreadVersion") or 0),
            expected_session_version=int(payload.get("expectedSessionVersion") or 0),
            author=ConversationMessageAuthor.OWNER,
            kind=ConversationMessageKind.NARRATIVE,
            text=str(payload.get("text") or ""),
        )
        with store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-input-append:"
                f"{context.vault_id}:{command.session_id}"
            ),
            command_id=command.command_id,
        ):
            result = OwnerTruthConversationService(
                store.owner_truth_conversation_repository()
            ).append_message(
                command=command,
                context=context,
            )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_session_state_http_error(error) from error
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "ownerTruthInterviewSessionInvalid"},
        ) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content=_owner_truth_interview_session_command_response(
            vault_id=context.vault_id,
            result=result,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.get(
    "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-review",
    include_in_schema=False,
)
def read_owner_truth_interview_candidate_review(
    request: Request,
    vault_id: str,
    review_batch_id: str,
) -> JSONResponse:
    """QA-only M0-A review read; it never turns a Candidate into a Memory."""

    try:
        context = _owner_truth_candidate_review_context(request, vault_id=vault_id)
        result = OwnerTruthInterviewCandidateReviewReadService(store).read(
            review_batch_id=review_batch_id,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_candidate_review_http_error(error) from error
    return JSONResponse(
        status_code=200,
        content=_owner_truth_interview_candidate_review_read_response(
            vault_id=context.vault_id,
            result=result,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.get(
    "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/confirmation",
    include_in_schema=False,
)
def read_owner_truth_interview_candidate_confirmation(
    request: Request,
    vault_id: str,
    review_batch_id: str,
) -> JSONResponse:
    """Default-off product confirmation read for owner-reviewed Candidates.

    This is deliberately separate from the QA candidate-review route. A QA
    header alone cannot read it; the caller needs an allowed, captured
    ``ownerTruthCandidateReview`` release-policy decision.
    """

    try:
        context = _owner_truth_interview_candidate_confirmation_context(
            request,
            vault_id=vault_id,
        )
        result = OwnerTruthInterviewCandidateReviewReadService(store).read(
            review_batch_id=review_batch_id,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_candidate_review_http_error(error) from error
    return JSONResponse(
        status_code=200,
        content=_owner_truth_interview_candidate_confirmation_read_response(
            vault_id=context.vault_id,
            result=result,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/confirmation/batch-accept",
    include_in_schema=False,
)
def accept_owner_truth_interview_candidate_confirmation_batch(
    request: Request,
    vault_id: str,
    review_batch_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """Default-off product batch confirmation for ordinary Candidates.

    This formal path deliberately does not delegate to or share the QA route.
    It accepts a captured release-policy decision, records terminal Candidate
    receipts only, and never activates a MemoryVersion.
    """

    try:
        context = _owner_truth_interview_candidate_confirmation_write_context(
            request,
            vault_id=vault_id,
        )
        command = OwnerTruthInterviewCandidateBatchAcceptCommand(
            command_id=str(payload.get("commandId") or ""),
            review_batch_id=review_batch_id,
            selections=_owner_truth_interview_candidate_batch_selections(payload),
            reason_code="ownerConfirmedAtBoundary",
        )
        result = OwnerTruthInterviewCandidateBatchDecisionService(store).accept_selected(
            command=command,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_candidate_review_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content=_owner_truth_interview_candidate_confirmation_batch_decision_response(result),
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-review/batch-accept",
    include_in_schema=False,
)
def accept_owner_truth_interview_candidate_batch(
    request: Request,
    vault_id: str,
    review_batch_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """QA-only partial acceptance for standard M0-A Candidates.

    It records terminal Candidate decisions and immutable receipts only. The
    existing initial-Memory activation route remains a separate Authority step.
    """

    try:
        context = _owner_truth_candidate_review_context(request, vault_id=vault_id)
        command = OwnerTruthInterviewCandidateBatchAcceptCommand(
            command_id=str(payload.get("commandId") or ""),
            review_batch_id=review_batch_id,
            selections=_owner_truth_interview_candidate_batch_selections(payload),
            reason_code=str(payload.get("reasonCode") or "ownerReviewed"),
        )
        result = OwnerTruthInterviewCandidateBatchDecisionService(store).accept_selected(
            command=command,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_candidate_review_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content=_owner_truth_interview_candidate_batch_decision_response(result),
    )


@app.post(
    "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-review/candidates/{candidate_id}/decision",
    include_in_schema=False,
)
def review_owner_truth_interview_candidate_single(
    request: Request,
    vault_id: str,
    review_batch_id: str,
    candidate_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """QA-only sensitive/explicit-single M0-A Candidate decision."""

    try:
        context = _owner_truth_candidate_review_context(request, vault_id=vault_id)
        command = OwnerTruthInterviewCandidateSingleReviewCommand(
            command_id=str(payload.get("commandId") or ""),
            review_batch_id=review_batch_id,
            candidate_id=candidate_id,
            expected_candidate_version=_owner_truth_candidate_expected_version(payload),
            action=CandidateReviewAction(str(payload.get("action") or "")),
            corrected_value=payload.get("correctedValue"),
            corrected_value_schema_version=str(
                payload.get("correctedValueSchemaVersion")
                or OWNER_TRUTH_SCHEMA_VERSION
            ),
            reason_code=str(payload.get("reasonCode") or "ownerReviewed"),
        )
        result = OwnerTruthInterviewCandidateSingleReviewService(store).review_single(
            command=command,
            context=context,
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_interview_candidate_review_http_error(error) from error
    except (TypeError, ValueError) as error:
        raise _owner_truth_interview_candidate_review_http_error(
            OwnerTruthInterviewCandidateSingleReviewError(
                "single-review action is unsupported"
            )
        ) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content=_owner_truth_interview_candidate_single_review_response(result),
    )


@app.get(
    "/v2/vaults/{vault_id}/memory-projection",
    include_in_schema=False,
)
def read_owner_truth_memory_projection(
    request: Request,
    vault_id: str,
) -> Dict[str, Any]:
    """QA-only read of the default-off MemoryVersion compatibility projection."""

    try:
        context = _owner_truth_memory_projection_context(request, vault_id=vault_id)
        snapshot = OwnerTruthMemoryProjectionService(store).read(context=context)
    except OwnerTruthMemoryProjectionError as error:
        raise _owner_truth_memory_projection_http_error(error) from error
    return {
        "schemaVersion": "owner-truth-memory-projection-read-v1",
        "projection": projection_summary(snapshot),
    }


@app.post(
    "/v2/vaults/{vault_id}/memory-projection/rebuild",
    include_in_schema=False,
)
def rebuild_owner_truth_memory_projection(
    request: Request,
    vault_id: str,
) -> Dict[str, Any]:
    """QA-only deterministic rebuild; no public KBLite or Context read switch."""

    try:
        context = _owner_truth_memory_projection_context(request, vault_id=vault_id)
        result = OwnerTruthMemoryProjectionService(store).rebuild(context=context)
    except OwnerTruthMemoryProjectionError as error:
        raise _owner_truth_memory_projection_http_error(error) from error
    return {
        "schemaVersion": "owner-truth-memory-projection-rebuild-v1",
        "outcome": result.outcome,
        "projection": projection_summary(result.snapshot),
    }


@app.get(
    "/v2/vaults/{vault_id}/kblite-compatibility",
    include_in_schema=False,
)
def read_owner_truth_kblite_compatibility(
    request: Request,
    vault_id: str,
) -> Dict[str, Any]:
    """QA-only summary of the default-off, read-only KBLite compatibility view."""

    try:
        context = _owner_truth_kblite_compatibility_context(request, vault_id=vault_id)
        compatibility = OwnerTruthKBLiteCompatibilityReadService(
            store,
            enabled=True,
        ).read(context=context)
    except OwnerTruthMemoryProjectionError as error:
        raise _owner_truth_memory_projection_http_error(error) from error
    return {
        "schemaVersion": "owner-truth-kblite-compatibility-read-v1",
        "compatibility": kblite_compatibility_summary(compatibility),
    }


@app.get(
    "/v2/vaults/{vault_id}/kblite-compatibility/read-envelope",
    include_in_schema=False,
)
def read_owner_truth_kblite_compatibility_envelope(
    request: Request,
    vault_id: str,
) -> JSONResponse:
    """QA-only, cacheable Projection read contract for the isolated iOS cohort.

    This route remains invisible and default-off.  It does not alter legacy
    KBLite snapshot/change routes or public Echo context selection.
    """

    try:
        context = _owner_truth_kblite_compatibility_context(request, vault_id=vault_id)
        compatibility = OwnerTruthKBLiteCompatibilityReadService(
            store,
            enabled=True,
        ).read(context=context)
        envelope = kblite_compatibility_read_envelope(compatibility)
    except OwnerTruthMemoryProjectionError as error:
        raise _owner_truth_memory_projection_http_error(error) from error
    return JSONResponse(
        content=envelope,
        headers={"Cache-Control": "no-store"},
    )


@app.get(
    "/v2/vaults/{vault_id}/context-shadow",
    include_in_schema=False,
)
def read_owner_truth_context_shadow(
    request: Request,
    vault_id: str,
) -> Dict[str, Any]:
    """QA-only citation selection plan; legacy Context behavior is untouched."""

    try:
        context = _owner_truth_context_shadow_context(request, vault_id=vault_id)
        shadow = OwnerTruthContextShadowReadService(store, enabled=True).read(context=context)
    except OwnerTruthMemoryProjectionError as error:
        raise _owner_truth_memory_projection_http_error(error) from error
    return {
        "schemaVersion": "owner-truth-context-shadow-read-v1",
        "contextShadow": context_shadow_summary(shadow),
    }


@app.post(
    "/v2/vaults/{vault_id}/context-shadow/build",
    include_in_schema=False,
)
def build_owner_truth_context_shadow(
    request: Request,
    vault_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """QA-only Context V4 shadow build; the public Context Packet is untouched."""

    try:
        context = _owner_truth_context_shadow_context(request, vault_id=vault_id)
        shadow = OwnerTruthContextShadowBuildService(store, enabled=True).build(
            context=context,
            payload=payload,
        )
    except OwnerTruthMemoryProjectionError as error:
        raise _owner_truth_memory_projection_http_error(error) from error
    return {
        "schemaVersion": "owner-truth-context-shadow-build-response-v1",
        "contextShadow": context_shadow_build_summary(shadow),
    }


@app.post(
    "/v2/vaults/{vault_id}/answer-citation-receipts",
    include_in_schema=False,
)
def record_owner_truth_answer_citation(
    request: Request,
    vault_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """Persist hash-only Answer/Citation proof for the Context V4 QA lane.

    This endpoint deliberately does not generate an answer or expose an Echo
    feature.  It proves that a future answer can be bound to exactly the typed
    MemoryVersion citations selected by the default-off Context shadow.
    """

    try:
        context = _owner_truth_answer_citation_context(request, vault_id=vault_id)
        command = OwnerTruthAnswerCitationCommand(
            command_id=payload.get("commandId"),
            answer_text=payload.get("answerText"),
        )
        result = OwnerTruthAnswerCitationService(store, enabled=True).record(
            context=context,
            command=command,
            context_payload={
                "intent": payload.get("intent"),
                "query": payload.get("query"),
            },
        )
    except OwnerTruthMemoryProjectionError as error:
        raise _owner_truth_answer_citation_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content={
            "schemaVersion": "owner-truth-answer-citation-receipt-response-v1",
            "status": result.outcome,
            "answerCitation": answer_citation_summary(result),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/memory-versions/{memory_version_id}/knowledge-dimension-confirmations",
    include_in_schema=False,
)
def confirm_owner_truth_knowledge_dimension(
    request: Request,
    vault_id: str,
    memory_version_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """Record one explicit, immutable Owner classification in the M0-B QA lane.

    This does not alter a MemoryVersion or expose a public recommendation/Echo
    feature.  The service binds the receipt to the current projection hash and
    Postgres revalidates the authoritative current-version record on insert.
    """

    try:
        context = _owner_truth_knowledge_dimension_confirmation_context(
            request,
            vault_id=vault_id,
        )
        command = OwnerTruthKnowledgeDimensionConfirmationCommand(
            command_id=payload.get("commandId"),
            expected_content_hash=payload.get("expectedContentHash"),
            dimension=payload.get("dimension"),
            covered_facets=tuple(payload.get("coveredFacets") or ()),
            confirmation_method=payload.get("confirmationMethod")
            or "ownerExplicitSelection",
            ui_schema_version=payload.get("uiSchemaVersion")
            or "knowledge-dimension-review-v1",
        )
        result = OwnerTruthKnowledgeDimensionConfirmationService(
            store,
            enabled=True,
        ).confirm(
            context=context,
            memory_version_id=memory_version_id,
            command=command,
        )
    except OwnerTruthKnowledgeDimensionConfirmationError as error:
        raise _owner_truth_knowledge_dimension_confirmation_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content={
            "schemaVersion": "owner-truth-knowledge-dimension-confirmation-response-v1",
            "status": result.outcome,
            "confirmation": knowledge_dimension_confirmation_summary(result),
        },
        headers={"Cache-Control": "no-store"},
    )


_OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_CANDIDATE_FIELDS = frozenset(
    {
        "candidateId",
        "slot",
        "threadId",
        "targetDimension",
        "missingFacet",
        "questionTemplateId",
        "evidenceKind",
        "evidenceRefs",
        "reasonCode",
        "explicitIntentPriority",
        "continuityScore",
        "importanceScore",
        "isAccessible",
        "isDoNotAsk",
        "isInCooldown",
        "consecutiveSkipCount",
        "wasReopenedByUser",
        "isSensitive",
        "hasRecentUserConsent",
        "isAiInferenceOnly",
        "isDeleted",
        "isRevoked",
        "isDisputed",
        "isMinorRisk",
        "requiresPersona",
        "expiresAt",
    }
)

_OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_FIELDS = frozenset(
    {
        "candidates",
        "crisisActive",
    }
)


def _owner_truth_knowledge_recommendation_candidate(
    payload: Any,
    *,
    owner_subject_id: str,
    vault_id: str,
) -> RecommendationCandidate:
    """Parse a strict, value-free candidate envelope for hidden QA only."""

    if not isinstance(payload, dict):
        raise OwnerTruthKnowledgeRecommendationReadError("recommendation candidate must be an object")
    unsupported = sorted(set(payload).difference(_OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_CANDIDATE_FIELDS))
    if unsupported:
        raise OwnerTruthKnowledgeRecommendationReadError(
            "recommendation candidate contains unsupported fields"
        )
    evidence_refs = payload.get("evidenceRefs")
    if not isinstance(evidence_refs, list):
        raise OwnerTruthKnowledgeRecommendationReadError("recommendation candidate evidenceRefs must be a list")
    expires_at = payload.get("expiresAt")
    if expires_at is not None:
        if not isinstance(expires_at, str):
            raise OwnerTruthKnowledgeRecommendationReadError(
                "recommendation candidate expiresAt must be an ISO timestamp"
            )
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise OwnerTruthKnowledgeRecommendationReadError(
                "recommendation candidate expiresAt must be an ISO timestamp"
            ) from error
        if expires_at.tzinfo is None:
            raise OwnerTruthKnowledgeRecommendationReadError(
                "recommendation candidate expiresAt must include a timezone"
            )
    return RecommendationCandidate(
        candidate_id=payload.get("candidateId"),
        owner_subject_id=owner_subject_id,
        vault_id=vault_id,
        slot=payload.get("slot"),
        thread_id=payload.get("threadId"),
        target_dimension=payload.get("targetDimension"),
        missing_facet=payload.get("missingFacet"),
        question_template_id=payload.get("questionTemplateId"),
        evidence_kind=payload.get("evidenceKind"),
        evidence_refs=tuple(evidence_refs),
        reason_code=payload.get("reasonCode"),
        explicit_intent_priority=payload.get("explicitIntentPriority", 0),
        continuity_score=payload.get("continuityScore", 0),
        importance_score=payload.get("importanceScore", 0),
        is_accessible=payload.get("isAccessible", True),
        is_do_not_ask=payload.get("isDoNotAsk", False),
        is_in_cooldown=payload.get("isInCooldown", False),
        consecutive_skip_count=payload.get("consecutiveSkipCount", 0),
        was_reopened_by_user=payload.get("wasReopenedByUser", False),
        is_sensitive=payload.get("isSensitive", False),
        has_recent_user_consent=payload.get("hasRecentUserConsent", False),
        is_ai_inference_only=payload.get("isAiInferenceOnly", False),
        is_deleted=payload.get("isDeleted", False),
        is_revoked=payload.get("isRevoked", False),
        is_disputed=payload.get("isDisputed", False),
        is_minor_risk=payload.get("isMinorRisk", False),
        requires_persona=payload.get("requiresPersona", False),
        expires_at=expires_at,
    )


@app.post(
    "/v2/vaults/{vault_id}/knowledge-recommendations/read",
    include_in_schema=False,
)
def read_owner_truth_knowledge_recommendations(
    request: Request,
    vault_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """Read receipt-bound M0-B selection policy without exposing Echo UI.

    This is a default-off QA adapter. It does not generate question text, write
    a recommendation record, call a provider, or alter a ConversationThread,
    Candidate, DecisionReceipt, MemoryVersion, or projection.
    """

    try:
        context = _owner_truth_knowledge_recommendation_read_context(
            request,
            vault_id=vault_id,
        )
        unsupported = sorted(
            set(payload).difference(_OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_FIELDS)
        )
        if unsupported:
            raise OwnerTruthKnowledgeRecommendationReadError(
                "knowledge recommendation read contains unsupported fields"
            )
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raise OwnerTruthKnowledgeRecommendationReadError("candidates must be a list")
        candidates = tuple(
            _owner_truth_knowledge_recommendation_candidate(
                item,
                owner_subject_id=context.owner_subject_id,
                vault_id=context.vault_id,
            )
            for item in raw_candidates
        )
        result = OwnerTruthKnowledgeRecommendationReadService(store).read(
            context=context,
            candidates=candidates,
            crisis_active=payload.get("crisisActive", False),
        )
    except OwnerTruthContractError as error:
        raise _owner_truth_knowledge_recommendation_read_http_error(error) from error
    return JSONResponse(
        status_code=200,
        content={
            "schemaVersion": "owner-truth-knowledge-recommendation-read-response-v1",
            "vaultId": context.vault_id,
            "recommendations": result.value_free_summary(),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/memories/{memory_id}/corrections",
    include_in_schema=False,
)
def request_owner_truth_answer_citation_correction(
    request: Request,
    vault_id: str,
    memory_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """Create a pending correction Candidate without changing Memory authority.

    The raw correction is retained only by a private Owner Truth Source.  The
    response is intentionally value-free and no public Echo behavior changes.
    """

    try:
        context = _owner_truth_correction_request_context(request, vault_id=vault_id)
        command = OwnerTruthCorrectionRequestCommand(
            command_id=payload.get("commandId"),
            answer_id=payload.get("answerId"),
            citation_id=payload.get("citationId"),
            memory_id=memory_id,
            expected_memory_version_id=payload.get("expectedMemoryVersionId"),
            correction_text=payload.get("correctionText"),
            reason_code=payload.get("reasonCode"),
        )
        result = OwnerTruthCorrectionRequestService(store, enabled=True).request(
            context=context,
            command=command,
        )
    except OwnerTruthCorrectionRequestError as error:
        raise _owner_truth_correction_request_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content={
            "schemaVersion": "owner-truth-correction-request-response-v1",
            "status": result.outcome,
            "correctionRequest": correction_request_summary(result),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/correction-requests/{correction_request_id}/resolve",
    include_in_schema=False,
)
def resolve_owner_truth_answer_citation_correction(
    request: Request,
    vault_id: str,
    correction_request_id: str,
    payload: Dict[str, Any],
) -> JSONResponse:
    """QA-only terminal correction resolver for one cited Owner Truth answer.

    This route is hidden by the same explicit Owner Truth QA gate as the
    request endpoint.  It does not expose raw correction text and it never
    creates a separate MemoryRecord for a correction.
    """

    try:
        context = _owner_truth_correction_request_context(request, vault_id=vault_id)
        command = OwnerTruthCorrectionResolutionCommand(
            command_id=payload.get("commandId"),
            expected_candidate_version=_owner_truth_candidate_expected_version(payload),
            expected_memory_version_id=payload.get("expectedMemoryVersionId"),
            action=payload.get("action"),
            corrected_value=payload.get("correctedValue"),
            corrected_value_schema_version=(
                payload.get("correctedValueSchemaVersion") or OWNER_TRUTH_SCHEMA_VERSION
            ),
            reason_code=payload.get("reasonCode"),
        )
        result = OwnerTruthCorrectionRequestService(store, enabled=True).resolve(
            context=context,
            correction_request_id=correction_request_id,
            command=command,
        )
    except OwnerTruthCorrectionRequestError as error:
        raise _owner_truth_correction_request_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content={
            "schemaVersion": "owner-truth-correction-resolution-response-v1",
            "status": result.outcome,
            "correctionResolution": correction_resolution_summary(result),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/legacy-migration/inventory",
    include_in_schema=False,
)
def inventory_owner_truth_legacy_evidence(
    request: Request,
    vault_id: str,
) -> JSONResponse:
    """QA-only, hash-only legacy evidence inventory with no promotion effects."""

    try:
        context = _owner_truth_legacy_migration_context(request, vault_id=vault_id)
        result = OwnerTruthLegacyMigrationInventoryService(store, enabled=True).inventory(
            context=context,
        )
    except OwnerTruthLegacyMigrationError as error:
        raise _owner_truth_legacy_migration_http_error(error) from error
    return JSONResponse(
        status_code=201 if result.outcome == "created" else 200,
        content=legacy_migration_summary(result),
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/v2/vaults/{vault_id}/legacy-migration/shadow-parity",
    include_in_schema=False,
)
def observe_owner_truth_legacy_shadow_parity(
    request: Request,
    vault_id: str,
) -> JSONResponse:
    """QA-only parity readiness observation; never a backfill or cutover command."""

    try:
        context = _owner_truth_legacy_migration_context(request, vault_id=vault_id)
        observation = OwnerTruthLegacyShadowParityService(store, enabled=True).observe(
            context=context,
        )
    except OwnerTruthLegacyMigrationError as error:
        raise _owner_truth_legacy_migration_http_error(error) from error
    return JSONResponse(
        status_code=201 if observation.inventory_outcome == "created" else 200,
        content=legacy_shadow_parity_summary(observation),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/ops/release-policy/observations")
def release_policy_observations(request: Request) -> Dict[str, Any]:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, RequestPrincipal) or principal.kind != PrincipalKind.MACHINE:
        raise HTTPException(status_code=403, detail="machine principal required")
    summary = RELEASE_POLICY_DECISION_RECORDER.summary()
    summary["operationMetrics"] = summarize_operation_metrics_for_observations(
        OPERATION_METRIC_RECORDER.summary()
    )
    summary["providerCostEvidence"] = summarize_provider_cost_evidence_for_observations(
        PROVIDER_COST_EVIDENCE_RECORDER.summary()
    )
    summary["routeAuthentication"] = ROUTE_AUTHENTICATION_DECISION_RECORDER.summary()
    summary["clientCompatibility"] = (
        CLIENT_COMPATIBILITY_DECISION_RECORDER.summary(
            mode=CLIENT_COMPATIBILITY_POLICY.mode,
            minimum_client_build=(
                CLIENT_COMPATIBILITY_POLICY.minimum_client_build
            ),
        )
    )
    unit_of_work_metrics = getattr(store, "uow_metrics", None)
    if callable(unit_of_work_metrics):
        summary["databaseUnitOfWork"] = unit_of_work_metrics()
    summary["incidentLifecycle"] = _incident_lifecycle_service().summary()
    return summary


def _evidence_manifest_error_response(error: EvidenceManifestError) -> HTTPException:
    status_code = 503 if error.code.endswith(("Unavailable", "Failed")) else 400
    return HTTPException(status_code=status_code, detail={"code": error.code})


@app.post("/ops/evidence-manifests")
def issue_evidence_manifest(payload: Dict[str, Any], request: Request) -> JSONResponse:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, RequestPrincipal) or principal.kind != PrincipalKind.MACHINE:
        raise HTTPException(status_code=403, detail="machine principal required")
    allowed_fields = {
        "manifestType",
        "sourceCommit",
        "commandId",
        "sampleCount",
        "sampleSetHash",
        "exclusionCodes",
        "sourceSchemaVersions",
        "artifactHashes",
        "windowStartedAt",
        "windowEndedAt",
        "issuer",
        "manifestStatus",
        "build",
        "ownerLeaseHash",
    }
    if set(payload).difference(allowed_fields):
        # This endpoint is deliberately metadata-only. Reject unknown fields
        # rather than silently dropping a report body, raw trace, or secret.
        raise HTTPException(
            status_code=422,
            detail={"code": "evidenceManifestUnexpectedField"},
        )
    try:
        manifest = _evidence_manifest_service().issue(
            manifest_type=payload.get("manifestType", ""),
            source_commit=payload.get("sourceCommit", ""),
            command_id=payload.get("commandId", ""),
            sample_count=payload.get("sampleCount", 0),
            sample_set_hash=payload.get("sampleSetHash", ""),
            exclusion_codes=payload.get("exclusionCodes", ()),
            source_schema_versions=payload.get("sourceSchemaVersions", ()),
            artifact_hashes=payload.get("artifactHashes", ()),
            window_started_at=payload.get("windowStartedAt"),
            window_ended_at=payload.get("windowEndedAt"),
            issuer=payload.get("issuer", ""),
            manifest_status=payload.get("manifestStatus", ""),
            build=payload.get("build"),
            owner_lease_hash=payload.get("ownerLeaseHash"),
        )
    except EvidenceManifestError as exc:
        raise _evidence_manifest_error_response(exc) from exc
    except (TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "evidenceManifestInvalid"},
        ) from exc
    return _set_no_store_headers(JSONResponse(content=manifest))


@app.get("/ops/evidence-manifests")
def list_evidence_manifests(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> JSONResponse:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, RequestPrincipal) or principal.kind != PrincipalKind.MACHINE:
        raise HTTPException(status_code=403, detail="machine principal required")
    try:
        summary = _evidence_manifest_service().list_manifests(limit=limit)
    except EvidenceManifestError as exc:
        raise _evidence_manifest_error_response(exc) from exc
    return _set_no_store_headers(JSONResponse(content=summary))


def _incident_error_response(error: IncidentLifecycleError) -> HTTPException:
    code = error.code
    if code == "incidentNotFound":
        status_code = 404
    elif code in {
        "incidentAlreadyExists",
        "incidentAlreadyResolved",
        "incidentAcknowledgementInvalidState",
        "incidentAcknowledgementRequired",
        "incidentFenceIncomplete",
        "incidentFenceActionAlreadyRecorded",
        "incidentReopenRequiresResolvedSource",
    }:
        status_code = 409
    elif code in {
        "incidentEvidenceSinkUnavailable",
        "incidentEvidenceAppendFailed",
        "incidentEvidenceQueryUnavailable",
        "incidentEvidenceQueryFailed",
        "incidentEvidenceInvalid",
    }:
        status_code = 503
    else:
        status_code = 400
    return HTTPException(status_code=status_code, detail={"code": code})


@app.post("/ops/incidents")
def open_incident(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _incident_lifecycle_service().open(
            incident_id=payload.get("incidentId", ""),
            category=payload.get("category", ""),
            severity=payload.get("severity", ""),
            owner=payload.get("owner", ""),
            runbook_id=payload.get("runbookId", ""),
            reason=payload.get("reason", ""),
            required_fence_actions=payload.get("requiredFenceActions", ()),
            command_id=payload.get("commandId", ""),
            surface=payload.get("surface", "operations"),
        )
    except IncidentLifecycleError as exc:
        raise _incident_error_response(exc) from exc


@app.get("/ops/incidents/readiness")
def incident_readiness() -> Dict[str, Any]:
    return _incident_lifecycle_service().summary()


@app.get("/ops/incidents/{incident_id}")
def incident_detail(incident_id: str) -> Dict[str, Any]:
    try:
        return {
            "schemaVersion": IncidentLifecycleService.SCHEMA_VERSION,
            "incident": _incident_lifecycle_service().get(incident_id),
        }
    except IncidentLifecycleError as exc:
        raise _incident_error_response(exc) from exc


@app.post("/ops/incidents/{incident_id}/ack")
def acknowledge_incident(incident_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _incident_lifecycle_service().acknowledge(
            incident_id=incident_id,
            reason=payload.get("reason", ""),
            command_id=payload.get("commandId", ""),
        )
    except IncidentLifecycleError as exc:
        raise _incident_error_response(exc) from exc


@app.post("/ops/incidents/{incident_id}/fence")
def fence_incident(incident_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _incident_lifecycle_service().fence(
            incident_id=incident_id,
            reason=payload.get("reason", ""),
            fence_actions=payload.get("fenceActions", ()),
            command_id=payload.get("commandId", ""),
        )
    except IncidentLifecycleError as exc:
        raise _incident_error_response(exc) from exc


@app.post("/ops/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _incident_lifecycle_service().resolve(
            incident_id=incident_id,
            reason=payload.get("reason", ""),
            evidence_ids=payload.get("evidenceIds", ()),
            command_id=payload.get("commandId", ""),
        )
    except IncidentLifecycleError as exc:
        raise _incident_error_response(exc) from exc


@app.post("/ops/incidents/{incident_id}/reopen")
def reopen_incident(incident_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _incident_lifecycle_service().reopen(
            incident_id=incident_id,
            new_incident_id=payload.get("newIncidentId", ""),
            owner=payload.get("owner", ""),
            reason=payload.get("reason", ""),
            runbook_id=payload.get("runbookId", ""),
            required_fence_actions=payload.get("requiredFenceActions", ()),
            command_id=payload.get("commandId", ""),
        )
    except IncidentLifecycleError as exc:
        raise _incident_error_response(exc) from exc


@app.post("/digital-human/sessions")
def create_digital_human_session(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    persona_id = str(payload.get("personaId") or "").strip()
    scene = str(payload.get("scene") or "echo").strip() or "echo"
    lifecycle_mode = str(payload.get("lifecycleMode") or "sunlight").strip() or "sunlight"
    if not persona_id:
        raise HTTPException(status_code=400, detail="personaId is required")
    _evaluate_subject_eligibility_payload(
        payload,
        HighRiskCapability.DIGITAL_HUMAN,
        required=True,
    )
    if lifecycle_mode not in DIGITAL_HUMAN_MODE_LABELS:
        raise HTTPException(status_code=400, detail=f"unsupported lifecycleMode: {lifecycle_mode}")
    if lifecycle_mode == "silent":
        raise HTTPException(status_code=409, detail="silent mode must not create a digital human render session")
    blocked_contract = DigitalHumanAccessPolicy().blocked_mobile_contract()
    raise HTTPException(
        status_code=503,
        detail={
            **blocked_contract,
            "code": "digital_human_credential_broker_unavailable",
            "message": "digital human rendering requires a revocable scoped session credential broker",
        },
    )


@app.post("/digital-human/sessions/{session_id}/heartbeat")
def heartbeat_digital_human_session(request: Request, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    device_id = str(payload.get("deviceId") or "").strip()
    if not device_id:
        raise HTTPException(status_code=400, detail="deviceId is required")
    now = datetime.now(timezone.utc)
    result = store.heartbeat_digital_human_session_lease(
        session_id,
        user_id=user_id,
        device_id=device_id,
        heartbeat_at_iso=now.isoformat(),
        expires_at_iso=(now + timedelta(seconds=DIGITAL_HUMAN_SESSION_TTL_SECONDS)).isoformat(),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="digital human session lease not found")
    if result["outcome"] != "active":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "digital_human_session_lease_inactive",
                "status": result["lease"].get("status"),
                "message": "digital human session lease is no longer active",
            },
        )
    return {
        "status": "active",
        "sessionId": session_id,
        "lease": _digital_human_session_lease_response(result["lease"], reused=True),
    }


@app.post("/digital-human/sessions/{session_id}/release")
def release_digital_human_session(request: Request, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    device_id = str(payload.get("deviceId") or "").strip()
    reason = str(payload.get("reason") or "clientRelease").strip()[:80] or "clientRelease"
    if not device_id:
        raise HTTPException(status_code=400, detail="deviceId is required")
    now_iso = datetime.now(timezone.utc).isoformat()
    result = store.release_digital_human_session_lease(
        session_id,
        user_id=user_id,
        device_id=device_id,
        released_at_iso=now_iso,
        reason=reason,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="digital human session lease not found")
    return {
        "status": result["outcome"],
        "sessionId": session_id,
        "lease": {
            **_digital_human_session_lease_response(result["lease"], reused=False),
            "releaseReason": result["lease"].get("releaseReason"),
            "releasedAt": result["lease"].get("releasedAt"),
        },
    }


@app.post("/v2/auth/challenges", status_code=202)
def create_identity_challenge(
    request: Request,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        service = _identity_binding_service()
        return service.create_challenge(
            identity_type=str(payload.get("identityType") or "phone"),
            target=str(payload.get("target") or ""),
            purpose=str(payload.get("purpose") or "login"),
        )
    except IdentityChallengeValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "identity_challenge_invalid_request",
                "message": str(exc),
            },
        ) from exc
    except IdentityChallengeRateLimited as exc:
        request.state.commit_security_attempt = True
        raise HTTPException(
            status_code=429,
            detail={
                "code": "identity_challenge_rate_limited",
                "message": "identity challenge is temporarily unavailable",
                "retryAfterSeconds": exc.retry_after_seconds,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except IdentityChallengeConfigurationError as exc:
        logger.error("identity_challenge_configuration_unavailable")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "identity_challenge_unavailable",
                "message": "identity challenge is unavailable",
            },
        ) from exc


@app.post("/v2/auth/challenges/{challenge_id}/verify")
def verify_identity_challenge(
    request: Request,
    challenge_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        service = _identity_binding_service()
        return service.verify_challenge(
            challenge_id,
            str(payload.get("code") or ""),
            nickname=str(payload.get("nickname") or ""),
        )
    except IdentityChallengeVerificationFailed as exc:
        request.state.commit_security_attempt = True
        raise HTTPException(
            status_code=401,
            detail={
                "code": "challenge_verification_failed",
                "message": "challenge could not be verified",
            },
        ) from exc
    except AuthSessionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except IdentityChallengeConfigurationError as exc:
        logger.error("identity_challenge_configuration_unavailable")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "identity_challenge_unavailable",
                "message": "identity challenge is unavailable",
            },
        ) from exc


@app.post("/auth/login")
def login(request: Request, payload: Dict[str, Any]) -> Any:
    if not AUTH_LEGACY_PHONE_LOGIN_ENABLED:
        return _legacy_identity_upgrade_response(request)
    phone = str(payload.get("phone") or "").strip()
    nickname = str(payload.get("nickname") or "").strip()
    password = _optional_password(payload, "password")
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    user_id = stable_user_id(phone)
    with store.auth_user_operation(user_id):
        credential = store.get_password_credential(user_id)
        if credential is not None and not password:
            raise HTTPException(status_code=401, detail="password is required")
        if credential is not None and password and not verify_password(password, credential):
            raise HTTPException(status_code=401, detail="invalid password")

        existing_user = _store_get_user(user_id)
        deletion_state = str((existing_user or {}).get("deletionState") or "active")
        if deletion_state == "purged":
            raise HTTPException(status_code=410, detail="account was permanently deleted")
        if deletion_state == "softDeleted":
            user = _restore_soft_deleted_account_or_raise(
                user_id=user_id,
                phone=phone,
                nickname=nickname,
            )
            user["passwordConfigured"] = credential is not None
            return {
                "status": "restored",
                "user": user,
                "auth": _auth_session_service().issue(user_id),
            }

        user = store.upsert_user(phone=phone, nickname=nickname)
        if password and credential is None:
            credential = store.save_password_credential(user_id, make_password_credential(password))
        user["passwordConfigured"] = credential is not None
        return {
            "user": user,
            "auth": _auth_session_service().issue(user_id),
        }


@app.post("/auth/refresh")
def refresh_auth_session(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    refresh_token = str(payload.get("refreshToken") or "").strip()
    try:
        auth = _auth_session_service().refresh(refresh_token)
    except AuthSessionError as exc:
        if exc.commit_state_change:
            request.state.commit_security_attempt = True
        raise HTTPException(
            status_code=401,
            detail={
                "code": exc.code,
                "message": str(exc),
                "reauthenticationRequired": True,
            },
        ) from exc
    return {"status": "refreshed", "auth": auth}


@app.post("/auth/logout")
def logout_auth_session(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, RequestPrincipal) or principal.kind != PrincipalKind.USER:
        raise HTTPException(status_code=401, detail="user access token is required")
    scope = str(payload.get("scope") or "session").strip()
    try:
        revoked = _auth_session_service().revoke_access_token(
            _request_bearer_token(request),
            scope=scope,
            reason="logout",
        )
    except AuthSessionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if revoked is None:
        raise HTTPException(status_code=401, detail="invalid access token")
    return {
        "status": "revoked",
        "sessionId": revoked.get("sessionId"),
        "scope": revoked.get("scope", scope),
        "tokenFamilyId": revoked.get("tokenFamilyId"),
        "revocationReceiptId": revoked.get("revocationReceiptId"),
        "revokedSessionCount": int(revoked.get("revokedSessionCount") or 0),
        "revokedFamilyCount": int(revoked.get("revokedFamilyCount") or 0),
        "contractVersion": 2,
    }


@app.post("/auth/data-export")
def export_account_data(request: Request) -> JSONResponse:
    """Return the caller's bounded application-data export with no caching."""

    user_id = _request_user_principal_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="user access token is required")
    user = _store_get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="account not found")
    if str(user.get("deletionState") or "active") != "active":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "data_export_unavailable_after_deletion",
                "message": "data export must be requested before account deletion",
            },
        )
    try:
        export = build_module_owned_data_export(store, user_id=user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="account not found") from exc
    return JSONResponse(
        content=export,
        headers={
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
        },
    )


@app.get(
    "/ops/data-rights/requests/{request_id}/evidence",
    include_in_schema=False,
)
def observe_data_rights_evidence(request_id: str) -> JSONResponse:
    """Return a machine-only, read-only evidence projection for one request."""

    summary = store.summarize_rights_request(request_id)
    if summary is None:
        raise HTTPException(status_code=404, detail={"code": "rightsRequestNotFound"})
    list_access_revocations = getattr(store, "list_rights_access_revocation_outbox", None)
    access_revocations = (
        list_access_revocations(request_id)
        if callable(list_access_revocations)
        else []
    )
    try:
        report = build_data_rights_evidence_projection(
            summary,
            access_revocation_events=access_revocations,
        )
    except DataRightsEvidenceProjectionError as exc:
        logger.error("data rights evidence projection failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "rightsEvidenceUnavailable"},
        ) from exc
    return JSONResponse(
        content=report,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


def _store_get_user(user_id: str) -> Optional[Dict[str, Any]]:
    get_user = getattr(store, "get_user", None)
    if not callable(get_user):
        return None
    return get_user(user_id)


def _parse_account_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _account_restore_deadline_expired(user: Dict[str, Any]) -> bool:
    deadline = _parse_account_datetime(user.get("restoreDeadline") or user.get("purgeAfter"))
    return deadline < datetime.now(timezone.utc)


def _restore_soft_deleted_account_or_raise(
    *,
    user_id: str,
    phone: str,
    nickname: str = "",
) -> Dict[str, Any]:
    user = _store_get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="account not found")
    if user.get("deletionState") == "purged":
        raise HTTPException(status_code=410, detail="account was permanently deleted")
    if user.get("deletionState") != "softDeleted":
        return user
    if int(user.get("restoreCount") or 0) >= ACCOUNT_RESTORE_LIMIT:
        raise HTTPException(status_code=410, detail="account restore chance already used")
    if _account_restore_deadline_expired(user):
        raise HTTPException(status_code=410, detail="account restore deadline expired")
    restored = store.restore_user(user_id, phone=phone, nickname=nickname)
    if restored is None:
        raise HTTPException(status_code=404, detail="account not found")
    return restored


def _require_account_deletion_confirmations(payload: Dict[str, Any]) -> None:
    if not bool(payload.get("firstConfirmation")) or not bool(payload.get("secondConfirmation")):
        raise HTTPException(status_code=400, detail="two deletion confirmations are required")


def _account_delete_command_id(request: Request, payload: Dict[str, Any], user_id: str) -> str:
    explicit = str(
        payload.get("commandId")
        or request.headers.get("idempotency-key")
        or ""
    ).strip()
    if explicit:
        return explicit
    # Legacy callers do not have a command id. Keep compatibility without
    # pretending separate legacy requests are idempotent.
    return f"legacy-account-delete:{user_id}:{secrets.token_hex(16)}"


def _account_delete_rights_summary(request_id: str, *, outcome: Optional[str] = None) -> Dict[str, Any]:
    summary = store.summarize_rights_request(request_id) or {}
    request_record = summary.get("request") or {}
    response = {
        "requestId": str(request_record.get("id") or request_id),
        "status": str(request_record.get("status") or "unknown"),
        "contractVersion": int(request_record.get("contractVersion") or 1),
        "executionCount": len(summary.get("executions") or []),
        "receiptCount": len(summary.get("receipts") or []),
    }
    if outcome:
        response["outcome"] = outcome
    return response


def _prepare_account_delete_rights_request(
    request: Request,
    *,
    payload: Dict[str, Any],
    user_id: str,
    phone: str,
    existing_user: Dict[str, Any],
) -> Dict[str, Any]:
    lifecycle_marker = str(int(existing_user.get("restoreCount") or 0))
    try:
        contract_request = make_account_delete_request(
            command_id=_account_delete_command_id(request, payload, user_id),
            subject_id=user_id,
            phone=phone,
            lifecycle_marker=lifecycle_marker,
            scope=payload.get("rightsScope"),
        )
        return store.create_rights_request(contract_request)
    except DataRightsCommandConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rightsCommandConflict",
                "message": "commandId cannot be reused with a different deletion payload",
            },
        ) from exc
    except DataRightsContractError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "rightsRequestInvalid",
                "message": str(exc),
            },
        ) from exc


def _record_account_delete_rights_completion(
    request_id: str,
    *,
    request_record: Dict[str, Any],
    deletion: Dict[str, Any],
) -> Dict[str, Any]:
    updated_at = str(
        deletion.get("updatedAt")
        or deletion.get("deletedAt")
        or datetime.now(timezone.utc).isoformat()
    )
    # Rebuild only the redacted contract fields returned by the store. No raw
    # phone or request payload is required to create the execution receipt.
    from app.services.data_rights_contract import DataRightsRequest

    redacted_request = DataRightsRequest(
        request_id=str(request_record.get("id") or request_id),
        command_id_hash=str(request_record.get("commandIdHash") or ""),
        payload_hash=str(request_record.get("payloadHash") or ""),
        subject_hash=str(request_record.get("subjectHash") or ""),
        identity_proof_hash=str(request_record.get("identityProofHash") or ""),
        action=str(request_record.get("action") or "account.delete"),
        scope_hash=str(request_record.get("scopeHash") or ""),
        executions=(),
        created_at=str(request_record.get("createdAt") or updated_at),
        updated_at=str(request_record.get("updatedAt") or updated_at),
    )
    plan = completed_account_delete_execution(redacted_request, updated_at=updated_at)
    store.record_rights_execution(
        request_id,
        module_id=plan["moduleId"],
        resource_type=plan["resourceType"],
        execution_id_hash=plan["executionIdHash"],
        outcome=plan["outcome"],
        evidence_id_hash=plan["evidenceIdHash"],
        updated_at=plan["updatedAt"],
    )
    store.append_resource_deletion_receipt(
        receipt_id=plan["receiptId"],
        request_id=request_id,
        execution_id_hash=plan["executionIdHash"],
        module_id=plan["moduleId"],
        resource_scope_hash=plan["resourceScopeHash"],
        outcome=plan["outcome"],
        receipt_hash=plan["receiptHash"],
        evidence_event_id_hash=plan["evidenceIdHash"],
        created_at=plan["updatedAt"],
        retention_until=deletion.get("purgeAfter"),
    )
    return _account_delete_rights_summary(request_id, outcome="recorded")


def _record_account_access_revocation(
    *,
    request_id: str,
    user_id: str,
    deletion: Dict[str, Any],
    session_revocation: Dict[str, Any],
    delegated_grant_revocation: Dict[str, Any],
) -> Dict[str, Any]:
    auth_epoch = int(deletion.get("authEpoch") or 0)
    event_id = "rar_" + hashlib.sha256(
        f"{request_id}:RightsAccessRevoked".encode("utf-8")
    ).hexdigest()[:32]
    recorded = store.record_rights_access_revocation_outbox(
        event_id=event_id,
        request_id=request_id,
        user_id=user_id,
        auth_epoch=auth_epoch,
        provider_capability_state=str(
            deletion.get("providerCapabilityState") or "revoked"
        ),
        session_revocation=session_revocation,
        delegated_grant_revocation=delegated_grant_revocation,
        created_at=str(
            deletion.get("updatedAt")
            or deletion.get("deletedAt")
            or datetime.now(timezone.utc).isoformat()
        ),
    )
    event = recorded["event"]
    return {
        "eventId": event["id"],
        "eventType": event["eventType"],
        "status": event["status"],
        "authEpoch": event["authEpoch"],
        "providerCapabilityState": event["providerCapabilityState"],
        "outcome": recorded["outcome"],
    }


@app.post("/auth/delete")
def soft_delete_account(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    phone = str(payload.get("phone") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    _require_account_deletion_confirmations(payload)
    with store.auth_user_operation(user_id):
        existing_user = _store_get_user(user_id)
        if existing_user is not None and existing_user.get("deletionState") == "purged":
            raise HTTPException(status_code=410, detail="account was permanently deleted")
        if existing_user is None:
            raise HTTPException(status_code=404, detail="account not found")
        rights_result = _prepare_account_delete_rights_request(
            request,
            payload=payload,
            user_id=user_id,
            phone=phone,
            existing_user=existing_user,
        )
        rights_record = rights_result["request"]
        if (
            rights_result["outcome"] == "deduplicated"
            and str(rights_record.get("status") or "") == "completed"
        ):
            deletion = store.soft_delete_user(
                user_id,
                phone=phone,
                deletion_request_id=str(rights_record.get("id") or ""),
            )
            if deletion is None:
                raise HTTPException(status_code=404, detail="account not found")
            delegated_grant_revocation = _delegated_access_service().revoke_subject_access(
                user_id,
                reason="accountSoftDeleted",
            )
            session_revocation = _auth_session_service().revoke_all_for_user(
                user_id,
                reason="accountSoftDeleted",
            )
            access_revocation = _record_account_access_revocation(
                request_id=str(rights_record.get("id") or ""),
                user_id=user_id,
                deletion=deletion,
                session_revocation=session_revocation,
                delegated_grant_revocation=delegated_grant_revocation,
            )
            rights_summary = _account_delete_rights_summary(
                str(rights_record.get("id") or ""),
                outcome="deduplicated",
            )
            return {
                "status": "softDeleted",
                "contractVersion": ACCOUNT_DELETION_CONTRACT_VERSION,
                "deletion": deletion,
                "policy": {
                    "dataExportSupported": True,
                    "dataExportState": "availableBeforeDeletionOnly",
                    "retentionDays": ACCOUNT_DELETION_RETENTION_DAYS,
                    "restoreLimit": ACCOUNT_RESTORE_LIMIT,
                    "restoreBySamePhone": True,
                },
                "sessionRevocation": session_revocation,
                "delegatedGrantRevocation": delegated_grant_revocation,
                "accessRevocation": access_revocation,
                "rights": rights_summary,
            }
        deletion = store.soft_delete_user(
            user_id,
            phone=phone,
            deletion_request_id=str(rights_record.get("id") or ""),
        )
        if deletion is None:
            raise HTTPException(status_code=404, detail="account not found")
        delegated_grant_revocation = _delegated_access_service().revoke_subject_access(
            user_id,
            reason="accountSoftDeleted",
        )
        session_revocation = _auth_session_service().revoke_all_for_user(
            user_id,
            reason="accountSoftDeleted",
        )
        access_revocation = _record_account_access_revocation(
            request_id=str(rights_record.get("id") or ""),
            user_id=user_id,
            deletion=deletion,
            session_revocation=session_revocation,
            delegated_grant_revocation=delegated_grant_revocation,
        )
        rights_summary = _record_account_delete_rights_completion(
            str(rights_record.get("id") or ""),
            request_record=rights_record,
            deletion=deletion,
        )
    return {
        "status": "softDeleted",
        "contractVersion": ACCOUNT_DELETION_CONTRACT_VERSION,
        "deletion": deletion,
        "policy": {
            "dataExportSupported": True,
            "dataExportState": "availableBeforeDeletionOnly",
            "retentionDays": ACCOUNT_DELETION_RETENTION_DAYS,
            "restoreLimit": ACCOUNT_RESTORE_LIMIT,
            "restoreBySamePhone": True,
        },
        "sessionRevocation": session_revocation,
        "delegatedGrantRevocation": delegated_grant_revocation,
        "accessRevocation": access_revocation,
        "rights": rights_summary,
    }


@app.post("/auth/restore")
def restore_account(request: Request, payload: Dict[str, Any]) -> Any:
    if not AUTH_LEGACY_PHONE_LOGIN_ENABLED:
        return _legacy_identity_upgrade_response(request)
    phone = str(payload.get("phone") or "").strip()
    nickname = str(payload.get("nickname") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    user_id = stable_user_id(phone)
    with store.auth_user_operation(user_id):
        user = _restore_soft_deleted_account_or_raise(
            user_id=user_id,
            phone=phone,
            nickname=nickname,
        )
    return {"status": "restored", "user": user}


def _account_purge_server_cutoff() -> str:
    """Provide the only allowed purge clock for the machine-only endpoint."""

    return datetime.now(timezone.utc).isoformat()


@app.post("/auth/purge-expired-deletions")
def purge_expired_account_deletions(_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # Client-supplied cutoffs could permanently delete an otherwise restorable
    # account. The scheduler may supply a body for tracing, but never a clock.
    cutoff = _account_purge_server_cutoff()
    purged = store.purge_expired_deleted_users(cutoff)
    return {
        "status": "purgeScanCompleted",
        "cutoff": cutoff,
        "cutoffSource": "serverClock",
        "purgedCount": len(purged),
        "contractVersion": ACCOUNT_DELETION_CONTRACT_VERSION,
    }


def _optional_password(payload: Dict[str, Any], key: str) -> Optional[str]:
    if key not in payload or payload.get(key) is None:
        return None
    value = str(payload.get(key) or "")
    return value if value else None


def _required_password(payload: Dict[str, Any], key: str, *, min_length: int = 1) -> str:
    value = str(payload.get(key) or "")
    if not value:
        raise HTTPException(status_code=400, detail=f"{key} is required")
    if len(value) < min_length:
        raise HTTPException(status_code=400, detail=f"{key} is too short")
    return value


@app.post("/auth/password")
def change_password(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    old_password = _required_password(payload, "oldPassword")
    new_password = _required_password(payload, "newPassword", min_length=8)

    with store.auth_user_operation(user_id):
        credential = store.get_password_credential(user_id)
        if credential is None:
            raise HTTPException(status_code=409, detail="password credential not configured")
        if not verify_password(old_password, credential):
            raise HTTPException(status_code=401, detail="invalid password")

        store.save_password_credential(user_id, make_password_credential(new_password))
        session_revocation = _auth_session_service().revoke_all_for_user(
            user_id,
            reason="passwordChanged",
        )
    return {
        "status": "changed",
        "userId": user_id,
        "sessionRevocation": session_revocation,
    }


_ALLOWED_PROFILE_GENDERS = {"男", "女", "不便透露"}


def _optional_profile_text(payload: Dict[str, Any], key: str, max_length: int) -> Optional[str]:
    if key not in payload or payload.get(key) is None:
        return None
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{key} is too long")
    return value


def _sanitize_profile_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    nickname = str(payload.get("nickname") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not nickname:
        raise HTTPException(status_code=400, detail="nickname is required")
    if len(nickname) > 24:
        raise HTTPException(status_code=400, detail="nickname is too long")

    gender = _optional_profile_text(payload, "gender", 8)
    if gender is not None and gender not in _ALLOWED_PROFILE_GENDERS:
        raise HTTPException(status_code=400, detail="unsupported gender")

    profile = {
        "userId": user_id,
        "nickname": nickname,
    }
    region = _optional_profile_text(payload, "region", 32)
    avatar_name = _optional_profile_text(payload, "avatarName", 64)
    if gender is not None:
        profile["gender"] = gender
    if region is not None:
        profile["region"] = region
    if avatar_name is not None:
        profile["avatarName"] = avatar_name
    return profile


def _required_text(payload: Dict[str, Any], key: str, max_length: int = 160) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{key} is required")
    return value[:max_length]


def _safe_file_name(value: str) -> str:
    candidate = PurePosixPath(value).name.strip()
    if not candidate or candidate in {".", ".."}:
        return "media.bin"
    return "".join(ch for ch in candidate if ch.isalnum() or ch in {".", "-", "_"}) or "media.bin"


def _safe_object_segment(value: str, fallback: str) -> str:
    segment = "".join(ch for ch in value.strip() if ch.isalnum() or ch in {"-", "_"})
    return segment or fallback


def _archive_media_upload_intent_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        safe_scope = sanitize_archive_item_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    user_id = _required_text(payload, "userId", 96)
    archive_item_id = _required_text(payload, "archiveItemId", 128)
    kind = _required_text(payload, "kind", 32).lower()
    if kind not in ARCHIVE_MEDIA_UPLOAD_LIMITS:
        raise HTTPException(status_code=400, detail=f"unsupported media kind: {kind}")

    file_size_bytes = int(payload.get("fileSizeBytes") or 0)
    if file_size_bytes <= 0:
        raise HTTPException(status_code=400, detail="fileSizeBytes is required")
    max_file_size_bytes = ARCHIVE_MEDIA_UPLOAD_LIMITS[kind]
    if file_size_bytes > max_file_size_bytes:
        raise HTTPException(status_code=413, detail="file too large")

    content_type = _required_text(payload, "contentType", 128)
    if not content_type.startswith(f"{kind}/"):
        raise HTTPException(status_code=400, detail="contentType does not match media kind")

    file_name = _safe_file_name(_required_text(payload, "fileName", 180))
    user_id_segment = _safe_object_segment(user_id, "user")
    archive_item_id_segment = _safe_object_segment(archive_item_id, "archive_item")
    persona_scope = str(safe_scope["personaScope"])
    digital_human_id = str(safe_scope["digitalHumanId"])
    digital_human_id_segment = _safe_object_segment(digital_human_id, "digital_human")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ARCHIVE_MEDIA_UPLOAD_TTL_SECONDS)
    upload_intent_id = f"upload_intent_{archive_item_id}"
    object_key = "/".join(
        [
            user_id_segment,
            persona_scope,
            digital_human_id_segment,
            kind,
            archive_item_id_segment,
            file_name,
        ]
    )

    return {
        "uploadIntentId": upload_intent_id,
        "archiveItemId": archive_item_id,
        "kind": kind,
        "storageProvider": ARCHIVE_MEDIA_UPLOAD_PROVIDER,
        "providerDisplayName": ARCHIVE_MEDIA_UPLOAD_PROVIDER_DISPLAY_NAME,
        "providerMode": ARCHIVE_MEDIA_UPLOAD_PROVIDER_MODE,
        "requiresClientUpload": ARCHIVE_MEDIA_REQUIRES_CLIENT_UPLOAD,
        "uploadURLScheme": ARCHIVE_MEDIA_UPLOAD_URL_SCHEME,
        "realProviderReady": ARCHIVE_MEDIA_REAL_PROVIDER_READY,
        "providerSwitchContractVersion": ARCHIVE_MEDIA_PROVIDER_SWITCH_CONTRACT_VERSION,
        "clientUploadAction": ARCHIVE_MEDIA_CLIENT_UPLOAD_ACTION,
        "objectKey": object_key,
        "uploadURL": f"mock://archive-media/{object_key}",
        "expiresAt": expires_at.isoformat(),
        "expiresInSeconds": ARCHIVE_MEDIA_UPLOAD_TTL_SECONDS,
        "maxFileSizeBytes": max_file_size_bytes,
        "requiredHeaders": {
            "Content-Type": content_type,
            "x-dreamjourney-upload-intent": upload_intent_id,
        },
        "fileSizeBytes": file_size_bytes,
        "fileName": file_name,
        "contentType": content_type,
        "personaScope": persona_scope,
        "digitalHumanId": digital_human_id,
        "metadataOnly": True,
    }


def _safe_voice_profile_id(value: str, user_id: str) -> str:
    candidate = _safe_object_segment(value, "")
    if candidate:
        return candidate[:96]
    return f"voice_profile_{_safe_object_segment(user_id, 'user')}"


def _voice_clone_provider_speaker_id(profile: Dict[str, Any]) -> str:
    provider_speaker_id = str(profile.get("providerSpeakerId") or "").strip()
    if provider_speaker_id:
        return provider_speaker_id
    voice_profile_id = str(profile.get("voiceProfileId") or "").strip()
    if voice_profile_id.startswith("S_"):
        return voice_profile_id
    return ""


def _voice_clone_public_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    public_profile = dict(profile)
    public_profile.pop("providerSpeakerId", None)
    provider_request_id = str(public_profile.pop("providerRequestId", "") or "").strip()
    provider_log_id = str(public_profile.pop("providerLogId", "") or "").strip()
    public_profile.pop("providerMessage", None)
    public_profile.pop("providerErrorReferenceHash", None)
    public_profile.pop("providerMessageHash", None)
    if provider_request_id:
        public_profile["providerRequestIdHash"] = _provider_reference_hash(provider_request_id)
    if provider_log_id:
        public_profile["providerLogIdHash"] = _provider_reference_hash(provider_log_id)
    public_profile.setdefault(
        "providerBindingMode",
        "legacyDirectProviderId"
        if str(public_profile.get("voiceProfileId") or "").startswith("S_")
        else "unassigned",
    )
    public_profile.setdefault("providerSlotManaged", public_profile.get("providerBindingMode") == "exclusiveSlot")
    return public_profile


def _provider_reference_hash(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _provider_public_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    public_payload = dict(payload)
    provider_request_id = str(
        public_payload.pop("providerRequestId", "")
        or public_payload.pop("request_id", "")
        or public_payload.pop("reqid", "")
        or ""
    ).strip()
    provider_log_id = str(
        public_payload.pop("providerLogId", "")
        or public_payload.pop("log_id", "")
        or public_payload.pop("logid", "")
        or ""
    ).strip()
    public_payload.pop("providerMessage", None)
    public_payload.pop("message", None)
    for key in tuple(public_payload):
        normalized_key = "".join(character for character in key.lower() if character.isalnum())
        if normalized_key in {"appkey", "accesstoken", "apptoken", "apikey", "secretkey"}:
            public_payload.pop(key, None)
    if provider_request_id:
        public_payload["providerRequestIdHash"] = _provider_reference_hash(provider_request_id)
    if provider_log_id:
        public_payload["providerLogIdHash"] = _provider_reference_hash(provider_log_id)
    return public_payload


def _voice_clone_provider_error_code(error: Exception) -> str:
    candidate = str(getattr(error, "provider_error_code", "") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", candidate):
        return candidate
    if isinstance(error, VoiceCloneProviderUnavailable):
        return "providerUnavailable"
    return "providerOperationFailed"


def _sanitize_voice_profile_provider_metadata(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Remove legacy raw provider diagnostics before a profile is persisted."""

    sanitized = dict(profile)
    sanitized.pop("providerMessage", None)
    sanitized.pop("providerErrorReferenceHash", None)
    sanitized.pop("providerMessageHash", None)
    return sanitized


def _voice_clone_slot_status(sample_status: str) -> str:
    return {
        "pending": "training",
        "ready": "ready",
        "failed": "failed",
        "disabled": "disabled",
        "deleted": "retired",
    }.get(sample_status, "assigned")


def _update_voice_clone_slot(
    voice_profile_id: str,
    sample_status: str,
    *,
    increment_training_attempts: bool = False,
) -> Optional[Dict[str, Any]]:
    update = getattr(store, "update_voice_clone_slot", None)
    if not callable(update):
        return None
    return update(
        voice_profile_id,
        status=_voice_clone_slot_status(sample_status),
        increment_training_attempts=increment_training_attempts,
    )


def _validate_voice_profile_for_synthesis(user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found for user")
    if str(profile.get("deletionState") or "") == "deleted" or str(profile.get("sampleStatus") or "") == "deleted":
        raise HTTPException(status_code=409, detail="voice profile is deleted")
    if str(profile.get("sampleStatus") or "") != "ready":
        raise HTTPException(status_code=409, detail="voice profile is not ready")
    if not bool(profile.get("isEnabled")):
        raise HTTPException(status_code=409, detail="voice profile is disabled")
    if not bool(profile.get("realCloneProviderReady")):
        raise HTTPException(status_code=409, detail="voice profile provider is not ready")
    persona_scope = str(profile.get("personaScope") or "personal").strip()
    digital_human_id = str(profile.get("digitalHumanId") or user_id).strip()
    if persona_scope != "personal" or digital_human_id != user_id:
        _subject_eligibility_hard_deny(
            HighRiskCapability.CLONED_VOICE,
            SubjectEligibilityReason.FAMILY_SUBJECT,
        )
    stored_eligibility = profile.get("subjectEligibilityDecision")
    if isinstance(stored_eligibility, dict) and stored_eligibility.get("allowed") is not True:
        reason_value = str(stored_eligibility.get("reason") or "")
        try:
            reason = SubjectEligibilityReason(reason_value)
        except ValueError:
            reason = SubjectEligibilityReason.SUBJECT_MISMATCH
        _subject_eligibility_hard_deny(HighRiskCapability.CLONED_VOICE, reason)
    if bool(profile.get("qualityAcceptanceRequired", True)):
        raise HTTPException(status_code=409, detail="voice profile quality acceptance is required")
    provider_speaker_id = _voice_clone_provider_speaker_id(profile)
    if not provider_speaker_id:
        raise HTTPException(status_code=409, detail="voice profile provider binding is missing")
    return profile


def _sanitize_voice_profile_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _required_text(payload, "userId", 96)
    privacy_metadata = payload.get("privacyMetadata") or {}
    if not isinstance(privacy_metadata, dict) or privacy_metadata.get("scope") not in {"generationAllowed", "familyCircle"}:
        raise HTTPException(status_code=403, detail="voice clone profile requires syncable authorization scope")

    authorization_confirmed = bool(payload.get("authorizationConfirmed"))
    if not authorization_confirmed:
        raise HTTPException(status_code=403, detail="authorizationConfirmed is required")

    sample_status = str(payload.get("sampleStatus") or "notProvided").strip()
    if sample_status not in VOICE_CLONE_SAMPLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"unsupported sampleStatus: {sample_status}")

    voice_profile_id = _safe_voice_profile_id(str(payload.get("voiceProfileId") or ""), user_id)
    persona_scope = str(payload.get("personaScope") or "personal").strip()
    if persona_scope not in {"personal", "family"}:
        raise HTTPException(status_code=400, detail="unsupported personaScope")
    digital_human_id = str(payload.get("digitalHumanId") or user_id).strip() or user_id
    if persona_scope != "personal" or digital_human_id != user_id:
        _subject_eligibility_hard_deny(
            HighRiskCapability.CLONED_VOICE,
            SubjectEligibilityReason.FAMILY_SUBJECT,
        )
    try:
        sample_count = int(payload.get("sampleCount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="sampleCount must be an integer")
    sample_count = max(0, min(sample_count, 20))

    now = datetime.now(timezone.utc).isoformat()
    provider = VoiceCloneProviderFactory(settings).make()
    provider_mode = provider.provider_mode
    provider_result: Dict[str, Any] = {}
    audio_base64 = str(payload.get("audioBase64") or "").strip()
    eligibility_decision = _evaluate_subject_eligibility_payload(
        payload,
        HighRiskCapability.CLONED_VOICE,
        required=provider.is_configured and bool(audio_base64),
    )
    existing_profile = store.get_voice_profile(user_id, voice_profile_id) or {}
    provider_speaker_id = _voice_clone_provider_speaker_id(existing_profile)
    provider_binding_mode = str(existing_profile.get("providerBindingMode") or "unassigned")
    provider_slot_state = str(existing_profile.get("providerSlotState") or "")
    if not existing_profile and voice_profile_id.startswith("S_"):
        provider_speaker_id = voice_profile_id
        provider_binding_mode = "legacyDirectProviderId"
    if provider.is_configured and audio_base64:
        if uses_voice_clone_speaker_pool(settings):
            provider_speaker_ids = configured_voice_clone_speaker_ids(settings)
            slot = store.allocate_voice_clone_slot(
                provider_speaker_ids,
                user_id=user_id,
                voice_profile_id=voice_profile_id,
                persona_scope=persona_scope,
                digital_human_id=digital_human_id,
            )
            if slot is None:
                raise HTTPException(
                    status_code=409,
                    detail="voice clone speaker slot capacity exhausted; provision or release a provider slot",
                )
            provider_speaker_id = str(slot.get("providerSpeakerId") or "").strip()
            provider_binding_mode = "exclusiveSlot"
            provider_slot_state = "training"
            _update_voice_clone_slot(voice_profile_id, "pending", increment_training_attempts=True)
        else:
            provider_speaker_id = voice_profile_id
            provider_binding_mode = "customSpeakerId"
        try:
            provider_result = provider.submit_training(
                voice_profile_id=provider_speaker_id,
                audio_base64=audio_base64,
                audio_format=str(payload.get("audioFormat") or "wav").strip() or "wav",
                language=int(payload.get("language") or 0),
            )
            sample_status = str(provider_result.get("sampleStatus") or sample_status)
            if sample_status not in VOICE_CLONE_SAMPLE_STATUSES:
                sample_status = "pending"
        except (ValueError, VoiceCloneProviderUnavailable) as exc:
            provider_result = {
                "providerStatus": "failed",
                "providerErrorCode": _voice_clone_provider_error_code(exc),
                "sampleStatus": "failed",
            }
            provider_request_id = str(getattr(exc, "provider_request_id", "") or "").strip()
            provider_log_id = str(getattr(exc, "provider_log_id", "") or "").strip()
            if provider_request_id:
                provider_result["providerRequestId"] = provider_request_id
            if provider_log_id:
                provider_result["providerLogId"] = provider_log_id
            sample_status = "failed"
        slot_update = _update_voice_clone_slot(voice_profile_id, sample_status)
        if slot_update is not None:
            provider_slot_state = str(slot_update.get("status") or provider_slot_state)

    profile = {
        "id": voice_profile_id,
        "voiceProfileId": voice_profile_id,
        "userId": user_id,
        "personaScope": persona_scope,
        "digitalHumanId": digital_human_id,
        "sampleStatus": sample_status,
        "sampleCount": sample_count,
        "authorizationConfirmed": True,
        "authorizationVersion": str(payload.get("authorizationVersion") or "voice-clone-consent-v1"),
        "authorizationText": str(payload.get("authorizationText") or VOICE_CLONE_AUTHORIZATION_COPY)[:300],
        "authorizationConfirmedAt": str(payload.get("authorizationConfirmedAt") or now),
        "authorizationCopy": VOICE_CLONE_AUTHORIZATION_COPY,
        "providerMode": provider_mode,
        "realCloneProviderReady": provider.is_configured,
        "providerStatus": str(provider_result.get("providerStatus") or ("notSubmitted" if provider.is_configured else "mockOnly")),
        "providerBindingMode": provider_binding_mode,
        "providerSlotManaged": provider_binding_mode == "exclusiveSlot",
        "providerSlotState": provider_slot_state,
        "qualityAcceptanceRequired": True,
        "isEnabled": sample_status == "ready",
        "defaultReleaseVisible": False,
        "contractVersion": VOICE_CLONE_CONTRACT_VERSION,
        "disableContract": VOICE_CLONE_DISABLE_CONTRACT,
        "deleteContract": VOICE_CLONE_DELETE_CONTRACT,
        "updatedAt": now,
        "privacyMetadata": {
            "scope": privacy_metadata.get("scope"),
        },
    }
    if eligibility_decision is not None:
        profile["subjectEligibilityDecision"] = eligibility_decision.model_dump(mode="json")
    if provider_speaker_id:
        profile["providerSpeakerId"] = provider_speaker_id
    provider_request_id = str(provider_result.get("providerRequestId") or "").strip()
    provider_log_id = str(provider_result.get("providerLogId") or "").strip()
    provider_error_code = str(provider_result.get("providerErrorCode") or "").strip()
    if provider_request_id:
        profile["providerRequestId"] = provider_request_id
    if provider_log_id:
        profile["providerLogId"] = provider_log_id[:160]
    if provider_error_code:
        profile["providerErrorCode"] = provider_error_code[:128]
    if "createdAt" in payload:
        profile["createdAt"] = str(payload.get("createdAt") or now)
    else:
        profile["createdAt"] = now
    return _sanitize_voice_profile_provider_metadata(profile)


def _sanitize_family_member_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _required_text(payload, "userId", 96)
    safe_payload = dict(payload)
    persona_scope = str(safe_payload.get("personaScope") or "family").strip()
    if persona_scope != "family":
        raise HTTPException(status_code=400, detail="family member personaScope must be family")

    digital_human_id = str(safe_payload.get("digitalHumanId") or "family_default").strip()
    if not digital_human_id:
        digital_human_id = "family_default"
    digital_human_mode = str(safe_payload.get("digitalHumanMode") or "sunlight").strip()
    if digital_human_mode not in DIGITAL_HUMAN_MODE_LABELS:
        raise HTTPException(status_code=400, detail=f"unsupported digitalHumanMode: {digital_human_mode}")

    safe_payload["userId"] = user_id
    safe_payload["personaScope"] = "family"
    safe_payload["digitalHumanId"] = digital_human_id
    safe_payload["digitalHumanMode"] = digital_human_mode
    safe_payload["digitalHumanModeLabel"] = DIGITAL_HUMAN_MODE_LABELS[digital_human_mode]
    safe_payload["backendContractMode"] = FAMILY_PERSONA_CONTRACT_MODE
    safe_payload["familyPersonaContractVersion"] = FAMILY_PERSONA_CONTRACT_VERSION
    safe_payload["defaultReleaseVisible"] = False
    return safe_payload


def _voice_profile_lifecycle_update(profile: Dict[str, Any], sample_status: str) -> Dict[str, Any]:
    updated = _sanitize_voice_profile_provider_metadata(profile)
    now = datetime.now(timezone.utc).isoformat()
    updated["sampleStatus"] = sample_status
    updated["isEnabled"] = False
    updated["updatedAt"] = now
    if sample_status == "disabled":
        updated["disabledAt"] = now
    if sample_status == "deleted":
        updated["deletedAt"] = now
        updated["deletionState"] = "deleted"
        updated["sampleCount"] = 0
    return updated


def _voice_profile_refresh_update(profile: Dict[str, Any]) -> Dict[str, Any]:
    provider = VoiceCloneProviderFactory(settings).make()
    if not provider.is_configured:
        raise HTTPException(status_code=503, detail="voice clone provider is not configured")
    voice_profile_id = str(profile.get("voiceProfileId") or "").strip()
    provider_speaker_id = _voice_clone_provider_speaker_id(profile)
    if not provider_speaker_id:
        raise HTTPException(status_code=409, detail="voice profile provider binding is missing")
    try:
        provider_result = provider.query_status(voice_profile_id=provider_speaker_id)
    except (ValueError, VoiceCloneProviderUnavailable) as exc:
        provider_result = {
            "providerStatus": "failed",
            "providerErrorCode": _voice_clone_provider_error_code(exc),
            "sampleStatus": "failed",
        }
        provider_request_id = str(getattr(exc, "provider_request_id", "") or "").strip()
        provider_log_id = str(getattr(exc, "provider_log_id", "") or "").strip()
        if provider_request_id:
            provider_result["providerRequestId"] = provider_request_id
        if provider_log_id:
            provider_result["providerLogId"] = provider_log_id
    sample_status = str(provider_result.get("sampleStatus") or profile.get("sampleStatus") or "pending")
    if sample_status not in VOICE_CLONE_SAMPLE_STATUSES:
        sample_status = "pending"
    updated = _sanitize_voice_profile_provider_metadata(profile)
    updated["sampleStatus"] = sample_status
    updated["isEnabled"] = sample_status == "ready"
    updated["providerMode"] = provider.provider_mode
    updated["realCloneProviderReady"] = provider.is_configured
    updated["providerStatus"] = str(provider_result.get("providerStatus") or updated.get("providerStatus") or "unknown")
    provider_request_id = str(provider_result.get("providerRequestId") or "").strip()
    provider_log_id = str(provider_result.get("providerLogId") or "").strip()
    provider_error_code = str(provider_result.get("providerErrorCode") or "").strip()
    if provider_request_id:
        updated["providerRequestId"] = provider_request_id
    if provider_log_id:
        updated["providerLogId"] = provider_log_id[:160]
    if provider_error_code:
        updated["providerErrorCode"] = provider_error_code[:128]
    updated["updatedAt"] = datetime.now(timezone.utc).isoformat()
    slot_update = _update_voice_clone_slot(voice_profile_id, sample_status)
    if slot_update is not None:
        updated["providerSlotState"] = str(slot_update.get("status") or "")
    return updated


def _voice_profile_quality_acceptance_update(profile: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    if profile.get("sampleStatus") != "ready" or not bool(profile.get("isEnabled")) or not bool(profile.get("realCloneProviderReady")):
        raise HTTPException(status_code=409, detail="voice profile is not ready for quality acceptance")

    now = datetime.now(timezone.utc).isoformat()
    updated = dict(profile)
    updated["qualityAcceptanceRequired"] = False
    updated["qualityAcceptanceState"] = "accepted"
    updated["qualityAcceptedAt"] = now
    updated["qualityAcceptedBy"] = user_id
    updated["updatedAt"] = now
    return updated


@app.post("/profile")
def save_profile(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, owned_payload = _principal_owned_payload(request, payload)
    profile = _sanitize_profile_payload(owned_payload)
    saved = store.save_profile(profile["userId"], profile)
    return {"status": "saved", "profile": saved}


@app.get("/profile/{user_id}")
def get_profile(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    profile = store.get_profile(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"userId": user_id, "profile": profile}


@app.get("/config/runtime")
def runtime_config() -> Dict[str, Any]:
    return RuntimeConfigService(settings).public_config()


@app.post("/context/build")
def build_context(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, payload = _principal_owned_payload(request, payload)
    try:
        packet = ContextPacketBuilder(store, settings).build(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "built", "contextPacket": packet}


@app.post("/voice/realtime-token")
def realtime_token(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    try:
        return TokenService(settings).realtime_config(user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/voice/profiles")
def save_voice_profile(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, payload = _principal_owned_payload(request, payload)
    profile = _sanitize_voice_profile_payload(payload)
    try:
        saved = store.save_voice_profile(profile["userId"], profile)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "saved", "profile": _voice_clone_public_profile(saved)}


@app.get("/voice/profiles/{user_id}")
def list_voice_profiles(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    return {
        "userId": user_id,
        "profiles": [_voice_clone_public_profile(profile) for profile in store.list_voice_profiles(user_id)],
    }


@app.post("/voice/profiles/{user_id}/{voice_profile_id}/disable")
def disable_voice_profile(request: Request, user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    disabled = _voice_profile_lifecycle_update(profile, "disabled")
    slot_update = _update_voice_clone_slot(voice_profile_id, "disabled")
    if slot_update is not None:
        disabled["providerSlotState"] = str(slot_update.get("status") or "disabled")
    saved = store.save_voice_profile(user_id, disabled)
    return {"status": "disabled", "profile": _voice_clone_public_profile(saved)}


@app.post("/voice/profiles/{user_id}/{voice_profile_id}/refresh")
def refresh_voice_profile(request: Request, user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    refreshed = _voice_profile_refresh_update(profile)
    saved = store.save_voice_profile(user_id, refreshed)
    return {"status": "refreshed", "profile": _voice_clone_public_profile(saved)}


@app.post("/voice/profiles/{user_id}/{voice_profile_id}/quality-acceptance")
def accept_voice_profile_quality(request: Request, user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    accepted = _voice_profile_quality_acceptance_update(profile, user_id)
    saved = store.save_voice_profile(user_id, accepted)
    _update_voice_clone_slot(voice_profile_id, "ready")
    return {"status": "accepted", "profile": _voice_clone_public_profile(saved)}


@app.post("/voice/synthesis")
def synthesize_voice_profile(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    voice_profile_id = _required_text(payload, "voiceProfileId", 96)
    _evaluate_subject_eligibility_payload(
        payload,
        HighRiskCapability.CLONED_VOICE,
        required=True,
    )
    profile = _validate_voice_profile_for_synthesis(user_id, voice_profile_id)
    provider_speaker_id = _voice_clone_provider_speaker_id(profile)
    text = _required_text(payload, "text", 4000)
    audio_format = str(payload.get("format") or "mp3").strip() or "mp3"
    sample_rate = int(payload.get("sampleRate") or 24000)
    speech_rate = int(payload.get("speechRate") or -10)
    loudness_rate = int(payload.get("loudnessRate") or 10)
    output_mode = str(payload.get("outputMode") or "default").strip() or "default"
    provider_audio_format = audio_format
    provider_sample_rate = sample_rate
    if output_mode == "tencentAudioDrive":
        provider_audio_format = "wav"
        provider_sample_rate = TencentAudioDrivePCMAdapter.sample_rate

    provider = VoiceCloneTTSProviderFactory(settings).make()
    if not provider.is_configured:
        raise HTTPException(status_code=503, detail="voice clone TTS provider is not configured")
    provider_call_started = time.monotonic()
    try:
        result = provider.synthesize(
            text=text,
            user_id=user_id,
            voice_profile_id=provider_speaker_id,
            audio_format=provider_audio_format,
            sample_rate=provider_sample_rate,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
        )
    except ValueError as exc:
        _record_provider_cost_attempt(
            request,
            provider="volcengineVoiceClone",
            capability="voiceCloneSynthesis",
            unit_type="character",
            units=len(text),
            state="failed",
            reason="providerCallFailed",
            started_at=provider_call_started,
            provider_request_key=getattr(exc, "provider_request_id", None),
        )
        detail = {
            "code": "voice_synthesis_provider_failed",
            "message": "voice synthesis provider failed",
            "retryable": True,
        }
        provider_request_hash = _provider_reference_hash(getattr(exc, "provider_request_id", ""))
        provider_log_hash = _provider_reference_hash(getattr(exc, "provider_log_id", ""))
        if provider_request_hash:
            detail["providerRequestIdHash"] = provider_request_hash
        if provider_log_hash:
            detail["providerLogIdHash"] = provider_log_hash
        raise HTTPException(status_code=502, detail=detail) from exc

    provider_request_id = str(result.get("providerRequestId") or "").strip()
    _record_provider_cost_attempt(
        request,
        provider="volcengineVoiceClone",
        capability="voiceCloneSynthesis",
        unit_type="character",
        units=len(text),
        state="succeeded",
        reason="providerUsageObserved",
        started_at=provider_call_started,
        provider_request_key=provider_request_id or None,
    )
    audio_payload = {
        "encoding": "base64",
        "format": result["audioFormat"],
        "data": result["audioBase64"],
        "byteCount": result["byteCount"],
    }
    if output_mode == "tencentAudioDrive":
        audio_payload = TencentAudioDrivePCMAdapter().adapt(
            audio_base64=result["audioBase64"],
            audio_format=result["audioFormat"],
        )

    response = {
        "status": "synthesized",
        "voiceProfileId": voice_profile_id,
        "providerMode": result["providerMode"],
        "providerBindingMode": str(profile.get("providerBindingMode") or (
            "legacyDirectProviderId" if voice_profile_id.startswith("S_") else "customSpeakerId"
        )),
        "visemeTimeline": result.get("visemeTimeline"),
        "audio": audio_payload,
    }
    provider_log_id = str(result.get("providerLogId") or "").strip()
    if provider_request_id:
        response["providerRequestIdHash"] = _provider_reference_hash(provider_request_id)
    if provider_log_id:
        response["providerLogIdHash"] = _provider_reference_hash(provider_log_id)
    if output_mode != "default":
        response["outputMode"] = output_mode
    return response


@app.delete("/voice/profiles/{user_id}/{voice_profile_id}")
def delete_voice_profile(request: Request, user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    deleted = _voice_profile_lifecycle_update(profile, "deleted")
    slot_update = _update_voice_clone_slot(voice_profile_id, "deleted")
    if slot_update is not None:
        deleted["providerSlotState"] = str(slot_update.get("status") or "retired")
    saved = store.save_voice_profile(user_id, deleted)
    return {"status": "deleted", "profile": _voice_clone_public_profile(saved)}


@app.post("/tts")
def tts(request: Request, payload: Dict[str, Any], dryRun: bool = False) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    text = str(payload.get("text") or "").strip()
    voice_type = payload.get("voiceType")
    encoding = str(payload.get("encoding") or "wav")
    speed_ratio = float(payload.get("speedRatio") or 1.0)
    proxy = VolcTTSProxy(settings)
    provider_call_started = time.monotonic()
    provider_call_succeeded = False
    try:
        if not dryRun:
            result = proxy.request_tts(
                text=text,
                user_id=user_id,
                voice_type=voice_type,
                encoding=encoding,
                speed_ratio=speed_ratio,
            )
            provider_call_succeeded = True
            _record_provider_cost_attempt(
                request,
                provider="volcengine",
                capability="legacyTts",
                unit_type="character",
                units=len(text),
                state="succeeded",
                reason="providerUsageObserved",
                started_at=provider_call_started,
                provider_request_key=str(result.get("reqid") or "").strip() or None,
            )
            public_payload = _provider_public_payload(result)
        else:
            dry_run_report = proxy.dry_run_report(
                text=text,
                user_id=user_id,
                voice_type=voice_type,
                encoding=encoding,
                speed_ratio=speed_ratio,
            )
    except ValueError as exc:
        if not dryRun and not provider_call_succeeded:
            _record_provider_cost_attempt(
                request,
                provider="volcengine",
                capability="legacyTts",
                unit_type="character",
                units=len(text),
                state="failed",
                reason="providerCallFailed",
                started_at=provider_call_started,
            )
        raise HTTPException(
            status_code=400,
            detail=provider_error_detail(
                code="ttsRequestInvalid",
                provider="volcengine",
                capability="legacyTts",
                retryable=False,
                configured=bool(settings.volcengine_api_key and settings.volcengine_voice_type),
            ),
        ) from exc

    if not dryRun:
        return public_payload

    return {
        "provider": "volcengine",
        "capability": "legacyTts",
        "dryRun": dry_run_report,
        "note": "dryRun=true returns allowlisted metadata without calling the provider.",
    }


@app.get("/maps/district")
def amap_district(request: Request, keyword: str, dryRun: bool = False) -> Dict[str, Any]:
    provider_call_started = time.monotonic()
    try:
        proxy = AMapDistrictProxy(settings)
        if not dryRun:
            result = proxy.request_district(keyword=keyword)
            _record_provider_cost_attempt(
                request,
                provider="amap",
                capability="districtLookup",
                unit_type="request",
                units=1,
                state="succeeded",
                reason="providerUsageObserved",
                started_at=provider_call_started,
            )
            return result
        dry_run_report = proxy.dry_run_report(keyword=keyword)
    except ValueError as exc:
        if not dryRun:
            _record_provider_cost_attempt(
                request,
                provider="amap",
                capability="districtLookup",
                unit_type="request",
                units=1,
                state="failed",
                reason="providerCallFailed",
                started_at=provider_call_started,
            )
        raise HTTPException(
            status_code=400,
            detail=provider_error_detail(
                code="districtLookupRequestInvalid",
                provider="amap",
                capability="districtLookup",
                retryable=False,
                configured=bool(settings.amap_web_service_key),
            ),
        ) from exc
    return {
        "provider": "amap",
        "capability": "districtLookup",
        "dryRun": dry_run_report,
        "note": "dryRun=true returns allowlisted metadata without calling the provider.",
    }


@app.post("/kb/sync")
def sync_kb(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    graph = payload.get("graph") or {}
    has_base_revision = payload.get("baseRevision") is not None
    base_revision = payload.get("baseRevision")
    operation_id = str(payload.get("operationId") or "").strip()
    has_client_operation_id = bool(operation_id)
    if not isinstance(graph, dict):
        raise HTTPException(status_code=400, detail="graph must be an object")
    if has_base_revision and (
        isinstance(base_revision, bool)
        or not isinstance(base_revision, int)
        or base_revision < 0
    ):
        raise HTTPException(status_code=400, detail="baseRevision must be a non-negative integer")
    if not operation_id:
        operation_id = f"legacy-sync-{secrets.token_hex(16)}"
    filtered = filter_syncable_graph(graph)
    compatibility_noop = False
    try:
        snapshot = store.apply_kb_mutation(
            user_id,
            filtered,
            operation_id=operation_id,
            base_revision=base_revision if has_base_revision else 0,
            operation_kind=KB_OPERATION_SYNC,
            operation_schema_version=1,
            operation_payload=filtered,
            allow_revision_noop=not has_base_revision,
            record_compatibility_noop_receipt=has_client_operation_id,
        )
    except KnowledgeOperationPayloadConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledgeOperationPayloadConflict",
                "operationId": operation_id,
            },
        ) from exc
    except KnowledgeRevisionConflict as exc:
        if has_base_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "knowledgeRevisionConflict",
                    "expectedRevision": exc.expected_revision,
                    "currentRevision": exc.current_revision,
                },
            )
        current = store.get_kb_snapshot_record(user_id)
        if current is None:
            raise HTTPException(status_code=409, detail="knowledge snapshot changed during sync")
        snapshot = {
            **current,
            "operationId": operation_id,
            "duplicate": False,
        }
        compatibility_noop = True
    compatibility_noop = bool(snapshot.get("compatibilityNoOp", compatibility_noop))
    response_graph = snapshot["graph"]
    duplicate = bool(snapshot.get("duplicate"))
    response = {
        "status": "synced",
        "userId": user_id,
        "operationId": operation_id,
        "updatedAt": snapshot["updatedAt"],
        "revision": snapshot["revision"],
        "applied": not duplicate and not compatibility_noop,
        "duplicate": duplicate,
        "operationPayloadVerified": bool(snapshot.get("operationPayloadVerified")),
        "compatibilityNoOp": compatibility_noop,
        "counts": {
            "people": len(response_graph.get("people", [])),
            "places": len(response_graph.get("places", [])),
            "events": len(response_graph.get("events", [])),
            "facts": len(response_graph.get("facts", [])),
        },
    }
    if bool(snapshot.get("receiptCompacted")):
        response["receiptCompacted"] = True
        response["originalRevision"] = snapshot["originalRevision"]
    return response


@app.get("/kb/snapshot/{user_id}")
def kb_snapshot(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    snapshot = store.get_kb_snapshot_record(user_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return {
        "userId": user_id,
        "graph": snapshot["graph"],
        "revision": snapshot["revision"],
        "updatedAt": snapshot["updatedAt"],
    }


@app.post("/kb/mutations")
def mutate_kb(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    operation_id = str(payload.get("operationId") or "").strip()
    base_revision = payload.get("baseRevision")
    mutation_schema_version = payload.get("mutationSchemaVersion", 1)
    if not operation_id:
        raise HTTPException(status_code=400, detail="operationId is required")
    if (
        isinstance(mutation_schema_version, bool)
        or not isinstance(mutation_schema_version, int)
        or mutation_schema_version not in (1, 2)
    ):
        raise HTTPException(status_code=400, detail="mutationSchemaVersion must be 1 or 2")
    if base_revision is not None and (
        isinstance(base_revision, bool)
        or not isinstance(base_revision, int)
        or base_revision < 0
    ):
        raise HTTPException(status_code=400, detail="baseRevision must be a non-negative integer")

    mutation = None
    if mutation_schema_version == 2:
        mutation = {
            "upserts": payload.get("upserts", {}),
            "tombstones": payload.get("tombstones", []),
        }
        graph = None
    else:
        graph = payload.get("graph")
        if not isinstance(graph, dict):
            raise HTTPException(status_code=400, detail="graph must be an object")
        graph = filter_syncable_graph(graph)

    try:
        result = store.apply_kb_mutation(
            user_id,
            graph,
            operation_id=operation_id,
            base_revision=base_revision,
            mutation=mutation,
            operation_kind=KB_OPERATION_MUTATION,
            operation_schema_version=mutation_schema_version,
        )
    except KnowledgeOperationPayloadConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledgeOperationPayloadConflict",
                "operationId": operation_id,
            },
        ) from exc
    except KnowledgeRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledgeRevisionConflict",
                "expectedRevision": exc.expected_revision,
                "currentRevision": exc.current_revision,
            },
        )
    except KnowledgeMutationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    response = {
        "status": "duplicate" if result["duplicate"] else "applied",
        "userId": user_id,
        "operationId": operation_id,
        "revision": result["revision"],
        "updatedAt": result["updatedAt"],
        "duplicate": result["duplicate"],
        "operationPayloadVerified": bool(result.get("operationPayloadVerified")),
    }
    if result.get("mutationSchemaVersion") == 2:
        response.update(
            {
                "graph": result["graph"],
                "mutationSchemaVersion": 2,
                "mutation": result["mutation"],
            }
        )
    if bool(result.get("receiptCompacted")):
        response["receiptCompacted"] = True
        response["originalRevision"] = result["originalRevision"]
    return response


@app.post("/kb/governance/actions")
def govern_kb(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    governance_schema_version = payload.get("governanceSchemaVersion")
    operation_id = str(payload.get("operationId") or "").strip()
    base_revision = payload.get("baseRevision")
    if (
        isinstance(governance_schema_version, bool)
        or not isinstance(governance_schema_version, int)
        or governance_schema_version != GOVERNANCE_SCHEMA_VERSION
    ):
        raise HTTPException(status_code=400, detail="governanceSchemaVersion must be 1")
    if not operation_id:
        raise HTTPException(status_code=400, detail="operationId is required")
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0:
        raise HTTPException(
            status_code=400,
            detail="baseRevision is required and must be a non-negative integer",
        )

    try:
        normalized_action = normalize_knowledge_governance_action(payload.get("action"))
        result = store.get_kb_operation_replay(
            user_id,
            operation_id,
            operation_kind=KB_OPERATION_GOVERNANCE,
            operation_schema_version=governance_schema_version,
            operation_payload=normalized_action,
        )
        if result is None:
            snapshot = store.get_kb_snapshot_record(user_id)
            governance = build_knowledge_governance_mutation(
                user_id=user_id,
                operation_id=operation_id,
                base_revision=base_revision,
                action=normalized_action,
                snapshot=snapshot,
            )
            result = store.apply_kb_mutation(
                user_id,
                None,
                operation_id=operation_id,
                base_revision=base_revision,
                mutation={
                    "upserts": governance["upserts"],
                    "tombstones": governance["tombstones"],
                },
                operation_kind=KB_OPERATION_GOVERNANCE,
                operation_schema_version=governance_schema_version,
                operation_payload=normalized_action,
                receipt_governance_summary=governance["summary"],
            )
    except KnowledgeGovernanceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (KnowledgeGovernanceValidationError, KnowledgeMutationValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KnowledgeRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledgeRevisionConflict",
                "expectedRevision": exc.expected_revision,
                "currentRevision": exc.current_revision,
            },
        )
    except KnowledgeOperationPayloadConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledgeOperationPayloadConflict",
                "operationId": operation_id,
            },
        ) from exc

    summary = result.get("governanceSummary") or summarize_knowledge_governance_mutation(
        result.get("mutation"),
        operation_id=operation_id,
    )
    if summary is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledgeGovernanceOperationConflict"},
        )
    response = {
        "governanceSchemaVersion": GOVERNANCE_SCHEMA_VERSION,
        "mutationSchemaVersion": 2,
        "status": "duplicate" if result["duplicate"] else "applied",
        "userId": user_id,
        "operationId": operation_id,
        "revision": result["revision"],
        "updatedAt": result["updatedAt"],
        "duplicate": result["duplicate"],
        "operationPayloadVerified": bool(result.get("operationPayloadVerified")),
        "graph": result["graph"],
        "mutation": result["mutation"],
        "summary": summary,
    }
    if bool(result.get("receiptCompacted")):
        response["receiptCompacted"] = True
        response["originalRevision"] = result["originalRevision"]
    return response


@app.get("/kb/changes/{user_id}")
def kb_changes(
    request: Request,
    user_id: str,
    sinceRevision: int = 0,
    targetRevision: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    if sinceRevision < 0:
        raise HTTPException(status_code=400, detail="sinceRevision must be non-negative")
    if limit is not None and (limit < 1 or limit > 100):
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    if targetRevision is not None and (
        targetRevision < 0 or sinceRevision > targetRevision
    ):
        raise HTTPException(
            status_code=400,
            detail="revisions must satisfy 0 <= sinceRevision <= targetRevision",
        )

    page = store.get_kb_change_page(
        user_id,
        sinceRevision,
        through_revision=targetRevision if limit is not None else None,
        limit=limit,
    )
    current_revision = int(page["currentRevision"])
    if targetRevision is not None and targetRevision > current_revision:
        raise HTTPException(
            status_code=400,
            detail="targetRevision must not exceed current revision",
        )
    minimum_since_revision = int(page["minimumSinceRevision"])
    if sinceRevision < minimum_since_revision:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "knowledgeChangeFeedCompacted",
                "message": "requested revision is no longer retained",
                "userId": user_id,
                "sinceRevision": sinceRevision,
                "minimumSinceRevision": minimum_since_revision,
                "currentRevision": current_revision,
                "snapshotRevision": current_revision,
            },
        )

    if limit is None:
        return {
            "userId": user_id,
            "sinceRevision": sinceRevision,
            "currentRevision": current_revision,
            "changes": page["changes"],
        }

    target_revision = current_revision if targetRevision is None else targetRevision
    if not 0 <= sinceRevision <= target_revision <= current_revision:
        raise HTTPException(
            status_code=400,
            detail="revisions must satisfy 0 <= sinceRevision <= targetRevision <= current revision",
        )

    changes = page["changes"]
    next_since_revision = (
        int(changes[-1]["revision"])
        if changes
        else sinceRevision
    )
    has_more = next_since_revision < target_revision
    if has_more and not changes:
        raise HTTPException(status_code=500, detail="knowledge change feed has a revision gap")

    return {
        "userId": user_id,
        "sinceRevision": sinceRevision,
        "currentRevision": target_revision,
        "targetRevision": target_revision,
        "nextSinceRevision": next_since_revision,
        "hasMore": has_more,
        "pageLimit": limit,
        "changes": changes,
    }


@app.get("/kb/source-ref-audit/{user_id}")
def kb_source_ref_audit(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    return {
        "userId": user_id,
        **audit_knowledge_source_refs(store.get_kb_snapshot_record(user_id)),
    }


@app.post("/kb/extract")
def extract_kb(request: Request, payload: Dict[str, Any], dryRun: bool = False) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    try:
        extraction_input = normalize_knowledge_extraction_input(payload)
    except KnowledgeExtractionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    existing_summary = str(payload.get("existingSummary") or "").strip()

    try:
        safe_context = sanitize_knowledge_extraction_context(
            sanitize_knowledge_extraction_payload(payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    proxy = DeepSeekKnowledgeExtractionProxy(settings)
    provider_call_started = time.monotonic()
    provider_call_succeeded = False
    try:
        if not dryRun:
            extraction = proxy.request_extraction(
                transcript=extraction_input.transcript,
                existing_summary=existing_summary,
                turns=extraction_input.turns,
                source_policy=extraction_input.source_policy,
            )
            provider_call_succeeded = True
            _record_provider_cost_attempt(
                request,
                provider="deepseek",
                capability="kbExtract",
                unit_type="request",
                units=1,
                state="succeeded",
                reason="providerUsageObserved",
                started_at=provider_call_started,
            )
            extraction, evidence_policy = filter_extraction_by_evidence(
                extraction,
                extraction_input,
            )
            response = {
                "provider": "deepseek",
                "capability": "kbExtract",
                "userId": user_id,
                "extraction": extraction,
                "evidencePolicy": evidence_policy,
                "context": safe_context,
            }
            if extraction_input.schema_version == 2:
                response["extractionSchemaVersion"] = 2
                try:
                    response["mutationProposal"] = build_knowledge_mutation_proposal(
                        user_id=user_id,
                        persona_scope=payload.get("personaScope", "personal"),
                        digital_human_id=payload.get("digitalHumanId"),
                        extraction=extraction,
                        safe_context=safe_context,
                        snapshot=store.get_kb_snapshot_record(user_id),
                    )
                except KnowledgeProposalValidationError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
            return response
        dry_run_report = proxy.dry_run_report(
            transcript=extraction_input.transcript,
            existing_summary=existing_summary,
            turns=extraction_input.turns,
            source_policy=extraction_input.source_policy,
        )
    except ValueError as exc:
        if not dryRun and not provider_call_succeeded:
            _record_provider_cost_attempt(
                request,
                provider="deepseek",
                capability="kbExtract",
                unit_type="request",
                units=1,
                state="failed",
                reason="providerCallFailed",
                started_at=provider_call_started,
            )
        configured = bool(settings.deepseek_api_key)
        raise HTTPException(
            status_code=503 if not configured else 502,
            detail=provider_error_detail(
                code=(
                    "knowledgeExtractionProviderNotConfigured"
                    if not configured
                    else "knowledgeExtractionProviderFailed"
                ),
                provider="deepseek",
                capability="kbExtract",
                retryable=configured,
                configured=configured,
            ),
        ) from exc
    except Exception as exc:
        if not dryRun and not provider_call_succeeded:
            _record_provider_cost_attempt(
                request,
                provider="deepseek",
                capability="kbExtract",
                unit_type="request",
                units=1,
                state="failed",
                reason="providerCallFailed",
                started_at=provider_call_started,
            )
        raise HTTPException(
            status_code=502,
            detail=provider_error_detail(
                code="knowledgeExtractionProviderFailed",
                provider="deepseek",
                capability="kbExtract",
                retryable=True,
                configured=bool(settings.deepseek_api_key),
            ),
        ) from exc

    response = {
        "provider": "deepseek",
        "capability": "kbExtract",
        "dryRun": dry_run_report,
        "evidencePolicy": empty_evidence_policy(extraction_input),
        "note": "dryRun=true returns allowlisted metadata without calling the provider.",
    }
    if extraction_input.schema_version == 2:
        response["extractionSchemaVersion"] = 2
    return response


@app.post("/memories")
def create_memory(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    return {"memory": store.add_memory(user_id, payload)}


@app.get("/memories/{user_id}")
def list_memories(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    return {"userId": user_id, "memories": store.list_memories(user_id)}


@app.post("/archive/photos")
def create_archive_photo(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(
        request,
        payload,
        aliases=("ownerId", "ownerUserId", "uploadedByUserId", "uploaderUserId"),
    )
    try:
        safe_payload = sanitize_archive_item_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    try:
        item = store.add_archive_item(user_id, safe_payload)
    except ArchiveItemOwnershipConflict:
        raise HTTPException(
            status_code=409,
            detail={"code": "archiveItemOwnershipConflict"},
        )
    return {"status": "queued", "item": item}


@app.post("/archive/items")
def create_archive_item(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(
        request,
        payload,
        aliases=("ownerId", "ownerUserId", "uploadedByUserId", "uploaderUserId"),
    )
    try:
        safe_payload = sanitize_archive_item_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    try:
        item = store.add_archive_item(user_id, safe_payload)
    except ArchiveItemOwnershipConflict:
        raise HTTPException(
            status_code=409,
            detail={"code": "archiveItemOwnershipConflict"},
        )
    shadow = ArchiveOwnerTruthCompatibilityFacade(store).shadow_archive_item(
        owner_subject_id=user_id,
        item=item,
    )
    return {
        "status": "saved",
        "item": item,
        "ownerTruthShadow": shadow.public_contract(),
    }


@app.post("/archive/media/upload-intent")
def archive_media_upload_intent(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, payload = _principal_owned_payload(
        request,
        payload,
        aliases=("ownerId", "ownerUserId", "uploadedByUserId", "uploaderUserId"),
    )
    return {
        "status": "mock_ready",
        "uploadIntent": _archive_media_upload_intent_payload(payload),
    }


@app.get("/archive/items/{user_id}")
def list_archive_items(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    return {"userId": user_id, "items": store.list_archive_items(user_id)}


@app.get("/archive/time-letters/{owner_user_id}/{item_id}/detail")
def get_time_letter_detail(
    request: Request,
    owner_user_id: str,
    item_id: str,
    viewerUserId: str,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    _require_user_principal_identity(
        request,
        viewerUserId,
        "authenticated user does not match timeLetter viewer",
    )
    # `now` remains a tolerated compatibility query only; client clocks never
    # participate in the server-authoritative opening decision.
    _ = now
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        return time_letter_detail_for_viewer(
            store=store,
            owner_user_id=owner_user_id,
            item_id=item_id,
            viewer_user_id=viewerUserId,
            now_iso=now_iso,
            record_access_receipt=False,
        )
    except TimeLetterAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@app.delete("/archive/items/{user_id}/{item_id}")
def delete_archive_item(
    request: Request,
    user_id: str,
    item_id: str,
    operationId: Optional[str] = None,
    expectedVersion: Optional[int] = None,
) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    operation_id = str(operationId or "").strip() or f"archive-delete-{secrets.token_hex(16)}"
    snapshot = store.get_kb_snapshot_record(user_id)
    base_revision = int((snapshot or {}).get("revision") or 0)
    decided_at = datetime.now(timezone.utc).isoformat()
    governance = None
    try:
        governance = build_knowledge_governance_mutation(
            user_id=user_id,
            operation_id=operation_id,
            base_revision=base_revision,
            action={
                "kind": "deleteSource",
                "sourceRef": {"kind": "memoryArchiveItem", "id": item_id},
                "decidedAt": decided_at,
            },
            snapshot=snapshot,
        )
    except KnowledgeGovernanceNotFound:
        governance = None
    except KnowledgeGovernanceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    mutation = None
    if governance is not None:
        mutation = {
            "upserts": governance["upserts"],
            "tombstones": governance["tombstones"],
        }
    try:
        result = store.delete_archive_item_with_kb_mutation(
            user_id,
            item_id,
            operation_id=operation_id,
            base_revision=base_revision,
            mutation=mutation,
            governance_summary=None if governance is None else governance["summary"],
            expected_version=expectedVersion,
        )
    except ArchiveItemNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ArchiveItemDeletionForbidden as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ResourceVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "resourceVersionConflict",
                "expectedVersion": exc.expected_version,
                "currentVersion": exc.current_version,
            },
        )
    except KnowledgeRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledgeRevisionConflict",
                "expectedRevision": exc.expected_revision,
                "currentRevision": exc.current_revision,
            },
        )
    except KnowledgeMutationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KnowledgeOperationPayloadConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledgeOperationPayloadConflict",
                "operationId": operation_id,
            },
        ) from exc

    summary = None if governance is None else governance["summary"]
    if result["duplicate"]:
        summary = result.get("governanceSummary") or summarize_knowledge_governance_mutation(
            result.get("mutation"),
            operation_id=operation_id,
        ) or summary
        if (
            summary is None
            and result.get("mutation") is not None
            and not bool(result.get("receiptCompacted"))
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "knowledgeGovernanceOperationConflict"},
            )
    response = {
        "status": "duplicate" if result["duplicate"] else "deleted",
        "id": item_id,
        "item": result["item"],
        "operationPayloadVerified": bool(result.get("operationPayloadVerified")),
        "cascade": {
            "action": "deleteSource",
            "operationId": operation_id,
            "revision": result["revision"],
            "duplicate": result["duplicate"],
            "affectedEntityCount": int((summary or {}).get("affectedEntityCount") or 0),
            "sourceMatched": summary is not None,
        },
    }
    if bool(result.get("receiptCompacted")):
        response["receiptCompacted"] = True
        response["originalRevision"] = result["originalRevision"]
    return response


@app.post("/archive/image-analysis")
def archive_image_analysis(request: Request, payload: Dict[str, Any], dryRun: bool = False) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(
        request,
        payload,
        aliases=("ownerId", "ownerUserId", "uploadedByUserId", "uploaderUserId"),
    )
    image_base64 = str(payload.get("imageBase64") or "").strip()
    if not image_base64:
        raise HTTPException(status_code=400, detail="imageBase64 is required")
    archive_item_id = str(payload.get("archiveItemId") or "").strip()
    if not archive_item_id:
        raise HTTPException(status_code=400, detail="archiveItemId is required")

    try:
        safe_context = sanitize_image_analysis_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    provider = ArchiveImageAnalysisProviderFactory(settings).make()
    provider_call_started = time.monotonic()
    try:
        if not dryRun:
            result = provider.request_analysis(image_base64=image_base64)
            _record_provider_cost_attempt(
                request,
                provider="deepseekTextOnly",
                capability="archiveImageAnalysis",
                unit_type="image",
                units=1,
                state="succeeded",
                reason="providerUsageObserved",
                started_at=provider_call_started,
            )
            return result
        dry_run_report = provider.dry_run_report(image_base64=image_base64)
    except ValueError as exc:
        if not dryRun:
            _record_provider_cost_attempt(
                request,
                provider="deepseekTextOnly",
                capability="archiveImageAnalysis",
                unit_type="image",
                units=1,
                state="failed",
                reason="providerCallFailed",
                started_at=provider_call_started,
            )
        if not dryRun:
            return provider.failure_contract(
                provider_error_code="providerUnavailable",
            )
        raise HTTPException(
            status_code=502,
            detail=provider_error_detail(
                code="archiveImageAnalysisProviderFailed",
                provider=provider.provider_id,
                capability="archiveImageAnalysis",
                retryable=True,
                configured=bool(provider.enabled),
            ),
        ) from exc
    except Exception as exc:
        if not dryRun:
            _record_provider_cost_attempt(
                request,
                provider="deepseekTextOnly",
                capability="archiveImageAnalysis",
                unit_type="image",
                units=1,
                state="failed",
                reason="providerCallFailed",
                started_at=provider_call_started,
            )
        if not dryRun:
            return provider.failure_contract(
                provider_error_code="providerUnavailable",
            )
        raise HTTPException(
            status_code=502,
            detail=provider_error_detail(
                code="archiveImageAnalysisProviderFailed",
                provider=provider.provider_id,
                capability="archiveImageAnalysis",
                retryable=True,
                configured=bool(provider.enabled),
            ),
        ) from exc

    return {
        "provider": provider.provider_id,
        "capability": provider.public_capability(),
        "dryRun": dry_run_report,
        "responseContract": provider.response_contract(),
        "note": "dryRun=true returns allowlisted metadata without calling the provider.",
    }


@app.post("/mailbox/letters")
def create_mailbox_letter(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    try:
        safe_payload = sanitize_mailbox_letter_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    item = store.add_mailbox_letter(user_id, safe_payload)
    return {"status": "saved", "item": item}


@app.get("/mailbox/letters/{user_id}")
def list_mailbox_letters(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    return {"userId": user_id, "items": store.list_mailbox_letters(user_id)}


@app.post("/mailbox/letters/{user_id}/{letter_id}/read")
def mark_mailbox_letter_read(
    request: Request,
    user_id: str,
    letter_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    read_at = str(payload.get("readAt") or datetime.now(timezone.utc).isoformat()).strip()
    _parse_iso_datetime(read_at, "readAt")
    item = store.mark_mailbox_letter_read(user_id, letter_id, read_at)
    if item is None:
        raise HTTPException(status_code=404, detail="mailbox letter not found")
    return {"status": "read", "item": item}


@app.post("/mailbox/letters/{user_id}/{letter_id}/archive")
def archive_mailbox_letter(
    request: Request,
    user_id: str,
    letter_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    archived_at = str(payload.get("archivedAt") or datetime.now(timezone.utc).isoformat()).strip()
    _parse_iso_datetime(archived_at, "archivedAt")
    item = store.archive_mailbox_letter(user_id, letter_id, archived_at)
    if item is None:
        raise HTTPException(status_code=404, detail="mailbox letter not found")
    return {"status": "archived", "item": item}


_ALLOWED_ECHO_DELAYED_REPLY_TRIGGERS = {"tenRoundBaseline", "contentSignal"}
_ALLOWED_PUSH_PLATFORMS = {"ios"}
_ALLOWED_PUSH_ENVIRONMENTS = {"sandbox", "production"}
_DEVICE_TOKEN_PATTERN = re.compile(r"^[0-9a-fA-F]{16,256}$")


def _sanitize_push_device_token_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    device_token = str(payload.get("deviceToken") or "").strip().replace(" ", "")
    platform = str(payload.get("platform") or "").strip().lower()
    environment = str(payload.get("environment") or "").strip().lower()
    device_id = str(payload.get("deviceId") or "").strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not device_token:
        raise HTTPException(status_code=400, detail="deviceToken is required")
    if _DEVICE_TOKEN_PATTERN.match(device_token) is None:
        raise HTTPException(status_code=400, detail="deviceToken is invalid")
    if platform not in _ALLOWED_PUSH_PLATFORMS:
        raise HTTPException(status_code=400, detail="unsupported push platform")
    if environment not in _ALLOWED_PUSH_ENVIRONMENTS:
        raise HTTPException(status_code=400, detail="unsupported push environment")
    if len(device_id) > 64:
        raise HTTPException(status_code=400, detail="deviceId is too long")

    token_hash = hashlib.sha256(device_token.lower().encode("utf-8")).hexdigest()
    device_token_id = f"push_{user_id}_{token_hash[:16]}"
    return {
        "id": device_token_id,
        "deviceTokenId": device_token_id,
        "userId": user_id,
        "platform": platform,
        "environment": environment,
        "deviceId": device_id or f"ios_{token_hash[:12]}",
        "deviceTokenHash": token_hash,
        "deviceTokenPreview": f"{device_token[:6].lower()}...{device_token[-4:].lower()}",
        "deliveryProviderState": "pending",
        "containsRawToken": False,
    }


def _sanitize_echo_delayed_reply_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    requested_protocol = str(payload.get("deliveryProtocolVersion") or "").strip()
    if requested_protocol == ECHO_DELAYED_REPLY_SCHEMA_VERSION:
        # The typed V4 path is intentionally server-off until its worker and
        # Provider-effect gates are enabled. Do not silently reinterpret a V4
        # envelope as the legacy scheduled -> readyForProvider protocol.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "echo_delayed_reply_v4_disabled",
                "message": "typed delayed reply completion is not enabled",
                "retryable": False,
            },
        )
    user_id = str(payload.get("userId") or "").strip()
    delayed_reply_id = str(payload.get("delayedReplyId") or "").strip()
    deliver_at = str(payload.get("deliverAt") or "").strip()
    trigger = str(payload.get("trigger") or "").strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not delayed_reply_id:
        raise HTTPException(status_code=400, detail="delayedReplyId is required")
    if not deliver_at:
        raise HTTPException(status_code=400, detail="deliverAt is required")

    try:
        minutes = int(payload.get("minutes"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="minutes is required") from exc
    if minutes < 1:
        raise HTTPException(status_code=400, detail="minutes must be positive")

    if trigger not in _ALLOWED_ECHO_DELAYED_REPLY_TRIGGERS:
        raise HTTPException(status_code=400, detail="unsupported delayed reply trigger")

    return {
        "id": delayed_reply_id,
        "delayedReplyId": delayed_reply_id,
        "userId": user_id,
        "deliverAt": deliver_at,
        "minutes": minutes,
        "trigger": trigger,
        "deliveryState": "scheduled",
        "pushProviderState": "pending",
        "containsRawTranscript": False,
        **({"deviceTokenId": str(payload.get("deviceTokenId")).strip()} if str(payload.get("deviceTokenId") or "").strip() else {}),
    }


def _echo_delayed_reply_field(item: Dict[str, Any], name: str) -> Any:
    metadata = item.get("metadata")
    if item.get(name) is not None:
        return item.get(name)
    return metadata.get(name) if isinstance(metadata, dict) else None


def _echo_delayed_reply_answer_read_contract(
    receipt: Dict[str, Any],
    *,
    user_id: str,
    delayed_reply_id: str,
) -> Dict[str, Any]:
    """Build the Owner-only result for a persisted V4 delayed reply Answer."""

    reply = receipt.get("reply")
    answer = receipt.get("answer")
    if not isinstance(reply, dict):
        raise HTTPException(
            status_code=404,
            detail={"code": "echo_delayed_reply_not_found", "retryable": False},
        )

    protocol = str(_echo_delayed_reply_field(reply, "deliveryProtocolVersion") or "").strip()
    state = str(_echo_delayed_reply_field(reply, "deliveryState") or "scheduled").strip()
    if protocol != ECHO_DELAYED_REPLY_SCHEMA_VERSION:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "echo_delayed_reply_answer_legacy_unavailable",
                "deliveryState": state,
                "retryable": False,
            },
        )
    if state != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "echo_delayed_reply_answer_not_ready",
                "deliveryState": state,
                "retryable": state in {"scheduled", "ready", "generating", "unknown"},
            },
        )
    if not isinstance(answer, dict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "echo_delayed_reply_answer_reconcile_required",
                "deliveryState": state,
                "retryable": True,
            },
        )

    answer_id = str(answer.get("answerId") or answer.get("id") or "").strip()
    expected_answer_id = str(_echo_delayed_reply_field(reply, "responseAnswerId") or "").strip()
    if (
        not answer_id
        or answer_id != expected_answer_id
        or str(answer.get("ownerSubjectId") or answer.get("userId") or "").strip() != user_id
        or str(answer.get("delayedReplyId") or "").strip() != delayed_reply_id
        or str(answer.get("schemaVersion") or "").strip() != ECHO_DELAYED_REPLY_SCHEMA_VERSION
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "echo_delayed_reply_answer_reconcile_required",
                "deliveryState": state,
                "retryable": True,
            },
        )

    body = str(answer.get("body") or "").strip()
    if not body:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "echo_delayed_reply_answer_reconcile_required",
                "deliveryState": state,
                "retryable": True,
            },
        )

    return {
        "status": "completed",
        "userId": user_id,
        "delayedReplyId": delayed_reply_id,
        "answer": {
            "answerId": answer_id,
            "body": body,
            "completedAt": str(answer.get("completedAt") or ""),
            "conversationId": str(answer.get("conversationId") or ""),
            "requestId": str(answer.get("requestId") or ""),
            "replyGeneration": answer.get("replyGeneration"),
            "contextReceipt": {
                "contextHash": str(answer.get("contextHash") or ""),
                "contextVersion": str(answer.get("contextVersion") or ""),
                "citationReceiptHash": str(answer.get("citationReceiptHash") or ""),
                "policyVersion": str(answer.get("policyVersion") or ""),
            },
        },
        "receipt": {
            "deliveryState": state,
            "deliveryProtocolVersion": protocol,
            "mailboxProjectionBodyRedacted": True,
            "sourceAnswerId": answer_id,
        },
    }


def _enforce_echo_delayed_reply_safety(payload: Dict[str, Any]) -> None:
    raw_transcript = str(payload.get("rawTranscript") or "").strip()
    if not raw_transcript:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "echo_delayed_reply_safety_input_required",
                "message": "rawTranscript is required for transient safety classification",
                "persisted": False,
            },
        )
    decision = SAFETY_POLICY.evaluate(raw_transcript)
    if not decision.effects.delayedReplyAllowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "echo_delayed_reply_blocked_by_safety_policy",
                "message": "high-risk expression must use the immediate neutral safety path",
                "safetyDecision": decision.model_dump(mode="json"),
                "retryable": False,
                "persisted": False,
            },
        )


def _parse_iso_datetime(value: str, field_name: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be ISO-8601") from exc


def _sanitize_echo_dispatch_due_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    now = str(payload.get("now") or "").strip()
    if not now:
        raise HTTPException(status_code=400, detail="now is required")
    _parse_iso_datetime(now, "now")

    try:
        limit = int(payload.get("limit", 25))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="limit must be an integer") from exc
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be positive")
    return {"now": now, "limit": min(limit, 100)}


@app.post("/devices/push-token")
def register_push_device_token(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, payload = _principal_owned_payload(request, payload)
    item = _sanitize_push_device_token_payload(payload)
    saved = store.save_push_device_token(item["userId"], item)
    return {"status": "registered", "item": saved}


@app.post("/echo/delayed-replies")
def schedule_echo_delayed_reply(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, payload = _principal_owned_payload(request, payload)
    _enforce_echo_delayed_reply_safety(payload)
    item = _sanitize_echo_delayed_reply_payload(payload)
    saved = store.add_echo_delayed_reply(item["userId"], item)
    return {"status": "scheduled", "item": saved}


@app.post("/echo/delayed-replies/dispatch-due")
def dispatch_due_echo_delayed_replies(payload: Dict[str, Any]) -> Dict[str, Any]:
    contract = _sanitize_echo_dispatch_due_payload(payload)
    items = store.mark_due_echo_delayed_replies_for_dispatch(
        cutoff_iso=contract["now"],
        dispatched_at_iso=contract["now"],
        limit=contract["limit"],
    )
    return {
        "status": "queued",
        "cutoff": contract["now"],
        "itemCount": len(items),
        "items": items,
        "providerDeliveryAttempted": False,
    }


@app.post("/archive/time-letters/dispatch-due")
def dispatch_due_time_letters(payload: Dict[str, Any]) -> Dict[str, Any]:
    contract = _sanitize_echo_dispatch_due_payload(payload)
    return dispatch_due_time_letters_for_store(store, now_iso=contract["now"], limit=contract["limit"])


@app.get("/echo/delayed-replies/{user_id}")
def list_echo_delayed_replies(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    return {"userId": user_id, "items": store.list_echo_delayed_replies(user_id)}


@app.get("/echo/delayed-replies/{user_id}/{delayed_reply_id}/answer")
def get_echo_delayed_reply_answer(
    request: Request,
    user_id: str,
    delayed_reply_id: str,
) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    receipt = store.get_echo_delayed_reply_answer_receipt(user_id, delayed_reply_id)
    if receipt is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "echo_delayed_reply_not_found", "retryable": False},
        )
    return _echo_delayed_reply_answer_read_contract(
        receipt,
        user_id=user_id,
        delayed_reply_id=delayed_reply_id,
    )


@app.post("/family/invite")
def invite_family(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, payload = _principal_owned_payload(request, payload)
    invite_payload = _sanitize_family_member_payload(payload)
    user_id = str(invite_payload["userId"])
    invite_payload.setdefault("accessStatus", "pending")
    invite_payload.setdefault("invitationStatus", "pending")
    invitation_code = str(invite_payload.get("invitationCode") or "").strip()
    if not invitation_code:
        invitation_code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10].upper()
    invite_payload["invitationCode"] = invitation_code
    invite_payload["invitationURL"] = f"dreamjourney://family/invite?code={invitation_code}"
    member = store.add_family_member(user_id, invite_payload)
    member = _delegated_access_service().decorate_family_member(
        owner_subject_id=user_id,
        member=member,
    )
    return {"status": "created", "member": member}


@app.get("/family/members/{user_id}")
def family_members(request: Request, user_id: str) -> Dict[str, Any]:
    user_id = _principal_path_owner(request, user_id)
    return {
        "userId": user_id,
        "members": _delegated_access_service().list_decorated_family_members(user_id),
    }


@app.post("/family/members/{user_id}/{member_id}/accept")
def accept_family_member(request: Request, user_id: str, member_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(payload.get("phone") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    principal_user_id = _request_user_principal_id(request)
    if principal_user_id is not None and principal_user_id != stable_user_id(phone):
        raise HTTPException(status_code=403, detail="authenticated user does not match family invitation")
    member = store.accept_family_member(user_id, member_id, phone=phone)
    if member is None:
        raise HTTPException(status_code=404, detail="family member not found or phone mismatch")
    accepted_subject_id = stable_user_id(phone)
    service = _delegated_access_service()
    service.ensure_relationship_for_member(
        owner_subject_id=user_id,
        member=member,
        accepted_subject_id=accepted_subject_id,
    )
    return {
        "status": "accepted",
        "member": service.decorate_family_member(owner_subject_id=user_id, member=member),
    }


@app.post("/family/invitations/{invitation_code}/accept")
def accept_family_invitation_code(request: Request, invitation_code: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(payload.get("phone") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    _require_user_principal_identity(
        request,
        stable_user_id(phone),
        "authenticated user does not match family invitation",
    )
    member = store.accept_family_invitation_code(invitation_code, phone=phone)
    if member is None:
        raise HTTPException(status_code=404, detail="invitation not found or phone mismatch")
    owner_user_id = str(member.get("ownerUserId") or member.get("userId") or "").strip()
    service = _delegated_access_service()
    service.ensure_relationship_for_member(
        owner_subject_id=owner_user_id,
        member=member,
        accepted_subject_id=_request_user_principal_id(request) or stable_user_id(phone),
    )
    return {
        "status": "accepted",
        "member": service.decorate_family_member(owner_subject_id=owner_user_id, member=member),
    }


@app.post("/family/members/{user_id}/{member_id}/revoke")
def revoke_family_member(request: Request, user_id: str, member_id: str) -> Dict[str, Any]:
    _principal_path_owner(request, user_id)
    raise HTTPException(status_code=409, detail="family member removal is not supported")


@app.post("/family/relationships/{user_id}/{relationship_id}/lifecycle")
def change_family_relationship_lifecycle(
    request: Request,
    user_id: str,
    relationship_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    _require_delegated_access_contract_api()
    user_id = _principal_path_owner(request, user_id)
    try:
        command = RelationshipLifecycleCommand.model_validate(
            {
                "ownerSubjectId": user_id,
                "relationshipId": relationship_id,
                "operation": payload.get("operation"),
                "expectedEpoch": payload.get("expectedEpoch"),
            }
        )
        relationship = _delegated_access_service().change_relationship(command)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"code": "relationshipCommandInvalid"}) from exc
    except DelegatedAccessError as exc:
        raise _delegated_access_http_error(exc) from exc
    return {"status": relationship["status"], "relationship": relationship}


@app.post("/family/access-grants")
def grant_family_access(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_delegated_access_contract_api()
    user_id, payload = _principal_owned_payload(request, payload)
    try:
        command = AccessGrantCommand.model_validate(
            {
                "grantorSubjectId": user_id,
                "relationshipId": payload.get("relationshipId"),
                "granteeSubjectId": payload.get("granteeSubjectId"),
                "purpose": payload.get("purpose"),
                "resourceType": payload.get("resourceType"),
                "resourceId": payload.get("resourceId"),
                "operations": payload.get("operations"),
                "expiresAt": payload.get("expiresAt"),
            }
        )
        grant = _delegated_access_service().grant_access(command)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"code": "accessGrantCommandInvalid"}) from exc
    except DelegatedAccessError as exc:
        raise _delegated_access_http_error(exc) from exc
    return {"status": "granted", "grant": grant}


@app.get("/family/access-grants/{user_id}")
def family_access_grants(
    request: Request,
    user_id: str,
    relationshipId: str = None,
) -> Dict[str, Any]:
    _require_delegated_access_contract_api()
    user_id = _principal_path_owner(request, user_id)
    grants = _delegated_access_service().list_relationship_grants(
        owner_subject_id=user_id,
        relationship_id=str(relationshipId or "").strip(),
    ) if relationshipId else [
        grant
        for relationship in store.list_family_relationships(user_id)
        for grant in _delegated_access_service().list_relationship_grants(
            owner_subject_id=user_id,
            relationship_id=str(relationship["id"]),
        )
    ]
    return {"userId": user_id, "grants": grants}


@app.post("/family/access-grants/{user_id}/{grant_id}/revoke")
def revoke_family_access(
    request: Request,
    user_id: str,
    grant_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    _require_delegated_access_contract_api()
    user_id = _principal_path_owner(request, user_id)
    try:
        command = RevokeAccessGrantCommand.model_validate(
            {
                "grantorSubjectId": user_id,
                "grantId": grant_id,
                "expectedVersion": payload.get("expectedVersion"),
                "reason": payload.get("reason") or "ownerRequested",
            }
        )
        grant = _delegated_access_service().revoke_access(command)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"code": "revokeGrantCommandInvalid"}) from exc
    except DelegatedAccessError as exc:
        raise _delegated_access_http_error(exc) from exc
    return {"status": "revoked", "grant": grant}


def _normalize_viewer_family_member_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalized_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _ensure_active_family_viewer(
    user_id: str,
    viewer_family_member_id: Optional[str],
    requester_user_id: Optional[str] = None,
    requester_phone: Optional[str] = None,
    require_requester_identity: bool = False,
) -> None:
    if viewer_family_member_id is None:
        if requester_user_id is not None and requester_user_id != user_id:
            raise HTTPException(status_code=403, detail="authenticated user is not the care snapshot owner")
        return
    for member in store.list_family_members(user_id):
        if str(member.get("id") or "") != viewer_family_member_id:
            continue
        if member.get("accessStatus") == "active" and member.get("invitationStatus") == "accepted":
            relationship = store.get_family_relationship_by_member(user_id, viewer_family_member_id)
            if relationship is None or relationship.get("status") != "accepted":
                raise HTTPException(status_code=403, detail="family relationship is not active")
            grantee_subject_id = str(relationship.get("memberSubjectId") or "").strip()
            if require_requester_identity:
                effective_requester_id = str(requester_user_id or "").strip()
                if (
                    not effective_requester_id
                    and AUTH_ROUTE_MODE != "enforce"
                    and not bool(_configured_backend_api_token())
                ):
                    normalized_requester_phone = _normalized_phone(requester_phone)
                    if normalized_requester_phone:
                        effective_requester_id = stable_user_id(normalized_requester_phone)
                if not effective_requester_id:
                    raise HTTPException(status_code=403, detail="verified requester identity is required")
                if effective_requester_id != grantee_subject_id:
                    raise HTTPException(status_code=403, detail="authenticated user is not authorized for this care snapshot")
            access = _delegated_access_service().authorize(
                owner_subject_id=user_id,
                grantee_subject_id=grantee_subject_id,
                family_member_id=viewer_family_member_id,
                purpose=AccessGrantPurpose.CARE_SNAPSHOT,
                operation=GrantOperation.READ,
                resource_type=ResourceScopeType.CARE_SNAPSHOT,
                record_receipt=False,
            )
            if access.allowed:
                return
            raise HTTPException(status_code=403, detail="active care grant is required")
        raise HTTPException(status_code=403, detail="family member access is not active")
    raise HTTPException(status_code=403, detail="family member is not authorized")


@app.post("/care/snapshots")
def save_care_snapshot(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id, payload = _principal_owned_payload(request, payload)
    snapshot = payload.get("snapshot")
    viewer_family_member_id = _normalize_viewer_family_member_id(payload.get("viewerFamilyMemberID"))
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=400, detail="snapshot must be an object")
    _ensure_active_family_viewer(user_id, viewer_family_member_id)
    try:
        sanitized_snapshot = sanitize_care_snapshot_payload(snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item = store.save_care_snapshot(
        user_id,
        sanitized_snapshot,
        viewer_family_member_id=viewer_family_member_id,
    )
    return {"status": "saved", "item": item}


@app.get("/care/snapshots/latest/{user_id}")
def latest_care_snapshot(
    request: Request,
    user_id: str,
    viewerFamilyMemberID: str = None,
    requesterPhone: str = None,
) -> Dict[str, Any]:
    viewer_family_member_id = _normalize_viewer_family_member_id(viewerFamilyMemberID)
    _ensure_active_family_viewer(
        user_id,
        viewer_family_member_id,
        requester_user_id=_request_user_principal_id(request),
        requester_phone=requesterPhone,
        require_requester_identity=True,
    )
    item = store.get_latest_care_snapshot(
        user_id,
        viewer_family_member_id=viewer_family_member_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="care snapshot not found")
    return {"userId": user_id, "item": item}


@app.get("/care/snapshots/{user_id}")
def care_snapshot_history(
    request: Request,
    user_id: str,
    viewerFamilyMemberID: str = None,
    requesterPhone: str = None,
    limit: int = 7,
) -> Dict[str, Any]:
    viewer_family_member_id = _normalize_viewer_family_member_id(viewerFamilyMemberID)
    _ensure_active_family_viewer(
        user_id,
        viewer_family_member_id,
        requester_user_id=_request_user_principal_id(request),
        requester_phone=requesterPhone,
        require_requester_identity=True,
    )
    items = store.list_care_snapshots(
        user_id,
        viewer_family_member_id=viewer_family_member_id,
        limit=limit,
    )
    return {"userId": user_id, "items": items}
