from __future__ import annotations

import ast
from pathlib import Path
import unittest

from app.observability.operation_metric_coverage import (
    CRITICAL_WORKER_COVERAGE,
    OPERATION_METRIC_COVERAGE_SCHEMA_VERSION,
    OperationMetricCoverageEntry,
    OperationMetricCoverageError,
    OperationMetricCoverageManifest,
    OperationMetricCoverageStatus,
    build_operation_metric_coverage_manifest,
)


class OperationMetricCoverageTests(unittest.TestCase):
    def test_registered_routes_are_all_classified_as_middleware_instrumented(self) -> None:
        import app.main as main_module

        expected_routes = main_module._operation_metric_expected_routes()
        manifest = build_operation_metric_coverage_manifest(expected_routes)
        route_entries = [entry for entry in manifest.entries if entry.component_kind == "httpRoute"]

        self.assertEqual({entry.component_id for entry in route_entries}, expected_routes)
        self.assertTrue(
            all(entry.status is OperationMetricCoverageStatus.INSTRUMENTED for entry in route_entries)
        )

    def test_all_worker_runtimes_are_explicitly_cataloged_and_not_overclaimed(self) -> None:
        root = Path(__file__).resolve().parents[1] / "app" / "async_effects"
        discovered: set[str] = set()
        for path in root.rglob("*.py"):
            if path.name == "__init__.py":
                continue
            module = "app." + ".".join(path.relative_to(root.parent).with_suffix("").parts)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("WorkerRuntime"):
                    discovered.add(f"{module}.{node.name}")

        catalog = {entry.component_id for entry in CRITICAL_WORKER_COVERAGE}
        self.assertEqual(discovered, catalog)
        self.assertTrue(
            all(
                entry.status is OperationMetricCoverageStatus.NOT_INSTRUMENTED
                for entry in CRITICAL_WORKER_COVERAGE
            )
        )

    def test_worker_gap_is_fail_closed_and_observation_summary_is_value_free(self) -> None:
        manifest = build_operation_metric_coverage_manifest({"GET /health"})
        summary = manifest.observation_summary()

        self.assertEqual(summary["schemaVersion"], OPERATION_METRIC_COVERAGE_SCHEMA_VERSION)
        self.assertEqual(summary["httpRouteCoverage"]["instrumentedCount"], 1)
        self.assertEqual(summary["criticalWorkerCoverage"]["notInstrumentedCount"], 2)
        self.assertFalse(summary["coverageComplete"])
        self.assertFalse(summary["sloClaimAllowed"])
        self.assertTrue(summary["valueFree"])
        rendered = str(summary)
        self.assertNotIn("GET /health", rendered)
        self.assertNotIn("AsyncEffectWorkerRuntime", rendered)

    def test_manifest_rejects_missing_worker_catalog_and_invalid_status(self) -> None:
        route = OperationMetricCoverageEntry(
            component_id="GET /health",
            component_kind="httpRoute",
            status=OperationMetricCoverageStatus.INSTRUMENTED,
            reason_code="routeAuthenticationMiddleware",
        )
        with self.assertRaises(OperationMetricCoverageError):
            OperationMetricCoverageManifest(entries=(route,))
        with self.assertRaises(OperationMetricCoverageError):
            OperationMetricCoverageEntry(
                component_id="worker",
                component_kind="worker",
                status="unknown",  # type: ignore[arg-type]
                reason_code="unknown",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
