"""Owner Truth V1 domain contracts.

This package deliberately contains no legacy Archive/KBLite adapters.  Those
systems remain compatibility sources until a later, explicitly gated slice
adds the corresponding facade.
"""

from .contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    OwnerTruthContractError,
    PerspectiveType,
    SensitivityLevel,
    SourceKind,
    SourceState,
    decision_receipt_matches_candidate,
)
from .ontology import (
    OWNER_TRUTH_SCHEMA_VERSION,
    OntologyValidation,
    validate_memory_payload,
)

__all__ = [
    "CandidateDecision",
    "EpistemicStatus",
    "MemoryKind",
    "OWNER_TRUTH_SCHEMA_VERSION",
    "OntologyValidation",
    "OwnerTruthContractError",
    "PerspectiveType",
    "SensitivityLevel",
    "SourceKind",
    "SourceState",
    "decision_receipt_matches_candidate",
    "validate_memory_payload",
]
