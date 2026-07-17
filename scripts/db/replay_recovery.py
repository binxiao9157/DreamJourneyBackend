#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.recovery import (
    RecoveryContractError,
    build_replay_plan,
    finalize_replay_plan,
    write_recovery_record_atomic,
)


def load_optional(path):
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RecoveryContractError("replayEvidenceInvalid")
    return payload


def main():
    parser = argparse.ArgumentParser(
        description="Validate and bind trusted recovery replay evidence. This tool does not synthesize receipts."
    )
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--cutoff-lsn", required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--application-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        plan = build_replay_plan(
            load_optional(args.bundle),
            backup_id=args.backup_id,
            cutoff_lsn=args.cutoff_lsn,
        )
        application = load_optional(args.application_evidence)
        if application is not None:
            plan = finalize_replay_plan(plan, application_evidence=application)
        write_recovery_record_atomic(args.output, plan)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecoveryContractError) as exc:
        code = exc.code if isinstance(exc, RecoveryContractError) else type(exc).__name__
        print(json.dumps({"schemaVersion": 1, "status": "failed", "errorCode": code}), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
