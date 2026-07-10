from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


KNOWLEDGE_ENTITY_TYPES = ("people", "places", "events", "facts")
USER_EVIDENCE_ONLY = "userEvidenceOnly"
LEGACY_TRANSCRIPT = "legacyTranscript"
MAX_TRANSCRIPT_CHARS = 30000
MAX_TURN_COUNT = 200
MAX_TURN_TEXT_CHARS = 4000


class KnowledgeExtractionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgeExtractionInput:
    schema_version: int
    source_policy: str
    transcript: str
    turns: Optional[List[Dict[str, Any]]]
    user_turn_indices: FrozenSet[int]

    @property
    def enforces_user_evidence(self) -> bool:
        return self.schema_version == 2 and self.source_policy == USER_EVIDENCE_ONLY


def normalize_knowledge_extraction_input(payload: Dict[str, Any]) -> KnowledgeExtractionInput:
    schema_version = payload.get("extractionSchemaVersion", 1)
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in (1, 2)
    ):
        raise KnowledgeExtractionValidationError("extractionSchemaVersion must be 1 or 2")

    if schema_version == 1:
        transcript = str(payload.get("transcript") or "").strip()
        if not transcript:
            raise KnowledgeExtractionValidationError("transcript is required")
        if len(transcript) > MAX_TRANSCRIPT_CHARS:
            raise KnowledgeExtractionValidationError(
                f"transcript must not exceed {MAX_TRANSCRIPT_CHARS} characters"
            )
        return KnowledgeExtractionInput(
            schema_version=1,
            source_policy=LEGACY_TRANSCRIPT,
            transcript=transcript,
            turns=None,
            user_turn_indices=frozenset(),
        )

    source_policy = payload.get("sourcePolicy")
    if source_policy != USER_EVIDENCE_ONLY:
        raise KnowledgeExtractionValidationError(
            "sourcePolicy must be userEvidenceOnly for extractionSchemaVersion 2"
        )

    raw_turns = payload.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise KnowledgeExtractionValidationError("turns must be a non-empty array")
    if len(raw_turns) > MAX_TURN_COUNT:
        raise KnowledgeExtractionValidationError(
            f"turns must not contain more than {MAX_TURN_COUNT} entries"
        )

    turns: List[Dict[str, Any]] = []
    user_turn_indices = set()
    total_text_chars = 0
    for position, raw_turn in enumerate(raw_turns):
        if not isinstance(raw_turn, dict):
            raise KnowledgeExtractionValidationError(f"turns[{position}] must be an object")

        index = raw_turn.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise KnowledgeExtractionValidationError(
                f"turns[{position}].index must be a non-negative integer"
            )
        if index != position:
            raise KnowledgeExtractionValidationError(
                f"turns[{position}].index must equal its zero-based position"
            )

        role = raw_turn.get("role")
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            raise KnowledgeExtractionValidationError(
                f"turns[{position}].role must be user or assistant"
            )

        text = raw_turn.get("text")
        if not isinstance(text, str) or not text.strip():
            raise KnowledgeExtractionValidationError(
                f"turns[{position}].text must be a non-empty string"
            )
        normalized_text = text.strip()
        if len(normalized_text) > MAX_TURN_TEXT_CHARS:
            raise KnowledgeExtractionValidationError(
                f"turns[{position}].text must not exceed {MAX_TURN_TEXT_CHARS} characters"
            )
        total_text_chars += len(normalized_text)
        if total_text_chars > MAX_TRANSCRIPT_CHARS:
            raise KnowledgeExtractionValidationError(
                f"turn text must not exceed {MAX_TRANSCRIPT_CHARS} characters in total"
            )

        normalized_turn = {"index": index, "role": role, "text": normalized_text}
        turns.append(normalized_turn)
        if role == "user":
            user_turn_indices.add(index)

    return KnowledgeExtractionInput(
        schema_version=2,
        source_policy=USER_EVIDENCE_ONLY,
        transcript="",
        turns=turns,
        user_turn_indices=frozenset(user_turn_indices),
    )


def sanitize_knowledge_extraction_context(context: Dict[str, Any]) -> Dict[str, Any]:
    safe_context = deepcopy(context)
    for key in (
        "transcript",
        "rawTranscript",
        "messages",
        "sourceTexts",
        "turns",
        "existingSummary",
    ):
        safe_context.pop(key, None)
    return safe_context


def empty_evidence_policy(extraction_input: KnowledgeExtractionInput) -> Dict[str, Any]:
    return {
        "version": 1,
        "sourcePolicy": extraction_input.source_policy,
        "userTurnCount": len(extraction_input.user_turn_indices),
        "acceptedEntityCount": 0,
        "filteredEntityCount": 0,
        "filteredReasons": {},
    }


def filter_extraction_by_evidence(
    extraction: Dict[str, Any],
    extraction_input: KnowledgeExtractionInput,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    filtered_extraction: Dict[str, List[Dict[str, Any]]] = {
        entity_type: [] for entity_type in KNOWLEDGE_ENTITY_TYPES
    }
    evidence_policy = empty_evidence_policy(extraction_input)
    filtered_reasons: Dict[str, int] = {}
    all_turn_indices = {
        turn["index"] for turn in (extraction_input.turns or [])
    }

    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        entities = extraction.get(entity_type)
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            copied_entity = deepcopy(entity)
            if not extraction_input.enforces_user_evidence:
                filtered_extraction[entity_type].append(copied_entity)
                evidence_policy["acceptedEntityCount"] += 1
                continue

            rejection_reason = _evidence_rejection_reason(
                copied_entity.get("sourceTurnIndices"),
                all_turn_indices=all_turn_indices,
                user_turn_indices=extraction_input.user_turn_indices,
            )
            if rejection_reason is None:
                filtered_extraction[entity_type].append(copied_entity)
                evidence_policy["acceptedEntityCount"] += 1
                continue

            evidence_policy["filteredEntityCount"] += 1
            filtered_reasons[rejection_reason] = filtered_reasons.get(rejection_reason, 0) + 1

    evidence_policy["filteredReasons"] = dict(sorted(filtered_reasons.items()))
    return filtered_extraction, evidence_policy


def _evidence_rejection_reason(
    source_turn_indices: Any,
    *,
    all_turn_indices: set,
    user_turn_indices: FrozenSet[int],
) -> Optional[str]:
    if source_turn_indices is None or source_turn_indices == []:
        return "missingSourceTurnIndices"
    if not isinstance(source_turn_indices, list) or any(
        isinstance(index, bool) or not isinstance(index, int)
        for index in source_turn_indices
    ):
        return "invalidSourceTurnIndices"
    if any(index not in all_turn_indices for index in source_turn_indices):
        return "outOfRangeSourceTurnIndices"
    if any(index not in user_turn_indices for index in source_turn_indices):
        return "nonUserSourceTurnIndices"
    return None
