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
    identity_challenge_http_json_url: Optional[str] = None
    identity_challenge_http_json_status_url: Optional[str] = None
    identity_challenge_http_json_api_key: Optional[str] = None
    identity_challenge_http_json_timeout_seconds: float = 10.0
    identity_challenge_ttl_seconds: int = 300
    identity_challenge_max_attempts: int = 5
    identity_challenge_retry_after_seconds: int = 30
    password_authentication_enabled: bool = True
    # A machine-managed, target-restricted login lane for synthetic QA
    # accounts. It is disabled by default and accepts only explicitly
    # configured phone prefixes so it cannot become an arbitrary OTP bypass.
    test_account_allowlist_enabled: bool = False
    test_account_allowed_phone_prefixes: Optional[str] = None
    test_account_admin_enabled: bool = False
    test_account_admin_username: Optional[str] = None
    test_account_admin_password_hash: Optional[str] = None
    test_account_admin_session_hmac_key: Optional[str] = None
    test_account_admin_session_ttl_seconds: int = 7200
    test_account_admin_cookie_name: str = "dj_test_account_admin"
    test_account_admin_cookie_path: str = "/ops/test-accounts"
    test_account_admin_cookie_secure: bool = True
    # Voice cloning requires a separate strong adult identity + liveness
    # verifier. OTP/phone authentication is intentionally insufficient.
    # ``disabled`` is the only default and causes the training path to fail
    # closed even when a voice provider key is present.
    voice_identity_eligibility_provider: str = "disabled"
    voice_identity_eligibility_http_json_url: Optional[str] = None
    voice_identity_eligibility_http_json_api_key: Optional[str] = None
    voice_identity_eligibility_http_json_timeout_seconds: float = 10.0
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
    # Promote the private V4 Source -> Candidate -> MemoryVersion -> Projection
    # -> Search/Echo chain to every authenticated Owner. Login test-account
    # allowlists do not participate in this decision.
    release_policy_authenticated_owner_v4_enabled: bool = False
    # Operational capability evidence is intentionally short-lived. A stale
    # observation closes the affected lane, and a later recovery receives a
    # new readiness epoch instead of reviving a cached client decision.
    runtime_capability_readiness_ttl_seconds: int = 180
    runtime_capability_probe_interval_seconds: float = 5.0
    runtime_capability_backlog_limit: int = 50
    runtime_capability_dead_letter_limit: int = 0
    async_effect_v1_enabled: bool = False
    async_effect_worker_enabled: bool = False
    # Both typed Owner Truth workers use this bounded idle delay when their
    # explicitly selected Compose profile is running.
    owner_truth_worker_poll_seconds: float = 2.0
    # Candidate extraction is a separate, deterministic QA worker. It remains
    # off unless all async-effect flags and this explicit switch are enabled.
    owner_truth_candidate_extraction_worker_enabled: bool = False
    # Narrative generation is an independent text-only lane. The worker and
    # provider both remain disabled until their privacy/release evidence exists.
    narrative_generation_worker_enabled: bool = False
    narrative_generation_provider: str = "disabled"
    narrative_generation_model: str = "disabled"
    narrative_generation_prompt_version: str = "narrative-writing-v3-progressive-auditions"
    narrative_generation_pipeline_version: str = "selection-manifest-progressive-artifact-repair-v3"
    narrative_audition_length_validation_enabled: bool = True
    narrative_generation_timeout_seconds: float = 120.0
    narrative_generation_max_concurrency: int = 2
    # Closed Live sessions may contain the complete user/assistant text
    # transcript. Sending that transcript to DeepSeek for semantic memory
    # organization requires this separate, explicit production switch.
    owner_truth_live_memory_organization_enabled: bool = False
    # Owner-authored Archive text uses a separate consent and rollout lane.
    # When enabled, the text is organized into typed review Candidates instead
    # of being persisted as one undifferentiated echo of the Source.
    owner_truth_text_memory_organization_enabled: bool = False
    owner_truth_memory_projection_worker_enabled: bool = False
    # SearchDocument rebuilds are an optional private derived step after the
    # default-off MemoryProjection worker succeeds. This never exposes search
    # or enables a public retrieval surface by itself.
    owner_truth_memory_search_projection_worker_enabled: bool = False
    # Enable confirmed V4 Projection Context for an authenticated Owner's
    # personal Echo. This is independent of login test-account allowlists.
    owner_truth_context_authority_enabled: bool = False
    # Deprecated deployment alias retained while older environments migrate to
    # OWNER_TRUTH_CONTEXT_AUTHORITY_ENABLED.
    owner_truth_context_authority_closed_pilot_enabled: bool = False
    # Stage 2 media ingestion stays separately default-off. When enabled it
    # writes only into a server-private object adapter and still requires a
    # configured safety scanner before bytes can become verified input.
    owner_truth_media_capture_enabled: bool = False
    owner_truth_media_storage_provider: str = "disabled"
    owner_truth_media_storage_root: str = "/var/lib/dreamjourney/media"
    # Ordinary authenticated users require a current external verification
    # receipt in addition to operational readiness. These fields never make
    # ``filesystem`` public; that provider remains internal-entitlement only.
    owner_truth_media_storage_external_verified: bool = False
    owner_truth_media_storage_evidence_timestamp: Optional[str] = None
    # ``cos`` is the selected M0 production adapter and uses Tencent COS via
    # its S3-compatible endpoint. ``s3`` remains available for isolated
    # compatibility testing. Credentials stay server-side; the media API never
    # issues a bucket URL or object key to the mobile client.
    owner_truth_media_s3_bucket: Optional[str] = None
    owner_truth_media_s3_prefix: str = "dreamjourney/private-media"
    owner_truth_media_s3_region: Optional[str] = None
    owner_truth_media_s3_endpoint_url: Optional[str] = None
    owner_truth_media_s3_access_key_id: Optional[str] = None
    owner_truth_media_s3_secret_access_key: Optional[str] = None
    owner_truth_media_s3_server_side_encryption: Optional[str] = None
    owner_truth_media_s3_kms_key_id: Optional[str] = None
    owner_truth_media_upload_intent_ttl_seconds: int = 900
    owner_truth_media_max_upload_bytes: int = 50 * 1024 * 1024
    owner_truth_media_content_safety_provider: str = "disabled"
    # When set, ``clamav`` routes scans through an internal clamd sidecar
    # instead of requiring clamscan in every API/worker image. The host must
    # stay Docker-internal because clamd TCP is neither authenticated nor encrypted.
    owner_truth_media_clamav_host: Optional[str] = None
    owner_truth_media_clamav_port: int = 3310
    owner_truth_media_clamav_timeout_seconds: int = 30
    # The private parser/OCR/ASR queue is a separate worker lane. It remains
    # off until capture, storage and the selected processor rollout are ready.
    owner_truth_media_processing_worker_enabled: bool = False
    owner_truth_media_processing_external_verified: bool = False
    owner_truth_media_processing_evidence_timestamp: Optional[str] = None
    # Private physical deletion is a separate revocation-first worker lane.
    # It must not run merely because media capture or processing is enabled.
    owner_truth_media_deletion_worker_enabled: bool = False
    # Voice-profile deletion follows the same revocation-first rule.  The
    # mobile delete request fences synthesis immediately, while this separate
    # worker may later obtain a provider cleanup receipt.  It remains off
    # until the selected provider exposes a reviewed deletion contract.
    voice_clone_deletion_worker_enabled: bool = False
    # Business message projections stay separate from the public mailbox and
    # notification delivery. This worker only writes metadata-only shadows.
    business_message_projection_worker_enabled: bool = False
    # Image OCR and audio ASR stay provider-neutral. ``httpJson`` sends only
    # user-consented bytes to a server-configured HTTPS adapter and expects a
    # JSON ``text`` or ``transcript`` response. No mobile credential or object
    # URL is ever used here; the default remains disabled.
    owner_truth_media_image_ocr_provider: str = "disabled"
    owner_truth_media_image_ocr_url: Optional[str] = None
    owner_truth_media_image_ocr_api_key: Optional[str] = None
    owner_truth_media_audio_asr_provider: str = "disabled"
    owner_truth_media_audio_asr_url: Optional[str] = None
    owner_truth_media_audio_asr_api_key: Optional[str] = None
    owner_truth_media_external_processor_timeout_seconds: float = 30.0
    owner_truth_media_external_processor_max_payload_bytes: int = 10 * 1024 * 1024
    # TXT/PDF/DOCX extraction runs in a separate process with independent
    # timeout and resource ceilings. These limits never enable media capture.
    owner_truth_document_parser_timeout_seconds: int = 15
    owner_truth_document_parser_max_input_bytes: int = 20 * 1024 * 1024
    owner_truth_document_parser_max_memory_bytes: int = 512 * 1024 * 1024
    owner_truth_document_parser_max_cpu_seconds: int = 10
    owner_truth_document_parser_max_pdf_pages: int = 100
    owner_truth_document_parser_max_docx_entries: int = 2_048
    owner_truth_document_parser_max_docx_uncompressed_bytes: int = 20 * 1024 * 1024
    owner_truth_document_parser_max_docx_compression_ratio: int = 200
    delegated_access_contract_api_enabled: bool = False
    # Publication is an M2 capability.  The first owner-authority writer is
    # intentionally QA-only until visitor grants and revocation propagation
    # have their own completed release gates.
    publication_authority_qa_enabled: bool = False
    # ShareGrant issuance and Visitor session admission stay separately
    # default-off until the public reader, safety execution and release gates
    # are complete. This is an internal QA contract, never a public feature.
    publication_visitor_access_qa_enabled: bool = False
    # Withdrawal and third-party objection execution is an even narrower QA
    # capability. It is separately default-off so enabling owner drafts or
    # visitor reads never exposes a destructive lifecycle command.
    publication_lifecycle_qa_enabled: bool = False
    # The lifecycle cleanup materializer only binds already-denied receipts to
    # value-minimized async effects. It never calls a Provider, but still
    # remains independently default-off until the closed-beta worker profile
    # has been validated in its target environment.
    publication_external_cleanup_materializer_enabled: bool = False
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
    # Realtime Dialog credentials stay server-side. Mobile receives a
    # single-purpose, short-lived ticket for this backend WebSocket proxy.
    realtime_voice_proxy_enabled: bool = False
    realtime_voice_ticket_ttl_seconds: int = 60
    realtime_voice_max_session_seconds: int = 60 * 60
    realtime_voice_max_concurrent_sessions_per_user: int = 1
    realtime_voice_auth_recheck_seconds: float = 10.0
    realtime_voice_upstream_connect_timeout_seconds: float = 10.0
    realtime_voice_max_frame_bytes: int = 2 * 1024 * 1024
    realtime_voice_max_session_bytes: int = 512 * 1024 * 1024
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

    # APNs remains disabled until both a production Provider and a durable
    # token vault are configured. The bundled fake/ephemeral pair is only for
    # deterministic contract and worker smoke tests.
    apns_delivery_provider: str = "disabled"
    apns_token_vault_provider: str = "disabled"
    apns_topic: Optional[str] = None
    apns_environment: str = "sandbox"
    apns_max_attempts: int = 3
    apns_token_encryption_key: Optional[str] = None
    apns_token_encryption_key_version: str = "v1"
    apns_team_id: Optional[str] = None
    apns_key_id: Optional[str] = None
    apns_private_key_path: Optional[str] = None
    apns_request_timeout_seconds: int = 15
    apns_external_verified: bool = False

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
            identity_challenge_http_json_url=_env(
                "IDENTITY_CHALLENGE_HTTP_JSON_URL"
            ),
            identity_challenge_http_json_status_url=_env(
                "IDENTITY_CHALLENGE_HTTP_JSON_STATUS_URL"
            ),
            identity_challenge_http_json_api_key=_env(
                "IDENTITY_CHALLENGE_HTTP_JSON_API_KEY"
            ),
            identity_challenge_http_json_timeout_seconds=_env_float(
                "IDENTITY_CHALLENGE_HTTP_JSON_TIMEOUT_SECONDS",
                cls.identity_challenge_http_json_timeout_seconds,
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
            password_authentication_enabled=_env_bool(
                "PASSWORD_AUTHENTICATION_ENABLED",
                cls.password_authentication_enabled,
            ),
            test_account_allowlist_enabled=_env_bool(
                "TEST_ACCOUNT_ALLOWLIST_ENABLED",
                cls.test_account_allowlist_enabled,
            ),
            test_account_allowed_phone_prefixes=_env(
                "TEST_ACCOUNT_ALLOWED_PHONE_PREFIXES"
            ),
            test_account_admin_enabled=_env_bool(
                "TEST_ACCOUNT_ADMIN_ENABLED",
                cls.test_account_admin_enabled,
            ),
            test_account_admin_username=_env("TEST_ACCOUNT_ADMIN_USERNAME"),
            test_account_admin_password_hash=_env(
                "TEST_ACCOUNT_ADMIN_PASSWORD_HASH"
            ),
            test_account_admin_session_hmac_key=_env(
                "TEST_ACCOUNT_ADMIN_SESSION_HMAC_KEY"
            ),
            test_account_admin_session_ttl_seconds=_env_int(
                "TEST_ACCOUNT_ADMIN_SESSION_TTL_SECONDS",
                cls.test_account_admin_session_ttl_seconds,
            ),
            test_account_admin_cookie_name=_env(
                "TEST_ACCOUNT_ADMIN_COOKIE_NAME",
                cls.test_account_admin_cookie_name,
            )
            or cls.test_account_admin_cookie_name,
            test_account_admin_cookie_path=_env(
                "TEST_ACCOUNT_ADMIN_COOKIE_PATH",
                cls.test_account_admin_cookie_path,
            )
            or cls.test_account_admin_cookie_path,
            test_account_admin_cookie_secure=_env_bool(
                "TEST_ACCOUNT_ADMIN_COOKIE_SECURE",
                cls.test_account_admin_cookie_secure,
            ),
            voice_identity_eligibility_provider=_env(
                "VOICE_IDENTITY_ELIGIBILITY_PROVIDER",
                cls.voice_identity_eligibility_provider,
            ) or cls.voice_identity_eligibility_provider,
            voice_identity_eligibility_http_json_url=_env(
                "VOICE_IDENTITY_ELIGIBILITY_HTTP_JSON_URL"
            ),
            voice_identity_eligibility_http_json_api_key=_env(
                "VOICE_IDENTITY_ELIGIBILITY_HTTP_JSON_API_KEY"
            ),
            voice_identity_eligibility_http_json_timeout_seconds=_env_float(
                "VOICE_IDENTITY_ELIGIBILITY_HTTP_JSON_TIMEOUT_SECONDS",
                cls.voice_identity_eligibility_http_json_timeout_seconds,
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
            release_policy_authenticated_owner_v4_enabled=_env_bool(
                "RELEASE_POLICY_AUTHENTICATED_OWNER_V4_ENABLED",
                cls.release_policy_authenticated_owner_v4_enabled,
            ),
            runtime_capability_readiness_ttl_seconds=max(
                30,
                _env_int(
                    "RUNTIME_CAPABILITY_READINESS_TTL_SECONDS",
                    cls.runtime_capability_readiness_ttl_seconds,
                ),
            ),
            runtime_capability_probe_interval_seconds=max(
                0.5,
                _env_float(
                    "RUNTIME_CAPABILITY_PROBE_INTERVAL_SECONDS",
                    cls.runtime_capability_probe_interval_seconds,
                ),
            ),
            runtime_capability_backlog_limit=max(
                0,
                min(
                    99,
                    _env_int(
                        "RUNTIME_CAPABILITY_BACKLOG_LIMIT",
                        cls.runtime_capability_backlog_limit,
                    ),
                ),
            ),
            runtime_capability_dead_letter_limit=max(
                0,
                _env_int(
                    "RUNTIME_CAPABILITY_DEAD_LETTER_LIMIT",
                    cls.runtime_capability_dead_letter_limit,
                ),
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
            narrative_generation_worker_enabled=_env_bool(
                "NARRATIVE_GENERATION_WORKER_ENABLED",
                cls.narrative_generation_worker_enabled,
            ),
            narrative_generation_provider=_env(
                "NARRATIVE_GENERATION_PROVIDER", cls.narrative_generation_provider
            ) or cls.narrative_generation_provider,
            narrative_generation_model=_env(
                "NARRATIVE_GENERATION_MODEL", cls.narrative_generation_model
            ) or cls.narrative_generation_model,
            narrative_generation_prompt_version=_env(
                "NARRATIVE_GENERATION_PROMPT_VERSION",
                cls.narrative_generation_prompt_version,
            ) or cls.narrative_generation_prompt_version,
            narrative_generation_pipeline_version=_env(
                "NARRATIVE_GENERATION_PIPELINE_VERSION",
                cls.narrative_generation_pipeline_version,
            ) or cls.narrative_generation_pipeline_version,
            narrative_audition_length_validation_enabled=_env_bool(
                "NARRATIVE_AUDITION_LENGTH_VALIDATION_ENABLED",
                cls.narrative_audition_length_validation_enabled,
            ),
            narrative_generation_timeout_seconds=max(
                10.0,
                _env_float(
                    "NARRATIVE_GENERATION_TIMEOUT_SECONDS",
                    cls.narrative_generation_timeout_seconds,
                ),
            ),
            narrative_generation_max_concurrency=max(
                1,
                min(
                    16,
                    _env_int(
                        "NARRATIVE_GENERATION_MAX_CONCURRENCY",
                        cls.narrative_generation_max_concurrency,
                    ),
                ),
            ),
            owner_truth_live_memory_organization_enabled=_env_bool(
                "OWNER_TRUTH_LIVE_MEMORY_ORGANIZATION_ENABLED",
                cls.owner_truth_live_memory_organization_enabled,
            ),
            owner_truth_text_memory_organization_enabled=_env_bool(
                "OWNER_TRUTH_TEXT_MEMORY_ORGANIZATION_ENABLED",
                cls.owner_truth_text_memory_organization_enabled,
            ),
            owner_truth_memory_projection_worker_enabled=_env_bool(
                "OWNER_TRUTH_MEMORY_PROJECTION_WORKER_ENABLED",
                cls.owner_truth_memory_projection_worker_enabled,
            ),
            owner_truth_memory_search_projection_worker_enabled=_env_bool(
                "OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_WORKER_ENABLED",
                cls.owner_truth_memory_search_projection_worker_enabled,
            ),
            owner_truth_context_authority_enabled=_env_bool(
                "OWNER_TRUTH_CONTEXT_AUTHORITY_ENABLED",
                _env_bool(
                    "OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED",
                    cls.owner_truth_context_authority_enabled,
                ),
            ),
            owner_truth_context_authority_closed_pilot_enabled=_env_bool(
                "OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED",
                cls.owner_truth_context_authority_closed_pilot_enabled,
            ),
            owner_truth_media_capture_enabled=_env_bool(
                "OWNER_TRUTH_MEDIA_CAPTURE_ENABLED",
                cls.owner_truth_media_capture_enabled,
            ),
            owner_truth_media_storage_provider=_env(
                "OWNER_TRUTH_MEDIA_STORAGE_PROVIDER",
                cls.owner_truth_media_storage_provider,
            ) or cls.owner_truth_media_storage_provider,
            owner_truth_media_storage_root=_env(
                "OWNER_TRUTH_MEDIA_STORAGE_ROOT",
                cls.owner_truth_media_storage_root,
            ) or cls.owner_truth_media_storage_root,
            owner_truth_media_storage_external_verified=_env_bool(
                "OWNER_TRUTH_MEDIA_STORAGE_EXTERNAL_VERIFIED",
                cls.owner_truth_media_storage_external_verified,
            ),
            owner_truth_media_storage_evidence_timestamp=_env(
                "OWNER_TRUTH_MEDIA_STORAGE_EVIDENCE_TIMESTAMP"
            ),
            owner_truth_media_s3_bucket=_env("OWNER_TRUTH_MEDIA_S3_BUCKET"),
            owner_truth_media_s3_prefix=_env(
                "OWNER_TRUTH_MEDIA_S3_PREFIX",
                cls.owner_truth_media_s3_prefix,
            ) or cls.owner_truth_media_s3_prefix,
            owner_truth_media_s3_region=_env("OWNER_TRUTH_MEDIA_S3_REGION"),
            owner_truth_media_s3_endpoint_url=_env("OWNER_TRUTH_MEDIA_S3_ENDPOINT_URL"),
            owner_truth_media_s3_access_key_id=_env("OWNER_TRUTH_MEDIA_S3_ACCESS_KEY_ID"),
            owner_truth_media_s3_secret_access_key=_env("OWNER_TRUTH_MEDIA_S3_SECRET_ACCESS_KEY"),
            owner_truth_media_s3_server_side_encryption=_env(
                "OWNER_TRUTH_MEDIA_S3_SERVER_SIDE_ENCRYPTION"
            ),
            owner_truth_media_s3_kms_key_id=_env("OWNER_TRUTH_MEDIA_S3_KMS_KEY_ID"),
            owner_truth_media_upload_intent_ttl_seconds=max(
                60,
                _env_int(
                    "OWNER_TRUTH_MEDIA_UPLOAD_INTENT_TTL_SECONDS",
                    cls.owner_truth_media_upload_intent_ttl_seconds,
                ),
            ),
            owner_truth_media_max_upload_bytes=max(
                1,
                _env_int(
                    "OWNER_TRUTH_MEDIA_MAX_UPLOAD_BYTES",
                    cls.owner_truth_media_max_upload_bytes,
                ),
            ),
            owner_truth_media_content_safety_provider=_env(
                "OWNER_TRUTH_MEDIA_CONTENT_SAFETY_PROVIDER",
                cls.owner_truth_media_content_safety_provider,
            ) or cls.owner_truth_media_content_safety_provider,
            owner_truth_media_clamav_host=_env("OWNER_TRUTH_MEDIA_CLAMAV_HOST"),
            owner_truth_media_clamav_port=min(
                65535,
                max(
                    1,
                    _env_int(
                        "OWNER_TRUTH_MEDIA_CLAMAV_PORT",
                        cls.owner_truth_media_clamav_port,
                    ),
                ),
            ),
            owner_truth_media_clamav_timeout_seconds=min(
                60,
                max(
                    1,
                    _env_int(
                        "OWNER_TRUTH_MEDIA_CLAMAV_TIMEOUT_SECONDS",
                        cls.owner_truth_media_clamav_timeout_seconds,
                    ),
                ),
            ),
            owner_truth_media_processing_worker_enabled=_env_bool(
                "OWNER_TRUTH_MEDIA_PROCESSING_WORKER_ENABLED",
                cls.owner_truth_media_processing_worker_enabled,
            ),
            owner_truth_media_processing_external_verified=_env_bool(
                "OWNER_TRUTH_MEDIA_PROCESSING_EXTERNAL_VERIFIED",
                cls.owner_truth_media_processing_external_verified,
            ),
            owner_truth_media_processing_evidence_timestamp=_env(
                "OWNER_TRUTH_MEDIA_PROCESSING_EVIDENCE_TIMESTAMP"
            ),
            owner_truth_media_deletion_worker_enabled=_env_bool(
                "OWNER_TRUTH_MEDIA_DELETION_WORKER_ENABLED",
                cls.owner_truth_media_deletion_worker_enabled,
            ),
            voice_clone_deletion_worker_enabled=_env_bool(
                "VOICE_CLONE_DELETION_WORKER_ENABLED",
                cls.voice_clone_deletion_worker_enabled,
            ),
            business_message_projection_worker_enabled=_env_bool(
                "BUSINESS_MESSAGE_PROJECTION_WORKER_ENABLED",
                cls.business_message_projection_worker_enabled,
            ),
            owner_truth_media_image_ocr_provider=_env(
                "OWNER_TRUTH_MEDIA_IMAGE_OCR_PROVIDER",
                cls.owner_truth_media_image_ocr_provider,
            ) or cls.owner_truth_media_image_ocr_provider,
            owner_truth_media_image_ocr_url=_env("OWNER_TRUTH_MEDIA_IMAGE_OCR_URL"),
            owner_truth_media_image_ocr_api_key=_env("OWNER_TRUTH_MEDIA_IMAGE_OCR_API_KEY"),
            owner_truth_media_audio_asr_provider=_env(
                "OWNER_TRUTH_MEDIA_AUDIO_ASR_PROVIDER",
                cls.owner_truth_media_audio_asr_provider,
            ) or cls.owner_truth_media_audio_asr_provider,
            owner_truth_media_audio_asr_url=_env("OWNER_TRUTH_MEDIA_AUDIO_ASR_URL"),
            owner_truth_media_audio_asr_api_key=_env("OWNER_TRUTH_MEDIA_AUDIO_ASR_API_KEY"),
            owner_truth_media_external_processor_timeout_seconds=max(
                1.0,
                min(
                    120.0,
                    _env_float(
                        "OWNER_TRUTH_MEDIA_EXTERNAL_PROCESSOR_TIMEOUT_SECONDS",
                        cls.owner_truth_media_external_processor_timeout_seconds,
                    ),
                ),
            ),
            owner_truth_media_external_processor_max_payload_bytes=max(
                1,
                min(
                    50 * 1024 * 1024,
                    _env_int(
                        "OWNER_TRUTH_MEDIA_EXTERNAL_PROCESSOR_MAX_PAYLOAD_BYTES",
                        cls.owner_truth_media_external_processor_max_payload_bytes,
                    ),
                ),
            ),
            owner_truth_document_parser_timeout_seconds=max(
                1,
                min(
                    120,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_TIMEOUT_SECONDS",
                        cls.owner_truth_document_parser_timeout_seconds,
                    ),
                ),
            ),
            owner_truth_document_parser_max_input_bytes=max(
                1,
                min(
                    50 * 1024 * 1024,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_MAX_INPUT_BYTES",
                        cls.owner_truth_document_parser_max_input_bytes,
                    ),
                ),
            ),
            owner_truth_document_parser_max_memory_bytes=max(
                64 * 1024 * 1024,
                min(
                    2 * 1024 * 1024 * 1024,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_MAX_MEMORY_BYTES",
                        cls.owner_truth_document_parser_max_memory_bytes,
                    ),
                ),
            ),
            owner_truth_document_parser_max_cpu_seconds=max(
                1,
                min(
                    60,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_MAX_CPU_SECONDS",
                        cls.owner_truth_document_parser_max_cpu_seconds,
                    ),
                ),
            ),
            owner_truth_document_parser_max_pdf_pages=max(
                1,
                min(
                    1_000,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_MAX_PDF_PAGES",
                        cls.owner_truth_document_parser_max_pdf_pages,
                    ),
                ),
            ),
            owner_truth_document_parser_max_docx_entries=max(
                1,
                min(
                    10_000,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_MAX_DOCX_ENTRIES",
                        cls.owner_truth_document_parser_max_docx_entries,
                    ),
                ),
            ),
            owner_truth_document_parser_max_docx_uncompressed_bytes=max(
                1,
                min(
                    100 * 1024 * 1024,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_MAX_DOCX_UNCOMPRESSED_BYTES",
                        cls.owner_truth_document_parser_max_docx_uncompressed_bytes,
                    ),
                ),
            ),
            owner_truth_document_parser_max_docx_compression_ratio=max(
                1,
                min(
                    1_000,
                    _env_int(
                        "OWNER_TRUTH_DOCUMENT_PARSER_MAX_DOCX_COMPRESSION_RATIO",
                        cls.owner_truth_document_parser_max_docx_compression_ratio,
                    ),
                ),
            ),
            delegated_access_contract_api_enabled=_env_bool(
                "DELEGATED_ACCESS_CONTRACT_API_ENABLED",
                cls.delegated_access_contract_api_enabled,
            ),
            publication_authority_qa_enabled=_env_bool(
                "PUBLICATION_AUTHORITY_QA_ENABLED",
                cls.publication_authority_qa_enabled,
            ),
            publication_visitor_access_qa_enabled=_env_bool(
                "PUBLICATION_VISITOR_ACCESS_QA_ENABLED",
                cls.publication_visitor_access_qa_enabled,
            ),
            publication_lifecycle_qa_enabled=_env_bool(
                "PUBLICATION_LIFECYCLE_QA_ENABLED",
                cls.publication_lifecycle_qa_enabled,
            ),
            publication_external_cleanup_materializer_enabled=_env_bool(
                "PUBLICATION_EXTERNAL_CLEANUP_MATERIALIZER_ENABLED",
                cls.publication_external_cleanup_materializer_enabled,
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
            realtime_voice_proxy_enabled=_env_bool(
                "REALTIME_VOICE_PROXY_ENABLED",
                cls.realtime_voice_proxy_enabled,
            ),
            realtime_voice_ticket_ttl_seconds=_env_int(
                "REALTIME_VOICE_TICKET_TTL_SECONDS",
                cls.realtime_voice_ticket_ttl_seconds,
            ),
            realtime_voice_max_session_seconds=_env_int(
                "REALTIME_VOICE_MAX_SESSION_SECONDS",
                cls.realtime_voice_max_session_seconds,
            ),
            realtime_voice_max_concurrent_sessions_per_user=_env_int(
                "REALTIME_VOICE_MAX_CONCURRENT_SESSIONS_PER_USER",
                cls.realtime_voice_max_concurrent_sessions_per_user,
            ),
            realtime_voice_auth_recheck_seconds=_env_float(
                "REALTIME_VOICE_AUTH_RECHECK_SECONDS",
                cls.realtime_voice_auth_recheck_seconds,
            ),
            realtime_voice_upstream_connect_timeout_seconds=_env_float(
                "REALTIME_VOICE_UPSTREAM_CONNECT_TIMEOUT_SECONDS",
                cls.realtime_voice_upstream_connect_timeout_seconds,
            ),
            realtime_voice_max_frame_bytes=_env_int(
                "REALTIME_VOICE_MAX_FRAME_BYTES",
                cls.realtime_voice_max_frame_bytes,
            ),
            realtime_voice_max_session_bytes=_env_int(
                "REALTIME_VOICE_MAX_SESSION_BYTES",
                cls.realtime_voice_max_session_bytes,
            ),
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
            apns_delivery_provider=_env(
                "APNS_DELIVERY_PROVIDER",
                cls.apns_delivery_provider,
            ) or cls.apns_delivery_provider,
            apns_token_vault_provider=_env(
                "APNS_TOKEN_VAULT_PROVIDER",
                cls.apns_token_vault_provider,
            ) or cls.apns_token_vault_provider,
            apns_topic=_env("APNS_TOPIC"),
            apns_environment=_env(
                "APNS_ENVIRONMENT",
                cls.apns_environment,
            ) or cls.apns_environment,
            apns_max_attempts=_env_int(
                "APNS_MAX_ATTEMPTS",
                cls.apns_max_attempts,
            ),
            apns_token_encryption_key=_env("APNS_TOKEN_ENCRYPTION_KEY"),
            apns_token_encryption_key_version=_env(
                "APNS_TOKEN_ENCRYPTION_KEY_VERSION",
                cls.apns_token_encryption_key_version,
            ) or cls.apns_token_encryption_key_version,
            apns_team_id=_env("APNS_TEAM_ID"),
            apns_key_id=_env("APNS_KEY_ID"),
            apns_private_key_path=_env("APNS_PRIVATE_KEY_PATH"),
            apns_request_timeout_seconds=_env_int(
                "APNS_REQUEST_TIMEOUT_SECONDS",
                cls.apns_request_timeout_seconds,
            ),
            apns_external_verified=_env_bool(
                "APNS_EXTERNAL_VERIFIED",
                cls.apns_external_verified,
            ),
        )


settings = Settings.from_env()
