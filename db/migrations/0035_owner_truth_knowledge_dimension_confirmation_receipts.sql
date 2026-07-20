-- migration:owner_truth_knowledge_dimension_confirmation_receipts
--
-- Append-only, value-free Owner confirmation receipts for M0-B knowledge
-- dimensions.  This is not a Candidate decision and does not alter a
-- MemoryVersion.  Each receipt binds an explicit Owner UI selection to the
-- current MemoryVersion hash; replacing that version makes the old receipt
-- unreadable to the dimension projection without deleting historical proof.

CREATE TABLE owner_truth.knowledge_dimension_confirmation_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    memory_id UUID NOT NULL,
    memory_version_id UUID NOT NULL,
    bound_content_hash TEXT NOT NULL CHECK (bound_content_hash ~ '^[a-f0-9]{64}$'),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    dimension TEXT NOT NULL CHECK (dimension IN (
        'lifeStage',
        'importantPeople',
        'keyDecisions',
        'professionalExperience',
        'values',
        'aspirationsAndBoundaries'
    )),
    covered_facets JSONB NOT NULL CHECK (
        jsonb_typeof(covered_facets) = 'array'
        AND jsonb_array_length(covered_facets) > 0
    ),
    confirmation_method TEXT NOT NULL
        CHECK (confirmation_method = 'ownerExplicitSelection'),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-knowledge-dimension-confirmation-v1'),
    ui_schema_version TEXT NOT NULL
        CHECK (ui_schema_version = 'knowledge-dimension-review-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, memory_version_id, dimension),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_knowledge_dimension_confirmation_receipt()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    memory_owner_subject_id TEXT;
    memory_kind TEXT;
    memory_perspective_type TEXT;
    memory_epistemic_status TEXT;
    memory_sensitivity TEXT;
    memory_status TEXT;
    memory_authority_epoch BIGINT;
    version_memory_id UUID;
    version_is_current BOOLEAN;
    version_content_hash TEXT;
    facet_count INTEGER;
    distinct_facet_count INTEGER;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT
        memory.owner_subject_id,
        memory.memory_kind,
        memory.perspective_type,
        memory.epistemic_status,
        memory.sensitivity,
        memory.status,
        memory.authority_epoch,
        version.memory_id,
        version.is_current,
        version.content_hash
    INTO
        memory_owner_subject_id,
        memory_kind,
        memory_perspective_type,
        memory_epistemic_status,
        memory_sensitivity,
        memory_status,
        memory_authority_epoch,
        version_memory_id,
        version_is_current,
        version_content_hash
    FROM owner_truth.memories AS memory
    JOIN owner_truth.memory_versions AS version
      ON version.vault_id = memory.vault_id
     AND version.memory_id = memory.id
    WHERE memory.vault_id = NEW.vault_id
      AND version.id = NEW.memory_version_id;

    IF NOT FOUND
        OR vault_status IS DISTINCT FROM 'active'
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.actor_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR memory_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR memory_kind IS DISTINCT FROM 'knowledge'
        OR memory_sensitivity IS DISTINCT FROM 'standard'
        OR memory_perspective_type IS NOT DISTINCT FROM 'inferred'
        OR memory_epistemic_status IS NOT DISTINCT FROM 'inferred'
        OR memory_status IS DISTINCT FROM 'active'
        OR memory_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR version_memory_id IS DISTINCT FROM NEW.memory_id
        OR version_is_current IS DISTINCT FROM TRUE
        OR version_content_hash IS DISTINCT FROM NEW.bound_content_hash
    THEN
        RAISE EXCEPTION 'knowledge dimension confirmation must bind an active Owner current MemoryVersion';
    END IF;

    SELECT COUNT(*), COUNT(DISTINCT facet)
    INTO facet_count, distinct_facet_count
    FROM jsonb_array_elements_text(NEW.covered_facets) AS item(facet);

    IF facet_count < 1 OR facet_count IS DISTINCT FROM distinct_facet_count THEN
        RAISE EXCEPTION 'knowledge dimension confirmation facets must be a non-empty unique list';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(NEW.covered_facets) AS item(facet)
        WHERE NOT (
            (NEW.dimension = 'lifeStage' AND item.facet IN ('timeContext', 'experience'))
            OR (NEW.dimension = 'importantPeople' AND item.facet IN ('person', 'relationshipChange'))
            OR (NEW.dimension = 'keyDecisions' AND item.facet IN ('choice', 'reason', 'outcome'))
            OR (NEW.dimension = 'professionalExperience' AND item.facet IN ('practice', 'judgment'))
            OR (NEW.dimension = 'values' AND item.facet IN ('priority', 'reflection'))
            OR (NEW.dimension = 'aspirationsAndBoundaries' AND item.facet IN ('aspiration', 'boundary'))
        )
    ) THEN
        RAISE EXCEPTION 'knowledge dimension confirmation contains unsupported facets';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_knowledge_dimension_confirmation_receipts_validate
BEFORE INSERT ON owner_truth.knowledge_dimension_confirmation_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_knowledge_dimension_confirmation_receipt();

CREATE OR REPLACE FUNCTION owner_truth.knowledge_dimension_confirmation_receipt_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'knowledge dimension confirmation receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_knowledge_dimension_confirmation_receipts_no_update
BEFORE UPDATE ON owner_truth.knowledge_dimension_confirmation_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.knowledge_dimension_confirmation_receipt_append_only();

CREATE TRIGGER owner_truth_knowledge_dimension_confirmation_receipts_no_delete
BEFORE DELETE ON owner_truth.knowledge_dimension_confirmation_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.knowledge_dimension_confirmation_receipt_append_only();

CREATE INDEX owner_truth_knowledge_dimension_confirmation_receipts_projection_lookup
    ON owner_truth.knowledge_dimension_confirmation_receipts(
        vault_id,
        owner_subject_id,
        memory_version_id,
        dimension
    );
