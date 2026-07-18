-- migration:rights_requests

CREATE TABLE rights_requests (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('account.delete', 'account.restore', 'account.purge')),
    scope JSONB NOT NULL,
    scope_hash TEXT NOT NULL,
    command_id TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    identity_proof_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'requested',
            'accessRevoked',
            'pending',
            'partial',
            'completed',
            'unsupported',
            'failed',
            'restored',
            'purged'
        )
    ),
    contract_version INTEGER NOT NULL CHECK (contract_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (jsonb_typeof(scope) = 'object'),
    UNIQUE (subject_id, command_id)
);

CREATE INDEX idx_rights_requests_subject_status
    ON rights_requests(subject_id, status, updated_at DESC);

CREATE TABLE rights_executions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES rights_requests(id),
    module_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('pending', 'completed', 'partial', 'unsupported', 'failed')
    ),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    receipt_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    UNIQUE (request_id, module_id, resource_type, attempt)
);

CREATE INDEX idx_rights_executions_request
    ON rights_executions(request_id, module_id, resource_type, attempt DESC);

CREATE TABLE resource_deletion_receipts (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES rights_requests(id),
    execution_id TEXT NOT NULL REFERENCES rights_executions(id),
    module_id TEXT NOT NULL,
    resource_scope_hash TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('completed', 'partial', 'unsupported', 'failed')
    ),
    evidence_event_id_hash TEXT,
    receipt_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retention_until TIMESTAMPTZ
);

CREATE INDEX idx_resource_deletion_receipts_request
    ON resource_deletion_receipts(request_id, module_id, created_at DESC);

CREATE OR REPLACE FUNCTION reject_resource_deletion_receipt_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'resource_deletion_receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER resource_deletion_receipts_no_update
BEFORE UPDATE OR DELETE ON resource_deletion_receipts
FOR EACH ROW EXECUTE FUNCTION reject_resource_deletion_receipt_mutation();
