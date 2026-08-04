#!/usr/bin/env python3
"""Non-network OTP provider smoke for the C1 challenge contract.

This uses an in-process accepted-only gateway. It proves that a production
adapter can issue a server-owned code, persist only hashes, verify once, and
fail neutrally before persistence when delivery is rejected.
"""

import json
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.identity_bindings import (
    HttpJsonIdentityChallengeAdapter,
    IdentityBindingService,
    IdentityChallengeDeliveryError,
    UnavailableIdentityChallengeAdapter,
    make_identity_challenge_adapter,
)
from app.services.in_memory_store import InMemoryStore
from app.services.runtime_config import RuntimeConfigService


HMAC_KEY = "identity-challenge-provider-smoke-key-" + ("x" * 40)
TARGET = "+86 138-0013-8000"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class AcceptedGateway:
    def __init__(self, *, accepted=True):
        self.accepted = accepted
        self.requests = []

    def post_json(self, *, url, headers, payload, timeout_seconds):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": deepcopy(payload),
                "timeoutSeconds": timeout_seconds,
            }
        )
        if not self.accepted:
            raise IdentityChallengeDeliveryError()
        return {"accepted": True}


def service_for(store, gateway):
    return IdentityBindingService(
        store,
        hmac_key=HMAC_KEY,
        hmac_key_version="v1",
        adapter=HttpJsonIdentityChallengeAdapter(
            endpoint="https://sms.example.test/v1/challenges",
            api_key="test-server-only-api-key",
            timeout_seconds=5,
            transport=gateway,
        ),
        challenge_ttl_seconds=60,
        max_attempts=3,
    )


def main():
    production = Settings(
        environment="production",
        identity_binding_hmac_key=HMAC_KEY,
        identity_challenge_adapter="httpJson",
        identity_challenge_http_json_url="https://sms.example.test/v1/challenges",
        identity_challenge_http_json_api_key="test-server-only-api-key",
    )
    runtime = RuntimeConfigService(production).public_config()["auth"]["identityChallenge"]
    require(runtime["providerMode"] == "httpJson", "provider mode drift")
    require(runtime["productionReady"] is True, "configured provider must be ready")
    require(runtime["internalVerificationEnabled"] is False, "synthetic flag leaked")

    fail_closed = Settings(
        environment="production",
        identity_binding_hmac_key=HMAC_KEY,
        identity_challenge_adapter="httpJson",
        identity_challenge_http_json_url="http://insecure.example.test/challenges",
        identity_challenge_http_json_api_key="test-server-only-api-key",
    )
    require(
        isinstance(make_identity_challenge_adapter(fail_closed), UnavailableIdentityChallengeAdapter),
        "invalid production provider must fail closed",
    )

    gateway = AcceptedGateway()
    store = InMemoryStore()
    with (
        patch.object(main_module, "store", store),
        patch.object(main_module, "_identity_binding_service", return_value=service_for(store, gateway)),
    ):
        client = TestClient(app)
        challenge_response = client.post(
            "/v2/auth/challenges",
            json={"identityType": "phone", "target": TARGET, "purpose": "login"},
        )
        require(challenge_response.status_code == 202, "challenge must be accepted")
        challenge = challenge_response.json()["challenge"]
        require(len(gateway.requests) == 1, "gateway must receive one challenge")
        delivered = gateway.requests[0]["payload"]
        require(delivered["target"] == "8613800138000", "target normalization drift")
        require(delivered["challengeId"] == challenge["challengeId"], "challenge ID drift")
        delivered_code = delivered["code"]
        require(len(delivered_code) == 6 and delivered_code.isdigit(), "OTP shape drift")
        require(TARGET not in challenge_response.text, "response exposed raw target")
        require(delivered_code not in challenge_response.text, "response exposed OTP")

        wrong = client.post(
            f"/v2/auth/challenges/{challenge['challengeId']}/verify",
            json={"code": "000000"},
        )
        require(wrong.status_code == 401, "wrong OTP must fail neutrally")
        verified = client.post(
            f"/v2/auth/challenges/{challenge['challengeId']}/verify",
            json={"code": delivered_code},
        )
        require(verified.status_code == 200, "delivered OTP must verify")
        replay = client.post(
            f"/v2/auth/challenges/{challenge['challengeId']}/verify",
            json={"code": delivered_code},
        )
        require(replay.status_code == 401, "OTP replay must fail")

    persisted = json.dumps(store._auth_challenges, sort_keys=True)
    require(TARGET not in persisted, "persistence exposed raw target")
    require("8613800138000" not in persisted, "persistence exposed normalized target")
    require(delivered_code not in persisted, "persistence exposed OTP")

    failed_gateway = AcceptedGateway(accepted=False)
    failed_store = InMemoryStore()
    with (
        patch.object(main_module, "store", failed_store),
        patch.object(
            main_module,
            "_identity_binding_service",
            return_value=service_for(failed_store, failed_gateway),
        ),
    ):
        client = TestClient(app)
        failure = client.post(
            "/v2/auth/challenges",
            json={"identityType": "phone", "target": TARGET, "purpose": "login"},
        )
    require(failure.status_code == 503, "delivery failure must be unavailable")
    require(
        (failure.json().get("detail") or {}).get("code")
        == "identity_challenge_delivery_failed",
        "delivery failure contract drift",
    )
    require(TARGET not in failure.text, "failure exposed target")
    require(not failed_store._auth_challenges, "failed delivery persisted a challenge")

    print("Identity challenge provider smoke passed")


if __name__ == "__main__":
    main()
