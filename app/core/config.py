from dataclasses import dataclass
import os
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    app_name: str = "DreamJourney Backend"
    environment: str = "development"
    public_base_url: Optional[str] = None
    store_backend: str = "postgres"
    database_url: str = "postgresql://dreamjourney:dreamjourney@postgres:5432/dreamjourney"
    redis_url: str = "redis://redis:6379/0"

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

    amap_web_service_key: Optional[str] = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=_env("APP_NAME", "DreamJourney Backend") or "DreamJourney Backend",
            environment=_env("APP_ENV", "development") or "development",
            public_base_url=_env("PUBLIC_BASE_URL"),
            store_backend=_env("STORE_BACKEND", cls.store_backend) or cls.store_backend,
            database_url=_env("DATABASE_URL", cls.database_url) or cls.database_url,
            redis_url=_env("REDIS_URL", cls.redis_url) or cls.redis_url,
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
            amap_web_service_key=_env("AMAP_WEB_SERVICE_KEY"),
        )


settings = Settings.from_env()
