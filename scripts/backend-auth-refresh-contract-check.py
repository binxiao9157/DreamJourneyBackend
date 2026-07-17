#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSTGRES_STORE = ROOT / "app/services/postgres_store.py"
MEMORY_STORE = ROOT / "app/services/in_memory_store.py"
AUTH_SERVICE = ROOT / "app/services/auth_sessions.py"
DEPLOYED_SMOKE = ROOT / "scripts/backend-auth-refresh-deployed-smoke.py"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    postgres = POSTGRES_STORE.read_text()
    memory = MEMORY_STORE.read_text()
    auth = AUTH_SERVICE.read_text()
    smoke = DEPLOYED_SMOKE.read_text()

    for snippet in (
        'str(family.get("user_id") or "") != str(row.get("user_id") or "")',
        'family.get("current_session_version")',
        "AND user_id = %s AND family_id = %s AND session_version = %s",
        "AND user_id = %s AND current_session_version = %s",
    ):
        require(snippet in postgres, f"Postgres refresh CAS invariant missing: {snippet}")

    for snippet in (
        'family.get("userId")',
        'family.get("currentSessionVersion")',
        'session.get("sessionVersion")',
    ):
        require(snippet in memory, f"in-memory refresh invariant missing: {snippet}")

    for snippet in (
        '"subjectId": str(record["userId"])',
        'public["parentSessionId"] = parent_session_id',
    ):
        require(snippet in auth, f"public refresh lineage field missing: {snippet}")

    for assertion in (
        'second["subjectId"] == first["userId"]',
        'second["parentSessionId"] == first["sessionId"]',
        'second["sessionVersion"] == first["sessionVersion"] + 1',
        'detail.get("code") == "refresh_token_reuse_detected"',
    ):
        require(assertion in smoke, f"deployed refresh smoke assertion missing: {assertion}")

    print("Backend auth refresh contract check passed")


if __name__ == "__main__":
    main()
