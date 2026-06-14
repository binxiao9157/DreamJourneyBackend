import secrets
from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover - exercised only without runtime deps
    raise RuntimeError("FastAPI is not installed. Run `pip install -r requirements.txt`.") from exc

from app.core.config import settings
from app.services.amap import AMapDistrictProxy
from app.services.deepseek import DeepSeekImageAnalysisProxy
from app.services.privacy import (
    filter_syncable_graph,
    sanitize_archive_item_payload,
    sanitize_care_snapshot_payload,
    sanitize_image_analysis_payload,
    sanitize_knowledge_extraction_payload,
    sanitize_mailbox_letter_payload,
)
from app.services.deepseek import DeepSeekKnowledgeExtractionProxy
from app.services.runtime_config import RuntimeConfigService
from app.services.store_factory import init_store, make_store
from app.services.tokens import TokenService
from app.services.tts import VolcTTSProxy


app = FastAPI(title=settings.app_name, version="0.1.0")
store = make_store(settings)


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


@app.post("/auth/login")
def login(payload: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(payload.get("phone") or "").strip()
    nickname = str(payload.get("nickname") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    return {"user": store.upsert_user(phone=phone, nickname=nickname)}


@app.get("/config/runtime")
def runtime_config() -> Dict[str, Any]:
    return RuntimeConfigService(settings).public_config()


@app.post("/voice/realtime-token")
def realtime_token(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    try:
        return TokenService(settings).realtime_config(user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


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


@app.get("/archive/items/{user_id}")
def list_archive_items(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "items": store.list_archive_items(user_id)}


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

    proxy = DeepSeekImageAnalysisProxy(settings)
    try:
        if not dryRun:
            return proxy.request_analysis(image_base64=image_base64)
        request = proxy.redacted_request(image_base64=image_base64)
    except ValueError as exc:
        status_code = 503 if "DEEPSEEK_API_KEY" in str(exc) else 502
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "provider": "deepseek",
        "request": request,
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


@app.post("/family/invite")
def invite_family(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    invite_payload = dict(payload)
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
    member = store.revoke_family_member(user_id, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="family member not found")
    return {"status": "revoked", "member": member}


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
