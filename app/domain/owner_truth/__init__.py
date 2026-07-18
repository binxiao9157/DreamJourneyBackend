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
from .memory_activation import (
    OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION,
    OwnerTruthMemoryActivationError,
    OwnerTruthMemoryActivationPlan,
    OwnerTruthMemoryActivationResult,
    build_memory_activation_plan,
)

__all__ = [
    "CandidateDecision",
    "EpistemicStatus",
    "MemoryKind",
    "OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION",
    "OWNER_TRUTH_SCHEMA_VERSION",
    "OntologyValidation",
    "OwnerTruthContractError",
    "OwnerTruthMemoryActivationError",
    "OwnerTruthMemoryActivationPlan",
    "OwnerTruthMemoryActivationResult",
    "PerspectiveType",
    "SensitivityLevel",
    "SourceKind",
    "SourceState",
    "decision_receipt_matches_candidate",
    "build_memory_activation_plan",
    "validate_memory_payload",
]
