"""Typed, value-free failures for the closed-Live candidate pipeline."""

from __future__ import annotations

from dataclasses import dataclass


_STAGES = frozenset(
    {
        "sourceRead",
        "organizationInput",
        "organizationRequest",
        "organizationDecode",
        "organizationValidate",
        "pipelineInput",
        "relationInput",
        "relationRequest",
        "relationDecode",
        "relationValidate",
        "supportInput",
        "supportRequest",
        "supportDecode",
        "supportValidate",
        "proposalBuild",
        "candidateCommit",
        "manifestBuild",
        "workerExecution",
    }
)


@dataclass(frozen=True)
class LiveMemoryContractFailure(ValueError):
    """A safe classification that never carries transcript or provider body."""

    stage: str
    reason: str
    category: str
    contract_retry_eligible: bool = False
    provider_status: int | None = None
    transport_retryable: bool = False
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.stage not in _STAGES:
            raise ValueError("unsupported Live memory failure stage")
        if not self.reason or not self.reason.replace("_", "").isalnum():
            raise ValueError("Live memory failure reason must be an opaque identifier")
        if not self.category or not self.category.replace("_", "").isalnum():
            raise ValueError("Live memory failure category must be an opaque identifier")
        if self.retry_after_seconds is not None and (
            type(self.retry_after_seconds) is not int or self.retry_after_seconds < 0
        ):
            raise ValueError("Live memory retry delay must be a nonnegative integer")
        ValueError.__init__(self, self.code)

    @property
    def code(self) -> str:
        return f"candidateExtraction.live.{self.stage}.{self.reason}"


@dataclass(frozen=True)
class LiveMemoryContractRetryContext:
    """Persisted safe feedback for a later attempt of the same immutable job."""

    attempt: int
    stage: str
    reason: str

    @property
    def repair_hint(self) -> str:
        if self.stage.startswith("support"):
            return "Re-run the full organization and return a complete independent support review."
        if self.stage.startswith("relation"):
            return "Return one complete bounded relation decision for every supplied existing atom."
        if self.reason in {"evidenceInvalid", "evidenceOutOfRange"}:
            return "Return only evidence indices from role=user turns in the supplied conversation."
        return "Return the complete strict JSON contract without commentary or truncation."


def contract_failure(
    stage: str,
    reason: str,
    *,
    category: str = "contract",
    eligible: bool = False,
) -> LiveMemoryContractFailure:
    return LiveMemoryContractFailure(
        stage=stage,
        reason=reason,
        category=category,
        contract_retry_eligible=eligible,
    )


__all__ = [
    "LiveMemoryContractFailure",
    "LiveMemoryContractRetryContext",
    "contract_failure",
]
