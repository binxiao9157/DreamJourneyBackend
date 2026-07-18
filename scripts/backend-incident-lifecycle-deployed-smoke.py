#!/usr/bin/env python3
"""Exercise the deployed append-only incident lifecycle without leaving it open.

The smoke creates a unique warning incident, validates machine-only access and
idempotency, then acknowledges, fences, and resolves it. Warning severity is
intentional: this validates the production route and evidence contract without
turning the public readiness endpoint into a stop-the-line state.
"""

import json
import os
import secrets
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).strip().rstrip("/")
API_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(method, path, *, token="", payload=None, expected_status=200):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            response_body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {key.lower(): value for key, value in error.headers.items()}
        response_body = json.loads(error.read().decode("utf-8"))
    require(status == expected_status, f"{method} {path} expected {expected_status}, got {status}")
    return response_body, response_headers


def machine_request(method, path, *, payload=None, expected_status=200):
    return request_json(
        method,
        path,
        token=API_TOKEN,
        payload=payload,
        expected_status=expected_status,
    )


def incident_state(incident_id):
    body, _ = machine_request("GET", f"/ops/incidents/{incident_id}")
    incident = dict(body.get("incident") or {})
    require(incident, "incident detail is required")
    return incident


def best_effort_resolve(incident_id, suffix):
    """Avoid leaving a QA incident behind when a later assertion fails."""
    try:
        incident = incident_state(incident_id)
        state = str(incident.get("state") or "")
        if state == "resolved":
            return
        if state == "open":
            machine_request(
                "POST",
                f"/ops/incidents/{incident_id}/ack",
                payload={
                    "reason": "qaSmokeCleanup",
                    "commandId": f"cmd-incident-cleanup-ack-{suffix}",
                },
            )
            incident = incident_state(incident_id)
        if str(incident.get("state") or "") in {"acknowledged", "fenced"}:
            remaining = sorted(
                set(incident.get("requiredFenceActions") or ())
                - set(incident.get("fenceActions") or ())
            )
            if remaining:
                machine_request(
                    "POST",
                    f"/ops/incidents/{incident_id}/fence",
                    payload={
                        "reason": "qaSmokeCleanup",
                        "fenceActions": remaining,
                        "commandId": f"cmd-incident-cleanup-fence-{suffix}",
                    },
                )
            machine_request(
                "POST",
                f"/ops/incidents/{incident_id}/resolve",
                payload={
                    "reason": "qaSmokeCleanup",
                    "evidenceIds": [f"qa-cleanup-evidence-{suffix}"],
                    "commandId": f"cmd-incident-cleanup-resolve-{suffix}",
                },
            )
    except Exception:
        # The original smoke failure remains the useful signal. A failed cleanup
        # is visible through the still-open, uniquely named QA incident.
        return


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")

    runtime, _ = request_json("GET", "/config/runtime")
    route_auth = dict((runtime.get("auth") or {}).get("routeAuthentication") or {})
    require(route_auth.get("mode") == "enforce", "deployed route authentication must enforce")
    require(int(route_auth.get("routeCount") or 0) >= 76, "incident routes are not deployed")

    _, anonymous_headers = request_json(
        "GET",
        "/ops/incidents/readiness",
        expected_status=401,
    )
    require(
        anonymous_headers.get("x-dreamjourney-route-auth-reason") == "machinePrincipalRequired",
        "incident readiness must remain machine-only",
    )

    readiness, _ = request_json("GET", "/ready")
    require(readiness.get("status") == "ready", "deployed service must be ready before smoke")
    components = {
        str(item.get("component") or ""): str(item.get("status") or "")
        for item in readiness.get("components") or []
        if isinstance(item, dict)
    }
    require(components.get("incident") == "ready", "incident readiness component must be ready")

    baseline, _ = machine_request("GET", "/ops/incidents/readiness")
    require(baseline.get("integrityState") == "valid", "incident evidence must be valid")
    require(baseline.get("stopTheLine") is False, "smoke must not run during an active stop-the-line")

    suffix = secrets.token_hex(8)
    incident_id = f"qa-incident-{suffix}"
    evidence_id = f"qa-evidence-{suffix}"
    open_payload = {
        "incidentId": incident_id,
        "category": "qaSmoke",
        "severity": "warning",
        "owner": "qaOperations",
        "runbookId": "runbook.qaIncidentLifecycle",
        "reason": "qaSmokeOpened",
        "requiredFenceActions": ["releasePolicy.echoTextInput"],
        "commandId": f"cmd-incident-open-{suffix}",
        "surface": "operations",
    }

    opened = False
    try:
        created, created_headers = machine_request("POST", "/ops/incidents", payload=open_payload)
        opened = True
        require(created_headers.get("cache-control") == "no-store", "incident response must not cache")
        require(created.get("eventOutcome") == "appended", "incident open must append evidence")
        require((created.get("incident") or {}).get("state") == "open", "incident must open")

        replayed, _ = machine_request("POST", "/ops/incidents", payload=open_payload)
        require(replayed.get("eventOutcome") == "deduplicated", "same command must deduplicate")

        acknowledged, _ = machine_request(
            "POST",
            f"/ops/incidents/{incident_id}/ack",
            payload={
                "reason": "qaSmokeAcknowledged",
                "commandId": f"cmd-incident-ack-{suffix}",
            },
        )
        require((acknowledged.get("incident") or {}).get("state") == "acknowledged", "incident must acknowledge")

        fenced, _ = machine_request(
            "POST",
            f"/ops/incidents/{incident_id}/fence",
            payload={
                "reason": "qaSmokeFenced",
                "fenceActions": ["releasePolicy.echoTextInput"],
                "commandId": f"cmd-incident-fence-{suffix}",
            },
        )
        require((fenced.get("incident") or {}).get("fenceStatus") == "complete", "incident fence must complete")

        resolved, _ = machine_request(
            "POST",
            f"/ops/incidents/{incident_id}/resolve",
            payload={
                "reason": "qaSmokeResolved",
                "evidenceIds": [evidence_id],
                "commandId": f"cmd-incident-resolve-{suffix}",
            },
        )
        require((resolved.get("incident") or {}).get("state") == "resolved", "incident must resolve")
        serialized = json.dumps(resolved, ensure_ascii=True, sort_keys=True)
        require(evidence_id not in serialized, "raw resolution evidence leaked")

        final_incident = incident_state(incident_id)
        require(final_incident.get("state") == "resolved", "resolved incident must replay from evidence")
        final_summary, _ = machine_request("GET", "/ops/incidents/readiness")
        require(final_summary.get("integrityState") == "valid", "incident evidence must remain valid")
        require(final_summary.get("stopTheLine") is False, "warning smoke must not leave a stop-the-line")
        final_ready, _ = request_json("GET", "/ready")
        require(final_ready.get("status") == "ready", "service must remain ready after smoke")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "anonymousAccessDenied": True,
                    "evidenceLifecycle": "appendOnly",
                    "incidentResolved": True,
                    "machineOnly": True,
                    "rawResolutionEvidenceLeaked": False,
                    "routeCount": route_auth["routeCount"],
                    "stopTheLine": False,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    finally:
        if opened:
            best_effort_resolve(incident_id, suffix)


if __name__ == "__main__":
    main()
