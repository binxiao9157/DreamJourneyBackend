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
    auth_ownership_mode: str = "shadow"
    recovery_access_mode: str = "normal"
    authority_epoch: str = "epoch-0"
    release_policy_command_mode: str = "observe"
    release_policy_revision: int = 1
    release_policy_min_client_build: int = 1
    release_policy_ttl_seconds: int = 300
    release_policy_emergency_revision: int = 0
    release_policy_enforced_features: Optional[str] = None
    release_policy_emergency_disabled_features: Optional[str] = None
    evidence_rollout_retention_days: int = 30

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
            auth_ownership_mode=_env(
                "AUTH_OWNERSHIP_MODE",
                cls.auth_ownership_mode,
            ) or cls.auth_ownership_mode,
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
            evidence_rollout_retention_days=_env_int(
                "EVIDENCE_ROLLOUT_RETENTION_DAYS",
                cls.evidence_rollout_retention_days,
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
