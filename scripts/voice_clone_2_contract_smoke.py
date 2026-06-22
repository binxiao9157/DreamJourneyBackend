#!/usr/bin/env python3
import json
import os

from app.core.config import Settings
from app.services.runtime_config import RuntimeConfigService
from app.services.tts import VolcVoiceCloneTTSProxy
from app.services.voice_clone import VolcEngineVoiceCloneV3Provider


def _settings() -> Settings:
    base = Settings.from_env()
    return Settings(
        **{
            **base.__dict__,
            "volcengine_voice_clone_api_key": base.volcengine_voice_clone_api_key or "dry-run-train-key",
            "volcengine_voice_clone_tts_api_key": base.volcengine_voice_clone_tts_api_key or "dry-run-tts-key",
            "volcengine_voice_clone_speaker_id_mode": (
                base.volcengine_voice_clone_speaker_id_mode
                if base.volcengine_voice_clone_speaker_ids or base.volcengine_voice_clone_speaker_id
                else "trialSpeakerIdPool"
            ),
            "volcengine_voice_clone_speaker_ids": (
                base.volcengine_voice_clone_speaker_ids
                or base.volcengine_voice_clone_speaker_id
                or "S_dryrun_001,S_dryrun_002"
            ),
            "volcengine_voice_clone_model_type": base.volcengine_voice_clone_model_type or 5,
            "volcengine_voice_clone_tts_resource_id": (
                base.volcengine_voice_clone_tts_resource_id or "seed-icl-2.0"
            ),
        }
    )


def main() -> None:
    profile_id = os.getenv("VOICE_CLONE_2_SMOKE_PROFILE_ID", "voice_profile_contract_smoke")
    settings = _settings()
    runtime = RuntimeConfigService(settings).public_config()["voiceClone"]
    train_request = VolcEngineVoiceCloneV3Provider(settings).build_training_request(
        voice_profile_id=profile_id,
        audio_base64="BASE64_AUDIO_SAMPLE",
        audio_format="wav",
        language=0,
    )
    synthesis_request = VolcVoiceCloneTTSProxy(settings).build_synthesis_request(
        text="你好，欢迎回家。",
        user_id="voice-clone-2-contract-smoke",
        voice_profile_id=train_request["json"]["speaker_id"],
    )

    payload = {
        "voiceClone2TrialReady": runtime["voiceClone2TrialReady"],
        "speakerIdMode": runtime["speakerIdMode"],
        "speakerIdPoolCount": runtime["speakerIdPoolCount"],
        "trainingSpeakerId": train_request["json"]["speaker_id"],
        "trainingModelType": train_request["json"]["model_type"],
        "trainingHasCustomSpeakerId": "custom_speaker_id" in train_request["json"],
        "ttsResourceId": synthesis_request["headers"].get("Resource-Id"),
        "ttsHasXApiResourceId": "X-Api-Resource-Id" in synthesis_request["headers"],
        "ttsVoiceType": synthesis_request["json"]["audio"]["voice_type"],
    }
    assert payload["speakerIdPoolCount"] >= 1, payload
    assert payload["trainingSpeakerId"].startswith("S_"), payload
    assert payload["trainingModelType"] == 5, payload
    assert payload["trainingHasCustomSpeakerId"] is False, payload
    assert payload["ttsResourceId"] == "seed-icl-2.0", payload
    assert payload["ttsHasXApiResourceId"] is False, payload
    assert payload["ttsVoiceType"] == payload["trainingSpeakerId"], payload
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
