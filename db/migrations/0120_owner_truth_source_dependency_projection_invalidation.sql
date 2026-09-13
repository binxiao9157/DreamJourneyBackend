-- migration:owner_truth_source_dependency_projection_invalidation
--
-- A newly captured Source is private evidence awaiting extraction/review. It
-- must not invalidate an existing formal-memory projection until a current,
-- active MemoryVersion actually depends on it. Source mutations that do affect
-- current formal evidence remain fail-closed and leave a durable rebuild request.

CREATE TABLE owner_truth.source_projection_rebuild_requests (
    request_id BIGSERIAL PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    source_id UUID NOT NULL,
    source_version BIGINT NOT NULL CHECK (source_version >= 1),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    memory_revision BIGINT NOT NULL CHECK (memory_revision >= 0),
    rights_revision BIGINT NOT NULL CHECK (rights_revision >= 0),
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'processing', 'completed', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (
        vault_id, source_id, source_version, authority_epoch,
        memory_revision, rights_revision, reason_code
    ),
    FOREIGN KEY (vault_id) REFERENCES owner_truth.vaults(vault_id) ON DELETE RESTRICT
);

CREATE INDEX owner_truth_source_projection_rebuild_pending_idx
    ON owner_truth.source_projection_rebuild_requests(state, created_at, request_id);

CREATE OR REPLACE FUNCTION owner_truth.source_has_current_formal_dependency(
    p_vault_id TEXT,
    p_source_id UUID
) RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
          FROM owner_truth.memory_versions AS version
          JOIN owner_truth.memories AS memory
            ON memory.vault_id = version.vault_id
           AND memory.id = version.memory_id
         WHERE version.vault_id = p_vault_id
           AND version.source_id = p_source_id
           AND version.is_current = TRUE
           AND memory.status = 'active'
           AND memory.owner_subject_id IS NOT DISTINCT FROM (
                SELECT vault.owner_subject_id
                  FROM owner_truth.vaults AS vault
                 WHERE vault.vault_id = p_vault_id
           )
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION owner_truth.invalidate_source_memory_derivatives_if_referenced()
RETURNS TRIGGER AS $$
DECLARE
    v_vault_id TEXT;
    v_owner_subject_id TEXT;
    v_source_id UUID;
    v_source_version BIGINT;
    v_authority_epoch BIGINT;
    v_memory_revision BIGINT;
    v_rights_revision BIGINT;
    v_referenced BOOLEAN := FALSE;
BEGIN
    -- A new Source cannot already be the provenance of a current formal
    -- MemoryVersion in the same AFTER INSERT event.
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        v_vault_id := OLD.vault_id;
        v_owner_subject_id := OLD.owner_subject_id;
        v_source_id := OLD.id;
        v_source_version := OLD.source_version;
        v_authority_epoch := OLD.authority_epoch;
        v_referenced := owner_truth.source_has_current_formal_dependency(
            OLD.vault_id, OLD.id
        );
    ELSE
        v_vault_id := NEW.vault_id;
        v_owner_subject_id := NEW.owner_subject_id;
        v_source_id := NEW.id;
        v_source_version := NEW.source_version;
        v_authority_epoch := NEW.authority_epoch;
        v_referenced := owner_truth.source_has_current_formal_dependency(
            OLD.vault_id, OLD.id
        ) OR owner_truth.source_has_current_formal_dependency(NEW.vault_id, NEW.id);
    END IF;

    IF NOT v_referenced THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;

    SELECT COALESCE(revision, 0)
      INTO v_memory_revision
      FROM owner_truth.memory_revisions
     WHERE vault_id = v_vault_id;
    SELECT COALESCE(MAX(revision), 0)
      INTO v_rights_revision
      FROM owner_truth.projection_rights_events
     WHERE vault_id = v_vault_id
       AND authority_epoch = v_authority_epoch;

    UPDATE owner_truth.memory_projection_checkpoints
       SET state = 'rebuilding', updated_at = NOW()
     WHERE vault_id = v_vault_id AND state = 'ready';
    UPDATE owner_truth.search_document_checkpoints
       SET state = 'rebuilding', updated_at = NOW()
     WHERE vault_id = v_vault_id AND state = 'ready';

    INSERT INTO owner_truth.source_projection_rebuild_requests (
        vault_id, owner_subject_id, source_id, source_version,
        authority_epoch, memory_revision, rights_revision, reason_code
    ) VALUES (
        v_vault_id, v_owner_subject_id, v_source_id, v_source_version,
        v_authority_epoch, COALESCE(v_memory_revision, 0),
        COALESCE(v_rights_revision, 0), 'formalSourceDependencyChanged'
    ) ON CONFLICT DO NOTHING;

    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS owner_truth_sources_invalidate_derivatives
    ON owner_truth.sources;
CREATE TRIGGER owner_truth_sources_invalidate_derivatives
AFTER INSERT OR DELETE OR UPDATE OF
    owner_subject_id, state, source_version, content_hash, authority_epoch
ON owner_truth.sources
FOR EACH ROW EXECUTE FUNCTION
    owner_truth.invalidate_source_memory_derivatives_if_referenced();
