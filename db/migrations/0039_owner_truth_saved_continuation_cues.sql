-- migration:owner_truth_saved_continuation_cues
--
-- An explicit Owner "continue later" cue is a private, append-only pointer.
-- It carries no transcript, question text, model output, candidate, or memory
-- mutation. A cue is valid only while its exact Owner/Vault authority, open
-- interview session, current confirmation, and missing facet remain current.

CREATE TABLE owner_truth.saved_continuation_cues (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    thread_id UUID NOT NULL,
    session_id UUID NOT NULL,
    expected_session_version BIGINT NOT NULL CHECK (expected_session_version >= 1),
    knowledge_confirmation_id UUID NOT NULL,
    memory_id UUID NOT NULL,
    memory_version_id UUID NOT NULL,
    bound_content_hash TEXT NOT NULL CHECK (bound_content_hash ~ '^[a-f0-9]{64}$'),
    target_dimension TEXT NOT NULL CHECK (target_dimension IN (
        'lifeStage',
        'importantPeople',
        'keyDecisions',
        'professionalExperience',
        'values',
        'aspirationsAndBoundaries'
    )),
    missing_facet TEXT NOT NULL,
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-saved-continuation-cue-v1'),
    ui_schema_version TEXT NOT NULL
        CHECK (ui_schema_version = 'saved-continuation-cue-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, session_id),
    CHECK (
        (target_dimension = 'lifeStage' AND missing_facet IN ('timeContext', 'experience'))
        OR (target_dimension = 'importantPeople' AND missing_facet IN ('person', 'relationshipChange'))
        OR (target_dimension = 'keyDecisions' AND missing_facet IN ('choice', 'reason', 'outcome'))
        OR (target_dimension = 'professionalExperience' AND missing_facet IN ('practice', 'judgment'))
        OR (target_dimension = 'values' AND missing_facet IN ('priority', 'reflection'))
        OR (target_dimension = 'aspirationsAndBoundaries' AND missing_facet IN ('aspiration', 'boundary'))
    ),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, session_id)
        REFERENCES owner_truth.interview_sessions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, knowledge_confirmation_id)
        REFERENCES owner_truth.knowledge_dimension_confirmation_receipts(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_saved_continuation_cue()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    thread_owner_subject_id TEXT;
    thread_authority_epoch BIGINT;
    thread_state TEXT;
    session_owner_subject_id TEXT;
    session_authority_epoch BIGINT;
    session_thread_id UUID;
    session_state TEXT;
    session_boundary TEXT;
    session_row_version BIGINT;
    confirmation_owner_subject_id TEXT;
    confirmation_actor_subject_id TEXT;
    confirmation_authority_epoch BIGINT;
    confirmation_memory_id UUID;
    confirmation_memory_version_id UUID;
    confirmation_bound_content_hash TEXT;
    confirmation_dimension TEXT;
    memory_owner_subject_id TEXT;
    memory_status TEXT;
    memory_authority_epoch BIGINT;
    version_memory_id UUID;
    version_is_current BOOLEAN;
    version_content_hash TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT owner_subject_id, authority_epoch, state
    INTO thread_owner_subject_id, thread_authority_epoch, thread_state
    FROM owner_truth.conversation_threads
    WHERE vault_id = NEW.vault_id AND id = NEW.thread_id;

    SELECT owner_subject_id, authority_epoch, current_thread_id, state, boundary, row_version
    INTO session_owner_subject_id, session_authority_epoch, session_thread_id,
        session_state, session_boundary, session_row_version
    FROM owner_truth.interview_sessions
    WHERE vault_id = NEW.vault_id AND id = NEW.session_id;

    SELECT receipt.owner_subject_id, receipt.actor_subject_id, receipt.authority_epoch,
        receipt.memory_id, receipt.memory_version_id, receipt.bound_content_hash,
        receipt.dimension,
        memory.owner_subject_id, memory.status, memory.authority_epoch,
        version.memory_id, version.is_current, version.content_hash
    INTO confirmation_owner_subject_id, confirmation_actor_subject_id,
        confirmation_authority_epoch, confirmation_memory_id,
        confirmation_memory_version_id, confirmation_bound_content_hash,
        confirmation_dimension, memory_owner_subject_id, memory_status,
        memory_authority_epoch, version_memory_id, version_is_current,
        version_content_hash
    FROM owner_truth.knowledge_dimension_confirmation_receipts AS receipt
    JOIN owner_truth.memories AS memory
      ON memory.vault_id = receipt.vault_id AND memory.id = receipt.memory_id
    JOIN owner_truth.memory_versions AS version
      ON version.vault_id = receipt.vault_id AND version.id = receipt.memory_version_id
    WHERE receipt.vault_id = NEW.vault_id AND receipt.id = NEW.knowledge_confirmation_id;

    IF NOT FOUND
        OR vault_status IS DISTINCT FROM 'active'
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.actor_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR thread_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR thread_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR thread_state IS DISTINCT FROM 'active'
        OR session_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR session_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR session_thread_id IS DISTINCT FROM NEW.thread_id
        OR session_state IS DISTINCT FROM 'active'
        OR session_boundary IS DISTINCT FROM 'open'
        OR session_row_version IS DISTINCT FROM NEW.expected_session_version
        OR confirmation_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR confirmation_actor_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR confirmation_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR confirmation_memory_id IS DISTINCT FROM NEW.memory_id
        OR confirmation_memory_version_id IS DISTINCT FROM NEW.memory_version_id
        OR confirmation_bound_content_hash IS DISTINCT FROM NEW.bound_content_hash
        OR confirmation_dimension IS DISTINCT FROM NEW.target_dimension
        OR memory_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR memory_status IS DISTINCT FROM 'active'
        OR memory_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR version_memory_id IS DISTINCT FROM NEW.memory_id
        OR version_is_current IS DISTINCT FROM TRUE
        OR version_content_hash IS DISTINCT FROM NEW.bound_content_hash
    THEN
        RAISE EXCEPTION 'saved continuation cue must bind an active Owner open session and current confirmed MemoryVersion';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM owner_truth.knowledge_dimension_confirmation_receipts AS receipt
        JOIN owner_truth.memories AS memory
          ON memory.vault_id = receipt.vault_id AND memory.id = receipt.memory_id
        JOIN owner_truth.memory_versions AS version
          ON version.vault_id = receipt.vault_id AND version.id = receipt.memory_version_id
        WHERE receipt.vault_id = NEW.vault_id
          AND receipt.owner_subject_id = vault_owner_subject_id
          AND receipt.actor_subject_id = vault_owner_subject_id
          AND receipt.authority_epoch = vault_authority_epoch
          AND receipt.dimension = NEW.target_dimension
          AND memory.owner_subject_id = vault_owner_subject_id
          AND memory.status = 'active'
          AND memory.authority_epoch = vault_authority_epoch
          AND version.is_current = TRUE
          AND receipt.covered_facets ? NEW.missing_facet
    ) THEN
        RAISE EXCEPTION 'saved continuation cue facet is already covered';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_saved_continuation_cues_validate
BEFORE INSERT ON owner_truth.saved_continuation_cues
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_saved_continuation_cue();

CREATE OR REPLACE FUNCTION owner_truth.saved_continuation_cue_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'saved continuation cue receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_saved_continuation_cues_no_update
BEFORE UPDATE ON owner_truth.saved_continuation_cues
FOR EACH ROW EXECUTE FUNCTION owner_truth.saved_continuation_cue_append_only();

CREATE TRIGGER owner_truth_saved_continuation_cues_no_delete
BEFORE DELETE ON owner_truth.saved_continuation_cues
FOR EACH ROW EXECUTE FUNCTION owner_truth.saved_continuation_cue_append_only();

CREATE INDEX owner_truth_saved_continuation_cues_recommendation_lookup
    ON owner_truth.saved_continuation_cues(
        vault_id,
        owner_subject_id,
        authority_epoch,
        session_id,
        memory_version_id
    );
