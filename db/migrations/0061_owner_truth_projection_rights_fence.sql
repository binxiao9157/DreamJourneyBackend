-- migration:owner_truth_projection_rights_fence
--
-- A MemoryVersion projection is derived data.  In addition to source/version
-- currentness, it must be fenced by a Vault-level, value-free rights revision.
-- This migration creates no public ingress and no backfill: later consent or
-- data-rights workflows must explicitly append a normalized event.

CREATE TABLE owner_truth.projection_rights_events (
    vault_id TEXT NOT NULL
        REFERENCES owner_truth.vaults(vault_id) ON DELETE RESTRICT,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    rights_state TEXT NOT NULL CHECK (rights_state IN ('active', 'revoked')),
    event_hash TEXT NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch, revision),
    UNIQUE (vault_id, authority_epoch, command_id_hash)
);

CREATE INDEX owner_truth_projection_rights_events_current
    ON owner_truth.projection_rights_events(vault_id, authority_epoch, revision DESC);

CREATE OR REPLACE FUNCTION owner_truth.validate_projection_rights_event()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    previous_revision BIGINT;
    previous_state TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id
    FOR SHARE;

    IF NOT FOUND
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR vault_status IS DISTINCT FROM 'active'
    THEN
        RAISE EXCEPTION 'owner truth projection rights event authority is stale';
    END IF;

    SELECT revision, rights_state
    INTO previous_revision, previous_state
    FROM owner_truth.projection_rights_events
    WHERE vault_id = NEW.vault_id
      AND authority_epoch = NEW.authority_epoch
      AND revision < NEW.revision
    ORDER BY revision DESC
    LIMIT 1
    FOR SHARE;

    IF NEW.revision IS DISTINCT FROM COALESCE(previous_revision, 0) + 1 THEN
        RAISE EXCEPTION 'owner truth projection rights revision must advance by one';
    END IF;
    IF previous_state = 'revoked' THEN
        RAISE EXCEPTION 'revoked owner truth projection rights require explicit future reconsent';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_projection_rights_events_validate_insert
BEFORE INSERT ON owner_truth.projection_rights_events
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_projection_rights_event();

CREATE OR REPLACE FUNCTION owner_truth.reject_projection_rights_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth projection rights events are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_projection_rights_events_reject_mutation
BEFORE UPDATE OR DELETE ON owner_truth.projection_rights_events
FOR EACH ROW EXECUTE FUNCTION owner_truth.reject_projection_rights_event_mutation();

ALTER TABLE owner_truth.memory_projection_checkpoints
    ADD COLUMN IF NOT EXISTS rights_revision BIGINT NOT NULL DEFAULT 0
        CHECK (rights_revision >= 0),
    ADD COLUMN IF NOT EXISTS rights_event_hash TEXT NOT NULL DEFAULT 'none'
        CHECK (rights_event_hash = 'none' OR rights_event_hash ~ '^[0-9a-f]{64}$');

CREATE OR REPLACE FUNCTION owner_truth.validate_memory_projection_checkpoint()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    current_rights_revision BIGINT;
    current_rights_state TEXT;
    current_rights_event_hash TEXT;
    vault_found BOOLEAN;
    rights_event_found BOOLEAN;
BEGIN
    IF NEW.projection_source <> 'v4' THEN
        RETURN NEW;
    END IF;
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;
    vault_found := FOUND;

    SELECT revision, rights_state, event_hash
    INTO current_rights_revision, current_rights_state, current_rights_event_hash
    FROM owner_truth.projection_rights_events
    WHERE vault_id = NEW.vault_id
      AND authority_epoch = NEW.authority_epoch
    ORDER BY revision DESC
    LIMIT 1;
    rights_event_found := FOUND;

    IF NOT rights_event_found THEN
        current_rights_revision := 0;
        current_rights_state := 'active';
        current_rights_event_hash := 'none';
    END IF;

    IF NOT vault_found
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR vault_status IS DISTINCT FROM 'active'
        OR current_rights_state IS DISTINCT FROM 'active'
        OR NEW.rights_revision IS DISTINCT FROM current_rights_revision
        OR NEW.rights_event_hash IS DISTINCT FROM current_rights_event_hash
    THEN
        RAISE EXCEPTION 'owner truth projection checkpoint rights fence is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS owner_truth_memory_projection_checkpoints_validate_vault
    ON owner_truth.memory_projection_checkpoints;
CREATE TRIGGER owner_truth_memory_projection_checkpoints_validate_vault
BEFORE INSERT OR UPDATE OF owner_subject_id, authority_epoch, projection_source,
    rights_revision, rights_event_hash
ON owner_truth.memory_projection_checkpoints
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_memory_projection_checkpoint();

CREATE OR REPLACE FUNCTION owner_truth.validate_memory_projection_entry_rights()
RETURNS TRIGGER AS $$
DECLARE
    current_rights_state TEXT;
BEGIN
    SELECT rights_state
    INTO current_rights_state
    FROM owner_truth.projection_rights_events
    WHERE vault_id = NEW.vault_id
      AND authority_epoch = NEW.authority_epoch
    ORDER BY revision DESC
    LIMIT 1;

    IF FOUND AND current_rights_state IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'owner truth projection entry rights fence is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_projection_entries_validate_rights
BEFORE INSERT OR UPDATE ON owner_truth.memory_projection_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_memory_projection_entry_rights();
