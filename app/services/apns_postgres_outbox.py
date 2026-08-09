"""Encrypted APNs token vault and lease-based PostgreSQL outbox adapters."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import replace
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.services.apns_delivery import (
    APNSDeliveryError,
    APNSDeliveryJob,
    APNSDeviceRegistration,
)


class APNSPostgresStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def _fetchone(
        self,
        sql: str,
        params: tuple = (),
        commit: bool = False,
    ) -> Mapping[str, Any] | None:
        ...

    def _fetchall(self, sql: str, params: tuple = ()) -> list[Mapping[str, Any]]:
        ...


class PostgresAPNSPersistence:
    """One persistence boundary for registrations, secrets, jobs and receipts."""

    def __init__(self, store: APNSPostgresStore) -> None:
        self._store = store

    def store_token_secret(
        self,
        *,
        token_reference: str,
        registration_id: str,
        ciphertext: str,
        key_version: str,
    ) -> None:
        self._store._fetchone(
            """
            INSERT INTO notification.apns_token_secrets (
                token_reference, registration_id, ciphertext, key_version,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (token_reference) DO UPDATE SET
                ciphertext = EXCLUDED.ciphertext,
                key_version = EXCLUDED.key_version,
                updated_at = NOW()
            WHERE notification.apns_token_secrets.registration_id = EXCLUDED.registration_id
            RETURNING token_reference
            """,
            (token_reference, registration_id, ciphertext, key_version),
        )

    def resolve_token_secret(self, token_reference: str) -> str | None:
        row = self._store._fetchone(
            """
            SELECT ciphertext
            FROM notification.apns_token_secrets
            WHERE token_reference = %s
            """,
            (token_reference,),
        )
        return None if row is None else str(row["ciphertext"])

    def delete_token_secret(self, token_reference: str) -> None:
        self._store._fetchone(
            """
            DELETE FROM notification.apns_token_secrets
            WHERE token_reference = %s
            RETURNING token_reference
            """,
            (token_reference,),
        )

    def upsert_registration(
        self,
        registration: APNSDeviceRegistration,
    ) -> APNSDeviceRegistration:
        with self._store.request_unit_of_work(
            correlation_id=f"apns-registration-{registration.registration_id}",
            command_id="apnsRegistrationUpsert",
        ):
            existing = self.get_registration(registration.registration_id)
            if existing is not None and (
                existing.owner_user_id != registration.owner_user_id
                or existing.installation_digest != registration.installation_digest
                or existing.topic != registration.topic
                or existing.environment != registration.environment
            ):
                raise APNSDeliveryError("apnsRegistrationConflict")
            generation = (
                existing.generation + 1
                if existing is not None and existing.token_hash != registration.token_hash
                else (existing.generation if existing is not None else 0)
            )
            row = self._store._fetchone(
                """
                INSERT INTO notification.apns_device_registrations (
                    id, owner_user_id, installation_digest, token_hash,
                    token_reference, topic, environment, generation, status,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    token_hash = EXCLUDED.token_hash,
                    token_reference = EXCLUDED.token_reference,
                    generation = EXCLUDED.generation,
                    status = 'active',
                    updated_at = NOW()
                WHERE notification.apns_device_registrations.owner_user_id = EXCLUDED.owner_user_id
                  AND notification.apns_device_registrations.installation_digest = EXCLUDED.installation_digest
                  AND notification.apns_device_registrations.topic = EXCLUDED.topic
                  AND notification.apns_device_registrations.environment = EXCLUDED.environment
                RETURNING *
                """,
                (
                    registration.registration_id,
                    registration.owner_user_id,
                    registration.installation_digest,
                    registration.token_hash,
                    registration.token_reference,
                    registration.topic,
                    registration.environment,
                    generation,
                ),
            )
            if row is None:
                raise APNSDeliveryError("apnsRegistrationConflict")
            return self._registration(row)

    def get_registration(self, registration_id: str) -> APNSDeviceRegistration | None:
        row = self._store._fetchone(
            """
            SELECT * FROM notification.apns_device_registrations WHERE id = %s
            """,
            (registration_id,),
        )
        return None if row is None else self._registration(row)

    def list_active_registrations(self, owner_user_id: str) -> list[APNSDeviceRegistration]:
        rows = self._store._fetchall(
            """
            SELECT * FROM notification.apns_device_registrations
            WHERE owner_user_id = %s AND status = 'active'
            ORDER BY updated_at DESC, id
            """,
            (owner_user_id,),
        )
        return [self._registration(row) for row in rows]

    def upsert(self, job: APNSDeliveryJob) -> APNSDeliveryJob:
        payload_hash = self._payload_hash(job.payload)
        with self._store.request_unit_of_work(
            correlation_id=f"apns-outbox-enqueue-{job.job_id}",
            command_id="apnsOutboxEnqueue",
        ):
            row = self._store._fetchone(
                """
                INSERT INTO notification.apns_delivery_outbox (
                    id, message_id, registration_id, owner_user_id,
                    registration_generation, payload, payload_hash,
                    state, attempt, reason_code,
                    retryable, available_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', 0, 'queued', FALSE, NOW(), NOW(), NOW())
                ON CONFLICT (id) DO NOTHING
                RETURNING *
                """,
                (
                    job.job_id,
                    job.message_id,
                    job.registration.registration_id,
                    job.registration.owner_user_id,
                    job.registration.generation,
                    dict(job.payload),
                    payload_hash,
                ),
            )
            if row is None:
                existing = self.get(job.job_id)
                if (
                    existing.message_id != job.message_id
                    or existing.registration.registration_id
                    != job.registration.registration_id
                    or self._payload_hash(existing.payload) != payload_hash
                ):
                    raise APNSDeliveryError("apnsJobConflict")
                return existing
            persisted = self._job(row, registration=job.registration)
            self._append_receipt(persisted)
            return persisted

    def get(self, job_id: str) -> APNSDeliveryJob:
        row = self._store._fetchone(
            """
            SELECT * FROM notification.apns_delivery_outbox WHERE id = %s
            """,
            (job_id,),
        )
        if row is None:
            raise APNSDeliveryError("apnsJobNotFound")
        registration = self.get_registration(str(row["registration_id"]))
        if registration is None:
            raise APNSDeliveryError("apnsRegistrationUnavailable")
        return self._job(row, registration=registration)

    def save(self, job: APNSDeliveryJob) -> APNSDeliveryJob:
        with self._store.request_unit_of_work(
            correlation_id=f"apns-outbox-save-{job.job_id}",
            command_id=f"apnsOutboxState:{job.state}:{job.attempt}",
        ):
            row = self._store._fetchone(
                """
                UPDATE notification.apns_delivery_outbox
                SET state = %s,
                    attempt = %s,
                    reason_code = %s,
                    provider_receipt_hash = %s,
                    retryable = %s,
                    available_at = CASE WHEN %s = 'queued' THEN NOW() ELSE available_at END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    job.state,
                    job.attempt,
                    job.reason_code,
                    job.provider_receipt_hash,
                    job.retryable,
                    job.state,
                    job.job_id,
                ),
            )
            if row is None:
                raise APNSDeliveryError("apnsJobNotFound")
            persisted = self._job(row, registration=job.registration)
            self._append_receipt(persisted)
            return persisted

    def claim_due(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[APNSDeliveryJob]:
        rows = self._store._fetchall(
            """
            WITH due AS (
                SELECT id
                FROM notification.apns_delivery_outbox
                WHERE available_at <= NOW()
                  AND (
                    state = 'queued'
                    OR (state = 'dispatching' AND lease_expires_at < NOW())
                  )
                ORDER BY available_at, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE notification.apns_delivery_outbox AS outbox
            SET state = 'dispatching',
                reason_code = 'workerLeaseClaimed',
                lease_owner = %s,
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                updated_at = NOW()
            FROM due
            WHERE outbox.id = due.id
            RETURNING outbox.*
            """,
            (limit, worker_id, lease_seconds),
        )
        jobs: list[APNSDeliveryJob] = []
        for row in rows:
            registration = self.get_registration(str(row["registration_id"]))
            if registration is not None:
                jobs.append(self._job(row, registration=registration))
        return jobs

    def receipt_count(self, job_id: str) -> int:
        row = self._store._fetchone(
            """
            SELECT COUNT(*) AS count
            FROM notification.apns_delivery_receipts
            WHERE job_id = %s
            """,
            (job_id,),
        )
        return 0 if row is None else int(row["count"])

    def _append_receipt(self, job: APNSDeliveryJob) -> None:
        self._store._fetchone(
            """
            INSERT INTO notification.apns_delivery_receipts (
                job_id, attempt, state, reason_code, provider_receipt_hash,
                retryable, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (job_id, attempt, state, reason_code) DO NOTHING
            RETURNING id
            """,
            (
                job.job_id,
                job.attempt,
                job.state,
                job.reason_code,
                job.provider_receipt_hash,
                job.retryable,
            ),
        )

    @staticmethod
    def _registration(row: Mapping[str, Any]) -> APNSDeviceRegistration:
        return APNSDeviceRegistration(
            registration_id=str(row["id"]),
            owner_user_id=str(row["owner_user_id"]),
            installation_digest=str(row["installation_digest"]),
            token_hash=str(row["token_hash"]),
            token_reference=str(row["token_reference"]),
            topic=str(row["topic"]),
            environment=str(row["environment"]),
            generation=int(row.get("generation") or 0),
            active=str(row.get("status") or "") == "active",
        )

    @staticmethod
    def _job(
        row: Mapping[str, Any],
        *,
        registration: APNSDeviceRegistration,
    ) -> APNSDeliveryJob:
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, Mapping):
            raise APNSDeliveryError("apnsJobPayloadInvalid")
        return APNSDeliveryJob(
            job_id=str(row["id"]),
            message_id=str(row["message_id"]),
            registration=replace(
                registration,
                generation=int(row.get("registration_generation") or 0),
            ),
            payload=dict(payload),
            state=str(row["state"]),
            attempt=int(row.get("attempt") or 0),
            reason_code=str(row.get("reason_code") or "unknown"),
            provider_receipt_hash=(
                str(row["provider_receipt_hash"])
                if row.get("provider_receipt_hash") is not None
                else None
            ),
            retryable=bool(row.get("retryable")),
        )

    @staticmethod
    def _payload_hash(payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


class EncryptedPostgresAPNSTokenVault:
    """Fernet-encrypted token vault; plaintext exists only during dispatch."""

    def __init__(
        self,
        *,
        persistence: PostgresAPNSPersistence,
        encryption_key: str,
        key_version: str = "v1",
    ) -> None:
        try:
            self._fernet = Fernet(str(encryption_key or "").strip().encode("ascii"))
        except (TypeError, ValueError) as exc:
            raise APNSDeliveryError("apnsTokenEncryptionKeyInvalid") from exc
        self._persistence = persistence
        self._key_version = str(key_version or "v1").strip() or "v1"

    def store(self, *, registration_id: str, token: str) -> str:
        token_hash = sha256(token.encode("utf-8")).hexdigest()
        reference = f"apnsref_{sha256((registration_id + ':' + token_hash).encode('utf-8')).hexdigest()[:32]}"
        ciphertext = self._fernet.encrypt(token.encode("utf-8")).decode("ascii")
        self._persistence.store_token_secret(
            token_reference=reference,
            registration_id=registration_id,
            ciphertext=ciphertext,
            key_version=self._key_version,
        )
        return reference

    def resolve(self, *, token_reference: str) -> str:
        ciphertext = self._persistence.resolve_token_secret(token_reference)
        if ciphertext is None:
            raise APNSDeliveryError("apnsTokenUnavailable")
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise APNSDeliveryError("apnsTokenDecryptFailed") from exc

    def delete(self, *, token_reference: str) -> None:
        self._persistence.delete_token_secret(token_reference)


__all__ = [
    "EncryptedPostgresAPNSTokenVault",
    "PostgresAPNSPersistence",
]
