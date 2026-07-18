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
from app.services.client_compatibility import (
    ClientCompatibilityDecision,
    ClientCompatibilityDecisionRecorder,
    ClientCompatibilityPolicy,
    resolve_client_compatibility_mode,
)
from app.services.deepseek import ArchiveImageAnalysisProviderFactory
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
from app.observability.operation_metrics import (
    OperationMetricRecorder,
    summarize_operation_metrics_for_observations,
)
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
VOICE_CLONE_PROVIDER_ERROR_PREFIX = "voice clone provider error"
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
    "/voice/",
    "/digital-human/",
)
NO_STORE_EXACT_PATHS = {
    "/health",
    "/live",
    "/ready",
    "/config/runtime",
    "/v2/release-policy",
    "/ops/release-policy/observations",
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
    payload = ReadinessService(settings=settings, store=store).evaluate()
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


@app.get("/ops/release-policy/observations")
def release_policy_observations(request: Request) -> Dict[str, Any]:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, RequestPrincipal) or principal.kind != PrincipalKind.MACHINE:
        raise HTTPException(status_code=403, detail="machine principal required")
    summary = RELEASE_POLICY_DECISION_RECORDER.summary()
    summary["operationMetrics"] = summarize_operation_metrics_for_observations(
        OPERATION_METRIC_RECORDER.summary()
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
    return summary


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
            deletion = existing_user
            delegated_grant_revocation = _delegated_access_service().revoke_subject_access(
                user_id,
                reason="accountSoftDeleted",
            )
            session_revocation = _auth_session_service().revoke_all_for_user(
                user_id,
                reason="accountSoftDeleted",
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
                    "dataExportSupported": False,
                    "retentionDays": ACCOUNT_DELETION_RETENTION_DAYS,
                    "restoreLimit": ACCOUNT_RESTORE_LIMIT,
                    "restoreBySamePhone": True,
                },
                "sessionRevocation": session_revocation,
                "delegatedGrantRevocation": delegated_grant_revocation,
                "rights": rights_summary,
            }
        deletion = store.soft_delete_user(user_id, phone=phone)
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
            "dataExportSupported": False,
            "retentionDays": ACCOUNT_DELETION_RETENTION_DAYS,
            "restoreLimit": ACCOUNT_RESTORE_LIMIT,
            "restoreBySamePhone": True,
        },
        "sessionRevocation": session_revocation,
        "delegatedGrantRevocation": delegated_grant_revocation,
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


@app.post("/auth/purge-expired-deletions")
def purge_expired_account_deletions(payload: Dict[str, Any]) -> Dict[str, Any]:
    cutoff = str(payload.get("cutoff") or datetime.now(timezone.utc).isoformat())
    purged = store.purge_expired_deleted_users(cutoff)
    return {
        "status": "purged",
        "cutoff": cutoff,
        "purgedCount": len(purged),
        "items": purged,
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
    provider_message = str(public_profile.pop("providerMessage", "") or "").strip()
    if provider_request_id:
        public_profile["providerRequestIdHash"] = _provider_reference_hash(provider_request_id)
    if provider_log_id:
        public_profile["providerLogIdHash"] = _provider_reference_hash(provider_log_id)
    if provider_message:
        public_profile["providerErrorCode"] = "providerOperationFailed"
        public_profile["providerErrorReferenceHash"] = _provider_reference_hash(provider_message)
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
    provider_message = str(
        public_payload.pop("providerMessage", "")
        or public_payload.pop("message", "")
        or ""
    ).strip()
    for key in tuple(public_payload):
        normalized_key = "".join(character for character in key.lower() if character.isalnum())
        if normalized_key in {"appkey", "accesstoken", "apptoken", "apikey", "secretkey"}:
            public_payload.pop(key, None)
    if provider_request_id:
        public_payload["providerRequestIdHash"] = _provider_reference_hash(provider_request_id)
    if provider_log_id:
        public_payload["providerLogIdHash"] = _provider_reference_hash(provider_log_id)
    if provider_message:
        public_payload["providerMessageHash"] = _provider_reference_hash(provider_message)
    return public_payload


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
                "providerMessage": f"{VOICE_CLONE_PROVIDER_ERROR_PREFIX}: {exc}",
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
    provider_message = str(provider_result.get("providerMessage") or "").strip()
    if provider_request_id:
        profile["providerRequestId"] = provider_request_id
    if provider_log_id:
        profile["providerLogId"] = provider_log_id[:160]
    if provider_message:
        profile["providerMessage"] = provider_message[:300]
    if "createdAt" in payload:
        profile["createdAt"] = str(payload.get("createdAt") or now)
    else:
        profile["createdAt"] = now
    return profile


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
    updated = dict(profile)
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
            "providerMessage": f"{VOICE_CLONE_PROVIDER_ERROR_PREFIX}: {exc}",
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
    updated = dict(profile)
    updated["sampleStatus"] = sample_status
    updated["isEnabled"] = sample_status == "ready"
    updated["providerMode"] = provider.provider_mode
    updated["realCloneProviderReady"] = provider.is_configured
    updated["providerStatus"] = str(provider_result.get("providerStatus") or updated.get("providerStatus") or "unknown")
    provider_request_id = str(provider_result.get("providerRequestId") or "").strip()
    provider_log_id = str(provider_result.get("providerLogId") or "").strip()
    provider_message = str(provider_result.get("providerMessage") or "").strip()
    if provider_request_id:
        updated["providerRequestId"] = provider_request_id
    if provider_log_id:
        updated["providerLogId"] = provider_log_id[:160]
    if provider_message:
        updated["providerMessage"] = provider_message[:300]
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
    except ValueError as exc:
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
    provider_request_id = str(result.get("providerRequestId") or "").strip()
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
    try:
        if not dryRun:
            return _provider_public_payload(
                proxy.request_tts(
                    text=text,
                    user_id=user_id,
                    voice_type=voice_type,
                    encoding=encoding,
                    speed_ratio=speed_ratio,
                )
            )
        request = proxy.build_request(
            text=text,
            user_id=user_id,
            voice_type=voice_type,
            encoding=encoding,
            speed_ratio=speed_ratio,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "tts_request_invalid",
                "message": "TTS request is unavailable",
                "errorReferenceHash": _provider_reference_hash(exc),
            },
        ) from exc

    return {
        "provider": "volcengine",
        "request": {
            "url": request["url"],
            "headers": {"x-api-key": "<server-side>", "Content-Type": "application/json"},
            "json": request["json"],
        },
        "note": "dryRun=true returns the redacted upstream request without calling VolcEngine.",
    }


@app.get("/maps/district")
def amap_district(keyword: str, dryRun: bool = False) -> Dict[str, Any]:
    try:
        proxy = AMapDistrictProxy(settings)
        if not dryRun:
            return proxy.request_district(keyword=keyword)
        url = proxy.build_url(keyword=keyword)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "provider": "amap",
        "keyword": keyword,
        "upstreamURL": proxy.redact_url(url, settings.amap_web_service_key),
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
    try:
        if not dryRun:
            extraction = proxy.request_extraction(
                transcript=extraction_input.transcript,
                existing_summary=existing_summary,
                turns=extraction_input.turns,
                source_policy=extraction_input.source_policy,
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
        request = proxy.redacted_request(
            transcript=extraction_input.transcript,
            existing_summary=existing_summary,
            turns=extraction_input.turns,
            source_policy=extraction_input.source_policy,
        )
    except ValueError as exc:
        status_code = 503 if "DEEPSEEK_API_KEY" in str(exc) else 502
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    response = {
        "provider": "deepseek",
        "capability": "kbExtract",
        "userId": user_id,
        "request": request,
        "evidencePolicy": empty_evidence_policy(extraction_input),
        "context": safe_context,
        "note": "dryRun=true returns the redacted upstream request without calling DeepSeek.",
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
    return {"status": "saved", "item": item}


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
    try:
        if not dryRun:
            return provider.request_analysis(image_base64=image_base64)
        request = provider.redacted_request(image_base64=image_base64)
    except ValueError as exc:
        if not dryRun:
            return provider.failure_contract(provider_message=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        if not dryRun:
            return provider.failure_contract(provider_message=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "provider": provider.provider_id,
        "capability": provider.public_capability(),
        "request": request,
        "responseContract": provider.response_contract(),
        "context": {
            "userId": user_id,
            "archiveItemId": archive_item_id,
            "privacyMetadata": safe_context.get("privacyMetadata"),
        },
        "note": "dryRun=true returns the redacted upstream request without calling DeepSeek.",
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
