"""Conservative comparison of typed wrappers around source-bound statements.

No fuzzy matching, inference of chronology, or mutation of reviewed content.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Mapping


def _text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).rstrip("。.!！?？")


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: v for k, child in value.items() if (v := _clean(child)) is not None} or None
    if isinstance(value, (list, tuple)):
        return [_clean(x) for x in value if _clean(x) is not None] or None
    return None if value is None or value == "" or value == "unknown" else value


def _contains(richer: Any, smaller: Any) -> bool:
    if isinstance(smaller, Mapping) and isinstance(richer, Mapping):
        return all(k in richer and _contains(richer[k], v) for k, v in smaller.items())
    return richer == smaller


def _identity(content: Mapping[str, Any]) -> Any:
    p = content.get("provenance") or {}
    return (content.get("claimSubjectId"), content.get("memorySubjectId"),
            _clean({k: v for k, v in p.items() if k != "evidenceRefs"}))


def explicit_year(content: Mapping[str, Any]) -> str | None:
    t = (content.get("qualifiers") or {}).get("validTime") or {}
    year = str(t.get("expression") or "")
    if (not re.fullmatch(r"[1-9][0-9]{3}年", year)
            or t.get("precision") not in {"unknown", "year"}
            or t.get("start") or t.get("end")
            or re.findall(r"[1-9][0-9]{3}年", str(content.get("statement") or "")) != [year]):
        return None
    return year


def _business(content: Mapping[str, Any], *, omit_year: bool) -> dict:
    c = deepcopy(dict(content))
    statement = _text(c.get("statement"))
    # These are derived indexes, merged separately for new candidates. Formal
    # evidence operations always preserve the entire reviewed target payload.
    for key in ("semantic", "facets", "dimensions", "provenance", "sourceTurnIndices"):
        c.pop(key, None)
    if _text(c.get("event")) == statement:
        c.pop("event", None)
    if c.get("knowledgeType") in {"personal_experience", "personal_preference"}:
        c.pop("knowledgeType")
    if (c.get("factType"), c.get("predicate")) in {("event", "occurred"), ("knowledge", "states")} and not c.get("object"):
        c.pop("factType"); c.pop("predicate")
    year = explicit_year(content)
    if year:
        t = c["qualifiers"]["validTime"]
        t["precision"] = "year"
        if omit_year:
            t["expression"] = "<year>"
            statement = statement.replace(year, "<year>", 1)
    c["statement"] = statement
    return _clean(c) or {}


def representation_relation(a: Mapping[str, Any], b: Mapping[str, Any], *, different_years: bool = False) -> str | None:
    """equal/a_contains_b/b_contains_a, or None when not proven compatible."""
    if not _text(a.get("statement")) or _identity(a) != _identity(b):
        return None
    if different_years and (not explicit_year(a) or not explicit_year(b)):
        return None
    av, bv = _business(a, omit_year=different_years), _business(b, omit_year=different_years)
    if av.get("statement") != bv.get("statement"):
        return None
    if av == bv:
        return "equal"
    if _contains(av, bv):
        return "a_contains_b"
    if _contains(bv, av):
        return "b_contains_a"
    return None


def formal_representation_match(a: Mapping[str, Any], b: Mapping[str, Any], *, different_years: bool) -> bool:
    if representation_relation(a, b, different_years=different_years) is None:
        return False
    av, bv = _business(a, omit_year=different_years), _business(b, omit_year=different_years)
    generic = any((c.get("factType"), c.get("predicate")) in {
        ("event", "occurred"), ("knowledge", "states")
    } and not c.get("object") for c in (a, b))
    if generic:
        # Derived default-vs-specific assertion labels may differ. All real
        # qualifications and extra business content still must agree exactly.
        for value in (av, bv):
            for key in ("factType", "predicate", "object"):
                value.pop(key, None)
            for key in ("polarity", "strengthExpression"):
                (value.get("qualifiers") or {}).pop(key, None)
    return _clean(av) == _clean(bv)


def _key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _union(a: list, b: list) -> list:
    return [v for _, v in sorted({_key(x): deepcopy(x) for x in a + b}.items())]


def _merge_indexes(winner: dict, other: dict) -> dict:
    result = deepcopy(winner)
    c, oc = result["content"], other["content"]
    c["dimensions"] = sorted(set(c.get("dimensions") or []) | set(oc.get("dimensions") or []))
    for field in ("facets", "semantic"):
        if field not in c or not isinstance(c[field], dict):
            continue
        for key, val in (oc.get(field) or {}).items():
            if field == "semantic" and key == "facets":
                continue  # the surviving class rebuilds this derived index
            if isinstance(val, list) and isinstance(c[field].get(key), list):
                c[field][key] = _union(c[field][key], val)
    if isinstance(c.get("provenance"), dict):
        c["provenance"]["evidenceRefs"] = _union(c["provenance"].get("evidenceRefs") or [], (oc.get("provenance") or {}).get("evidenceRefs") or [])
    return result


def coalesce_source_memories(memories: list[dict]) -> list[dict]:
    """One source only; select a complete compatible representation, never splice facts."""
    # Stable ordering makes a duplicate pair's survivor independent of model order.
    result: list[dict] = []
    for item in sorted(deepcopy(memories), key=_key):
        combined = False
        for i, existing in enumerate(result):
            if item.get("subjectRole") != existing.get("subjectRole"):
                continue
            if not {item["memoryKind"], existing["memoryKind"]} <= {"experience", "knowledge"}:
                # Keep distinct emotional evidence. Exact identical objects may dedupe.
                if item == existing:
                    combined = True
                    break
                continue
            relation = representation_relation(item["content"], existing["content"])
            if relation is None:
                continue
            if relation == "a_contains_b":
                winner, other = item, existing
            elif relation == "b_contains_a":
                winner, other = existing, item
            else:
                winner, other = sorted([item, existing], key=_key)
            result[i] = _merge_indexes(winner, other)
            combined = True
            break
        if not combined:
            result.append(item)
    return sorted(result, key=_key)


def restore_explicit_year_prefix(content: Mapping[str, Any], *, source_text: str) -> dict:
    """Restore only an exact source sentence's omitted, already-extracted year.

    Never borrow a year from another sentence, infer a date, or mutate a formal fact.
    """
    result = deepcopy(dict(content))
    statement = _text(result.get("statement"))
    temporal = (result.get("qualifiers") or {}).get("validTime") or {}
    year = str(temporal.get("expression") or "")
    if (not re.fullmatch(r"[1-9][0-9]{3}年", year) or not statement
            or re.search(r"[0-9]{4}年", statement) or temporal.get("start")
            or temporal.get("end") or temporal.get("precision") not in {"year", "unknown"}):
        return result
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])|[\r\n]+", source_text) if part.strip()]
    matched = []
    for sentence in sentences:
        compact = _text(sentence)
        if re.fullmatch(re.escape(year) + r"[，,：:]?" + re.escape(statement), compact):
            matched.append(sentence)
    if len(matched) != 1:
        return result
    if "event" in result and _text(result["event"]) == statement:
        result["event"] = matched[0]
    result["statement"] = matched[0]
    return result
