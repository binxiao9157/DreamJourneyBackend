from __future__ import annotations

from typing import Any, Callable, Dict

from app.db.migrator import (
    MigrationChecksumMismatch,
    MigrationError,
    MigrationHeadAhead,
    MigrationNotApplied,
)


class DatabaseReadinessError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SchemaReadinessError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PostgresReadinessProbe:
    def __init__(
        self,
        *,
        pool: Any,
        checkout_timeout_seconds: float,
        schema_verifier: Callable[[Any], Any],
        statement_timeout_ms: int = 2000,
    ) -> None:
        self.pool = pool
        self.checkout_timeout_seconds = max(0.1, float(checkout_timeout_seconds))
        self.schema_verifier = schema_verifier
        self.statement_timeout_ms = max(100, int(statement_timeout_ms))

    def run(self) -> Dict[str, str]:
        connection = self.pool.getconn(timeout=self.checkout_timeout_seconds)
        try:
            self._verify_read_write(connection)
            self._rollback(connection)
            self._verify_schema(connection)
            return {
                "databaseReason": "readWriteProbeSucceeded",
                "schemaReason": "migrationHeadVerified",
            }
        finally:
            self._rollback(connection)
            self.pool.putconn(connection)

    def _verify_read_write(self, connection: Any) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{self.statement_timeout_ms}ms",),
                )
                cursor.execute("SELECT 1 AS value")
                if int(self._row_value(cursor.fetchone(), "value") or 0) != 1:
                    raise DatabaseReadinessError("databaseReadProbeFailed")
                cursor.execute(
                    "SELECT current_setting('transaction_read_only') AS read_only"
                )
                read_only = str(
                    self._row_value(cursor.fetchone(), "read_only") or ""
                ).lower()
                if read_only in {"on", "true", "1"}:
                    raise DatabaseReadinessError("databaseReadOnly")
                cursor.execute(
                    """
                    CREATE TEMP TABLE dreamjourney_readiness_probe (
                        value INTEGER NOT NULL
                    ) ON COMMIT DROP
                    """
                )
                cursor.execute(
                    "INSERT INTO dreamjourney_readiness_probe (value) VALUES (1)"
                )
                cursor.execute(
                    "SELECT COUNT(*) AS value FROM dreamjourney_readiness_probe"
                )
                if int(self._row_value(cursor.fetchone(), "value") or 0) != 1:
                    raise DatabaseReadinessError("databaseWriteProbeFailed")
        except DatabaseReadinessError:
            raise
        except Exception as exc:
            raise DatabaseReadinessError("databaseProbeFailed") from exc

    @staticmethod
    def _row_value(row: Any, key: str) -> Any:
        if isinstance(row, dict):
            return row.get(key)
        if isinstance(row, (tuple, list)) and row:
            return row[0]
        return None

    def _verify_schema(self, connection: Any) -> None:
        try:
            self.schema_verifier(connection)
        except MigrationChecksumMismatch as exc:
            raise SchemaReadinessError("migrationChecksumMismatch") from exc
        except MigrationHeadAhead as exc:
            raise SchemaReadinessError("migrationHeadAhead") from exc
        except MigrationNotApplied as exc:
            raise SchemaReadinessError("migrationHeadMismatch") from exc
        except MigrationError as exc:
            raise SchemaReadinessError("migrationVerificationFailed") from exc
        except Exception as exc:
            raise SchemaReadinessError("migrationVerificationFailed") from exc

    @staticmethod
    def _rollback(connection: Any) -> None:
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass
