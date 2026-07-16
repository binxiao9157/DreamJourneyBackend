#!/usr/bin/env python3
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
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, expected_status=200, headers=None):
    request_headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {API_TOKEN}",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers=request_headers,
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = json.loads(response.read().decode("utf-8"))
        require(response.status == expected_status, f"GET {path} returned {response.status}")
        correlation_id = str(response.headers.get("X-DreamJourney-Correlation-Id") or "")
        require(len(correlation_id) == 32, f"GET {path} correlation id")
        return body


def uow_metrics():
    observations = request_json("/ops/release-policy/observations")
    metrics = observations.get("databaseUnitOfWork") or {}
    require(metrics.get("schemaVersion") == 1, "database UoW schema version")
    require(isinstance(metrics.get("pool"), dict), "database pool metrics")
    return metrics


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")

    before = uow_metrics()
    request_json(
        "/config/runtime",
        headers={
            "X-DreamJourney-Client-Build": "9004",
            "X-DreamJourney-Runtime-Contract-Version": "2",
        },
    )
    request_json("/v2/release-policy?audience=invalid", expected_status=422)
    after = uow_metrics()

    require(after.get("checkouts", 0) >= before.get("checkouts", 0) + 3, "request checkouts")
    require(after.get("committed", 0) >= before.get("committed", 0) + 2, "request commits")
    require(after.get("rolledBack", 0) >= before.get("rolledBack", 0) + 1, "error rollback")
    require(after.get("poolExhausted", 0) == before.get("poolExhausted", 0), "pool exhaustion")
    require(
        after.get("connectionReturnFailures", 0)
        == before.get("connectionReturnFailures", 0),
        "connection return failures",
    )

    result = {
        "status": "passed",
        "schemaVersion": 1,
        "checkoutDelta": after.get("checkouts", 0) - before.get("checkouts", 0),
        "commitDelta": after.get("committed", 0) - before.get("committed", 0),
        "rollbackDelta": after.get("rolledBack", 0) - before.get("rolledBack", 0),
        "poolExhaustedDelta": after.get("poolExhausted", 0) - before.get("poolExhausted", 0),
        "connectionReturnFailureDelta": (
            after.get("connectionReturnFailures", 0)
            - before.get("connectionReturnFailures", 0)
        ),
    }
    if OUTPUT_PATH:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(
        "Backend deployed database UoW smoke passed: "
        f"commit={result['commitDelta']} rollback={result['rollbackDelta']}"
    )


if __name__ == "__main__":
    main()
