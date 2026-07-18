import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "backend-resource-authorization-postgres-smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "backend_resource_authorization_postgres_smoke",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def runtime_contract(
    *,
    environment="production",
    route_mode="enforce",
    ownership_mode="enforce",
    cross_account_mode="enforce",
):
    return {
        "environment": environment,
        "auth": {
            "routeAuthentication": {"mode": route_mode},
            "ownershipMode": ownership_mode,
            "crossAccountPolicy": {"mode": cross_account_mode},
        },
    }


class ResourceAuthorizationSmokeTests(unittest.TestCase):
    def test_accepts_production_enforcement_contract(self):
        SMOKE.require_production_enforcement(runtime_contract())

    def test_rejects_non_production_or_non_enforcing_contracts(self):
        cases = (
            (
                runtime_contract(environment="development"),
                "requires a production runtime",
            ),
            (
                runtime_contract(route_mode="shadow"),
                "route authentication must enforce",
            ),
            (
                runtime_contract(ownership_mode="shadow"),
                "ownership authorization must enforce",
            ),
            (
                runtime_contract(cross_account_mode="shadow"),
                "cross-account authorization must enforce",
            ),
        )

        for runtime, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(AssertionError, message):
                    SMOKE.require_production_enforcement(runtime)

    def test_main_requires_explicit_database_url_before_http(self):
        with patch.object(SMOKE, "BASE_URL", "https://backend.invalid"):
            with patch.object(SMOKE, "MACHINE_TOKEN", "configured-token"):
                with patch.dict(os.environ, {}, clear=True):
                    with patch.object(SMOKE, "request_json") as request_json:
                        with self.assertRaisesRegex(
                            AssertionError,
                            "DATABASE_URL is required",
                        ):
                            SMOKE.main()

        request_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
