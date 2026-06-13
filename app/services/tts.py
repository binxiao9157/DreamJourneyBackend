import uuid
import json
import urllib.request
from typing import Any, Dict

from app.core.config import Settings


class VolcTTSProxy:
    endpoint = "https://openspeech.bytedance.com/api/v1/tts"

    def __init__(self, settings: Settings):
        self.settings = settings

    def build_request(
        self,
        text: str,
        user_id: str,
        voice_type: str = None,
        encoding: str = "wav",
        speed_ratio: float = 1.0,
    ) -> Dict[str, Any]:
        api_key = self.settings.volcengine_api_key
        resolved_voice = voice_type or self.settings.volcengine_voice_type
        if not api_key:
            raise ValueError("VolcEngineAPIKey is not configured")
        if not resolved_voice:
            raise ValueError("VolcEngineVoiceType is not configured")
        if not text.strip():
            raise ValueError("text is required")

        return {
            "url": self.endpoint,
            "headers": {
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            "json": {
                "app": {"cluster": "volcano_icl"},
                "user": {"uid": user_id},
                "audio": {
                    "voice_type": resolved_voice,
                    "encoding": encoding,
                    "speed_ratio": speed_ratio,
                },
                "request": {
                    "reqid": uuid.uuid4().hex,
                    "text": text,
                    "operation": "query",
                },
            },
        }

    def request_tts(
        self,
        text: str,
        user_id: str,
        voice_type: str = None,
        encoding: str = "wav",
        speed_ratio: float = 1.0,
    ) -> Dict[str, Any]:
        request = self.build_request(
            text=text,
            user_id=user_id,
            voice_type=voice_type,
            encoding=encoding,
            speed_ratio=speed_ratio,
        )
        body = json.dumps(request["json"], ensure_ascii=False).encode("utf-8")
        upstream = urllib.request.Request(
            request["url"],
            data=body,
            headers=request["headers"],
            method="POST",
        )
        with urllib.request.urlopen(upstream, timeout=30) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)
