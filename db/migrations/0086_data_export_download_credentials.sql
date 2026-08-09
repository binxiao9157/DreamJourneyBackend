-- migration:data_export_download_credentials
--
-- Short-lived, owner-scoped and single-use download credentials. Only the
-- credential hash is durable; the plaintext token is returned once to the
-- authenticated owner and is never stored in Postgres.

CREATE TABLE data_export_download_credentials (
    job_id TEXT PRIMARY KEY REFERENCES data_export_jobs(id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('active', 'consumed', 'expired', 'revoked')),
    generation INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    CHECK (expires_at > issued_at),
    CHECK ((status = 'consumed') = (consumed_at IS NOT NULL))
);

CREATE UNIQUE INDEX data_export_download_credentials_owner_token_idx
    ON data_export_download_credentials(owner_user_id, token_hash);
CREATE INDEX data_export_download_credentials_expiry_idx
    ON data_export_download_credentials(status, expires_at ASC);

REVOKE ALL ON TABLE data_export_download_credentials FROM PUBLIC;
