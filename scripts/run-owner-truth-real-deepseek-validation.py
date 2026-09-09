#!/usr/bin/env python3
"""Exercise the configured DeepSeek path with synthetic Owner Truth cases.

The command is deliberately opt-in because it performs three real provider
requests. It never reads a Vault or emits prompts, model responses, API keys,
or user data. The report contains only contract identifiers, latencies, and
pass/fail assertions suitable for an A-stage evidence bundle.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Callable


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import Settings
from app.services.deepseek import (
    DeepSeekEchoAnswerProxy,
    DeepSeekLiveMemoryOrganizationProxy,
    DeepSeekTextMemoryOrganizationProxy,
)


SCHEMA_VERSION = "dreamjourney-owner-truth-real-deepseek-validation-v1"
APPROVAL_ENV = "OWNER_TRUTH_REAL_MODEL_VALIDATION_APPROVED"


@dataclass(frozen=True)
class _CaseResult:
    case_id: str
    elapsed_ms: int
    assertions: int

    def descriptor(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "elapsedMs": self.elapsed_ms,
            "assertionCount": self.assertions,
            "status": "passed",
        }


def _contains(value: object, marker: str) -> bool:
    return marker in json.dumps(value, ensure_ascii=False, sort_keys=True)


def _timed(case_id: str, assertion_count: int, operation: Callable[[], None]) -> _CaseResult:
    started = perf_counter()
    operation()
    return _CaseResult(
        case_id=case_id,
        elapsed_ms=max(0, round((perf_counter() - started) * 1000)),
        assertions=assertion_count,
    )


def _validate_text_organization(proxy: DeepSeekTextMemoryOrganizationProxy) -> None:
    result = proxy.request_organization(
        text="我于2016年从晨光大学计算机专业毕业。毕业后，我去了海岚市工作。",
    )
    if not _contains(result, "晨光大学"):
        raise AssertionError("text organization omitted the synthetic school fact")
    if not _contains(result, "2016"):
        raise AssertionError("text organization omitted the synthetic graduation year")
    if _contains(result, "北辰大学"):
        raise AssertionError("text organization introduced a forbidden school")


def _validate_live_organization(proxy: DeepSeekLiveMemoryOrganizationProxy) -> None:
    turns = [
        {
            "index": 1,
            "role": "user",
            "text": "我小时候住在青河村，每年夏天会跟外婆去河边摘莲蓬。",
        },
        {
            "index": 2,
            "role": "assistant",
            "text": "你还记得星港小学的老师吗？",
        },
        {
            "index": 3,
            "role": "user",
            "text": "我记得那时很开心，不过具体哪一年想不起来了。",
        },
    ]
    result = proxy.request_organization(turns=turns)
    if not (_contains(result, "青河村") or _contains(result, "莲蓬")):
        raise AssertionError("live organization omitted the synthetic user evidence")
    if _contains(result, "星港小学") or _contains(result, "老师"):
        raise AssertionError("live organization promoted assistant-only content")
    for memory in result.get("memories", []):
        if not set(memory.get("sourceTurnIndices", [])).issubset({1, 3}):
            raise AssertionError("live organization cited a non-user turn")


def _validate_grounded_echo(proxy: DeepSeekEchoAnswerProxy) -> None:
    answer = proxy.request_answer(
        query="我是从哪所大学毕业的？",
        generation_context=(
            "[syntheticMemoryVersionId=mv-validation-1] "
            "你于2016年从晨光大学计算机专业毕业。"
        ),
        persona_scope="personal",
        requires_authorized_memory=True,
    )
    if "晨光大学" not in answer:
        raise AssertionError("grounded Echo answer omitted the authorized school fact")
    if DeepSeekEchoAnswerProxy.memory_gap_marker in answer:
        raise AssertionError("grounded Echo answer incorrectly reported a memory gap")
    if "北辰大学" in answer:
        raise AssertionError("grounded Echo answer introduced a forbidden school")


def _result_payload(cases: list[_CaseResult]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "executionMode": "realProviderSyntheticInput",
        "provider": "deepseek",
        "caseCount": len(cases),
        "requestCount": len(cases),
        "allPassed": len(cases) == 3,
        "cases": [case.descriptor() for case in cases],
        "privateVaultRead": False,
        "responseContentRetained": False,
        "credentialRetained": False,
        "status": "passed" if len(cases) == 3 else "failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()

    if os.environ.get(APPROVAL_ENV) != "1":
        print(
            f"BLOCKED: set {APPROVAL_ENV}=1 after approving three synthetic "
            "DeepSeek validation requests.",
            file=sys.stderr,
        )
        return 3

    settings = Settings.from_env()
    if not settings.deepseek_api_key:
        print(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "blocked",
                    "reason": "deepseekConfigurationIncomplete",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3

    cases = [
        _timed(
            "textOrganizationFactsRemainObjective",
            3,
            lambda: _validate_text_organization(
                DeepSeekTextMemoryOrganizationProxy(settings)
            ),
        ),
        _timed(
            "liveOrganizationUsesUserEvidenceOnly",
            3,
            lambda: _validate_live_organization(
                DeepSeekLiveMemoryOrganizationProxy(settings)
            ),
        ),
        _timed(
            "echoAnswerUsesAuthorizedFormalFact",
            3,
            lambda: _validate_grounded_echo(DeepSeekEchoAnswerProxy(settings)),
        ),
    ]
    payload = _result_payload(cases)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if args.result is not None:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
