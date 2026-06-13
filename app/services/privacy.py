from copy import deepcopy
from typing import Any, Dict, Iterable, List


SYNCABLE_SCOPES = {"generationAllowed", "familyCircle"}


def _scope(entity: Dict[str, Any]) -> str:
    metadata = entity.get("privacyMetadata") or {}
    return metadata.get("scope") or "localOnly"


def _is_syncable(entity: Dict[str, Any]) -> bool:
    return _scope(entity) in SYNCABLE_SCOPES


def _filter_ids(values: Iterable[str], allowed: set) -> List[str]:
    return [value for value in values if value in allowed]


def filter_syncable_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Return a backend-safe KBLite graph without localOnly entities."""
    people = [deepcopy(item) for item in graph.get("people", []) if _is_syncable(item)]
    places = [deepcopy(item) for item in graph.get("places", []) if _is_syncable(item)]
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
        events.append(copied)

    event_ids = {item.get("id") for item in events}
    facts = []
    for fact in graph.get("facts", []):
        if not _is_syncable(fact):
            continue
        copied = deepcopy(fact)
        copied["relatedPersonIds"] = _filter_ids(copied.get("relatedPersonIds", []), people_ids)
        copied["relatedPlaceIds"] = _filter_ids(copied.get("relatedPlaceIds", []), place_ids)
        copied["relatedEventIds"] = _filter_ids(copied.get("relatedEventIds", []), event_ids)
        facts.append(copied)

    return {
        "version": graph.get("version", 1),
        "lastUpdated": graph.get("lastUpdated"),
        "sessionCount": graph.get("sessionCount", 0),
        "people": people,
        "places": places,
        "events": events,
        "facts": facts,
    }
