from copy import deepcopy
from typing import Any, Dict, Iterable, List


SYNCABLE_SCOPES = {"generationAllowed", "familyCircle"}
AI_PROCESSABLE_SCOPES = {"generationAllowed"}

CARE_SNAPSHOT_SCALAR_KEYS = {
    "generatedAt",
    "windowStart",
    "windowEnd",
    "windowDayCount",
    "dataCoverageSummary",
    "totalTurns",
    "userTurnCount",
    "characterCount",
    "uniqueTokenCount",
    "lexicalDiversity",
    "negativeEmotionMentions",
    "sleepMentions",
    "bodyDiscomfortMentions",
    "repetitionRatio",
    "averageWordsPerMinute",
    "slowSpeechTurnCount",
    "longPauseTurnCount",
    "emotionVolatilityScore",
    "riskLevel",
    "summary",
    "trendSummary",
}

CARE_SNAPSHOT_REQUIRED_SCALAR_KEYS = {
    "generatedAt",
    "windowDayCount",
    "dataCoverageSummary",
    "totalTurns",
    "userTurnCount",
    "characterCount",
    "uniqueTokenCount",
    "lexicalDiversity",
    "negativeEmotionMentions",
    "sleepMentions",
    "bodyDiscomfortMentions",
    "repetitionRatio",
    "riskLevel",
    "summary",
    "trendSummary",
}

CARE_SNAPSHOT_STRING_LIST_KEYS = {
    "suggestions",
    "weeklyHighlights",
    "riskSignalDescriptions",
}

CARE_SNAPSHOT_REQUIRED_STRING_LIST_KEYS = CARE_SNAPSHOT_STRING_LIST_KEYS

CARE_SNAPSHOT_RISK_LEVELS = {
    "insufficientData",
    "stable",
    "watch",
    "attention",
}

CARE_SNAPSHOT_TEXT_KEYS = {
    "dataCoverageSummary",
    "summary",
    "trendSummary",
}

CARE_SNAPSHOT_RAW_TEXT_MARKERS = {
    "care_raw_sentinel",
    "rawtranscript",
    "raw transcript",
    "rawtext",
    "source text",
    "sourceTexts",
    "transcript",
    "messages",
    "原始对话",
    "原始聊天",
    "完整对话",
}

CARE_DAILY_TREND_SCALAR_KEYS = {
    "date",
    "userTurnCount",
    "negativeEmotionMentions",
    "sleepMentions",
    "bodyDiscomfortMentions",
    "repetitionRatio",
    "averageWordsPerMinute",
    "slowSpeechTurnCount",
    "longPauseTurnCount",
    "emotionVolatilityScore",
    "signalScore",
}

CARE_DAILY_TREND_REQUIRED_KEYS = {
    "date",
    "userTurnCount",
    "negativeEmotionMentions",
    "sleepMentions",
    "bodyDiscomfortMentions",
    "repetitionRatio",
    "signalScore",
}


def _scope(entity: Dict[str, Any]) -> str:
    metadata = entity.get("privacyMetadata") or {}
    return metadata.get("scope") or "localOnly"


def _is_syncable(entity: Dict[str, Any]) -> bool:
    return _scope(entity) in SYNCABLE_SCOPES


def _filter_ids(values: Iterable[str], allowed: set) -> List[str]:
    return [value for value in values if value in allowed]


def _external_source_title(kind: str) -> str:
    return {
        "conversationTurn": "对话来源",
        "memoryArchiveItem": "档案素材",
        "timeMailboxLetter": "时空信件",
        "kbLiteEntity": "知识条目",
        "memoir": "回忆录",
        "importRecord": "导入记录",
        "userAuthorization": "授权记录",
    }.get(kind, "来源记录")


def _redact_source_ref_titles(entity: Dict[str, Any]) -> Dict[str, Any]:
    metadata = entity.get("privacyMetadata")
    if not isinstance(metadata, dict):
        return entity
    source_refs = metadata.get("sourceRefs")
    if not isinstance(source_refs, list):
        return entity

    redacted_refs = []
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            continue
        copied_ref = deepcopy(source_ref)
        copied_ref["title"] = _external_source_title(str(copied_ref.get("kind") or "unknown"))
        redacted_refs.append(copied_ref)

    copied_metadata = deepcopy(metadata)
    copied_metadata["sourceRefs"] = redacted_refs
    entity["privacyMetadata"] = copied_metadata
    return entity


def filter_syncable_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Return a backend-safe KBLite graph without localOnly entities."""
    people = [_redact_source_ref_titles(deepcopy(item)) for item in graph.get("people", []) if _is_syncable(item)]
    places = [_redact_source_ref_titles(deepcopy(item)) for item in graph.get("places", []) if _is_syncable(item)]
    people_ids = {item.get("id") for item in people}
    place_ids = {item.get("id") for item in places}

    events = []
    for event in graph.get("events", []):
        if not _is_syncable(event):
            continue
        copied = deepcopy(event)
        copied["participantIds"] = _filter_ids(copied.get("participantIds", []), people_ids)
        if copied.get("locationId") not in place_ids:
            copied.pop("locationId", None)
        events.append(_redact_source_ref_titles(copied))

    event_ids = {item.get("id") for item in events}
    facts = []
    for fact in graph.get("facts", []):
        if not _is_syncable(fact):
            continue
        copied = deepcopy(fact)
        copied["relatedPersonIds"] = _filter_ids(copied.get("relatedPersonIds", []), people_ids)
        copied["relatedPlaceIds"] = _filter_ids(copied.get("relatedPlaceIds", []), place_ids)
        copied["relatedEventIds"] = _filter_ids(copied.get("relatedEventIds", []), event_ids)
        facts.append(_redact_source_ref_titles(copied))

    return {
        "version": graph.get("version", 1),
        "lastUpdated": graph.get("lastUpdated"),
        "sessionCount": graph.get("sessionCount", 0),
        "people": people,
        "places": places,
        "events": events,
        "facts": facts,
    }


def sanitize_archive_item_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return backend-safe archive metadata without local files or private scopes."""
    if not _is_syncable(payload):
        raise ValueError("archive item is not syncable")

    item = deepcopy(payload)
    item.pop("localPath", None)
    item.pop("fileURL", None)
    item.pop("absolutePath", None)
    item["metadataOnly"] = True
    return item


def sanitize_image_analysis_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return backend-safe image analysis context and reject non-AI scopes."""
    if _scope(payload) not in AI_PROCESSABLE_SCOPES:
        raise ValueError("archive image analysis requires generationAllowed privacy")

    item = deepcopy(payload)
    item.pop("imageBase64", None)
    item.pop("localPath", None)
    item.pop("fileURL", None)
    item.pop("absolutePath", None)
    item["metadataOnly"] = True
    return item


def sanitize_knowledge_extraction_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return backend-safe knowledge extraction context and reject non-AI scopes."""
    if _scope(payload) not in AI_PROCESSABLE_SCOPES:
        raise ValueError("knowledge extraction requires generationAllowed privacy")

    item = deepcopy(payload)
    item.pop("transcript", None)
    item.pop("rawTranscript", None)
    item.pop("messages", None)
    item.pop("sourceTexts", None)
    item.pop("localPath", None)
    item["metadataOnly"] = True

    metadata = item.get("privacyMetadata")
    if isinstance(metadata, dict):
        item["privacyMetadata"] = _redact_source_ref_titles({"privacyMetadata": metadata})["privacyMetadata"]
    return item


def sanitize_mailbox_letter_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return backend-safe mailbox metadata without full letter or echo text."""
    if not _is_syncable(payload):
        raise ValueError("mailbox letter is not syncable")

    allowed_keys = {
        "id",
        "recipientName",
        "title",
        "createdAt",
        "deliverAt",
        "deliveredAt",
        "status",
        "boundaryAcknowledged",
        "privacyMetadata",
    }
    item = {key: deepcopy(payload[key]) for key in allowed_keys if key in payload}

    item["metadataOnly"] = True
    item["contentRedacted"] = True
    return item


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _ensure_no_raw_text(value: str, field_name: str, max_length: int = 260) -> None:
    lowered = value.lower()
    if any(marker.lower() in lowered for marker in CARE_SNAPSHOT_RAW_TEXT_MARKERS):
        raise ValueError(f"raw care text is not allowed in {field_name}")
    if "\n" in value or "\r" in value:
        raise ValueError(f"raw care text is not allowed in {field_name}")
    if len(value) > max_length:
        raise ValueError(f"raw care text is not allowed in {field_name}")


def _sanitize_daily_trend(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("dailyTrend must be a list")

    points = []
    for index, point in enumerate(value):
        if not isinstance(point, dict):
            raise ValueError(f"dailyTrend[{index}] must be an object")
        missing = sorted(CARE_DAILY_TREND_REQUIRED_KEYS - set(point.keys()))
        if missing:
            raise ValueError(f"missing required care snapshot dailyTrend[{index}] fields: {', '.join(missing)}")
        sanitized_point = {
            key: deepcopy(point[key])
            for key in CARE_DAILY_TREND_SCALAR_KEYS
            if key in point and _is_json_scalar(point[key])
        }
        if not CARE_DAILY_TREND_REQUIRED_KEYS.issubset(sanitized_point.keys()):
            raise ValueError(f"invalid care snapshot dailyTrend[{index}] field types")
        points.append(sanitized_point)
    return points


def sanitize_care_snapshot_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Return backend-safe care dashboard aggregates without raw conversation text."""
    missing_scalar = sorted(CARE_SNAPSHOT_REQUIRED_SCALAR_KEYS - set(snapshot.keys()))
    missing_lists = sorted(CARE_SNAPSHOT_REQUIRED_STRING_LIST_KEYS - set(snapshot.keys()))
    missing = missing_scalar + missing_lists
    if "dailyTrend" not in snapshot:
        missing.append("dailyTrend")
    if missing:
        raise ValueError(f"missing required care snapshot fields: {', '.join(missing)}")

    item = {
        key: deepcopy(snapshot[key])
        for key in CARE_SNAPSHOT_SCALAR_KEYS
        if key in snapshot and _is_json_scalar(snapshot[key])
    }
    if not CARE_SNAPSHOT_REQUIRED_SCALAR_KEYS.issubset(item.keys()):
        raise ValueError("invalid care snapshot scalar field types")
    if item["riskLevel"] not in CARE_SNAPSHOT_RISK_LEVELS:
        raise ValueError("invalid care snapshot riskLevel")
    for key in CARE_SNAPSHOT_TEXT_KEYS:
        _ensure_no_raw_text(str(item[key]), key)

    for key in CARE_SNAPSHOT_STRING_LIST_KEYS:
        if not isinstance(snapshot.get(key), list):
            raise ValueError(f"{key} must be a list")
        item[key] = _string_list(snapshot[key])
        if len(item[key]) != len(snapshot[key]):
            raise ValueError(f"{key} must contain only strings")
        for index, text in enumerate(item[key]):
            _ensure_no_raw_text(text, f"{key}[{index}]", max_length=180)

    item["dailyTrend"] = _sanitize_daily_trend(snapshot["dailyTrend"])

    item["metadataOnly"] = True
    item["contentRedacted"] = True
    return item
