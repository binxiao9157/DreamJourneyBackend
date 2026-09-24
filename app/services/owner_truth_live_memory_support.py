"""Fail-closed semantic support contract for closed Live memory drafts.

The model supplies semantic judgments, while this module verifies their
structure and applies them before any CandidateProposal is constructed.
"""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from app.services.owner_truth_live_memory_contract_errors import contract_failure


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


def build_live_memory_evidence_catalog(
    turns: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build immutable, input-owned evidence fragments for organization output.

    The identifier is derived from the input position and exact text, never from
    a generated memory expression.  A single fragment may legitimately support
    more than one atom; page ownership is handled separately by atom IDs.
    """

    catalog: list[dict[str, Any]] = []
    for turn_ordinal, turn in enumerate(turns):
        if turn.get("role") != "user":
            continue
        turn_index = turn.get("index")
        text = turn.get("text")
        if (
            isinstance(turn_index, bool)
            or not isinstance(turn_index, int)
            or not isinstance(text, str)
            or not text
        ):
            continue
        matches = list(re.finditer(r"[^。！？；;!?]+[。！？；;!?]?", text))
        if not matches:
            matches = [re.match(r"[\s\S]+", text)]
        for match in matches:
            if match is None:
                continue
            raw_start, raw_end = match.span()
            fragment = match.group(0)
            leading = len(fragment) - len(fragment.lstrip())
            trailing = len(fragment) - len(fragment.rstrip())
            start = raw_start + leading
            end = raw_end - trailing
            if end <= start:
                continue
            exact_text = text[start:end]
            text_hash = sha256(exact_text.encode("utf-8")).hexdigest()
            evidence_id = sha256(
                json.dumps(
                    [turn_ordinal, turn_index, start, end, text_hash],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            catalog.append(
                {
                    "evidenceId": evidence_id,
                    "turnOrdinal": turn_ordinal,
                    "turnIndex": turn_index,
                    "start": start,
                    "end": end,
                    "textHash": text_hash,
                    "text": exact_text,
                }
            )
    return catalog


def _invalid(reason: str):
    return contract_failure("supportValidate", reason, eligible=True)


def validate_live_memory_support(
    *,
    turns: Sequence[Mapping[str, Any]],
    memories: Sequence[Mapping[str, Any]],
    review: Mapping[str, Any],
    responsibility_atom_ids: Sequence[str] | None = None,
    source_index_by_projected_index: Mapping[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Return the final supported drafts or reject an incomplete review."""

    if review.get("schemaVersion") != LIVE_MEMORY_SUPPORT_SCHEMA_VERSION:
        raise _invalid("schemaInvalid")

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
        raise _invalid("evidenceInvalid")
    source_indices_by_projection = source_index_by_projected_index or {
        index: index for index in user_indices
    }
    if set(source_indices_by_projection) != user_indices or any(
        type(source_index) is not int or source_index < 0
        for source_index in source_indices_by_projection.values()
    ):
        raise _invalid("evidenceInvalid")

    raw_turn_reviews = review.get("turnAssessments")
    if not isinstance(raw_turn_reviews, list):
        raise _invalid("coverageIncomplete")
    speech_acts: dict[int, str] = {}
    ignored_assistant_indices: set[int] = set()
    for item in raw_turn_reviews:
        if not isinstance(item, Mapping):
            raise _invalid("schemaInvalid")
        index = item.get("turnIndex")
        speech_act = item.get("speechAct")
        if isinstance(index, bool) or not isinstance(index, int):
            raise _invalid("evidenceInvalid")
        if index in assistant_indices:
            if speech_act not in _SPEECH_ACTS or index in ignored_assistant_indices:
                raise _invalid("evidenceInvalid")
            ignored_assistant_indices.add(index)
            continue
        if index not in user_indices or index in speech_acts or speech_act not in _SPEECH_ACTS:
            raise _invalid("evidenceInvalid")
        speech_acts[index] = str(speech_act)
    if set(speech_acts) != user_indices:
        raise _invalid("coverageIncomplete")

    raw_memory_reviews = review.get("memoryAssessments")
    if not isinstance(raw_memory_reviews, list):
        raise _invalid("coverageIncomplete")
    memory_reviews: dict[int, tuple[str, tuple[int, ...]]] = {}
    for item in raw_memory_reviews:
        if not isinstance(item, Mapping):
            raise _invalid("schemaInvalid")
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
            or any(
                isinstance(index, bool)
                or not isinstance(index, int)
                or index not in user_indices
                for index in supporting
            )
            or len(set(supporting)) != len(supporting)
        ):
            raise _invalid("evidenceInvalid")
        unique_supporting = tuple(supporting)
        source_indices = memories[memory_index].get("sourceTurnIndices")
        if (
            not isinstance(source_indices, list)
            or any(
                isinstance(index, bool) or not isinstance(index, int)
                for index in source_indices
            )
            or len(set(source_indices)) != len(source_indices)
        ):
            raise _invalid("evidenceInvalid")
        if any(source_indices_by_projection[index] not in source_indices for index in unique_supporting):
            raise _invalid("evidenceOutOfRange")
        if verdict == "supported":
            if not unique_supporting or any(
                speech_acts[index] not in _FACT_BEARING_ACTS for index in unique_supporting
            ):
                raise _invalid("evidenceInvalid")
        elif unique_supporting:
            raise _invalid("evidenceInvalid")
        if verdict == "uncertain":
            raise _invalid("semanticUncertain")
        memory_reviews[memory_index] = (str(verdict), unique_supporting)
    if set(memory_reviews) != set(range(len(memories))):
        raise _invalid("coverageIncomplete")

    omitted = review.get("omittedFactBearingTurnIndices")
    if (
        not isinstance(omitted, list)
        or any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or index not in user_indices
            for index in omitted
        )
        or len(set(omitted)) != len(omitted)
    ):
        raise _invalid("evidenceInvalid")
    atom_scoped = responsibility_atom_ids is not None
    if atom_scoped:
        expected_atom_ids = tuple(dict.fromkeys(str(value) for value in responsibility_atom_ids or ()))
        if not expected_atom_ids:
            raise _invalid("coverageIncomplete")
        observed_atom_ids = tuple(
            dict.fromkeys(
                str(atom_id)
                for memory in memories
                for atom_id in memory.get("_atomIds", ())
                if str(atom_id)
            )
        )
        reviewed_atom_ids = review.get("responsibilityAtomIds")
        omitted_atom_ids = review.get("omittedOwnedAtomIds")
        if (
            set(observed_atom_ids) != set(expected_atom_ids)
            or not isinstance(reviewed_atom_ids, list)
            or any(not isinstance(value, str) or not value for value in reviewed_atom_ids)
            or set(reviewed_atom_ids) != set(expected_atom_ids)
            or not isinstance(omitted_atom_ids, list)
            or any(not isinstance(value, str) or value not in expected_atom_ids for value in omitted_atom_ids)
        ):
            raise _invalid("coverageIncomplete")
        if omitted_atom_ids:
            raise _invalid("factOmitted")
        supported_atom_ids = {
            str(atom_id)
            for memory_index, (verdict, _supporting) in memory_reviews.items()
            if verdict == "supported"
            for atom_id in memories[memory_index].get("_atomIds", ())
            if str(atom_id)
        }
        if set(expected_atom_ids) - supported_atom_ids:
            raise _invalid("factWithoutFinalDraft")
    elif omitted:
        raise _invalid("factOmitted")

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
        for index in user_indices
        if source_indices_by_projection[index] in memories[memory_index].get("sourceTurnIndices", [])
    }
    if not atom_scoped and fact_bearing_indices - supported_indices - superseded_indices:
        raise _invalid("factWithoutFinalDraft")

    return [
        dict(memory)
        for index, memory in enumerate(memories)
        if memory_reviews[index][0] == "supported"
    ]
