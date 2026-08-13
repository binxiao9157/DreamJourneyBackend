from typing import Any, Dict

from app.core.config import Settings
from app.services.realtime_voice_proxy import RealtimeVoiceSessionBroker


class TokenService:
    _SCOPED_SESSION_REQUIREMENTS = ("scope", "ttl", "audience", "revocation")

    def __init__(self, settings: Settings):
        self.settings = settings

    def realtime_config(self, user_id: str) -> Dict[str, Any]:
        del user_id
        return RealtimeVoiceSessionBroker(self.settings, store=None).capability_descriptor()

    def issue_realtime_config(
        self,
        *,
        user_id: str,
        auth_session_id: str,
        store: Any,
    ) -> Dict[str, Any]:
        return RealtimeVoiceSessionBroker(self.settings, store).issue_runtime_config(
            user_id=user_id,
            auth_session_id=auth_session_id,
        )
