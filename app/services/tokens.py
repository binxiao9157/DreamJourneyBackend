from typing import Any, Dict, Mapping, Optional

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
        purpose: str = "echoLive",
        persona_scope: str = "personal",
        target_persona_id: Optional[str] = None,
        product_session_id: Optional[str] = None,
        client_session_id: Optional[str] = None,
        projection_checkpoint: Optional[str] = None,
        context_hash: Optional[str] = None,
        authority_epoch: Optional[int] = None,
        session_context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return RealtimeVoiceSessionBroker(self.settings, store).issue_runtime_config(
            user_id=user_id,
            auth_session_id=auth_session_id,
            purpose=purpose,
            persona_scope=persona_scope,
            target_persona_id=target_persona_id,
            product_session_id=product_session_id,
            client_session_id=client_session_id,
            projection_checkpoint=projection_checkpoint,
            context_hash=context_hash,
            authority_epoch=authority_epoch,
            session_context=session_context,
        )
