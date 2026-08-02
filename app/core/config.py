from dataclasses import dataclass
import os
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "DreamJourney Backend"
    environment: str = "development"
    public_base_url: Optional[str] = None
    store_backend: str = "postgres"
    database_url: str = "postgresql://dreamjourney:dreamjourney@postgres:5432/dreamjourney"
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    database_pool_timeout_seconds: float = 5.0
    redis_url: str = "redis://redis:6379/0"
    backend_api_token: Optional[str] = None
    identity_binding_hmac_key: Optional[str] = None
    identity_binding_hmac_key_version: str = "v1"
    identity_challenge_adapter: str = "disabled"
    identity_challenge_synthetic_code: Optional[str] = None
    identity_challenge_ttl_seconds: int = 300
    identity_challenge_max_attempts: int = 5
    identity_challenge_retry_after_seconds: int = 30
    auth_legacy_phone_login_enabled: bool = False
    auth_access_ttl_seconds: int = 900
    auth_refresh_ttl_seconds: int = 30 * 24 * 60 * 60
    auth_route_mode: str = "auto"
    auth_ownership_mode: str = "shadow"
    client_compatibility_mode: str = "observe"
    recovery_access_mode: str = "normal"
    authority_epoch: str = "epoch-0"
    release_policy_command_mode: str = "observe"
    release_policy_revision: int = 1
    release_policy_min_client_build: int = 1
    release_policy_ttl_seconds: int = 300
    release_policy_emergency_revision: int = 0
    release_policy_enforced_features: Optional[str] = None
    release_policy_emergency_disabled_features: Optional[str] = None
    # Closed-pilot exposure is granted only by the server. The client may
    # request a policy snapshot, but cannot nominate itself into this cohort.
    release_policy_closed_pilot_owner_ids: Optional[str] = None
    # Additional M0 features explicitly approved for server-granted pilot
    # owners. Unsupported or later-stage feature names fail at boot.
    release_policy_closed_pilot_features: Optional[str] = None
    async_effect_v1_enabled: bool = False
    async_effect_worker_enabled: bool = False
    # Both typed Owner Truth workers use this bounded idle delay when their
    # explicitly selected Compose profile is running.
    owner_truth_worker_poll_seconds: float = 2.0
    # Candidate extraction is a separate, deterministic QA worker. It remains
    # off unless all async-effect flags and this explicit switch are enabled.
    owner_truth_candidate_extraction_worker_enabled: bool = False
    owner_truth_memory_projection_worker_enabled: bool = False
    # SearchDocument rebuilds are an optional private derived step after the
    # default-off MemoryProjection worker succeeds. This never exposes search
    # or enables a public retrieval surface by itself.
    owner_truth_memory_search_projection_worker_enabled: bool = False
    delegated_access_contract_api_enabled: bool = False
    # Candidate review is an Owner Truth QA contract only until the M0 review
    # UI, release policy, and external gates are complete.
    owner_truth_candidate_review_qa_enabled: bool = False
    # Owner-controlled family reports remain a separate QA-only contract. An
    # accepted relationship still requires this explicit grant; it never opens
    # Vault read, Candidate decision, Voice, Digital Human, or public UI paths.
    owner_truth_family_contribution_qa_enabled: bool = False
    # A separate, default-off Owner-confirmed knowledge classification receipt
    # lane. It must never be enabled merely by exposing Candidate review QA.
    owner_truth_knowledge_dimension_confirmation_qa_enabled: bool = False
    # A third, independent QA gate for the value-free M0-B recommendation
    # reader. It does not expose a released Echo recommendation surface.
    owner_truth_knowledge_recommendation_read_qa_enabled: bool = False
    # An independent gate for server-planned, value-free M0-B recommendation
    # candidates. It must not become reachable when the caller-supplied QA
    # reader alone is enabled.
    owner_truth_knowledge_recommendation_plan_qa_enabled: bool = False
    # Accepting a server-planned recommendation persists only a minimal,
    # append-only QA receipt. It is independently default-off from planning.
    owner_truth_knowledge_recommendation_activation_qa_enabled: bool = False
    # Recommendation feedback records only value-free replacement or ranking
    # signals. It remains independently default-off from planning/activation.
    owner_truth_knowledge_recommendation_feedback_qa_enabled: bool = False
    # A server-side, value-free audit writer for successful natural-input
    # appends. It has no public API surface and remains default-off.
    owner_truth_interview_decision_audit_enabled: bool = False
    # A conservative, deterministic topic-shift detector may inspect the
    # current natural-input message only while the audit lane is enabled. It
    # writes no text and only shadows the existing pause decision.
    owner_truth_topic_shift_shadow_enabled: bool = False
    # A QA-only, write-free preflight may stop an explicit topic-change message
    # before it lands in the old Thread. It never pauses or starts a session.
    owner_truth_topic_shift_preflight_qa_enabled: bool = False
    # A separate write-free preflight may ask an Owner to explicitly confirm a
    # do-not-ask restore after an unambiguous natural-language reactivation.
    # It does not reopen the session or persist the attempted message.
    owner_truth_do_not_ask_reactivation_preflight_enabled: bool = False
    # A separate server-only M0-A cadence writer. When enabled, a formally
    # authorized owner transition may atomically create one pending ReviewBatch
    # at the persisted threshold or paused-session boundary. It never exposes
    # the batch through the natural-input response and remains default-off.
    owner_truth_interview_review_batch_automation_enabled: bool = False
    # Explicit Owner continuation cues remain a fourth, independently closed
    # M0-B QA lane. They do not expose recommendation text or public Echo UI.
    owner_truth_saved_continuation_cue_qa_enabled: bool = False
    # Thread summary/map reads are independently default-off. They only expose
    # value-free current Owner thread anchors and reversible associations.
    owner_truth_thread_summary_read_qa_enabled: bool = False
    # Persisted Thread-summary checkpoints are separately gated from the live
    # map read. They retain only opaque thread/session handles and current
    # confirmed-MemoryVersion anchors for QA replay.
    owner_truth_thread_summary_projection_qa_enabled: bool = False
    # Session outcome reads remain a separate Phase 4C QA lane. They report
    # only current confirmation-derived counts and continuation eligibility.
    owner_truth_interview_session_outcome_read_qa_enabled: bool = False
    # Life-map reads are a separate Phase 4C QA lane. They expose only stable
    # dimension coverage, thread state and reversible associations.
    owner_truth_life_map_read_qa_enabled: bool = False
    # SearchDocument reads are a separate Phase 4C QA lane. The first read
    # uses deterministic text fallback only and never enables public search.
    owner_truth_memory_search_read_qa_enabled: bool = False
    # Rebuilding the persisted SearchDocument index is independently closed.
    # It never enables public search, embeddings, or a Vector/Provider lane.
    owner_truth_memory_search_projection_qa_enabled: bool = False
    # Thread-scoped cooldown / do-not-ask remains independently default-off.
    # It never enables a public Echo control by itself.
    owner_truth_thread_preference_qa_enabled: bool = False
    owner_truth_thread_cooldown_seconds: int = 7 * 24 * 60 * 60
    evidence_rollout_retention_days: int = 30
    operations_evidence_hmac_key: Optional[str] = None
    incident_ack_timeout_seconds: int = 900

    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = "https://api.deepseek.com/v1/chat/completions"

    volcengine_api_key: Optional[str] = None
    volcengine_voice_type: Optional[str] = None
    volcengine_app_id: Optional[str] = None
    volcengine_app_key: Optional[str] = None
    volcengine_app_token: Optional[str] = None
    volcengine_realtime_resource_id: str = "volc.speech.dialog"
    volcengine_realtime_address: str = "wss://openspeech.bytedance.com"
    volcengine_realtime_uri: str = "/api/v3/realtime/dialogue"
    volcengine_voice_clone_api_key: Optional[str] = None
    volcengine_voice_clone_train_url: str = "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
    volcengine_voice_clone_query_url: str = "https://openspeech.bytedance.com/api/v3/tts/get_voice"
    volcengine_voice_clone_upgrade_url: str = "https://openspeech.bytedance.com/api/v3/tts/upgrade_voice"
    volcengine_voice_clone_speaker_id_mode: str = "customSpeakerId"
    volcengine_voice_clone_speaker_id: Optional[str] = None
    volcengine_voice_clone_speaker_ids: Optional[str] = None
    volcengine_voice_clone_model_type: int = 5
    volcengine_voice_clone_tts_api_key: Optional[str] = None
    volcengine_voice_clone_tts_url: str = "https://openspeech.bytedance.com/api/v1/tts"
    volcengine_voice_clone_tts_cluster: str = "volcano_icl"
    volcengine_voice_clone_tts_resource_id: str = "seed-icl-2.0"

    amap_web_service_key: Optional[str] = None
    tencent_digital_human_app_key: Optional[str] = None
    tencent_digital_human_access_token: Optional[str] = None
    tencent_digital_human_asset_virtualman_key: Optional[str] = None
    tencent_digital_human_virtualman_project_id: Optional[str] = None
    tencent_digital_human_app_id: Optional[str] = None
    tencent_digital_human_secret_id: Optional[str] = None
    tencent_digital_human_secret_key: Optional[str] = None
    tencent_digital_human_session_ttl_seconds: int = 180
    tencent_digital_human_heartbeat_interval_seconds: int = 45
    tencent_digital_human_max_concurrent_sessions: int = 1

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=_env("APP_NAME", "DreamJourney Backend") or "DreamJourney Backend",
            environment=_env("APP_ENV", "development") or "development",
            public_base_url=_env("PUBLIC_BASE_URL"),
            store_backend=_env("STORE_BACKEND", cls.store_backend) or cls.store_backend,
            database_url=_env("DATABASE_URL", cls.database_url) or cls.database_url,
            database_pool_min_size=_env_int(
                "DB_POOL_MIN_SIZE",
                cls.database_pool_min_size,
            ),
            database_pool_max_size=_env_int(
                "DB_POOL_MAX_SIZE",
                cls.database_pool_max_size,
            ),
            database_pool_timeout_seconds=_env_float(
                "DB_POOL_TIMEOUT_SECONDS",
                cls.database_pool_timeout_seconds,
            ),
            redis_url=_env("REDIS_URL", cls.redis_url) or cls.redis_url,
            backend_api_token=_env("BACKEND_API_TOKEN"),
            identity_binding_hmac_key=_env("IDENTITY_BINDING_HMAC_KEY"),
            identity_binding_hmac_key_version=_env(
                "IDENTITY_BINDING_HMAC_KEY_VERSION",
                cls.identity_binding_hmac_key_version,
            ) or cls.identity_binding_hmac_key_version,
            identity_challenge_adapter=_env(
                "IDENTITY_CHALLENGE_ADAPTER",
                cls.identity_challenge_adapter,
            ) or cls.identity_challenge_adapter,
            identity_challenge_synthetic_code=_env(
                "IDENTITY_CHALLENGE_SYNTHETIC_CODE"
            ),
            identity_challenge_ttl_seconds=_env_int(
                "IDENTITY_CHALLENGE_TTL_SECONDS",
                cls.identity_challenge_ttl_seconds,
            ),
            identity_challenge_max_attempts=_env_int(
                "IDENTITY_CHALLENGE_MAX_ATTEMPTS",
                cls.identity_challenge_max_attempts,
            ),
            identity_challenge_retry_after_seconds=_env_int(
                "IDENTITY_CHALLENGE_RETRY_AFTER_SECONDS",
                cls.identity_challenge_retry_after_seconds,
            ),
            auth_legacy_phone_login_enabled=_env_bool(
                "AUTH_LEGACY_PHONE_LOGIN_ENABLED",
                cls.auth_legacy_phone_login_enabled,
            ),
            auth_access_ttl_seconds=_env_int(
                "AUTH_ACCESS_TTL_SECONDS",
                cls.auth_access_ttl_seconds,
            ),
            auth_refresh_ttl_seconds=_env_int(
                "AUTH_REFRESH_TTL_SECONDS",
                cls.auth_refresh_ttl_seconds,
            ),
            auth_route_mode=_env(
                "AUTH_ROUTE_MODE",
                cls.auth_route_mode,
            ) or cls.auth_route_mode,
            auth_ownership_mode=_env(
                "AUTH_OWNERSHIP_MODE",
                cls.auth_ownership_mode,
            ) or cls.auth_ownership_mode,
            client_compatibility_mode=_env(
                "CLIENT_COMPATIBILITY_MODE",
                cls.client_compatibility_mode,
            ) or cls.client_compatibility_mode,
            recovery_access_mode=_env(
                "RECOVERY_ACCESS_MODE",
                cls.recovery_access_mode,
            ) or cls.recovery_access_mode,
            authority_epoch=_env(
                "AUTHORITY_EPOCH",
                cls.authority_epoch,
            ) or cls.authority_epoch,
            release_policy_command_mode=_env(
                "RELEASE_POLICY_COMMAND_MODE",
                cls.release_policy_command_mode,
            ) or cls.release_policy_command_mode,
            release_policy_revision=_env_int(
                "RELEASE_POLICY_REVISION",
                cls.release_policy_revision,
            ),
            release_policy_min_client_build=_env_int(
                "RELEASE_POLICY_MIN_CLIENT_BUILD",
                cls.release_policy_min_client_build,
            ),
            release_policy_ttl_seconds=_env_int(
                "RELEASE_POLICY_TTL_SECONDS",
                cls.release_policy_ttl_seconds,
            ),
            release_policy_emergency_revision=_env_int(
                "RELEASE_POLICY_EMERGENCY_REVISION",
                cls.release_policy_emergency_revision,
            ),
            release_policy_enforced_features=_env("RELEASE_POLICY_ENFORCED_FEATURES"),
            release_policy_emergency_disabled_features=_env(
                "RELEASE_POLICY_EMERGENCY_DISABLED_FEATURES"
            ),
            release_policy_closed_pilot_owner_ids=_env(
                "RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS"
            ),
            release_policy_closed_pilot_features=_env(
                "RELEASE_POLICY_CLOSED_PILOT_FEATURES"
            ),
            async_effect_v1_enabled=_env_bool(
                "ASYNC_EFFECT_V1_ENABLED",
                cls.async_effect_v1_enabled,
            ),
            async_effect_worker_enabled=_env_bool(
                "ASYNC_EFFECT_WORKER_ENABLED",
                cls.async_effect_worker_enabled,
            ),
            owner_truth_worker_poll_seconds=max(
                0.1,
                _env_float(
                    "OWNER_TRUTH_WORKER_POLL_SECONDS",
                    cls.owner_truth_worker_poll_seconds,
                ),
            ),
            owner_truth_candidate_extraction_worker_enabled=_env_bool(
                "OWNER_TRUTH_CANDIDATE_EXTRACTION_WORKER_ENABLED",
                cls.owner_truth_candidate_extraction_worker_enabled,
            ),
            owner_truth_memory_projection_worker_enabled=_env_bool(
                "OWNER_TRUTH_MEMORY_PROJECTION_WORKER_ENABLED",
                cls.owner_truth_memory_projection_worker_enabled,
            ),
            owner_truth_memory_search_projection_worker_enabled=_env_bool(
                "OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_WORKER_ENABLED",
                cls.owner_truth_memory_search_projection_worker_enabled,
            ),
            delegated_access_contract_api_enabled=_env_bool(
                "DELEGATED_ACCESS_CONTRACT_API_ENABLED",
                cls.delegated_access_contract_api_enabled,
            ),
            owner_truth_candidate_review_qa_enabled=_env_bool(
                "OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED",
                cls.owner_truth_candidate_review_qa_enabled,
            ),
            owner_truth_family_contribution_qa_enabled=_env_bool(
                "OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED",
                cls.owner_truth_family_contribution_qa_enabled,
            ),
            owner_truth_knowledge_dimension_confirmation_qa_enabled=_env_bool(
                "OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED",
                cls.owner_truth_knowledge_dimension_confirmation_qa_enabled,
            ),
            owner_truth_knowledge_recommendation_read_qa_enabled=_env_bool(
                "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED",
                cls.owner_truth_knowledge_recommendation_read_qa_enabled,
            ),
            owner_truth_knowledge_recommendation_plan_qa_enabled=_env_bool(
                "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED",
                cls.owner_truth_knowledge_recommendation_plan_qa_enabled,
            ),
            owner_truth_knowledge_recommendation_activation_qa_enabled=_env_bool(
                "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_QA_ENABLED",
                cls.owner_truth_knowledge_recommendation_activation_qa_enabled,
            ),
            owner_truth_interview_decision_audit_enabled=_env_bool(
                "OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED",
                cls.owner_truth_interview_decision_audit_enabled,
            ),
            owner_truth_topic_shift_shadow_enabled=_env_bool(
                "OWNER_TRUTH_TOPIC_SHIFT_SHADOW_ENABLED",
                cls.owner_truth_topic_shift_shadow_enabled,
            ),
            owner_truth_topic_shift_preflight_qa_enabled=_env_bool(
                "OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_QA_ENABLED",
                cls.owner_truth_topic_shift_preflight_qa_enabled,
            ),
            owner_truth_do_not_ask_reactivation_preflight_enabled=_env_bool(
                "OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_ENABLED",
                cls.owner_truth_do_not_ask_reactivation_preflight_enabled,
            ),
            owner_truth_interview_review_batch_automation_enabled=_env_bool(
                "OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED",
                cls.owner_truth_interview_review_batch_automation_enabled,
            ),
            owner_truth_saved_continuation_cue_qa_enabled=_env_bool(
                "OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED",
                cls.owner_truth_saved_continuation_cue_qa_enabled,
            ),
            owner_truth_thread_summary_read_qa_enabled=_env_bool(
                "OWNER_TRUTH_THREAD_SUMMARY_READ_QA_ENABLED",
                cls.owner_truth_thread_summary_read_qa_enabled,
            ),
            owner_truth_thread_summary_projection_qa_enabled=_env_bool(
                "OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED",
                cls.owner_truth_thread_summary_projection_qa_enabled,
            ),
            owner_truth_interview_session_outcome_read_qa_enabled=_env_bool(
                "OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_QA_ENABLED",
                cls.owner_truth_interview_session_outcome_read_qa_enabled,
            ),
            owner_truth_life_map_read_qa_enabled=_env_bool(
                "OWNER_TRUTH_LIFE_MAP_READ_QA_ENABLED",
                cls.owner_truth_life_map_read_qa_enabled,
            ),
            owner_truth_memory_search_read_qa_enabled=_env_bool(
                "OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED",
                cls.owner_truth_memory_search_read_qa_enabled,
            ),
            owner_truth_memory_search_projection_qa_enabled=_env_bool(
                "OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED",
                cls.owner_truth_memory_search_projection_qa_enabled,
            ),
            owner_truth_thread_preference_qa_enabled=_env_bool(
                "OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED",
                cls.owner_truth_thread_preference_qa_enabled,
            ),
            owner_truth_thread_cooldown_seconds=_env_int(
                "OWNER_TRUTH_THREAD_COOLDOWN_SECONDS",
                cls.owner_truth_thread_cooldown_seconds,
            ),
            evidence_rollout_retention_days=_env_int(
                "EVIDENCE_ROLLOUT_RETENTION_DAYS",
                cls.evidence_rollout_retention_days,
            ),
            operations_evidence_hmac_key=_env("OPERATIONS_EVIDENCE_HMAC_KEY"),
            incident_ack_timeout_seconds=_env_int(
                "INCIDENT_ACK_TIMEOUT_SECONDS",
                cls.incident_ack_timeout_seconds,
            ),
            deepseek_api_key=_env("DEEPSEEK_API_KEY"),
            deepseek_base_url=_env("DEEPSEEK_BASE_URL", cls.deepseek_base_url) or cls.deepseek_base_url,
            volcengine_api_key=_env("VOLCENGINE_API_KEY"),
            volcengine_voice_type=_env("VOLCENGINE_VOICE_TYPE"),
            volcengine_app_id=_env("VOLCENGINE_APP_ID"),
            volcengine_app_key=_env("VOLCENGINE_APP_KEY"),
            volcengine_app_token=_env("VOLCENGINE_APP_TOKEN"),
            volcengine_realtime_resource_id=_env("VOLCENGINE_REALTIME_RESOURCE_ID", cls.volcengine_realtime_resource_id) or cls.volcengine_realtime_resource_id,
            volcengine_realtime_address=_env("VOLCENGINE_REALTIME_ADDRESS", cls.volcengine_realtime_address) or cls.volcengine_realtime_address,
            volcengine_realtime_uri=_env("VOLCENGINE_REALTIME_URI", cls.volcengine_realtime_uri) or cls.volcengine_realtime_uri,
            volcengine_voice_clone_api_key=_env("VOLCENGINE_VOICE_CLONE_API_KEY"),
            volcengine_voice_clone_train_url=_env("VOLCENGINE_VOICE_CLONE_TRAIN_URL", cls.volcengine_voice_clone_train_url) or cls.volcengine_voice_clone_train_url,
            volcengine_voice_clone_query_url=_env("VOLCENGINE_VOICE_CLONE_QUERY_URL", cls.volcengine_voice_clone_query_url) or cls.volcengine_voice_clone_query_url,
            volcengine_voice_clone_upgrade_url=_env("VOLCENGINE_VOICE_CLONE_UPGRADE_URL", cls.volcengine_voice_clone_upgrade_url) or cls.volcengine_voice_clone_upgrade_url,
            volcengine_voice_clone_speaker_id_mode=_env("VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE", cls.volcengine_voice_clone_speaker_id_mode) or cls.volcengine_voice_clone_speaker_id_mode,
            volcengine_voice_clone_speaker_id=_env("VOLCENGINE_VOICE_CLONE_SPEAKER_ID"),
            volcengine_voice_clone_speaker_ids=_env("VOLCENGINE_VOICE_CLONE_SPEAKER_IDS"),
            volcengine_voice_clone_model_type=_env_int("VOLCENGINE_VOICE_CLONE_MODEL_TYPE", cls.volcengine_voice_clone_model_type),
            volcengine_voice_clone_tts_api_key=_env("VOLCENGINE_VOICE_CLONE_TTS_API_KEY"),
            volcengine_voice_clone_tts_url=_env("VOLCENGINE_VOICE_CLONE_TTS_URL", cls.volcengine_voice_clone_tts_url) or cls.volcengine_voice_clone_tts_url,
            volcengine_voice_clone_tts_cluster=_env("VOLCENGINE_VOICE_CLONE_TTS_CLUSTER", cls.volcengine_voice_clone_tts_cluster) or cls.volcengine_voice_clone_tts_cluster,
            volcengine_voice_clone_tts_resource_id=_env("VOLCENGINE_VOICE_CLONE_TTS_RESOURCE_ID", cls.volcengine_voice_clone_tts_resource_id) or cls.volcengine_voice_clone_tts_resource_id,
            amap_web_service_key=_env("AMAP_WEB_SERVICE_KEY"),
            tencent_digital_human_app_key=_env("TENCENT_DIGITAL_HUMAN_APP_KEY"),
            tencent_digital_human_access_token=_env("TENCENT_DIGITAL_HUMAN_ACCESS_TOKEN"),
            tencent_digital_human_asset_virtualman_key=_env("TENCENT_DIGITAL_HUMAN_ASSET_VIRTUALMAN_KEY"),
            tencent_digital_human_virtualman_project_id=_env("TENCENT_DIGITAL_HUMAN_VIRTUALMAN_PROJECT_ID"),
            tencent_digital_human_app_id=_env("TENCENT_DIGITAL_HUMAN_APP_ID"),
            tencent_digital_human_secret_id=_env("TENCENT_DIGITAL_HUMAN_SECRET_ID"),
            tencent_digital_human_secret_key=_env("TENCENT_DIGITAL_HUMAN_SECRET_KEY"),
            tencent_digital_human_session_ttl_seconds=_env_int(
                "TENCENT_DIGITAL_HUMAN_SESSION_TTL_SECONDS",
                cls.tencent_digital_human_session_ttl_seconds,
            ),
            tencent_digital_human_heartbeat_interval_seconds=_env_int(
                "TENCENT_DIGITAL_HUMAN_HEARTBEAT_INTERVAL_SECONDS",
                cls.tencent_digital_human_heartbeat_interval_seconds,
            ),
            tencent_digital_human_max_concurrent_sessions=_env_int(
                "TENCENT_DIGITAL_HUMAN_MAX_CONCURRENT_SESSIONS",
                cls.tencent_digital_human_max_concurrent_sessions,
            ),
        )


settings = Settings.from_env()
