#!/usr/bin/env python3
"""Verify the deployed value-free production readiness report."""

import json
import os
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).rstrip("/")
API_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()
EXPECTED_STATE = os.environ.get("EXPECTED_PRODUCTION_READINESS_STATE", "").strip()
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()

LANES = {
    "coreService",
    "identity",
    "contentSafety",
    "mediaStorage",
    "mediaProcessing",
    "workers",
    "context",
    "export",
    "deletion",
    "operationTelemetry",
    "providerTelemetry",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")
    request = urllib.request.Request(
        f"{BASE_URL}/ops/release-policy/observations",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {API_TOKEN}",
        },
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read().decode("utf-8")
        require(response.status == 200, f"observations returned {response.status}")
        require(response.headers.get("Cache-Control") == "no-store", "observations must be no-store")
    payload = json.loads(body)
    report = dict(payload.get("productionReadiness") or {})
    require(report.get("schemaVersion") == 1, "readiness schema version")
    require(report.get("status") in {"ready", "degraded", "blocked"}, "readiness state")
    require(report.get("releaseDecision") in {"go", "noGo"}, "release decision")
    require(set(dict(report.get("lanes") or {})) == LANES, "readiness lane inventory")
    require(int(dict(report.get("summary") or {}).get("laneCount") or 0) == len(LANES), "lane count")
    if EXPECTED_STATE:
        require(report.get("status") == EXPECTED_STATE, "unexpected production readiness state")

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True).lower()
    for forbidden in (
        "secret",
        "token",
        "password",
        "postgresql://",
        "database_url",
        "bucket",
        "endpoint",
        "accesskey",
        "providerlogid",
        "userid",
    ):
        require(forbidden not in serialized, f"readiness report leaks {forbidden}")

    result = {
        "status": "passed",
        "schemaVersion": 1,
        "productionReadinessState": report["status"],
        "releaseDecision": report["releaseDecision"],
        "laneCount": len(LANES),
        "valueFree": True,
    }
    if OUTPUT_PATH:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
