import base64
import uuid
import json
import urllib.error
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


class MockVoiceCloneTTSProvider:
    provider_mode = "mockContract"
    is_configured = False

    def synthesize(
        self,
        *,
        text: str,
        user_id: str,
        voice_profile_id: str,
        audio_format: str,
        sample_rate: int,
        speech_rate: int,
        loudness_rate: int,
    ) -> Dict[str, Any]:
        raise ValueError("VolcEngine voice clone TTS provider is not configured")


class VolcVoiceCloneTTSProxy:
    provider_mode = "volcengineVoiceCloneV3TTS"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.volcengine_voice_clone_api_key)

    def build_synthesis_request(
        self,
        *,
        text: str,
        user_id: str,
        voice_profile_id: str,
        audio_format: str = "mp3",
        sample_rate: int = 24000,
        speech_rate: int = -10,
        loudness_rate: int = 10,
    ) -> Dict[str, Any]:
        api_key = self._required_api_key()
        if not text.strip():
            raise ValueError("text is required")
        if not voice_profile_id.strip():
            raise ValueError("voiceProfileId is required")

        additions = json.dumps({"explicit_language": "zh-cn"}, ensure_ascii=False)
        return {
            "url": self.settings.volcengine_voice_clone_tts_url,
            "headers": {
                "Content-Type": "application/json",
                "X-Api-Key": api_key,
                "X-Api-Request-Id": str(uuid.uuid4()),
                "X-Api-Resource-Id": self.settings.volcengine_voice_clone_tts_resource_id,
            },
            "json": {
                "user": {
                    "uid": user_id,
                },
                "req_params": {
                    "text": text,
                    "speaker": voice_profile_id,
                    "audio_params": {
                        "format": audio_format,
                        "sample_rate": sample_rate,
                        "speech_rate": speech_rate,
                        "loudness_rate": loudness_rate,
                    },
                    "additions": additions,
                },
            },
        }

    def synthesize(
        self,
        *,
        text: str,
        user_id: str,
        voice_profile_id: str,
        audio_format: str = "mp3",
        sample_rate: int = 24000,
        speech_rate: int = -10,
        loudness_rate: int = 10,
    ) -> Dict[str, Any]:
        request = self.build_synthesis_request(
            text=text,
            user_id=user_id,
            voice_profile_id=voice_profile_id,
            audio_format=audio_format,
            sample_rate=sample_rate,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
        )
        body = json.dumps(request["json"], ensure_ascii=False).encode("utf-8")
        upstream = urllib.request.Request(
            request["url"],
            data=body,
            headers=request["headers"],
            method="POST",
        )
        try:
            with urllib.request.urlopen(upstream, timeout=60) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"voice clone TTS provider HTTP {exc.code}: {detail[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"voice clone TTS provider network error: {exc.reason}") from exc

        audio = self.parse_chunked_audio_response(payload)
        return {
            "audioBase64": base64.b64encode(audio).decode("ascii"),
            "audioFormat": audio_format,
            "byteCount": len(audio),
            "providerMode": self.provider_mode,
            "voiceProfileId": voice_profile_id,
        }

    def parse_chunked_audio_response(self, payload: str) -> bytes:
        audio = bytearray()
        for line in payload.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError("voice clone TTS provider returned invalid JSON chunk") from exc

            code = int(item.get("code") or 0)
            if code == 20000000:
                break
            if code != 0:
                message = str(item.get("message") or item.get("msg") or "unknown error")
                raise ValueError(f"voice clone TTS provider error {code}: {message}")
            data = item.get("data")
            if data:
                audio.extend(base64.b64decode(str(data)))

        if not audio:
            raise ValueError("voice clone TTS provider returned empty audio")
        return bytes(audio)

    def _required_api_key(self) -> str:
        api_key = self.settings.volcengine_voice_clone_api_key
        if not api_key:
            raise ValueError("VolcEngine voice clone API key is not configured")
        return api_key


class VoiceCloneTTSProviderFactory:
    def __init__(self, settings: Settings):
        self.settings = settings

    def make(self):
        if self.settings.volcengine_voice_clone_api_key:
            return VolcVoiceCloneTTSProxy(self.settings)
        return MockVoiceCloneTTSProvider()
