#!/usr/bin/env python3
"""Rebuild stale private Owner Truth projections after an additive migration.

The command never changes Source, Candidate, Memory, or MemoryVersion rows. It
reconstructs only derived memory/search projections from current authorized
formal memory. Output is value-free: identities are hashed and memory text is
never rendered.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.owner_truth_memory_search_projection import (
    OwnerTruthMemorySearchDocumentProjectionService,
)
from app.services.postgres_store import PostgresStore
from app.services.store_factory import close_store, make_store, open_store


@dataclass(frozen=True)
class ProjectionTarget:
    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    memory_state: str
    search_state: str


TargetSupplier = Callable[[int], tuple[tuple[ProjectionTarget, ...], int]]
TargetRebuilder = Callable[[ProjectionTarget], Mapping[str, object]]


def _opaque_target_hash(target: ProjectionTarget) -> str:
    return sha256(
        f"{target.vault_id}:{target.owner_subject_id}:{target.authority_epoch}".encode(
            "utf-8"
        )
    ).hexdigest()


def run_maintenance(
    *,
    apply: bool,
    limit: int,
    target_supplier: TargetSupplier,
    target_rebuilder: TargetRebuilder,
) -> dict[str, object]:
    bounded_limit = max(1, min(10_000, int(limit)))
    targets, total = target_supplier(bounded_limit)
    target_summaries = [
        {
            "targetHash": _opaque_target_hash(target),
            "authorityEpoch": target.authority_epoch,
            "memoryState": target.memory_state,
            "searchState": target.search_state,
        }
        for target in targets
    ]
    if not apply:
        return {
            "schemaVersion": "dreamjourney-owner-truth-derived-projection-maintenance-v1",
            "mode": "dryRun",
            "status": "pending" if total else "ready",
            "eligibleCount": total,
            "selectedCount": len(targets),
            "targets": target_summaries,
        }

    rebuilt: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for target in targets:
        target_hash = _opaque_target_hash(target)
        try:
            summary = dict(target_rebuilder(target))
            rebuilt.append({"targetHash": target_hash, **summary})
        except Exception as error:
            failures.append(
                {
                    "targetHash": target_hash,
                    "errorType": type(error).__name__,
                }
            )

    _, remaining = target_supplier(1)
    return {
        "schemaVersion": "dreamjourney-owner-truth-derived-projection-maintenance-v1",
        "mode": "apply",
        "status": "ready" if not failures and remaining == 0 else "failed",
        "eligibleCount": total,
        "selectedCount": len(targets),
        "rebuiltCount": len(rebuilt),
        "failureCount": len(failures),
        "remainingEligibleCount": remaining,
        "rebuilt": rebuilt,
        "failures": failures,
    }


def _load_targets(store: PostgresStore, *, limit: int) -> tuple[tuple[ProjectionTarget, ...], int]:
    rows = store._fetchall(  # Operational read; mutation remains in typed services.
        """
        WITH eligible AS (
            SELECT
                vault.vault_id,
                vault.owner_subject_id,
                vault.authority_epoch,
                COALESCE(memory_checkpoint.state, 'missing') AS memory_state,
                COALESCE(search_checkpoint.state, 'missing') AS search_state
            FROM owner_truth.vaults AS vault
            LEFT JOIN owner_truth.memory_revisions AS memory_revision
              ON memory_revision.vault_id = vault.vault_id
            LEFT JOIN owner_truth.memory_projection_checkpoints AS memory_checkpoint
              ON memory_checkpoint.vault_id = vault.vault_id
             AND memory_checkpoint.authority_epoch = vault.authority_epoch
            LEFT JOIN owner_truth.search_document_checkpoints AS search_checkpoint
              ON search_checkpoint.vault_id = vault.vault_id
             AND search_checkpoint.authority_epoch = vault.authority_epoch
            LEFT JOIN LATERAL (
                SELECT rights_state
                FROM owner_truth.projection_rights_events AS rights
                WHERE rights.vault_id = vault.vault_id
                  AND rights.authority_epoch = vault.authority_epoch
                ORDER BY rights.revision DESC
                LIMIT 1
            ) AS current_rights ON TRUE
            WHERE vault.status = 'active'
              AND COALESCE(current_rights.rights_state, 'active') = 'active'
              AND (
                    memory_revision.revision IS NULL
                 OR memory_checkpoint.state IS DISTINCT FROM 'ready'
                 OR memory_checkpoint.memory_revision IS DISTINCT FROM memory_revision.revision
                 OR search_checkpoint.state IS DISTINCT FROM 'ready'
              )
        ), counted AS (
            SELECT eligible.*, COUNT(*) OVER () AS total_count
            FROM eligible
        )
        SELECT *
        FROM counted
        ORDER BY vault_id, authority_epoch
        LIMIT %s
        """,
        (limit,),
    )
    targets = tuple(
        ProjectionTarget(
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            authority_epoch=int(row["authority_epoch"]),
            memory_state=str(row["memory_state"]),
            search_state=str(row["search_state"]),
        )
        for row in rows
    )
    total = int(rows[0]["total_count"]) if rows else 0
    return targets, total


def _rebuild_target(store: PostgresStore, target: ProjectionTarget) -> dict[str, object]:
    store._fetchone(  # Operational state only; no Source or formal fact mutation.
        """
        UPDATE owner_truth.source_projection_rebuild_requests
           SET state = 'processing', updated_at = NOW()
         WHERE vault_id = %s AND state = 'pending'
        RETURNING request_id
        """,
        (target.vault_id,),
    )
    context = OwnerTruthCommandContext(
        vault_id=target.vault_id,
        owner_subject_id=target.owner_subject_id,
        actor_subject_id=target.owner_subject_id,
    )
    try:
        memory_result = OwnerTruthMemoryProjectionService(store).rebuild(context=context)
        memory_snapshot = memory_result.snapshot
        if str(memory_snapshot.get("state") or "") != "ready":
            raise RuntimeError("memory projection did not become ready")
        checkpoint = str(memory_snapshot.get("checkpoint") or "")
        if len(checkpoint) != 64:
            raise RuntimeError("memory projection checkpoint is invalid")

        search_result = OwnerTruthMemorySearchDocumentProjectionService(store).rebuild(
            context=context
        )
        if search_result.projection is None:
            raise RuntimeError("search projection did not become ready")
        search_summary = search_result.projection.value_free_summary()
        if str(search_summary.get("checkpoint") or "") != checkpoint:
            raise RuntimeError("search projection checkpoint is stale")
    except Exception:
        store._fetchone(
            """
            UPDATE owner_truth.source_projection_rebuild_requests
               SET state = 'pending', updated_at = NOW()
             WHERE vault_id = %s AND state = 'processing'
            RETURNING request_id
            """,
            (target.vault_id,),
        )
        raise
    store._fetchone(
        """
        UPDATE owner_truth.source_projection_rebuild_requests
           SET state = 'completed', updated_at = NOW()
         WHERE vault_id = %s
           AND state IN ('pending', 'processing')
           AND authority_epoch = %s
           AND memory_revision <= %s
        RETURNING request_id
        """,
        (
            target.vault_id,
            target.authority_epoch,
            int(memory_snapshot.get("memoryRevision") or 0),
        ),
    )
    return {
        "memoryOutcome": memory_result.outcome,
        "searchOutcome": search_result.outcome,
        "memoryRevision": int(memory_snapshot.get("memoryRevision") or 0),
        "memoryEntryCount": int(memory_snapshot.get("entryCount") or 0),
        "searchDocumentCount": int(search_summary.get("documentCount") or 0),
        "checkpoint": checkpoint,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild stale Owner Truth memory and search projections"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rebuild eligible derived projections. Default is a value-free dry-run.",
    )
    parser.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    settings = Settings.from_env()
    store = make_store(settings)
    if not isinstance(store, PostgresStore):
        raise RuntimeError("projection maintenance requires STORE_BACKEND=postgres")
    open_store(store, wait=True)
    try:
        report = run_maintenance(
            apply=bool(args.apply),
            limit=args.limit,
            target_supplier=lambda limit: _load_targets(store, limit=limit),
            target_rebuilder=lambda target: _rebuild_target(store, target),
        )
    finally:
        close_store(store)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] in {"ready", "pending"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
