from typing import Any, Dict

try:
    from fastapi import FastAPI, HTTPException
except ImportError as exc:  # pragma: no cover - exercised only without runtime deps
    raise RuntimeError("FastAPI is not installed. Run `pip install -r requirements.txt`.") from exc

from app.core.config import settings
from app.services.amap import AMapDistrictProxy
from app.services.privacy import filter_syncable_graph
from app.services.runtime_config import RuntimeConfigService
from app.services.store_factory import init_store, make_store
from app.services.tokens import TokenService
from app.services.tts import VolcTTSProxy


app = FastAPI(title=settings.app_name, version="0.1.0")
store = make_store(settings)


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
    item = store.add_archive_item(user_id, payload)
    return {"status": "queued", "item": item}


@app.post("/family/invite")
def invite_family(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    member = store.add_family_member(user_id, payload)
    return {"status": "created", "member": member}


@app.get("/family/members/{user_id}")
def family_members(user_id: str) -> Dict[str, Any]:
    return {"userId": user_id, "members": store.list_family_members(user_id)}
