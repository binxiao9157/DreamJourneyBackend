#!/usr/bin/env python3
"""Run the checked-in K01-K26 backend regression mapping.

The mapping does not replace behavior tests. It makes every acceptance scenario
resolve to one or more real unittest selectors and emits a value-free result
that reviewers can rerun. Optional source-fixture verification prevents a
quietly changed handoff fixture from inheriting old evidence.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
DEFAULT_MAPPING = (
    ROOT_DIR / "tests/fixtures/owner_truth/key_scenario_automated_mapping_v1.json"
)
SCHEMA_VERSION = "owner-truth-key-scenario-gate-result-v1"


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_mapping(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != "owner-truth-key-scenario-automated-mapping-v1":
        raise ValueError("key scenario mapping schema is unsupported")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("key scenario mapping scenarios must be an array")
    expected_ids = [f"K{index:02d}" for index in range(1, 27)]
    actual_ids = [str(item.get("id")) for item in scenarios if isinstance(item, dict)]
    if actual_ids != expected_ids:
        raise ValueError("key scenario mapping must contain ordered K01-K26 exactly once")
    return payload


def _run_selector(selector: str) -> tuple[bool, int, str]:
    suite = unittest.defaultTestLoader.loadTestsFromName(selector)
    if suite.countTestCases() < 1:
        return False, 0, "selectorResolvedNoTests"
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if result.wasSuccessful():
        return True, result.testsRun, "passed"
    if result.errors and any("_FailedTest" in str(test) for test, _ in result.errors):
        return False, result.testsRun, "selectorImportFailed"
    return False, result.testsRun, "behaviorTestFailed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--source-fixture", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()

    mapping = _load_mapping(args.mapping)
    expected_source_hash = str(mapping.get("sourceFixtureSha256") or "")
    if args.source_fixture is not None:
        if _sha256(args.source_fixture) != expected_source_hash:
            raise ValueError("source key-scenario fixture hash does not match mapping")

    selector_results: dict[str, dict[str, object]] = {}
    scenario_results: list[dict[str, object]] = []
    for scenario in mapping["scenarios"]:
        assert isinstance(scenario, dict)
        selectors = scenario.get("backendTests")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"{scenario['id']} has no backend behavior tests")
        for raw_selector in selectors:
            selector = str(raw_selector)
            if selector not in selector_results:
                passed, count, status = _run_selector(selector)
                selector_results[selector] = {
                    "status": status,
                    "passed": passed,
                    "testsRun": count,
                }
        passed = all(bool(selector_results[str(item)]["passed"]) for item in selectors)
        scenario_results.append(
            {
                "id": scenario["id"],
                "name": scenario["name"],
                "backendSelectorCount": len(selectors),
                "iosSelectorCount": len(scenario.get("iosTests", [])),
                "status": "passed" if passed else "failed",
            }
        )

    all_passed = all(item["status"] == "passed" for item in scenario_results)
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "mappingSha256": _sha256(args.mapping),
        "sourceFixtureSha256": expected_source_hash,
        "sourceFixtureVerified": args.source_fixture is not None,
        "scenarioCount": len(scenario_results),
        "backendSelectorCount": len(selector_results),
        "allPassed": all_passed,
        "scenarios": scenario_results,
        "failedSelectors": sorted(
            selector for selector, result in selector_results.items() if not result["passed"]
        ),
    }
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.result is not None:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if all_passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
