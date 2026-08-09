#!/usr/bin/env python3
"""Validate a synthetic closed-pilot plan from stdin and print safe JSON."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.closed_pilot_admission import (
    ClosedPilotAdmissionError,
    ClosedPilotReadiness,
    build_closed_pilot_admission_plan,
)


def main() -> int:
    payload = json.load(sys.stdin)
    readiness = {
        str(key): ClosedPilotReadiness(
            ready=bool(value.get("ready")),
            reason=str(value.get("reason") or "unspecified"),
        )
        for key, value in dict(payload.get("readiness") or {}).items()
    }
    try:
        plan = build_closed_pilot_admission_plan(
            owner_ids=payload.get("ownerIds") or [],
            requested_features=payload.get("requestedFeatures") or [],
            current_features=payload.get("currentFeatures") or [],
            kill_switch_features=payload.get("killSwitchFeatures") or [],
            readiness=readiness,
            synthetic_only=bool(payload.get("syntheticOnly", True)),
        )
    except ClosedPilotAdmissionError as exc:
        print(json.dumps({"status": "invalid", "code": exc.code}, sort_keys=True))
        return 2
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if plan["status"] == "ready" else 3


if __name__ == "__main__":
    raise SystemExit(main())
