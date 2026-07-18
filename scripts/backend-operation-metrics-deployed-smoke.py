#!/usr/bin/env python3
"""Verify deployed shadow operation metrics without exposing request identifiers."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).rstrip("/")
API_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, headers, expected_status=200):
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read().decode("utf-8", errors="replace")
    require(status == expected_status, f"GET {path} returned {status}")
    return json.loads(body) if body else {}


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")

    request_id = str(uuid.uuid4())
    query = urllib.parse.urlencode(
        {
            "audience": "owner",
            "cohort": "closedPilotAdultSelf",
            "clientBuild": 1,
        }
    )
    request_json(
        f"/v2/release-policy?{query}",
        headers={
            "Accept": "application/json",
            "X-DreamJourney-Request-Id": request_id,
            "X-DreamJourney-Operation-Id": request_id,
            "X-DreamJourney-Operation-Attempt": "1",
            "X-DreamJourney-Feedback-State": "missing",
        },
    )
    observations = request_json(
        "/ops/release-policy/observations",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {API_TOKEN}",
        },
    )
    metrics = dict(observations.get("operationMetrics") or {})
    require(metrics.get("evidenceSource") == "persistent", "metrics must persist")
    require(
        metrics.get("identifierProtection") == "configuredHmac",
        "metrics identifiers must use the configured HMAC key",
    )
    require(int(metrics.get("eventCount") or 0) >= 1, "metrics event was not recorded")
    require(
        int(metrics.get("missingFeedbackOperationCount") or 0) >= 1,
        "missing feedback must be visible as a shadow outcome",
    )
    readiness = dict(metrics.get("readiness") or {})
    require(readiness.get("sloClaimAllowed") is False, "shadow metrics must not claim an SLO")

    serialized = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
    require(request_id not in serialized, "raw request identifier leaked")
    require("events" not in metrics, "observations must not expose raw events")
    require("routeCounts" not in metrics, "observations must not expose route names")

    print(
        json.dumps(
            {
                "status": "passed",
                "schemaVersion": 1,
                "evidenceSource": metrics["evidenceSource"],
                "identifierProtection": metrics["identifierProtection"],
                "missingFeedbackObserved": True,
                "sloClaimAllowed": False,
                "rawClientIdentifierLeaked": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
