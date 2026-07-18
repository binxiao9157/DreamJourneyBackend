import base64
from io import BytesIO
import uuid
import json
import urllib.error
import urllib.request
import wave
from typing import Any, Dict, Optional

try:
    import audioop
except ImportError:  # pragma: no cover - Python runtimes without audioop can still pass through exact PCM.
    audioop = None

from app.core.config import Settings
from app.observability.redaction import provider_dry_run_report


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

    def dry_run_report(
        self,
        *,
        text: str,
        user_id: str,
        voice_type: str = None,
        encoding: str = "wav",
        speed_ratio: float = 1.0,
    ) -> Dict[str, Any]:
        # Reuse production validation without returning the generated request,
        # which contains text, identity, selected voice ID, and credential.
        self.build_request(
            text=text,
            user_id=user_id,
            voice_type=voice_type,
            encoding=encoding,
            speed_ratio=speed_ratio,
        )
        normalized_encoding = str(encoding or "wav").strip().lower()
        return provider_dry_run_report(
            provider="volcengine",
            capability="legacyTts",
            method="POST",
            configured=bool(self.settings.volcengine_api_key and self.settings.volcengine_voice_type),
            input_summary={
                "encodingCategory": (
                    "standard" if normalized_encoding in {"wav", "mp3", "pcm", "ogg"} else "other"
                ),
                "speedRatio": speed_ratio,
                "textCharacterCount": len(text.strip()),
                "voiceSelectionMode": "requestOverride" if voice_type else "serverDefault",
            },
        )


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


class TencentAudioDrivePCMAdapter:
    audio_format = "pcm16kMono"
    sample_rate = 16000
    bits_per_sample = 16
    channel_count = 1

    def adapt(self, *, audio_base64: str, audio_format: str) -> Dict[str, Any]:
        try:
            audio = base64.b64decode(str(audio_base64))
        except ValueError as exc:
            raise ValueError("voice clone TTS provider returned invalid base64 audio") from exc

        normalized_format = str(audio_format or "").strip().lower()
        if normalized_format in {"pcm16kmono", "pcm_16k_mono", "pcm16", "pcm"}:
            pcm = audio
        elif normalized_format == "wav":
            pcm = self._wav_to_pcm16k_mono(audio)
        else:
            raise ValueError(
                "voice clone TTS audio format "
                f"{audio_format!r} cannot be converted to Tencent audio-drive PCM"
            )

        bytes_per_second = self.sample_rate * self.channel_count * (self.bits_per_sample // 8)
        duration_seconds = round(len(pcm) / bytes_per_second, 3) if bytes_per_second > 0 else 0

        return {
            "encoding": "base64",
            "format": self.audio_format,
            "sampleRate": self.sample_rate,
            "bitsPerSample": self.bits_per_sample,
            "channelCount": self.channel_count,
            "data": base64.b64encode(pcm).decode("ascii"),
            "byteCount": len(pcm),
            "durationSeconds": duration_seconds,
        }

    def _wav_to_pcm16k_mono(self, audio: bytes) -> bytes:
        try:
            with wave.open(BytesIO(audio), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                frame_rate = wav.getframerate()
                frames = wav.readframes(wav.getnframes())
        except wave.Error as exc:
            raise ValueError("voice clone TTS provider returned invalid WAV audio") from exc

        if channels <= 0 or sample_width <= 0 or frame_rate <= 0:
            raise ValueError("voice clone TTS provider returned invalid WAV metadata")

        if sample_width != 2:
            self._require_audioop("sample width conversion")
            frames = audioop.lin2lin(frames, sample_width, 2)
            sample_width = 2

        if channels == 2:
            self._require_audioop("stereo downmix")
            frames = audioop.tomono(frames, sample_width, 0.5, 0.5)
            channels = 1
        elif channels != 1:
            raise ValueError("voice clone TTS WAV must be mono or stereo for Tencent audio-drive conversion")

        if frame_rate != self.sample_rate:
            self._require_audioop("sample-rate conversion")
            frames, _ = audioop.ratecv(frames, sample_width, channels, frame_rate, self.sample_rate, None)

        return frames

    @staticmethod
    def _require_audioop(operation: str) -> None:
        if audioop is None:
            raise ValueError(f"Python audioop is required for {operation}")


class VolcVoiceCloneTTSProxy:
    provider_mode = "volcengineVoiceCloneV1TTS"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.volcengine_voice_clone_tts_api_key)

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

        speed_ratio = max(0.5, min(2.0, 1.0 + (speech_rate / 100.0)))
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }
        resource_id = str(self.settings.volcengine_voice_clone_tts_resource_id or "").strip()
        if resource_id:
            headers["Resource-Id"] = resource_id
        return {
            "url": self.settings.volcengine_voice_clone_tts_url,
            "headers": headers,
            "json": {
                "app": {
                    "cluster": self.settings.volcengine_voice_clone_tts_cluster,
                },
                "user": {
                    "uid": user_id,
                },
                "audio": {
                    "voice_type": voice_profile_id,
                    "encoding": audio_format,
                    "speed_ratio": speed_ratio,
                },
                "request": {
                    "reqid": uuid.uuid4().hex,
                    "text": text,
                    "operation": "query",
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
        provider_request_id = str(request["json"].get("request", {}).get("reqid") or "")
        body = json.dumps(request["json"], ensure_ascii=False).encode("utf-8")
        upstream = urllib.request.Request(
            request["url"],
            data=body,
            headers=request["headers"],
            method="POST",
        )
        provider_log_id = ""
        try:
            with urllib.request.urlopen(upstream, timeout=60) as response:
                payload = response.read().decode("utf-8")
                provider_log_id = self._header_value(response.headers, "X-Tt-Logid")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error = ValueError(f"voice clone TTS provider HTTP {exc.code}: {detail[:200]}")
            setattr(error, "provider_request_id", provider_request_id)
            setattr(error, "provider_log_id", self._header_value(exc.headers, "X-Tt-Logid"))
            raise error from exc
        except urllib.error.URLError as exc:
            error = ValueError(f"voice clone TTS provider network error: {exc.reason}")
            setattr(error, "provider_request_id", provider_request_id)
            raise error from exc

        try:
            response_json = json.loads(payload)
        except json.JSONDecodeError as exc:
            error = ValueError("voice clone TTS provider returned invalid JSON")
            setattr(error, "provider_request_id", provider_request_id)
            setattr(error, "provider_log_id", provider_log_id)
            raise error from exc

        audio = self.parse_tts_response(response_json)
        viseme_timeline = self.parse_viseme_timeline(
            response_json.get("visemeTimeline") or response_json.get("lipSyncTimeline")
        )
        provider_request_id = str(
            response_json.get("request_id")
            or response_json.get("reqid")
            or provider_request_id
        )
        provider_log_id = str(response_json.get("log_id") or response_json.get("logid") or provider_log_id)
        result = {
            "audioBase64": base64.b64encode(audio).decode("ascii"),
            "audioFormat": audio_format,
            "byteCount": len(audio),
            "providerMode": self.provider_mode,
            "voiceProfileId": voice_profile_id,
            "visemeTimeline": viseme_timeline,
        }
        if provider_request_id:
            result["providerRequestId"] = provider_request_id
        if provider_log_id:
            result["providerLogId"] = provider_log_id
        return result

    @staticmethod
    def _header_value(headers: Any, name: str) -> str:
        if not headers:
            return ""
        get = getattr(headers, "get", None)
        if callable(get):
            return str(get(name) or get(name.lower()) or get(name.upper()) or "").strip()
        try:
            return str(headers[name] or "").strip()
        except (KeyError, TypeError):
            return ""

    def parse_tts_response(self, payload: Dict[str, Any]) -> bytes:
        code = payload.get("code")
        if code is not None and int(code) not in {0, 3000, 20000000}:
            message = str(payload.get("message") or payload.get("msg") or "unknown error")
            raise ValueError(f"voice clone TTS provider error {code}: {message}")

        data = payload.get("data")
        if not data:
            raise ValueError("voice clone TTS provider returned empty audio")
        try:
            return base64.b64decode(str(data))
        except ValueError as exc:
            raise ValueError("voice clone TTS provider returned invalid base64 audio") from exc

    def parse_viseme_timeline(self, payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None

        raw_frames = payload.get("frames")
        if not isinstance(raw_frames, list):
            return None

        frames = []
        for item in raw_frames:
            if not isinstance(item, dict):
                continue
            try:
                time_offset = float(item.get("timeOffset"))
            except (TypeError, ValueError):
                continue
            if time_offset < 0:
                continue

            mouth_shape = str(item.get("mouthShape") or item.get("viseme") or "neutral").strip()[:32]
            if not mouth_shape:
                mouth_shape = "neutral"
            try:
                intensity = float(item.get("intensity") if item.get("intensity") is not None else 0)
            except (TypeError, ValueError):
                intensity = 0
            intensity = max(0.0, min(1.0, intensity))
            frames.append(
                {
                    "timeOffset": round(time_offset, 3),
                    "mouthShape": mouth_shape,
                    "intensity": round(intensity, 3),
                }
            )

        if not frames:
            return None

        frames.sort(key=lambda frame: frame["timeOffset"])
        try:
            duration = float(payload.get("duration") or payload.get("durationSeconds"))
        except (TypeError, ValueError):
            duration = frames[-1]["timeOffset"] + 0.12
        duration = max(0.0, duration, frames[-1]["timeOffset"])

        return {
            "source": str(payload.get("source") or "providerVisemeTimeline"),
            "duration": round(duration, 3),
            "frames": frames,
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
        api_key = self.settings.volcengine_voice_clone_tts_api_key
        if not api_key:
            raise ValueError("VolcEngine voice clone TTS API key is not configured")
        return api_key


class VoiceCloneTTSProviderFactory:
    def __init__(self, settings: Settings):
        self.settings = settings

    def make(self):
        if self.settings.volcengine_voice_clone_tts_api_key:
            return VolcVoiceCloneTTSProxy(self.settings)
        return MockVoiceCloneTTSProvider()
