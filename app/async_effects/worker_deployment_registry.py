"""Value-free deployment registry for long-running async-effect workers.

The registry is the single source of truth shared by worker activation and
post-migration image alignment. It contains only public code identifiers and
never serializes environment values or Provider credentials.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json

from app.core.config import Settings


@dataclass(frozen=True)
class WorkerDeploymentSpec:
    worker: str
    settings_flag: str
    compose_service: str

    def enabled(self, settings: Settings) -> bool:
        return bool(getattr(settings, self.settings_flag))

    def public_descriptor(
        self,
        *,
        settings: Settings | None = None,
    ) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "worker": self.worker,
            "settingsFlag": self.settings_flag,
            "composeService": self.compose_service,
        }
        if settings is not None:
            descriptor["enabled"] = self.enabled(settings)
        return descriptor


LONG_RUNNING_WORKERS: tuple[WorkerDeploymentSpec, ...] = (
    WorkerDeploymentSpec(
        worker="narrativeGeneration",
        settings_flag="narrative_generation_worker_enabled",
        compose_service="narrative-generation-worker",
    ),
    WorkerDeploymentSpec(
        worker="ownerTruthCandidateExtraction",
        settings_flag="owner_truth_candidate_extraction_worker_enabled",
        compose_service="owner-truth-candidate-extraction-worker",
    ),
    WorkerDeploymentSpec(
        worker="ownerTruthMemoryProjection",
        settings_flag="owner_truth_memory_projection_worker_enabled",
        compose_service="owner-truth-memory-projection-worker",
    ),
    WorkerDeploymentSpec(
        worker="ownerTruthMediaProcessing",
        settings_flag="owner_truth_media_processing_worker_enabled",
        compose_service="owner-truth-media-processing-worker",
    ),
    WorkerDeploymentSpec(
        worker="ownerTruthMediaDeletion",
        settings_flag="owner_truth_media_deletion_worker_enabled",
        compose_service="owner-truth-media-deletion-worker",
    ),
    WorkerDeploymentSpec(
        worker="businessMessageProjection",
        settings_flag="business_message_projection_worker_enabled",
        compose_service="business-message-projection-worker",
    ),
    WorkerDeploymentSpec(
        worker="publicationExternalCleanupMaterializer",
        settings_flag="publication_external_cleanup_materializer_enabled",
        compose_service="publication-external-cleanup-materializer-worker",
    ),
)


_BY_WORKER = {spec.worker: spec for spec in LONG_RUNNING_WORKERS}

if len(_BY_WORKER) != len(LONG_RUNNING_WORKERS):
    raise RuntimeError("duplicate long-running worker identifier")
if len({spec.settings_flag for spec in LONG_RUNNING_WORKERS}) != len(LONG_RUNNING_WORKERS):
    raise RuntimeError("duplicate long-running worker settings flag")
if len({spec.compose_service for spec in LONG_RUNNING_WORKERS}) != len(LONG_RUNNING_WORKERS):
    raise RuntimeError("duplicate long-running worker compose service")


def deployment_spec_for(worker: str) -> WorkerDeploymentSpec:
    try:
        return _BY_WORKER[worker]
    except KeyError as exc:
        raise ValueError(f"unsupported long-running worker: {worker}") from exc


def deployment_inventory(
    *,
    settings: Settings | None = None,
) -> tuple[dict[str, object], ...]:
    return tuple(
        spec.public_descriptor(settings=settings)
        for spec in LONG_RUNNING_WORKERS
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print the value-free long-running worker deployment inventory."
    )
    parser.add_argument(
        "--format",
        choices=("json", "lines"),
        default="json",
    )
    parser.add_argument(
        "--with-enabled-state",
        action="store_true",
        help="Resolve boolean worker switches from the current environment.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env() if args.with_enabled_state else None
    inventory = deployment_inventory(settings=settings)
    if args.format == "lines":
        for item in inventory:
            fields = [
                str(item["worker"]),
                str(item["settingsFlag"]),
                str(item["composeService"]),
            ]
            if settings is not None:
                fields.append("1" if item["enabled"] else "0")
            print("|".join(fields))
        return 0

    print(
        json.dumps(
            {
                "schemaVersion": "dreamjourney-long-running-worker-registry-v1",
                "workerCount": len(inventory),
                "workers": inventory,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LONG_RUNNING_WORKERS",
    "WorkerDeploymentSpec",
    "deployment_inventory",
    "deployment_spec_for",
    "main",
]
