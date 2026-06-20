import json
import uuid
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from app.core.config import Settings


class VoiceCloneProviderUnavailable(ValueError):
    pass


class VoiceCloneProviderError(ValueError):
    def __init__(self, message: str, *, provider_request_id: str = "", provider_log_id: str = ""):
        super().__init__(message)
        self.provider_request_id = provider_request_id
        self.provider_log_id = provider_log_id


class MockVoiceCloneProvider:
    provider_mode = "mockContract"
    is_configured = False

    def submit_training(
        self,
        *,
        voice_profile_id: str,
        audio_base64: str,
        audio_format: str,
        language: int,
    ) -> Dict[str, Any]:
        raise VoiceCloneProviderUnavailable("VolcEngine voice clone provider is not configured")

    def query_status(self, *, voice_profile_id: str) -> Dict[str, Any]:
        raise VoiceCloneProviderUnavailable("VolcEngine voice clone provider is not configured")


class VolcEngineVoiceCloneV3Provider:
    provider_mode = "volcengineVoiceCloneV3"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.volcengine_voice_clone_api_key)

    def build_training_request(
        self,
        *,
        voice_profile_id: str,
        audio_base64: str,
        audio_format: str,
        language: int,
    ) -> Dict[str, Any]:
        api_key = self._required_api_key()
        if not voice_profile_id.strip():
            raise ValueError("voiceProfileId is required")
        if not audio_base64.strip():
            raise ValueError("audioBase64 is required")
        speaker_payload = self._training_speaker_payload(voice_profile_id)
        return {
            "url": self.settings.volcengine_voice_clone_train_url,
            "headers": self._headers(api_key),
            "json": {
                **speaker_payload,
                "audio": {
                    "data": audio_base64,
                    "format": audio_format or "wav",
                },
                "language": language,
                "extra_params": {
                    "voice_clone_denoise_model_id": "",
                },
            },
        }

    def build_query_request(self, *, voice_profile_id: str) -> Dict[str, Any]:
        api_key = self._required_api_key()
        if not voice_profile_id.strip():
            raise ValueError("voiceProfileId is required")
        return {
            "url": self.settings.volcengine_voice_clone_query_url,
            "headers": self._headers(api_key),
            "json": {
                "speaker_id": voice_profile_id,
            },
        }

    def submit_training(
        self,
        *,
        voice_profile_id: str,
        audio_base64: str,
        audio_format: str,
        language: int,
    ) -> Dict[str, Any]:
        request = self.build_training_request(
            voice_profile_id=voice_profile_id,
            audio_base64=audio_base64,
            audio_format=audio_format,
            language=language,
        )
        response = self._post_json(request)
        fallback_voice_profile_id = str(
            request["json"].get("custom_speaker_id")
            or request["json"].get("speaker_id")
            or voice_profile_id
        )
        return self._normalize_response(response, fallback_voice_profile_id=fallback_voice_profile_id)

    def query_status(self, *, voice_profile_id: str) -> Dict[str, Any]:
        response = self._post_json(self.build_query_request(voice_profile_id=voice_profile_id))
        return self._normalize_response(response, fallback_voice_profile_id=voice_profile_id)

    def _required_api_key(self) -> str:
        api_key = self.settings.volcengine_voice_clone_api_key
        if api_key:
            return api_key
        raise VoiceCloneProviderUnavailable("VolcEngine voice clone API key is not configured")

    def _headers(self, api_key: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

    def _training_speaker_payload(self, voice_profile_id: str) -> Dict[str, str]:
        mode = str(self.settings.volcengine_voice_clone_speaker_id_mode or "customSpeakerId").strip().lower()
        if mode in {"consolespeakerid", "console_speaker_id", "prepaid", "free"}:
            speaker_id = str(self.settings.volcengine_voice_clone_speaker_id or "").strip()
            if not speaker_id:
                raise ValueError(
                    "VOLCENGINE_VOICE_CLONE_SPEAKER_ID is required when "
                    "VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=consoleSpeakerId"
                )
            return {"speaker_id": speaker_id}
        return {
            "speaker_id": "custom_speaker_id",
            "custom_speaker_id": voice_profile_id,
        }

    def _post_json(self, request: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(request["json"], ensure_ascii=False).encode("utf-8")
        provider_request_id = str(request["headers"].get("X-Api-Request-Id") or "")
        upstream = urllib.request.Request(
            request["url"],
            data=body,
            headers=request["headers"],
            method="POST",
        )
        try:
            with urllib.request.urlopen(upstream, timeout=45) as response:
                payload = response.read().decode("utf-8")
                provider_log_id = self._header_value(response.headers, "X-Tt-Logid")
            result = json.loads(payload) if payload else {}
            if isinstance(result, dict):
                result["_providerRequestId"] = provider_request_id
                if provider_log_id:
                    result["_providerLogId"] = provider_log_id
            return result
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise VoiceCloneProviderError(
                f"voice clone provider HTTP {exc.code}: {detail[:200]}",
                provider_request_id=provider_request_id,
                provider_log_id=self._header_value(exc.headers, "X-Tt-Logid"),
            ) from exc
        except urllib.error.URLError as exc:
            raise VoiceCloneProviderError(
                f"voice clone provider network error: {exc.reason}",
                provider_request_id=provider_request_id,
            ) from exc
        except json.JSONDecodeError as exc:
            raise VoiceCloneProviderError(
                "voice clone provider returned invalid JSON",
                provider_request_id=provider_request_id,
            ) from exc

    def _normalize_response(
        self,
        response: Dict[str, Any],
        *,
        fallback_voice_profile_id: str,
    ) -> Dict[str, Any]:
        code = response.get("code")
        message = str(response.get("message") or response.get("msg") or "")
        provider_request_id = str(response.get("request_id") or response.get("reqid") or response.get("task_id") or response.get("_providerRequestId") or "")
        provider_log_id = str(response.get("log_id") or response.get("logid") or response.get("_providerLogId") or "")
        if isinstance(code, int) and code not in {0, 20000000}:
            result = {
                "voiceProfileId": fallback_voice_profile_id,
                "providerStatus": "failed",
                "sampleStatus": "failed",
                "providerMessage": message or f"provider code {code}",
            }
            if provider_request_id:
                result["providerRequestId"] = provider_request_id
            if provider_log_id:
                result["providerLogId"] = provider_log_id
            return result

        provider_status = str(response.get("status") or response.get("state") or "pending")
        sample_status = self._sample_status(provider_status)
        provider_voice_profile_id = str(response.get("custom_speaker_id") or response.get("voiceProfileId") or fallback_voice_profile_id)
        response_speaker_id = str(response.get("speaker_id") or "").strip()
        if response_speaker_id and response_speaker_id != "custom_speaker_id":
            provider_voice_profile_id = response_speaker_id
        result = {
            "voiceProfileId": provider_voice_profile_id,
            "providerRequestId": provider_request_id,
            "providerStatus": provider_status,
            "sampleStatus": sample_status,
            "providerMessage": message,
        }
        if provider_log_id:
            result["providerLogId"] = provider_log_id
        return result

    @staticmethod
    def _header_value(headers: Any, name: str) -> str:
        if not headers:
            return ""
        value = ""
        get = getattr(headers, "get", None)
        if callable(get):
            value = get(name) or get(name.lower()) or get(name.upper()) or ""
        return str(value or "").strip()

    @staticmethod
    def _sample_status(provider_status: str) -> str:
        normalized = provider_status.strip().lower()
        if normalized in {"ready", "success", "succeeded", "done", "finished", "complete", "completed"}:
            return "ready"
        if normalized in {"failed", "fail", "error", "rejected"}:
            return "failed"
        return "pending"


class VoiceCloneProviderFactory:
    def __init__(self, settings: Settings):
        self.settings = settings

    def make(self):
        if self.settings.volcengine_voice_clone_api_key:
            return VolcEngineVoiceCloneV3Provider(self.settings)
        return MockVoiceCloneProvider()
