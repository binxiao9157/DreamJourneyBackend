#!/usr/bin/env python3
import json
import os
import secrets
import urllib.error
import urllib.request

from app.core.config import settings
from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
MACHINE_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()
# Keep this explicit so a deployed smoke also proves the expected release
# inventory, not merely that the server and its local registry agree.
EXPECTED_ROUTE_COUNT = 109


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(method, path, *, token=None, payload=None, expected_status=200):
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


def cleanup(store, user_id):
    with store.request_unit_of_work(
        correlation_id="route-auth-smoke-cleanup",
        command_id="cleanupRouteAuthenticationSmoke",
    ) as unit_of_work:
        with unit_of_work.connection.cursor() as cursor:
            cursor.execute("DELETE FROM session_events WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM token_families WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(MACHINE_TOKEN, "BACKEND_API_TOKEN is required")
    dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(dsn, "DATABASE_URL is required")

    suffix = secrets.token_hex(8)
    phone = f"197{suffix[:8]}"
    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    user_id = ""
    try:
        user = store.upsert_user(phone=phone, nickname="route auth smoke")
        user_id = str(user["id"])
        auth = AuthSessionService(
            store,
            access_ttl_seconds=300,
            refresh_ttl_seconds=900,
        ).issue(user_id)

        runtime, public_headers = request_json("GET", "/config/runtime")
        route_contract = runtime["auth"]["routeAuthentication"]
        require(route_contract["mode"] == "enforce", "deployed route auth must enforce")
        require(
            route_contract["routeCount"] == EXPECTED_ROUTE_COUNT,
            "deployed route registry does not match the current contract",
        )
        require(route_contract["unclassifiedCount"] == 0, "deployed route registry incomplete")
        require(
            public_headers.get("x-dreamjourney-route-auth-reason") == "publicRoute",
            "runtime config must be explicitly public",
        )

        _, anonymous_headers = request_json(
            "GET",
            f"/kb/snapshot/{user_id}",
            expected_status=401,
        )
        require(
            anonymous_headers.get("x-dreamjourney-route-auth-reason") == "userPrincipalRequired",
            "anonymous user route must fail closed",
        )

        _, machine_business_headers = request_json(
            "GET",
            f"/kb/snapshot/{user_id}",
            token=MACHINE_TOKEN,
            expected_status=403,
        )
        require(
            machine_business_headers.get("x-dreamjourney-route-auth-reason") == "userPrincipalRequired",
            "machine principal must not access user business routes",
        )

        _, user_headers = request_json(
            "GET",
            f"/kb/snapshot/{user_id}",
            token=auth["accessToken"],
            expected_status=404,
        )
        require(
            user_headers.get("x-dreamjourney-route-auth-reason") == "userPrincipalAuthorized",
            "user access token must satisfy the user route contract",
        )

        _, user_system_headers = request_json(
            "GET",
            "/ops/release-policy/observations",
            token=auth["accessToken"],
            expected_status=403,
        )
        require(
            user_system_headers.get("x-dreamjourney-route-auth-reason") == "machinePrincipalRequired",
            "user principal must not access machine routes",
        )

        observations, machine_headers = request_json(
            "GET",
            "/ops/release-policy/observations",
            token=MACHINE_TOKEN,
        )
        require(
            machine_headers.get("x-dreamjourney-auth-principal") == "machine",
            "backend service token must resolve to a typed machine principal",
        )
        route_observations = observations.get("routeAuthentication") or {}
        require(route_observations.get("eventCount", 0) > 0, "route decision denominator missing")
        require(route_observations.get("valueFree") is True, "route observations must be value-free")

        print(
            json.dumps(
                {
                    "anonymousUserRouteDenied": True,
                    "machineBusinessRouteDenied": True,
                    "machineSystemRouteAllowed": True,
                    "publicRuntimeAllowed": True,
                    "routeCount": route_contract["routeCount"],
                    "routeDecisionEvidence": True,
                    "status": "passed",
                    "userRouteAllowed": True,
                    "userSystemRouteDenied": True,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    finally:
        if user_id:
            cleanup(store, user_id)
        store.close_pool()


if __name__ == "__main__":
    main()
