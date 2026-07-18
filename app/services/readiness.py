from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.db.pool import ConnectionPoolExhausted
from app.db.readiness import DatabaseReadinessError, SchemaReadinessError
from app.services.route_authentication import resolve_route_authentication_mode


PRODUCTION_ENVIRONMENTS = {"prod", "production"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReadinessService:
    def __init__(
        self,
        *,
        settings: Any,
        store: Any,
        clock: Callable[[], str] = _now_iso,
        incident_component_source: Optional[Callable[[], Dict[str, str]]] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.clock = clock
        self.incident_component_source = incident_component_source

    def evaluate(self) -> Dict[str, Any]:
        evidence_timestamp = self.clock()
        components = self._store_components(evidence_timestamp)
        components.append(self._auth_component(evidence_timestamp))
        if self.incident_component_source is not None:
            components.append(self._incident_component(evidence_timestamp))
        status = (
            "ready"
            if all(component["status"] == "ready" for component in components)
            else "notReady"
        )
        return {
            "schemaVersion": 1,
            "status": status,
            "evidenceTimestamp": evidence_timestamp,
            "components": components,
        }

    def _store_components(self, timestamp: str) -> List[Dict[str, str]]:
        store_backend = str(getattr(self.settings, "store_backend", "") or "").lower()
        environment = str(getattr(self.settings, "environment", "") or "").lower()
        if store_backend == "memory":
            if environment in PRODUCTION_ENVIRONMENTS:
                return [
                    self._component("database", "notReady", "persistentStoreRequired", timestamp),
                    self._component("schema", "unknown", "databaseUnavailable", timestamp),
                ]
            return [
                self._component("database", "ready", "inMemoryStoreReady", timestamp),
                self._component("schema", "ready", "notApplicableInMemory", timestamp),
            ]
        if store_backend != "postgres":
            return [
                self._component("database", "notReady", "unsupportedStoreBackend", timestamp),
                self._component("schema", "unknown", "databaseUnavailable", timestamp),
            ]

        probe = getattr(self.store, "readiness_probe", None)
        if not callable(probe):
            return [
                self._component("database", "notReady", "databaseProbeUnavailable", timestamp),
                self._component("schema", "unknown", "databaseUnavailable", timestamp),
            ]
        try:
            result = probe()
            return [
                self._component(
                    "database",
                    "ready",
                    str(result.get("databaseReason") or "readWriteProbeSucceeded"),
                    timestamp,
                ),
                self._component(
                    "schema",
                    "ready",
                    str(result.get("schemaReason") or "migrationHeadVerified"),
                    timestamp,
                ),
            ]
        except ConnectionPoolExhausted:
            return [
                self._component("database", "notReady", "databasePoolExhausted", timestamp),
                self._component("schema", "unknown", "databaseUnavailable", timestamp),
            ]
        except DatabaseReadinessError as exc:
            return [
                self._component("database", "notReady", exc.code, timestamp),
                self._component("schema", "unknown", "databaseUnavailable", timestamp),
            ]
        except SchemaReadinessError as exc:
            return [
                self._component("database", "ready", "readWriteProbeSucceeded", timestamp),
                self._component("schema", "notReady", exc.code, timestamp),
            ]
        except Exception:
            return [
                self._component("database", "notReady", "databaseProbeFailed", timestamp),
                self._component("schema", "unknown", "databaseUnavailable", timestamp),
            ]

    def _auth_component(self, timestamp: str) -> Dict[str, str]:
        environment = str(getattr(self.settings, "environment", "") or "").lower()
        ownership_mode = str(
            getattr(self.settings, "auth_ownership_mode", "") or ""
        ).lower()
        route_mode = resolve_route_authentication_mode(
            environment,
            str(getattr(self.settings, "auth_route_mode", "") or ""),
        )
        access_ttl = int(getattr(self.settings, "auth_access_ttl_seconds", 0) or 0)
        refresh_ttl = int(getattr(self.settings, "auth_refresh_ttl_seconds", 0) or 0)
        if ownership_mode not in {"shadow", "enforce"} or access_ttl < 60 or refresh_ttl <= access_ttl:
            return self._component("auth", "notReady", "requiredAuthConfigInvalid", timestamp)
        if environment in PRODUCTION_ENVIRONMENTS and not str(
            getattr(self.settings, "backend_api_token", "") or ""
        ).strip():
            return self._component("auth", "notReady", "requiredAuthConfigMissing", timestamp)
        if environment in PRODUCTION_ENVIRONMENTS and route_mode != "enforce":
            return self._component("auth", "notReady", "routeAuthenticationNotEnforced", timestamp)
        return self._component("auth", "ready", "requiredAuthConfigPresent", timestamp)

    def _incident_component(self, timestamp: str) -> Dict[str, str]:
        try:
            component = dict(self.incident_component_source() or {})
        except Exception:
            return self._component("incident", "notReady", "incidentEvidenceUnavailable", timestamp)
        status = str(component.get("status") or "unknown")
        reason = str(component.get("reason") or "incidentEvidenceUnavailable")
        if status not in {"ready", "notReady", "unknown"}:
            return self._component("incident", "notReady", "incidentEvidenceInvalid", timestamp)
        return self._component("incident", status, reason, timestamp)

    @staticmethod
    def _component(
        component: str,
        status: str,
        reason: str,
        timestamp: str,
    ) -> Dict[str, str]:
        return {
            "component": component,
            "status": status,
            "reason": reason,
            "evidenceTimestamp": timestamp,
        }


def liveness_payload(*, clock: Callable[[], str] = _now_iso) -> Dict[str, str]:
    return {
        "component": "process",
        "status": "alive",
        "reason": "processRunning",
        "evidenceTimestamp": clock(),
    }
