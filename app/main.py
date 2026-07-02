import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover - exercised only without runtime deps
    raise RuntimeError("FastAPI is not installed. Run `pip install -r requirements.txt`.") from exc

from app.core.config import settings
from app.services.amap import AMapDistrictProxy
from app.services.deepseek import ArchiveImageAnalysisProviderFactory
from app.services.privacy import (
    filter_syncable_graph,
    sanitize_archive_item_payload,
    sanitize_care_snapshot_payload,
    sanitize_image_analysis_payload,
    sanitize_knowledge_extraction_payload,
    sanitize_mailbox_letter_payload,
)
from app.services.deepseek import DeepSeekKnowledgeExtractionProxy
from app.services.passwords import make_password_credential, verify_password
from app.services.runtime_config import RuntimeConfigService
from app.services.context_packet import ContextPacketBuilder
from app.services.store_factory import init_store, make_store
from app.services.tokens import TokenService
from app.services.tts import TencentAudioDrivePCMAdapter, VolcTTSProxy, VoiceCloneTTSProviderFactory
from app.services.time_letters import (
    TimeLetterAccessError,
    dispatch_due_time_letters_for_store,
    time_letter_detail_for_viewer,
)
from app.services.voice_clone import VoiceCloneProviderFactory, VoiceCloneProviderUnavailable
from app.services.user_identity import stable_user_id


app = FastAPI(title=settings.app_name, version="0.1.0")
store = make_store(settings)

ARCHIVE_MEDIA_UPLOAD_PROVIDER = "mockObjectStorage"
ARCHIVE_MEDIA_UPLOAD_PROVIDER_DISPLAY_NAME = "Mock Object Storage"
ARCHIVE_MEDIA_UPLOAD_PROVIDER_MODE = "mock"
ARCHIVE_MEDIA_REQUIRES_CLIENT_UPLOAD = False
ARCHIVE_MEDIA_UPLOAD_URL_SCHEME = "mock"
ARCHIVE_MEDIA_REAL_PROVIDER_READY = False
ARCHIVE_MEDIA_PROVIDER_SWITCH_CONTRACT_VERSION = 1
ARCHIVE_MEDIA_CLIENT_UPLOAD_ACTION = "metadataOnly"
ARCHIVE_MEDIA_UPLOAD_TTL_SECONDS = 900
ARCHIVE_AUDIO_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024
ARCHIVE_VIDEO_UPLOAD_LIMIT_BYTES = 200 * 1024 * 1024
ARCHIVE_MEDIA_UPLOAD_LIMITS = {
    "audio": ARCHIVE_AUDIO_UPLOAD_LIMIT_BYTES,
    "video": ARCHIVE_VIDEO_UPLOAD_LIMIT_BYTES,
}
VOICE_CLONE_SAMPLE_STATUSES = {"notProvided", "pending", "ready", "disabled", "deleted", "failed"}
VOICE_CLONE_CONTRACT_VERSION = 1
VOICE_CLONE_PROVIDER_MODE = "mockContract"
VOICE_CLONE_AUTHORIZATION_COPY = (
    "声音克隆必须由用户主动授权，仅使用用户确认提交的声音样本；"
    "未完成授权、样本质量和合规验收前不会公开训练或合成功能。"
)
VOICE_CLONE_PROVIDER_ERROR_PREFIX = "voice clone provider error"
VOICE_CLONE_DISABLE_CONTRACT = (
    "disableVoiceProfile(profileId:) 应撤销该 voiceProfileId 的合成权限，"
    "当前后端仅保存 mock 禁用状态。"
)
VOICE_CLONE_DELETE_CONTRACT = (
    "deleteVoiceProfile(profileId:) 应删除样本、训练产物和关联授权记录，"
    "当前后端保存 deleted tombstone 以便验收生命周期。"
)
FAMILY_PERSONA_CONTRACT_VERSION = 1
FAMILY_PERSONA_CONTRACT_MODE = "mockFamilyPersona"
DIGITAL_HUMAN_MODE_LABELS = {
    "sunlight": "阳光",
    "star": "星辰",
    "silent": "静默",
}
ACCOUNT_DELETION_RETENTION_DAYS = 30
ACCOUNT_RESTORE_LIMIT = 1
ACCOUNT_DELETION_CONTRACT_VERSION = 1
DIGITAL_HUMAN_SESSION_CONTRACT_VERSION = 1
DIGITAL_HUMAN_SESSION_PROVIDER = "tencent"
DIGITAL_HUMAN_SESSION_MOCK_PROVIDER_MODE = "mockContract"
DIGITAL_HUMAN_SESSION_CLOUD_PROVIDER_MODE = "cloudRender"
DIGITAL_HUMAN_SESSION_DRIVE_MODE = "streamText"
DIGITAL_HUMAN_SESSION_TTL_SECONDS = 180


def _digital_human_provider_ready() -> bool:
    return bool(
        settings.tencent_digital_human_app_key
        and settings.tencent_digital_human_access_token
        and (
            settings.tencent_digital_human_asset_virtualman_key
            or settings.tencent_digital_human_virtualman_project_id
        )
    )


def _request_backend_api_token(request: Request) -> str:
    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(request.headers.get("x-dreamjourney-api-token") or "").strip()


@app.middleware("http")
async def require_backend_api_token(request: Request, call_next):
    if request.url.path == "/health" or not settings.backend_api_token:
        return await call_next(request)
    token = _request_backend_api_token(request)
    if not token or not secrets.compare_digest(token, settings.backend_api_token):
        return JSONResponse(status_code=401, content={"detail": "invalid backend api token"})
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    init_store(store)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "store": settings.store_backend,
    }


@app.post("/digital-human/sessions")
def create_digital_human_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    persona_id = str(payload.get("personaId") or "").strip()
    scene = str(payload.get("scene") or "echo").strip() or "echo"
    device_id = str(payload.get("deviceId") or "").strip()
    lifecycle_mode = str(payload.get("lifecycleMode") or "sunlight").strip() or "sunlight"
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not persona_id:
        raise HTTPException(status_code=400, detail="personaId is required")
    if lifecycle_mode not in DIGITAL_HUMAN_MODE_LABELS:
        raise HTTPException(status_code=400, detail=f"unsupported lifecycleMode: {lifecycle_mode}")
    if lifecycle_mode == "silent":
        raise HTTPException(status_code=409, detail="silent mode must not create a digital human render session")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=DIGITAL_HUMAN_SESSION_TTL_SECONDS)
    session_seed = f"{user_id}:{persona_id}:{scene}:{device_id}:{now.isoformat()}"
    session_id = "dh_session_" + hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:24]
    cloud_render_ready = _digital_human_provider_ready()
    provider_mode = (
        DIGITAL_HUMAN_SESSION_CLOUD_PROVIDER_MODE
        if cloud_render_ready
        else DIGITAL_HUMAN_SESSION_MOCK_PROVIDER_MODE
    )
    provider_asset_id = settings.tencent_digital_human_asset_virtualman_key
    provider_project_id = settings.tencent_digital_human_virtualman_project_id
    if not cloud_render_ready:
        provider_asset_id = "mock_asset_" + hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:12]
        provider_project_id = None

    credential: Dict[str, Any] = {
        "mode": "backend-issued-tencent-cloud" if cloud_render_ready else "backend-issued-mock",
        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
    }
    if cloud_render_ready:
        credential["appkey"] = settings.tencent_digital_human_app_key
        credential["accesstoken"] = settings.tencent_digital_human_access_token

    response: Dict[str, Any] = {
        "sessionId": session_id,
        "provider": DIGITAL_HUMAN_SESSION_PROVIDER,
        "providerMode": provider_mode,
        "personaId": persona_id,
        "scene": scene,
        "deviceId": device_id,
        "lifecycleMode": lifecycle_mode,
        "lifecycleModeLabel": DIGITAL_HUMAN_MODE_LABELS[lifecycle_mode],
        "assetKey": provider_asset_id,
        "driveMode": DIGITAL_HUMAN_SESSION_DRIVE_MODE,
        "alphaEnabled": True,
        "smartActionEnabled": False,
        "sessionPolicy": {
            "allowInterrupt": True,
            "maxDurationSeconds": DIGITAL_HUMAN_SESSION_TTL_SECONDS,
            "proactiveSpeechAllowed": False,
        },
        "credential": credential,
        "fallback": {
            "mode": "none" if cloud_render_ready else "audioOnly",
            "reason": (
                "tencent cloud-render session credential issued"
                if cloud_render_ready
                else "tencent runtime is not connected in this mock contract"
            ),
        },
        "contractVersion": DIGITAL_HUMAN_SESSION_CONTRACT_VERSION,
    }
    if provider_asset_id:
        response["providerAssetId"] = provider_asset_id
    if provider_project_id:
        response["providerProjectId"] = provider_project_id

    return response


@app.post("/auth/login")
def login(payload: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(payload.get("phone") or "").strip()
    nickname = str(payload.get("nickname") or "").strip()
    password = _optional_password(payload, "password")
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    user_id = stable_user_id(phone)
    credential = store.get_password_credential(user_id)
    if credential is not None and not password:
        raise HTTPException(status_code=401, detail="password is required")
    if credential is not None and password and not verify_password(password, credential):
        raise HTTPException(status_code=401, detail="invalid password")

    existing_user = _store_get_user(user_id)
    if existing_user is not None and existing_user.get("deletionState") == "softDeleted":
        user = _restore_soft_deleted_account_or_raise(
            user_id=user_id,
            phone=phone,
            nickname=nickname,
        )
        user["passwordConfigured"] = credential is not None
        return {"status": "restored", "user": user}

    user = store.upsert_user(phone=phone, nickname=nickname)
    if password and credential is None:
        credential = store.save_password_credential(user_id, make_password_credential(password))
    user["passwordConfigured"] = credential is not None
    return {"user": user}


def _store_get_user(user_id: str) -> Optional[Dict[str, Any]]:
    get_user = getattr(store, "get_user", None)
    if not callable(get_user):
        return None
    return get_user(user_id)


def _parse_account_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _account_restore_deadline_expired(user: Dict[str, Any]) -> bool:
    deadline = _parse_account_datetime(user.get("restoreDeadline") or user.get("purgeAfter"))
    return deadline < datetime.now(timezone.utc)


def _restore_soft_deleted_account_or_raise(
    *,
    user_id: str,
    phone: str,
    nickname: str = "",
) -> Dict[str, Any]:
    user = _store_get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="account not found")
    if user.get("deletionState") != "softDeleted":
        return user
    if int(user.get("restoreCount") or 0) >= ACCOUNT_RESTORE_LIMIT:
        raise HTTPException(status_code=410, detail="account restore chance already used")
    if _account_restore_deadline_expired(user):
        raise HTTPException(status_code=410, detail="account restore deadline expired")
    restored = store.restore_user(user_id, phone=phone, nickname=nickname)
    if restored is None:
        raise HTTPException(status_code=404, detail="account not found")
    return restored


def _require_account_deletion_confirmations(payload: Dict[str, Any]) -> None:
    if not bool(payload.get("firstConfirmation")) or not bool(payload.get("secondConfirmation")):
        raise HTTPException(status_code=400, detail="two deletion confirmations are required")


@app.post("/auth/delete")
def soft_delete_account(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    phone = str(payload.get("phone") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    _require_account_deletion_confirmations(payload)
    deletion = store.soft_delete_user(user_id, phone=phone)
    if deletion is None:
        raise HTTPException(status_code=404, detail="account not found")
    return {
        "status": "softDeleted",
        "contractVersion": ACCOUNT_DELETION_CONTRACT_VERSION,
        "deletion": deletion,
        "policy": {
            "dataExportSupported": False,
            "retentionDays": ACCOUNT_DELETION_RETENTION_DAYS,
            "restoreLimit": ACCOUNT_RESTORE_LIMIT,
            "restoreBySamePhone": True,
        },
    }


@app.post("/auth/restore")
def restore_account(payload: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(payload.get("phone") or "").strip()
    nickname = str(payload.get("nickname") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    user_id = stable_user_id(phone)
    user = _restore_soft_deleted_account_or_raise(
        user_id=user_id,
        phone=phone,
        nickname=nickname,
    )
    return {"status": "restored", "user": user}


@app.post("/auth/purge-expired-deletions")
def purge_expired_account_deletions(payload: Dict[str, Any]) -> Dict[str, Any]:
    cutoff = str(payload.get("cutoff") or datetime.now(timezone.utc).isoformat())
    purged = store.purge_expired_deleted_users(cutoff)
    return {
        "status": "purged",
        "cutoff": cutoff,
        "purgedCount": len(purged),
        "items": purged,
        "contractVersion": ACCOUNT_DELETION_CONTRACT_VERSION,
    }


def _optional_password(payload: Dict[str, Any], key: str) -> Optional[str]:
    if key not in payload or payload.get(key) is None:
        return None
    value = str(payload.get(key) or "")
    return value if value else None


def _required_password(payload: Dict[str, Any], key: str, *, min_length: int = 1) -> str:
    value = str(payload.get(key) or "")
    if not value:
        raise HTTPException(status_code=400, detail=f"{key} is required")
    if len(value) < min_length:
        raise HTTPException(status_code=400, detail=f"{key} is too short")
    return value


@app.post("/auth/password")
def change_password(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    old_password = _required_password(payload, "oldPassword")
    new_password = _required_password(payload, "newPassword", min_length=8)

    credential = store.get_password_credential(user_id)
    if credential is None:
        raise HTTPException(status_code=409, detail="password credential not configured")
    if not verify_password(old_password, credential):
        raise HTTPException(status_code=401, detail="invalid password")

    store.save_password_credential(user_id, make_password_credential(new_password))
    return {"status": "changed", "userId": user_id}


_ALLOWED_PROFILE_GENDERS = {"男", "女", "不便透露"}


def _optional_profile_text(payload: Dict[str, Any], key: str, max_length: int) -> Optional[str]:
    if key not in payload or payload.get(key) is None:
        return None
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{key} is too long")
    return value


def _sanitize_profile_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    nickname = str(payload.get("nickname") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not nickname:
        raise HTTPException(status_code=400, detail="nickname is required")
    if len(nickname) > 24:
        raise HTTPException(status_code=400, detail="nickname is too long")

    gender = _optional_profile_text(payload, "gender", 8)
    if gender is not None and gender not in _ALLOWED_PROFILE_GENDERS:
        raise HTTPException(status_code=400, detail="unsupported gender")

    profile = {
        "userId": user_id,
        "nickname": nickname,
    }
    region = _optional_profile_text(payload, "region", 32)
    avatar_name = _optional_profile_text(payload, "avatarName", 64)
    if gender is not None:
        profile["gender"] = gender
    if region is not None:
        profile["region"] = region
    if avatar_name is not None:
        profile["avatarName"] = avatar_name
    return profile


def _required_text(payload: Dict[str, Any], key: str, max_length: int = 160) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{key} is required")
    return value[:max_length]


def _safe_file_name(value: str) -> str:
    candidate = PurePosixPath(value).name.strip()
    if not candidate or candidate in {".", ".."}:
        return "media.bin"
    return "".join(ch for ch in candidate if ch.isalnum() or ch in {".", "-", "_"}) or "media.bin"


def _safe_object_segment(value: str, fallback: str) -> str:
    segment = "".join(ch for ch in value.strip() if ch.isalnum() or ch in {"-", "_"})
    return segment or fallback


def _archive_media_upload_intent_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        safe_scope = sanitize_archive_item_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    user_id = _required_text(payload, "userId", 96)
    archive_item_id = _required_text(payload, "archiveItemId", 128)
    kind = _required_text(payload, "kind", 32).lower()
    if kind not in ARCHIVE_MEDIA_UPLOAD_LIMITS:
        raise HTTPException(status_code=400, detail=f"unsupported media kind: {kind}")

    file_size_bytes = int(payload.get("fileSizeBytes") or 0)
    if file_size_bytes <= 0:
        raise HTTPException(status_code=400, detail="fileSizeBytes is required")
    max_file_size_bytes = ARCHIVE_MEDIA_UPLOAD_LIMITS[kind]
    if file_size_bytes > max_file_size_bytes:
        raise HTTPException(status_code=413, detail="file too large")

    content_type = _required_text(payload, "contentType", 128)
    if not content_type.startswith(f"{kind}/"):
        raise HTTPException(status_code=400, detail="contentType does not match media kind")

    file_name = _safe_file_name(_required_text(payload, "fileName", 180))
    user_id_segment = _safe_object_segment(user_id, "user")
    archive_item_id_segment = _safe_object_segment(archive_item_id, "archive_item")
    persona_scope = str(safe_scope["personaScope"])
    digital_human_id = str(safe_scope["digitalHumanId"])
    digital_human_id_segment = _safe_object_segment(digital_human_id, "digital_human")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ARCHIVE_MEDIA_UPLOAD_TTL_SECONDS)
    upload_intent_id = f"upload_intent_{archive_item_id}"
    object_key = "/".join(
        [
            user_id_segment,
            persona_scope,
            digital_human_id_segment,
            kind,
            archive_item_id_segment,
            file_name,
        ]
    )

    return {
        "uploadIntentId": upload_intent_id,
        "archiveItemId": archive_item_id,
        "kind": kind,
        "storageProvider": ARCHIVE_MEDIA_UPLOAD_PROVIDER,
        "providerDisplayName": ARCHIVE_MEDIA_UPLOAD_PROVIDER_DISPLAY_NAME,
        "providerMode": ARCHIVE_MEDIA_UPLOAD_PROVIDER_MODE,
        "requiresClientUpload": ARCHIVE_MEDIA_REQUIRES_CLIENT_UPLOAD,
        "uploadURLScheme": ARCHIVE_MEDIA_UPLOAD_URL_SCHEME,
        "realProviderReady": ARCHIVE_MEDIA_REAL_PROVIDER_READY,
        "providerSwitchContractVersion": ARCHIVE_MEDIA_PROVIDER_SWITCH_CONTRACT_VERSION,
        "clientUploadAction": ARCHIVE_MEDIA_CLIENT_UPLOAD_ACTION,
        "objectKey": object_key,
        "uploadURL": f"mock://archive-media/{object_key}",
        "expiresAt": expires_at.isoformat(),
        "expiresInSeconds": ARCHIVE_MEDIA_UPLOAD_TTL_SECONDS,
        "maxFileSizeBytes": max_file_size_bytes,
        "requiredHeaders": {
            "Content-Type": content_type,
            "x-dreamjourney-upload-intent": upload_intent_id,
        },
        "fileSizeBytes": file_size_bytes,
        "fileName": file_name,
        "contentType": content_type,
        "personaScope": persona_scope,
        "digitalHumanId": digital_human_id,
        "metadataOnly": True,
    }


def _safe_voice_profile_id(value: str, user_id: str) -> str:
    candidate = _safe_object_segment(value, "")
    if candidate:
        return candidate[:96]
    return f"voice_profile_{_safe_object_segment(user_id, 'user')}"


def _sanitize_voice_profile_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _required_text(payload, "userId", 96)
    privacy_metadata = payload.get("privacyMetadata") or {}
    if not isinstance(privacy_metadata, dict) or privacy_metadata.get("scope") not in {"generationAllowed", "familyCircle"}:
        raise HTTPException(status_code=403, detail="voice clone profile requires syncable authorization scope")

    authorization_confirmed = bool(payload.get("authorizationConfirmed"))
    if not authorization_confirmed:
        raise HTTPException(status_code=403, detail="authorizationConfirmed is required")

    sample_status = str(payload.get("sampleStatus") or "notProvided").strip()
    if sample_status not in VOICE_CLONE_SAMPLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"unsupported sampleStatus: {sample_status}")

    voice_profile_id = _safe_voice_profile_id(str(payload.get("voiceProfileId") or ""), user_id)
    persona_scope = str(payload.get("personaScope") or "personal").strip()
    if persona_scope not in {"personal", "family"}:
        raise HTTPException(status_code=400, detail="unsupported personaScope")
    digital_human_id = str(payload.get("digitalHumanId") or user_id).strip() or user_id

    try:
        sample_count = int(payload.get("sampleCount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="sampleCount must be an integer")
    sample_count = max(0, min(sample_count, 20))

    now = datetime.now(timezone.utc).isoformat()
    provider = VoiceCloneProviderFactory(settings).make()
    provider_mode = provider.provider_mode
    provider_result: Dict[str, Any] = {}
    audio_base64 = str(payload.get("audioBase64") or "").strip()
    if provider.is_configured and audio_base64:
        try:
            provider_result = provider.submit_training(
                voice_profile_id=voice_profile_id,
                audio_base64=audio_base64,
                audio_format=str(payload.get("audioFormat") or "wav").strip() or "wav",
                language=int(payload.get("language") or 0),
            )
            voice_profile_id = str(provider_result.get("voiceProfileId") or voice_profile_id)
            sample_status = str(provider_result.get("sampleStatus") or sample_status)
            if sample_status not in VOICE_CLONE_SAMPLE_STATUSES:
                sample_status = "pending"
        except (ValueError, VoiceCloneProviderUnavailable) as exc:
            provider_result = {
                "providerStatus": "failed",
                "providerMessage": f"{VOICE_CLONE_PROVIDER_ERROR_PREFIX}: {exc}",
                "sampleStatus": "failed",
            }
            provider_request_id = str(getattr(exc, "provider_request_id", "") or "").strip()
            provider_log_id = str(getattr(exc, "provider_log_id", "") or "").strip()
            if provider_request_id:
                provider_result["providerRequestId"] = provider_request_id
            if provider_log_id:
                provider_result["providerLogId"] = provider_log_id
            sample_status = "failed"

    profile = {
        "id": voice_profile_id,
        "voiceProfileId": voice_profile_id,
        "userId": user_id,
        "personaScope": persona_scope,
        "digitalHumanId": digital_human_id,
        "sampleStatus": sample_status,
        "sampleCount": sample_count,
        "authorizationConfirmed": True,
        "authorizationVersion": str(payload.get("authorizationVersion") or "voice-clone-consent-v1"),
        "authorizationText": str(payload.get("authorizationText") or VOICE_CLONE_AUTHORIZATION_COPY)[:300],
        "authorizationConfirmedAt": str(payload.get("authorizationConfirmedAt") or now),
        "authorizationCopy": VOICE_CLONE_AUTHORIZATION_COPY,
        "providerMode": provider_mode,
        "realCloneProviderReady": provider.is_configured,
        "providerStatus": str(provider_result.get("providerStatus") or ("notSubmitted" if provider.is_configured else "mockOnly")),
        "qualityAcceptanceRequired": True,
        "isEnabled": sample_status == "ready",
        "defaultReleaseVisible": False,
        "contractVersion": VOICE_CLONE_CONTRACT_VERSION,
        "disableContract": VOICE_CLONE_DISABLE_CONTRACT,
        "deleteContract": VOICE_CLONE_DELETE_CONTRACT,
        "updatedAt": now,
        "privacyMetadata": {
            "scope": privacy_metadata.get("scope"),
        },
    }
    provider_request_id = str(provider_result.get("providerRequestId") or "").strip()
    provider_log_id = str(provider_result.get("providerLogId") or "").strip()
    provider_message = str(provider_result.get("providerMessage") or "").strip()
    if provider_request_id:
        profile["providerRequestId"] = provider_request_id
    if provider_log_id:
        profile["providerLogId"] = provider_log_id[:160]
    if provider_message:
        profile["providerMessage"] = provider_message[:300]
    if "createdAt" in payload:
        profile["createdAt"] = str(payload.get("createdAt") or now)
    else:
        profile["createdAt"] = now
    return profile


def _sanitize_family_member_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _required_text(payload, "userId", 96)
    safe_payload = dict(payload)
    persona_scope = str(safe_payload.get("personaScope") or "family").strip()
    if persona_scope != "family":
        raise HTTPException(status_code=400, detail="family member personaScope must be family")

    digital_human_id = str(safe_payload.get("digitalHumanId") or "family_default").strip()
    if not digital_human_id:
        digital_human_id = "family_default"
    digital_human_mode = str(safe_payload.get("digitalHumanMode") or "sunlight").strip()
    if digital_human_mode not in DIGITAL_HUMAN_MODE_LABELS:
        raise HTTPException(status_code=400, detail=f"unsupported digitalHumanMode: {digital_human_mode}")

    safe_payload["userId"] = user_id
    safe_payload["personaScope"] = "family"
    safe_payload["digitalHumanId"] = digital_human_id
    safe_payload["digitalHumanMode"] = digital_human_mode
    safe_payload["digitalHumanModeLabel"] = DIGITAL_HUMAN_MODE_LABELS[digital_human_mode]
    safe_payload["backendContractMode"] = FAMILY_PERSONA_CONTRACT_MODE
    safe_payload["familyPersonaContractVersion"] = FAMILY_PERSONA_CONTRACT_VERSION
    safe_payload["defaultReleaseVisible"] = False
    return safe_payload


def _voice_profile_lifecycle_update(profile: Dict[str, Any], sample_status: str) -> Dict[str, Any]:
    updated = dict(profile)
    now = datetime.now(timezone.utc).isoformat()
    updated["sampleStatus"] = sample_status
    updated["isEnabled"] = False
    updated["updatedAt"] = now
    if sample_status == "disabled":
        updated["disabledAt"] = now
    if sample_status == "deleted":
        updated["deletedAt"] = now
        updated["deletionState"] = "deleted"
        updated["sampleCount"] = 0
    return updated


def _voice_profile_refresh_update(profile: Dict[str, Any]) -> Dict[str, Any]:
    provider = VoiceCloneProviderFactory(settings).make()
    if not provider.is_configured:
        raise HTTPException(status_code=503, detail="voice clone provider is not configured")
    voice_profile_id = str(profile.get("voiceProfileId") or "").strip()
    try:
        provider_result = provider.query_status(voice_profile_id=voice_profile_id)
    except (ValueError, VoiceCloneProviderUnavailable) as exc:
        provider_result = {
            "providerStatus": "failed",
            "providerMessage": f"{VOICE_CLONE_PROVIDER_ERROR_PREFIX}: {exc}",
            "sampleStatus": "failed",
        }
        provider_request_id = str(getattr(exc, "provider_request_id", "") or "").strip()
        provider_log_id = str(getattr(exc, "provider_log_id", "") or "").strip()
        if provider_request_id:
            provider_result["providerRequestId"] = provider_request_id
        if provider_log_id:
            provider_result["providerLogId"] = provider_log_id
    sample_status = str(provider_result.get("sampleStatus") or profile.get("sampleStatus") or "pending")
    if sample_status not in VOICE_CLONE_SAMPLE_STATUSES:
        sample_status = "pending"
    updated = dict(profile)
    updated["sampleStatus"] = sample_status
    updated["isEnabled"] = sample_status == "ready"
    updated["providerMode"] = provider.provider_mode
    updated["realCloneProviderReady"] = provider.is_configured
    updated["providerStatus"] = str(provider_result.get("providerStatus") or updated.get("providerStatus") or "unknown")
    provider_request_id = str(provider_result.get("providerRequestId") or "").strip()
    provider_log_id = str(provider_result.get("providerLogId") or "").strip()
    provider_message = str(provider_result.get("providerMessage") or "").strip()
    if provider_request_id:
        updated["providerRequestId"] = provider_request_id
    if provider_log_id:
        updated["providerLogId"] = provider_log_id[:160]
    if provider_message:
        updated["providerMessage"] = provider_message[:300]
    updated["updatedAt"] = datetime.now(timezone.utc).isoformat()
    return updated


def _voice_profile_quality_acceptance_update(profile: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    if profile.get("sampleStatus") != "ready" or not bool(profile.get("isEnabled")) or not bool(profile.get("realCloneProviderReady")):
        raise HTTPException(status_code=409, detail="voice profile is not ready for quality acceptance")

    now = datetime.now(timezone.utc).isoformat()
    updated = dict(profile)
    updated["qualityAcceptanceRequired"] = False
    updated["qualityAcceptanceState"] = "accepted"
    updated["qualityAcceptedAt"] = now
    updated["qualityAcceptedBy"] = user_id
    updated["updatedAt"] = now
    return updated


@app.post("/profile")
def save_profile(payload: Dict[str, Any]) -> Dict[str, Any]:
    profile = _sanitize_profile_payload(payload)
    saved = store.save_profile(profile["userId"], profile)
    return {"status": "saved", "profile": saved}


@app.get("/profile/{user_id}")
def get_profile(user_id: str) -> Dict[str, Any]:
    profile = store.get_profile(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"userId": user_id, "profile": profile}


@app.get("/config/runtime")
def runtime_config() -> Dict[str, Any]:
    return RuntimeConfigService(settings).public_config()


@app.post("/context/build")
def build_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        packet = ContextPacketBuilder(store, settings).build(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "built", "contextPacket": packet}


@app.post("/voice/realtime-token")
def realtime_token(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    try:
        return TokenService(settings).realtime_config(user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/voice/profiles")
def save_voice_profile(payload: Dict[str, Any]) -> Dict[str, Any]:
    profile = _sanitize_voice_profile_payload(payload)
    saved = store.save_voice_profile(profile["userId"], profile)
    return {"status": "saved", "profile": saved}


@app.get("/voice/profiles/{user_id}")
def list_voice_profiles(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "profiles": store.list_voice_profiles(user_id)}


@app.post("/voice/profiles/{user_id}/{voice_profile_id}/disable")
def disable_voice_profile(user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    disabled = _voice_profile_lifecycle_update(profile, "disabled")
    saved = store.save_voice_profile(user_id, disabled)
    return {"status": "disabled", "profile": saved}


@app.post("/voice/profiles/{user_id}/{voice_profile_id}/refresh")
def refresh_voice_profile(user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    refreshed = _voice_profile_refresh_update(profile)
    saved = store.save_voice_profile(user_id, refreshed)
    return {"status": "refreshed", "profile": saved}


@app.post("/voice/profiles/{user_id}/{voice_profile_id}/quality-acceptance")
def accept_voice_profile_quality(user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    accepted = _voice_profile_quality_acceptance_update(profile, user_id)
    saved = store.save_voice_profile(user_id, accepted)
    return {"status": "accepted", "profile": saved}


@app.post("/voice/synthesis")
def synthesize_voice_profile(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _required_text(payload, "userId", 96)
    voice_profile_id = _required_text(payload, "voiceProfileId", 96)
    text = _required_text(payload, "text", 4000)
    audio_format = str(payload.get("format") or "mp3").strip() or "mp3"
    sample_rate = int(payload.get("sampleRate") or 24000)
    speech_rate = int(payload.get("speechRate") or -10)
    loudness_rate = int(payload.get("loudnessRate") or 10)
    output_mode = str(payload.get("outputMode") or "default").strip() or "default"
    provider_audio_format = audio_format
    provider_sample_rate = sample_rate
    if output_mode == "tencentAudioDrive":
        provider_audio_format = "wav"
        provider_sample_rate = TencentAudioDrivePCMAdapter.sample_rate

    provider = VoiceCloneTTSProviderFactory(settings).make()
    if not provider.is_configured:
        raise HTTPException(status_code=503, detail="voice clone TTS provider is not configured")
    try:
        result = provider.synthesize(
            text=text,
            user_id=user_id,
            voice_profile_id=voice_profile_id,
            audio_format=provider_audio_format,
            sample_rate=provider_sample_rate,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
        )
        audio_payload = {
            "encoding": "base64",
            "format": result["audioFormat"],
            "data": result["audioBase64"],
            "byteCount": result["byteCount"],
        }
        if output_mode == "tencentAudioDrive":
            audio_payload = TencentAudioDrivePCMAdapter().adapt(
                audio_base64=result["audioBase64"],
                audio_format=result["audioFormat"],
            )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    response = {
        "status": "synthesized",
        "voiceProfileId": result["voiceProfileId"],
        "providerMode": result["providerMode"],
        "visemeTimeline": result.get("visemeTimeline"),
        "audio": audio_payload,
    }
    provider_request_id = str(result.get("providerRequestId") or "").strip()
    provider_log_id = str(result.get("providerLogId") or "").strip()
    if provider_request_id:
        response["providerRequestId"] = provider_request_id
    if provider_log_id:
        response["providerLogId"] = provider_log_id[:160]
    if output_mode != "default":
        response["outputMode"] = output_mode
    return response


@app.delete("/voice/profiles/{user_id}/{voice_profile_id}")
def delete_voice_profile(user_id: str, voice_profile_id: str) -> Dict[str, Any]:
    profile = store.get_voice_profile(user_id, voice_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voice profile not found")
    deleted = _voice_profile_lifecycle_update(profile, "deleted")
    saved = store.save_voice_profile(user_id, deleted)
    return {"status": "deleted", "profile": saved}


@app.post("/tts")
def tts(payload: Dict[str, Any], dryRun: bool = False) -> Dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    user_id = str(payload.get("userId") or "anonymous").strip()
    voice_type = payload.get("voiceType")
    encoding = str(payload.get("encoding") or "wav")
    speed_ratio = float(payload.get("speedRatio") or 1.0)
    proxy = VolcTTSProxy(settings)
    try:
        if not dryRun:
            return proxy.request_tts(
                text=text,
                user_id=user_id,
                voice_type=voice_type,
                encoding=encoding,
                speed_ratio=speed_ratio,
            )
        request = proxy.build_request(
            text=text,
            user_id=user_id,
            voice_type=voice_type,
            encoding=encoding,
            speed_ratio=speed_ratio,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "provider": "volcengine",
        "request": {
            "url": request["url"],
            "headers": {"x-api-key": "<server-side>", "Content-Type": "application/json"},
            "json": request["json"],
        },
        "note": "dryRun=true returns the redacted upstream request without calling VolcEngine.",
    }


@app.get("/maps/district")
def amap_district(keyword: str, dryRun: bool = False) -> Dict[str, Any]:
    try:
        proxy = AMapDistrictProxy(settings)
        if not dryRun:
            return proxy.request_district(keyword=keyword)
        url = proxy.build_url(keyword=keyword)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "provider": "amap",
        "keyword": keyword,
        "upstreamURL": proxy.redact_url(url, settings.amap_web_service_key),
    }


@app.post("/kb/sync")
def sync_kb(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    graph = payload.get("graph") or {}
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not isinstance(graph, dict):
        raise HTTPException(status_code=400, detail="graph must be an object")
    filtered = filter_syncable_graph(graph)
    snapshot = store.save_kb_snapshot(user_id, filtered)
    return {
        "status": "synced",
        "userId": user_id,
        "updatedAt": snapshot["updatedAt"],
        "counts": {
            "people": len(filtered.get("people", [])),
            "places": len(filtered.get("places", [])),
            "events": len(filtered.get("events", [])),
            "facts": len(filtered.get("facts", [])),
        },
    }


@app.get("/kb/snapshot/{user_id}")
def kb_snapshot(user_id: str) -> Dict[str, Any]:
    graph = store.get_kb_snapshot(user_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return {"userId": user_id, "graph": graph}


@app.post("/kb/extract")
def extract_kb(payload: Dict[str, Any], dryRun: bool = False) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    transcript = str(payload.get("transcript") or "").strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript is required")
    existing_summary = str(payload.get("existingSummary") or "").strip()

    try:
        safe_context = sanitize_knowledge_extraction_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    proxy = DeepSeekKnowledgeExtractionProxy(settings)
    try:
        if not dryRun:
            extraction = proxy.request_extraction(
                transcript=transcript,
                existing_summary=existing_summary,
            )
            return {
                "provider": "deepseek",
                "capability": "kbExtract",
                "userId": user_id,
                "extraction": extraction,
                "context": safe_context,
            }
        request = proxy.redacted_request(
            transcript=transcript,
            existing_summary=existing_summary,
        )
    except ValueError as exc:
        status_code = 503 if "DEEPSEEK_API_KEY" in str(exc) else 502
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "provider": "deepseek",
        "capability": "kbExtract",
        "userId": user_id,
        "request": request,
        "context": safe_context,
        "note": "dryRun=true returns the redacted upstream request without calling DeepSeek.",
    }


@app.post("/memories")
def create_memory(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    return {"memory": store.add_memory(user_id, payload)}


@app.get("/memories/{user_id}")
def list_memories(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "memories": store.list_memories(user_id)}


@app.post("/archive/photos")
def create_archive_photo(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    try:
        safe_payload = sanitize_archive_item_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    item = store.add_archive_item(user_id, safe_payload)
    return {"status": "queued", "item": item}


@app.post("/archive/items")
def create_archive_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    try:
        safe_payload = sanitize_archive_item_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    item = store.add_archive_item(user_id, safe_payload)
    return {"status": "saved", "item": item}


@app.post("/archive/media/upload-intent")
def archive_media_upload_intent(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "mock_ready",
        "uploadIntent": _archive_media_upload_intent_payload(payload),
    }


@app.get("/archive/items/{user_id}")
def list_archive_items(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "items": store.list_archive_items(user_id)}


@app.get("/archive/time-letters/{owner_user_id}/{item_id}/detail")
def get_time_letter_detail(
    owner_user_id: str,
    item_id: str,
    viewerUserId: str,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    now_iso = str(now or datetime.now(timezone.utc).isoformat()).strip()
    try:
        return time_letter_detail_for_viewer(
            store=store,
            owner_user_id=owner_user_id,
            item_id=item_id,
            viewer_user_id=viewerUserId,
            now_iso=now_iso,
        )
    except TimeLetterAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


def _is_sealed_time_letter(item: Dict[str, Any]) -> bool:
    if str(item.get("kind") or "").strip() != "timeLetter":
        return False
    metadata = item.get("metadata")
    metadata_delivery_state = ""
    if isinstance(metadata, dict):
        metadata_delivery_state = str(metadata.get("deliveryState") or "").strip()
    delivery_state = str(item.get("deliveryState") or metadata_delivery_state).strip()
    return delivery_state == "sealed"


@app.delete("/archive/items/{user_id}/{item_id}")
def delete_archive_item(user_id: str, item_id: str) -> Dict[str, Any]:
    existing = next(
        (item for item in store.list_archive_items(user_id) if str(item.get("id") or "") == item_id),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="archive item not found")
    if _is_sealed_time_letter(existing):
        raise HTTPException(status_code=409, detail="sealed timeLetter cannot be deleted")

    deleted = store.delete_archive_item(user_id, item_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="archive item not found")
    return {"status": "deleted", "id": item_id, "item": deleted}


@app.post("/archive/image-analysis")
def archive_image_analysis(payload: Dict[str, Any], dryRun: bool = False) -> Dict[str, Any]:
    image_base64 = str(payload.get("imageBase64") or "").strip()
    if not image_base64:
        raise HTTPException(status_code=400, detail="imageBase64 is required")
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    archive_item_id = str(payload.get("archiveItemId") or "").strip()
    if not archive_item_id:
        raise HTTPException(status_code=400, detail="archiveItemId is required")

    try:
        safe_context = sanitize_image_analysis_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    provider = ArchiveImageAnalysisProviderFactory(settings).make()
    try:
        if not dryRun:
            return provider.request_analysis(image_base64=image_base64)
        request = provider.redacted_request(image_base64=image_base64)
    except ValueError as exc:
        if not dryRun:
            return provider.failure_contract(provider_message=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        if not dryRun:
            return provider.failure_contract(provider_message=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "provider": provider.provider_id,
        "capability": provider.public_capability(),
        "request": request,
        "responseContract": provider.response_contract(),
        "context": {
            "userId": user_id,
            "archiveItemId": archive_item_id,
            "privacyMetadata": safe_context.get("privacyMetadata"),
        },
        "note": "dryRun=true returns the redacted upstream request without calling DeepSeek.",
    }


@app.post("/mailbox/letters")
def create_mailbox_letter(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    try:
        safe_payload = sanitize_mailbox_letter_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    item = store.add_mailbox_letter(user_id, safe_payload)
    return {"status": "saved", "item": item}


@app.get("/mailbox/letters/{user_id}")
def list_mailbox_letters(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "items": store.list_mailbox_letters(user_id)}


@app.post("/mailbox/letters/{user_id}/{letter_id}/read")
def mark_mailbox_letter_read(user_id: str, letter_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    read_at = str(payload.get("readAt") or datetime.now(timezone.utc).isoformat()).strip()
    _parse_iso_datetime(read_at, "readAt")
    item = store.mark_mailbox_letter_read(user_id, letter_id, read_at)
    if item is None:
        raise HTTPException(status_code=404, detail="mailbox letter not found")
    return {"status": "read", "item": item}


_ALLOWED_ECHO_DELAYED_REPLY_TRIGGERS = {"tenRoundBaseline", "contentSignal"}
_ALLOWED_PUSH_PLATFORMS = {"ios"}
_ALLOWED_PUSH_ENVIRONMENTS = {"sandbox", "production"}
_DEVICE_TOKEN_PATTERN = re.compile(r"^[0-9a-fA-F]{16,256}$")


def _sanitize_push_device_token_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    device_token = str(payload.get("deviceToken") or "").strip().replace(" ", "")
    platform = str(payload.get("platform") or "").strip().lower()
    environment = str(payload.get("environment") or "").strip().lower()
    device_id = str(payload.get("deviceId") or "").strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not device_token:
        raise HTTPException(status_code=400, detail="deviceToken is required")
    if _DEVICE_TOKEN_PATTERN.match(device_token) is None:
        raise HTTPException(status_code=400, detail="deviceToken is invalid")
    if platform not in _ALLOWED_PUSH_PLATFORMS:
        raise HTTPException(status_code=400, detail="unsupported push platform")
    if environment not in _ALLOWED_PUSH_ENVIRONMENTS:
        raise HTTPException(status_code=400, detail="unsupported push environment")
    if len(device_id) > 64:
        raise HTTPException(status_code=400, detail="deviceId is too long")

    token_hash = hashlib.sha256(device_token.lower().encode("utf-8")).hexdigest()
    device_token_id = f"push_{user_id}_{token_hash[:16]}"
    return {
        "id": device_token_id,
        "deviceTokenId": device_token_id,
        "userId": user_id,
        "platform": platform,
        "environment": environment,
        "deviceId": device_id or f"ios_{token_hash[:12]}",
        "deviceTokenHash": token_hash,
        "deviceTokenPreview": f"{device_token[:6].lower()}...{device_token[-4:].lower()}",
        "deliveryProviderState": "pending",
        "containsRawToken": False,
    }


def _sanitize_echo_delayed_reply_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    delayed_reply_id = str(payload.get("delayedReplyId") or "").strip()
    deliver_at = str(payload.get("deliverAt") or "").strip()
    trigger = str(payload.get("trigger") or "").strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not delayed_reply_id:
        raise HTTPException(status_code=400, detail="delayedReplyId is required")
    if not deliver_at:
        raise HTTPException(status_code=400, detail="deliverAt is required")

    try:
        minutes = int(payload.get("minutes"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="minutes is required") from exc
    if minutes < 1:
        raise HTTPException(status_code=400, detail="minutes must be positive")

    if trigger not in _ALLOWED_ECHO_DELAYED_REPLY_TRIGGERS:
        raise HTTPException(status_code=400, detail="unsupported delayed reply trigger")

    return {
        "id": delayed_reply_id,
        "delayedReplyId": delayed_reply_id,
        "userId": user_id,
        "deliverAt": deliver_at,
        "minutes": minutes,
        "trigger": trigger,
        "deliveryState": "scheduled",
        "pushProviderState": "pending",
        "containsRawTranscript": False,
        **({"deviceTokenId": str(payload.get("deviceTokenId")).strip()} if str(payload.get("deviceTokenId") or "").strip() else {}),
    }


def _parse_iso_datetime(value: str, field_name: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be ISO-8601") from exc


def _sanitize_echo_dispatch_due_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    now = str(payload.get("now") or "").strip()
    if not now:
        raise HTTPException(status_code=400, detail="now is required")
    _parse_iso_datetime(now, "now")

    try:
        limit = int(payload.get("limit", 25))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="limit must be an integer") from exc
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be positive")
    return {"now": now, "limit": min(limit, 100)}


@app.post("/devices/push-token")
def register_push_device_token(payload: Dict[str, Any]) -> Dict[str, Any]:
    item = _sanitize_push_device_token_payload(payload)
    saved = store.save_push_device_token(item["userId"], item)
    return {"status": "registered", "item": saved}


@app.post("/echo/delayed-replies")
def schedule_echo_delayed_reply(payload: Dict[str, Any]) -> Dict[str, Any]:
    item = _sanitize_echo_delayed_reply_payload(payload)
    saved = store.add_echo_delayed_reply(item["userId"], item)
    return {"status": "scheduled", "item": saved}


@app.post("/echo/delayed-replies/dispatch-due")
def dispatch_due_echo_delayed_replies(payload: Dict[str, Any]) -> Dict[str, Any]:
    contract = _sanitize_echo_dispatch_due_payload(payload)
    items = store.mark_due_echo_delayed_replies_for_dispatch(
        cutoff_iso=contract["now"],
        dispatched_at_iso=contract["now"],
        limit=contract["limit"],
    )
    return {
        "status": "queued",
        "cutoff": contract["now"],
        "itemCount": len(items),
        "items": items,
        "providerDeliveryAttempted": False,
    }


@app.post("/archive/time-letters/dispatch-due")
def dispatch_due_time_letters(payload: Dict[str, Any]) -> Dict[str, Any]:
    contract = _sanitize_echo_dispatch_due_payload(payload)
    return dispatch_due_time_letters_for_store(store, now_iso=contract["now"], limit=contract["limit"])


@app.get("/echo/delayed-replies/{user_id}")
def list_echo_delayed_replies(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "items": store.list_echo_delayed_replies(user_id)}


@app.post("/family/invite")
def invite_family(payload: Dict[str, Any]) -> Dict[str, Any]:
    invite_payload = _sanitize_family_member_payload(payload)
    user_id = str(invite_payload["userId"])
    invite_payload.setdefault("accessStatus", "pending")
    invite_payload.setdefault("invitationStatus", "pending")
    invitation_code = str(invite_payload.get("invitationCode") or "").strip()
    if not invitation_code:
        invitation_code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10].upper()
    invite_payload["invitationCode"] = invitation_code
    invite_payload["invitationURL"] = f"dreamjourney://family/invite?code={invitation_code}"
    member = store.add_family_member(user_id, invite_payload)
    return {"status": "created", "member": member}


@app.get("/family/members/{user_id}")
def family_members(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "members": store.list_family_members(user_id)}


@app.post("/family/members/{user_id}/{member_id}/accept")
def accept_family_member(user_id: str, member_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(payload.get("phone") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    member = store.accept_family_member(user_id, member_id, phone=phone)
    if member is None:
        raise HTTPException(status_code=404, detail="family member not found or phone mismatch")
    return {"status": "accepted", "member": member}


@app.post("/family/invitations/{invitation_code}/accept")
def accept_family_invitation_code(invitation_code: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(payload.get("phone") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    member = store.accept_family_invitation_code(invitation_code, phone=phone)
    if member is None:
        raise HTTPException(status_code=404, detail="invitation not found or phone mismatch")
    return {"status": "accepted", "member": member}


@app.post("/family/members/{user_id}/{member_id}/revoke")
def revoke_family_member(user_id: str, member_id: str) -> Dict[str, Any]:
    raise HTTPException(status_code=409, detail="family member removal is not supported")


def _normalize_viewer_family_member_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalized_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _ensure_active_family_viewer(
    user_id: str,
    viewer_family_member_id: Optional[str],
    requester_phone: Optional[str] = None,
    require_requester_identity: bool = False,
) -> None:
    if viewer_family_member_id is None:
        return
    for member in store.list_family_members(user_id):
        if str(member.get("id") or "") != viewer_family_member_id:
            continue
        if member.get("accessStatus") == "active" and member.get("invitationStatus") == "accepted":
            if require_requester_identity:
                normalized_requester_phone = _normalized_phone(requester_phone)
                if not normalized_requester_phone:
                    raise HTTPException(status_code=403, detail="requester identity is required")
                normalized_member_phone = _normalized_phone(member.get("phone"))
                if normalized_member_phone and normalized_requester_phone == normalized_member_phone:
                    return
                raise HTTPException(status_code=403, detail="requester is not authorized for this care snapshot")
            return
        raise HTTPException(status_code=403, detail="family member access is not active")
    raise HTTPException(status_code=403, detail="family member is not authorized")


@app.post("/care/snapshots")
def save_care_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    snapshot = payload.get("snapshot")
    viewer_family_member_id = _normalize_viewer_family_member_id(payload.get("viewerFamilyMemberID"))
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=400, detail="snapshot must be an object")
    _ensure_active_family_viewer(user_id, viewer_family_member_id)
    try:
        sanitized_snapshot = sanitize_care_snapshot_payload(snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item = store.save_care_snapshot(
        user_id,
        sanitized_snapshot,
        viewer_family_member_id=viewer_family_member_id,
    )
    return {"status": "saved", "item": item}


@app.get("/care/snapshots/latest/{user_id}")
def latest_care_snapshot(
    user_id: str,
    viewerFamilyMemberID: str = None,
    requesterPhone: str = None,
) -> Dict[str, Any]:
    viewer_family_member_id = _normalize_viewer_family_member_id(viewerFamilyMemberID)
    _ensure_active_family_viewer(
        user_id,
        viewer_family_member_id,
        requester_phone=requesterPhone,
        require_requester_identity=True,
    )
    item = store.get_latest_care_snapshot(
        user_id,
        viewer_family_member_id=viewer_family_member_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="care snapshot not found")
    return {"userId": user_id, "item": item}


@app.get("/care/snapshots/{user_id}")
def care_snapshot_history(
    user_id: str,
    viewerFamilyMemberID: str = None,
    requesterPhone: str = None,
    limit: int = 7,
) -> Dict[str, Any]:
    viewer_family_member_id = _normalize_viewer_family_member_id(viewerFamilyMemberID)
    _ensure_active_family_viewer(
        user_id,
        viewer_family_member_id,
        requester_phone=requesterPhone,
        require_requester_identity=True,
    )
    items = store.list_care_snapshots(
        user_id,
        viewer_family_member_id=viewer_family_member_id,
        limit=limit,
    )
    return {"userId": user_id, "items": items}
