-- migration:formal_memory_markdown_export
--
-- Separates the public, Vault-scoped formal-memory Markdown export from the
-- product-closed full-account archive while reusing the bounded ExportJob
-- lifecycle and one-time download credential infrastructure.

ALTER TABLE data_export_jobs
    ADD COLUMN export_type TEXT NOT NULL DEFAULT 'fullAccountArchive' CHECK (
        export_type IN ('fullAccountArchive', 'formalMemoryMarkdown')
    ),
    ADD COLUMN scope_id TEXT NOT NULL DEFAULT 'account' CHECK (BTRIM(scope_id) <> '');

ALTER TABLE data_export_jobs
    DROP CONSTRAINT data_export_jobs_owner_user_id_request_key_hash_key;

ALTER TABLE data_export_jobs
    ADD CONSTRAINT data_export_jobs_owner_type_scope_request_key_unique UNIQUE (
        owner_user_id,
        export_type,
        scope_id,
        request_key_hash
    );

ALTER TABLE data_export_jobs
    DROP CONSTRAINT data_export_jobs_status_check;

ALTER TABLE data_export_jobs
    ADD CONSTRAINT data_export_jobs_status_check CHECK (status IN (
        'queued', 'running', 'ready', 'partial', 'failed', 'cancelled', 'expired'
    ));

ALTER TABLE data_export_jobs
    ADD CONSTRAINT data_export_jobs_cancelled_artifact_check CHECK (
        status <> 'cancelled'
        OR (
            artifact_hash IS NULL
            AND artifact_payload IS NULL
            AND manifest_payload IS NULL
            AND ready_at IS NULL
        )
    );

CREATE INDEX data_export_jobs_owner_type_scope_created_idx
    ON data_export_jobs(owner_user_id, export_type, scope_id, created_at DESC);

