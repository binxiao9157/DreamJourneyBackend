-- migration:account_purge_receipts
--
-- Terminal account purge must leave a durable, redacted receipt after the
-- account payload has been reduced to a tombstone. No phone or raw subject id
-- is retained in this table.

CREATE TABLE account_purge_receipts (
    id TEXT PRIMARY KEY,
    subject_hash TEXT NOT NULL UNIQUE,
    deletion_request_id_hash TEXT,
    deleted_at TIMESTAMPTZ,
    purge_after TIMESTAMPTZ,
    purged_at TIMESTAMPTZ NOT NULL,
    restore_count INTEGER NOT NULL CHECK (restore_count >= 0),
    receipt_hash TEXT NOT NULL UNIQUE,
    contract_version INTEGER NOT NULL CHECK (contract_version = 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_account_purge_receipts_purged_at
    ON account_purge_receipts(purged_at DESC);

CREATE OR REPLACE FUNCTION reject_account_purge_receipt_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'account_purge_receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER account_purge_receipts_no_mutation
BEFORE UPDATE OR DELETE ON account_purge_receipts
FOR EACH ROW EXECUTE FUNCTION reject_account_purge_receipt_mutation();
