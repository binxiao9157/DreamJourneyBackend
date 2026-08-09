-- migration:apns_postgres_outbox
--
-- Raw APNs device tokens are encrypted before persistence. Delivery jobs use
-- database leases and append-only receipts so API/worker restarts cannot lose
-- or silently duplicate notification work.

CREATE SCHEMA IF NOT EXISTS notification;

CREATE TABLE notification.apns_token_secrets (
    token_reference TEXT PRIMARY KEY CHECK (token_reference ~ '^apnsref_[a-f0-9]{32}$'),
    registration_id UUID NOT NULL,
    ciphertext TEXT NOT NULL CHECK (BTRIM(ciphertext) <> ''),
    key_version TEXT NOT NULL CHECK (BTRIM(key_version) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE notification.apns_device_registrations (
    id UUID PRIMARY KEY,
    owner_user_id TEXT NOT NULL CHECK (BTRIM(owner_user_id) <> ''),
    installation_digest TEXT NOT NULL CHECK (installation_digest ~ '^[a-f0-9]{64}$'),
    token_hash TEXT NOT NULL CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    token_reference TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL CHECK (BTRIM(topic) <> ''),
    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
    generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (token_reference)
        REFERENCES notification.apns_token_secrets(token_reference)
        ON DELETE RESTRICT,
    UNIQUE (owner_user_id, installation_digest, topic, environment)
);

CREATE INDEX apns_device_registrations_owner_active
    ON notification.apns_device_registrations(owner_user_id, status, updated_at DESC);

CREATE TABLE notification.apns_delivery_outbox (
    id UUID PRIMARY KEY,
    message_id TEXT NOT NULL CHECK (BTRIM(message_id) <> ''),
    registration_id UUID NOT NULL,
    owner_user_id TEXT NOT NULL CHECK (BTRIM(owner_user_id) <> ''),
    registration_generation BIGINT NOT NULL CHECK (registration_generation >= 0),
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'dispatching', 'accepted', 'failed', 'unknown', 'arrived')
    ),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    provider_receipt_hash TEXT CHECK (
        provider_receipt_hash IS NULL OR provider_receipt_hash ~ '^[a-f0-9]{64}$'
    ),
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (registration_id)
        REFERENCES notification.apns_device_registrations(id)
        ON DELETE RESTRICT,
    UNIQUE (message_id, registration_id)
);

CREATE INDEX apns_delivery_outbox_due
    ON notification.apns_delivery_outbox(state, available_at, created_at);

CREATE TABLE notification.apns_delivery_receipts (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'dispatching', 'accepted', 'failed', 'unknown', 'arrived')
    ),
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    provider_receipt_hash TEXT CHECK (
        provider_receipt_hash IS NULL OR provider_receipt_hash ~ '^[a-f0-9]{64}$'
    ),
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (job_id)
        REFERENCES notification.apns_delivery_outbox(id)
        ON DELETE RESTRICT,
    UNIQUE (job_id, attempt, state, reason_code)
);

CREATE INDEX apns_delivery_receipts_job_created
    ON notification.apns_delivery_receipts(job_id, created_at, id);

REVOKE ALL ON SCHEMA notification FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA notification FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA notification FROM PUBLIC;
