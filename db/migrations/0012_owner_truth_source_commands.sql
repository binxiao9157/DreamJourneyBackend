-- migration:owner_truth_source_commands
--
-- Add the narrow CreateSource command receipt lane. This remains a shadow
-- writer for legacy Archive text and does not switch read/write authority.

ALTER TABLE owner_truth.sources
    ADD COLUMN IF NOT EXISTS content_payload JSONB NOT NULL DEFAULT '{}'::JSONB
        CHECK (jsonb_typeof(content_payload) = 'object');

CREATE TABLE owner_truth.source_command_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    command_id_hash TEXT NOT NULL CHECK (BTRIM(command_id_hash) <> ''),
    payload_hash TEXT NOT NULL CHECK (BTRIM(payload_hash) <> ''),
    source_id UUID NOT NULL,
    expected_version BIGINT NOT NULL CHECK (expected_version >= 0),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, source_id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.guard_source_payload_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.source_kind IS DISTINCT FROM NEW.source_kind
        OR OLD.source_version IS DISTINCT FROM NEW.source_version
        OR OLD.content_hash IS DISTINCT FROM NEW.content_hash
        OR OLD.content_payload IS DISTINCT FROM NEW.content_payload THEN
        RAISE EXCEPTION 'owner truth source payload is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_sources_payload_immutable
BEFORE UPDATE ON owner_truth.sources
FOR EACH ROW EXECUTE FUNCTION owner_truth.guard_source_payload_immutable();

CREATE OR REPLACE FUNCTION owner_truth.source_command_receipts_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth source command receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_source_command_receipts_no_update
BEFORE UPDATE ON owner_truth.source_command_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.source_command_receipts_append_only();

CREATE TRIGGER owner_truth_source_command_receipts_no_delete
BEFORE DELETE ON owner_truth.source_command_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.source_command_receipts_append_only();

CREATE INDEX owner_truth_source_command_receipts_vault_created
    ON owner_truth.source_command_receipts(vault_id, created_at DESC);
