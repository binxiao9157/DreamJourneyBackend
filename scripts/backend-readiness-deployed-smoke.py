#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.request
from datetime import datetime


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).rstrip("/")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, expected_status=200):
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = json.loads(response.read().decode("utf-8"))
        require(response.status == expected_status, f"GET {path} returned {response.status}")
        return payload, {
            str(key).lower(): value for key, value in response.headers.items()
        }


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")

    live, live_headers = request_json("/live")
    ready, ready_headers = request_json("/ready")
    health, _ = request_json("/health")

    require(live == {
        "component": "process",
        "status": "alive",
        "reason": "processRunning",
        "evidenceTimestamp": live["evidenceTimestamp"],
    }, "liveness response contract")
    datetime.fromisoformat(live["evidenceTimestamp"])
    require(ready.get("schemaVersion") == 1, "readiness schema version")
    require(ready.get("status") == "ready", "deployed readiness status")
    require(
        [item.get("component") for item in ready.get("components") or []]
        == ["database", "schema", "auth"],
        "required readiness components",
    )
    for component in ready["components"]:
        require(
            set(component) == {"component", "status", "reason", "evidenceTimestamp"},
            "readiness component public fields",
        )
        require(component["status"] == "ready", f"{component['component']} readiness")
        datetime.fromisoformat(component["evidenceTimestamp"])
    serialized = json.dumps(ready, sort_keys=True).lower()
    for forbidden in ("postgresql://", "database_url", "dsn", "secret", "token", "checksum", "select ", "create "):
        require(forbidden not in serialized, f"readiness leaks {forbidden}")
    require(ready_headers.get("cache-control") == "no-store", "readiness no-store")
    require("x-dreamjourney-correlation-id" not in ready_headers, "readiness bypasses business UoW")
    require(live_headers.get("cache-control") == "no-store", "liveness no-store")
    require(health.get("deprecated") is True, "legacy health is deprecated")
    require(health.get("readinessEndpoint") == "/ready", "legacy health points to readiness")

    result = {
        "status": "passed",
        "schemaVersion": 1,
        "anonymousInfrastructureEndpoints": True,
        "databaseReady": True,
        "schemaReady": True,
        "authReady": True,
        "secretRedaction": True,
        "businessUowBypassed": True,
    }
    if OUTPUT_PATH:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print("Backend deployed readiness smoke passed: database/schema/auth ready")


if __name__ == "__main__":
    main()
