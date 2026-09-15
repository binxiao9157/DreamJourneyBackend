"""Fail-closed semantic support contract for closed Live memory drafts.

The model supplies semantic judgments, while this module verifies their
structure and applies them before any CandidateProposal is constructed.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


LIVE_MEMORY_SUPPORT_SCHEMA_VERSION = "owner-truth-live-memory-support-v1"
LIVE_MEMORY_SUPPORT_VALIDATOR_VERSION = "live-memory-support-validator-v1"

_SPEECH_ACTS = frozenset(
    {
        "assertion",
        "correction",
        "timeSupplement",
        "query",
        "quotedSpeech",
        "ambiguous",
    }
)
_FACT_BEARING_ACTS = frozenset({"assertion", "correction", "timeSupplement"})
_VERDICTS = frozenset({"supported", "unsupported", "superseded", "uncertain"})


def validate_live_memory_support(
    *,
    turns: Sequence[Mapping[str, Any]],
    memories: Sequence[Mapping[str, Any]],
    review: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return the final supported drafts or reject an incomplete review."""

    if review.get("schemaVersion") != LIVE_MEMORY_SUPPORT_SCHEMA_VERSION:
        raise ValueError("live memory support review has an unsupported schema")

    user_indices = {
        turn.get("index")
        for turn in turns
        if isinstance(turn, Mapping) and turn.get("role") == "user"
    }
    assistant_indices = {
        turn.get("index")
        for turn in turns
        if isinstance(turn, Mapping) and turn.get("role") == "assistant"
    }
    if not user_indices or any(isinstance(index, bool) or not isinstance(index, int) for index in user_indices):
        raise ValueError("live memory support review has invalid user turns")

    raw_turn_reviews = review.get("turnAssessments")
    if not isinstance(raw_turn_reviews, list):
        raise ValueError("live memory support review misses turn assessments")
    speech_acts: dict[int, str] = {}
    ignored_assistant_indices: set[int] = set()
    for item in raw_turn_reviews:
        if not isinstance(item, Mapping):
            raise ValueError("live memory support turn assessment must be an object")
        index = item.get("turnIndex")
        speech_act = item.get("speechAct")
        if index in assistant_indices:
            if speech_act not in _SPEECH_ACTS or index in ignored_assistant_indices:
                raise ValueError("live memory support assistant assessment is invalid")
            ignored_assistant_indices.add(index)
            continue
        if index not in user_indices or index in speech_acts or speech_act not in _SPEECH_ACTS:
            raise ValueError("live memory support turn assessment is invalid")
        speech_acts[index] = str(speech_act)
    if set(speech_acts) != user_indices:
        raise ValueError("live memory support review does not cover every user turn")

    raw_memory_reviews = review.get("memoryAssessments")
    if not isinstance(raw_memory_reviews, list):
        raise ValueError("live memory support review misses memory assessments")
    memory_reviews: dict[int, tuple[str, tuple[int, ...]]] = {}
    for item in raw_memory_reviews:
        if not isinstance(item, Mapping):
            raise ValueError("live memory support memory assessment must be an object")
        memory_index = item.get("memoryIndex")
        verdict = item.get("verdict")
        supporting = item.get("supportingTurnIndices")
        if (
            isinstance(memory_index, bool)
            or not isinstance(memory_index, int)
            or memory_index < 0
            or memory_index >= len(memories)
            or memory_index in memory_reviews
            or verdict not in _VERDICTS
            or not isinstance(supporting, list)
            or any(index not in user_indices for index in supporting)
        ):
            raise ValueError("live memory support memory assessment is invalid")
        unique_supporting = tuple(dict.fromkeys(supporting))
        source_indices = memories[memory_index].get("sourceTurnIndices")
        if not isinstance(source_indices, list):
            raise ValueError("live memory support draft misses source indices")
        if any(index not in source_indices for index in unique_supporting):
            raise ValueError("live memory support exceeds the draft evidence binding")
        if verdict == "supported":
            if not unique_supporting or any(
                speech_acts[index] not in _FACT_BEARING_ACTS for index in unique_supporting
            ):
                raise ValueError("live memory support uses a non-factual user turn")
        elif unique_supporting:
            raise ValueError("non-supported live memory draft cannot claim supporting turns")
        if verdict == "uncertain":
            raise ValueError("live memory support review is uncertain")
        memory_reviews[memory_index] = (str(verdict), unique_supporting)
    if set(memory_reviews) != set(range(len(memories))):
        raise ValueError("live memory support review does not cover every draft")

    omitted = review.get("omittedFactBearingTurnIndices")
    if (
        not isinstance(omitted, list)
        or any(index not in user_indices for index in omitted)
        or len(set(omitted)) != len(omitted)
    ):
        raise ValueError("live memory support omitted-turn contract is invalid")
    if omitted:
        raise ValueError("live memory organizer omitted fact-bearing user turns")

    fact_bearing_indices = {
        index for index, speech_act in speech_acts.items() if speech_act in _FACT_BEARING_ACTS
    }
    supported_indices = {
        index
        for verdict, supporting in memory_reviews.values()
        if verdict == "supported"
        for index in supporting
    }
    superseded_indices = {
        index
        for memory_index, (verdict, _supporting) in memory_reviews.items()
        if verdict == "superseded"
        for index in memories[memory_index].get("sourceTurnIndices", [])
        if index in user_indices
    }
    if fact_bearing_indices - supported_indices - superseded_indices:
        raise ValueError("live memory support review leaves facts without a final draft")

    return [
        dict(memory)
        for index, memory in enumerate(memories)
        if memory_reviews[index][0] == "supported"
    ]
