"""One fail-closed eligibility rule for formal-memory consumers.

Search, text Echo, Live snapshots and derived reading views all consume the
same current Owner Truth projection.  They must therefore agree about which
facts can be used: only a current projection entry is eligible; entries in an
unresolved semantic group are not, and a ready/merged group contributes only
its deterministic representative.  This module deliberately contains no
memory text in its public summary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping


FORMAL_FACT_ELIGIBILITY_SCHEMA_VERSION = "owner-truth-formal-fact-eligibility-v1"
_USABLE_GROUP_STATUSES = frozenset({"ready", "merged"})


class FormalFactEligibilityError(ValueError):
    """The current projection cannot be safely interpreted for consumption."""


def _nonblank(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FormalFactEligibilityError(f"{field} must be nonblank")
    return text


def _version_id(entry: Mapping[str, Any]) -> str:
    direct = str(entry.get("memoryVersionId") or "").strip()
    if direct:
        return direct
    citation = entry.get("citation")
    if isinstance(citation, Mapping):
        return _nonblank(citation.get("memoryVersionId"), field="entry memoryVersionId")
    raise FormalFactEligibilityError("projection entry memoryVersionId is missing")


def _semantic_groups(projection: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    model = projection.get("personMemoryModel")
    if not isinstance(model, Mapping):
        return None
    consolidation = model.get("semanticConsolidation")
    if not isinstance(consolidation, Mapping):
        return None
    groups = consolidation.get("groups")
    if not isinstance(groups, list) or any(not isinstance(item, Mapping) for item in groups):
        raise FormalFactEligibilityError("semantic consolidation groups are invalid")
    return list(groups)


@dataclass(frozen=True)
class FormalFactEligibilityResult:
    """Eligible current projection entries and a value-free exclusion summary."""

    eligible_entries: tuple[Mapping[str, Any], ...]
    eligible_memory_version_ids: frozenset[str]
    suppressed_by_reason: Mapping[str, int]
    group_count: int

    def public_summary(self) -> dict[str, Any]:
        return {
            "schemaVersion": FORMAL_FACT_ELIGIBILITY_SCHEMA_VERSION,
            "eligibleFactCount": len(self.eligible_entries),
            "suppressedFactCount": sum(self.suppressed_by_reason.values()),
            "suppressionReasons": dict(sorted(self.suppressed_by_reason.items())),
            "semanticGroupCount": self.group_count,
        }


def evaluate_formal_fact_eligibility(
    projection: Mapping[str, Any],
) -> FormalFactEligibilityResult:
    """Return the single safe fact set for all formal-memory consumers.

    ``OwnerTruthMemoryProjectionService`` already makes authority, revision and
    rights checks before returning a ready projection.  This function is the
    next layer: it validates current-entry identity and semantic consolidation
    state without reading historical Source or Candidate payloads.
    """

    if not isinstance(projection, Mapping):
        raise FormalFactEligibilityError("memory projection must be an object")
    if str(projection.get("state") or "") != "ready":
        raise FormalFactEligibilityError("formal fact eligibility requires a ready projection")
    raw_entries = projection.get("entries")
    if not isinstance(raw_entries, list):
        raise FormalFactEligibilityError("ready projection entries must be a list")

    entries_by_version: dict[str, Mapping[str, Any]] = {}
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            raise FormalFactEligibilityError("projection entry must be an object")
        version_id = _version_id(entry)
        if version_id in entries_by_version:
            raise FormalFactEligibilityError("projection contains duplicate memoryVersionId")
        entries_by_version[version_id] = entry

    groups = _semantic_groups(projection)
    if groups is None or not groups:
        return FormalFactEligibilityResult(
            eligible_entries=tuple(entries_by_version.values()),
            eligible_memory_version_ids=frozenset(entries_by_version),
            suppressed_by_reason={},
            group_count=0,
        )

    grouped_versions: set[str] = set()
    eligible_versions: set[str] = set()
    suppressed = Counter()
    for group in groups:
        status = _nonblank(group.get("status"), field="semantic group status")
        representative = _nonblank(
            group.get("representativeMemoryVersionId"),
            field="semantic representativeMemoryVersionId",
        )
        supporting = group.get("supportingMemoryVersionIds")
        if not isinstance(supporting, list) or not supporting:
            raise FormalFactEligibilityError("semantic group supportingMemoryVersionIds are invalid")
        supporting_ids = tuple(
            _nonblank(value, field="semantic supportingMemoryVersionId")
            for value in supporting
        )
        if len(set(supporting_ids)) != len(supporting_ids):
            raise FormalFactEligibilityError("semantic group has duplicate supporting memory versions")
        if representative not in supporting_ids:
            raise FormalFactEligibilityError("semantic representative is not part of its evidence")
        if representative not in entries_by_version:
            raise FormalFactEligibilityError("semantic representative is not in the current projection")
        for version_id in supporting_ids:
            if version_id not in entries_by_version:
                raise FormalFactEligibilityError("semantic evidence is not in the current projection")
            if version_id in grouped_versions:
                raise FormalFactEligibilityError("memory version belongs to multiple semantic groups")
            grouped_versions.add(version_id)

        if status in _USABLE_GROUP_STATUSES:
            eligible_versions.add(representative)
            suppressed["semanticGroupNonRepresentative"] += len(supporting_ids) - 1
        else:
            suppressed["semanticGroupNotReady"] += len(supporting_ids)

    for version_id in entries_by_version:
        if version_id not in grouped_versions:
            eligible_versions.add(version_id)

    return FormalFactEligibilityResult(
        eligible_entries=tuple(
            entry for version_id, entry in entries_by_version.items() if version_id in eligible_versions
        ),
        eligible_memory_version_ids=frozenset(eligible_versions),
        suppressed_by_reason=dict(suppressed),
        group_count=len(groups),
    )


__all__ = [
    "FORMAL_FACT_ELIGIBILITY_SCHEMA_VERSION",
    "FormalFactEligibilityError",
    "FormalFactEligibilityResult",
    "evaluate_formal_fact_eligibility",
]
