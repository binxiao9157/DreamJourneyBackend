#!/usr/bin/env python3
import json
import os
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).rstrip("/")
API_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()
BASELINE_PATH = os.environ.get("BASELINE_PATH", "").strip()
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, headers=None):
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
    with urllib.request.urlopen(request, timeout=20) as response:
        require(response.status == 200, f"GET {path} returned {response.status}")
        return json.loads(response.read().decode("utf-8"))


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")

    request_json(
        "/config/runtime",
        headers={
            "X-DreamJourney-Client-Build": "9002",
            "X-DreamJourney-Runtime-Contract-Version": "2",
        },
    )
    observations = request_json("/ops/release-policy/observations")
    operation_events = observations.get("operationEvents") or []
    event_ids = [str(event.get("eventId") or "") for event in operation_events]

    require(observations.get("evidenceStoreContractVersion") == 1, "store contract")
    require(observations.get("evidenceSource") == "persistent", "persistent source")
    require(observations.get("sinkFailureCount") == 0, "sink failure count")
    require(observations.get("sourceFailureCount") == 0, "source failure count")
    require(observations.get("eventCount", 0) >= 1, "persistent event count")
    require(event_ids and all(event_ids), "bounded persistent event sample")

    baseline = None
    if BASELINE_PATH:
        with open(BASELINE_PATH, "r", encoding="utf-8") as handle:
            baseline = json.load(handle)
        require(
            baseline.get("anchorEventId") in event_ids,
            "pre-restart anchor event must remain queryable",
        )
        require(
            observations.get("eventCount", 0) >= baseline.get("eventCount", 0),
            "persistent event denominator must not reset",
        )
        require(
            observations.get("windowStartedAt") == baseline.get("windowStartedAt"),
            "persistent observation window start must survive restart",
        )

    result = {
        "status": "passed",
        "phase": "afterRestart" if baseline is not None else "beforeRestart",
        "eventCount": observations.get("eventCount"),
        "windowStartedAt": observations.get("windowStartedAt"),
        "windowEndedAt": observations.get("windowEndedAt"),
        "anchorEventId": event_ids[-1],
        "evidenceSource": observations.get("evidenceSource"),
        "sinkFailureCount": observations.get("sinkFailureCount"),
        "sourceFailureCount": observations.get("sourceFailureCount"),
    }
    if OUTPUT_PATH:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(
        "Backend evidence persistence deployed smoke passed: "
        f"phase={result['phase']} count={result['eventCount']}"
    )


if __name__ == "__main__":
    main()
