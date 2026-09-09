#!/usr/bin/env python3
"""Execute one approved legacy replay batch through Candidate review only.

This command is intentionally operations-only. It requires a previously
persisted immutable dry-run report, two independent enablement switches, and a
PostgreSQL store. Output contains only hashes, counts, states, and opaque IDs;
legacy text and credentials are never printed.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_b_migration_execution import (
    OwnerTruthBMigrationExecutionService,
    owner_truth_b_migration_execution_summary,
)
from app.services.store_factory import close_store, make_store, open_store


def _required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _batch_size() -> int:
    raw = str(os.environ.get("OWNER_TRUTH_B_MIGRATION_BATCH_SIZE", "10")).strip()
    try:
        return int(raw)
    except ValueError as error:
        raise RuntimeError(
            "OWNER_TRUTH_B_MIGRATION_BATCH_SIZE must be an integer"
        ) from error


def _opaque_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    settings = Settings.from_env()
    if settings.store_backend != "postgres":
        raise RuntimeError("B migration execution requires STORE_BACKEND=postgres")
    if not settings.owner_truth_b_migration_execution_enabled:
        raise RuntimeError("B migration execution is disabled by server configuration")
    if str(os.environ.get("OWNER_TRUTH_B_MIGRATION_EXECUTION_ACK") or "").strip() != "YES":
        raise RuntimeError("OWNER_TRUTH_B_MIGRATION_EXECUTION_ACK=YES is required")

    owner_subject_id = _required_env("OWNER_TRUTH_B_MIGRATION_OWNER_SUBJECT_ID")
    vault_id = _required_env("OWNER_TRUTH_B_MIGRATION_VAULT_ID")
    report_id = _required_env("OWNER_TRUTH_B_MIGRATION_REPORT_ID")
    context = OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )
    store = make_store(settings)
    open_store(store, wait=True)
    try:
        result = OwnerTruthBMigrationExecutionService(
            store,
            enabled=settings.owner_truth_b_migration_execution_enabled,
        ).execute_next_batch(
            context=context,
            batch_size=_batch_size(),
            report_id=report_id,
        )
        summary = owner_truth_b_migration_execution_summary(result)
        summary["ownerSubjectIdHash"] = _opaque_hash(owner_subject_id)
        summary["vaultIdHash"] = _opaque_hash(vault_id)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        close_store(store)


if __name__ == "__main__":
    raise SystemExit(main())
