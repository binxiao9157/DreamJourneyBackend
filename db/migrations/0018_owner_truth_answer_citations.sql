-- migration:owner_truth_answer_citations
--
-- Add a default-off, hash-only Answer/Citation evidence ledger.  This is not
-- a public Echo answer store: it exists so the Owner Truth Context V4 QA lane
-- can prove that every personal-memory citation resolves to the exact current
-- confirmed MemoryVersion used at answer-recording time.

ALTER TABLE owner_truth.memory_versions
    ADD CONSTRAINT owner_truth_memory_versions_vault_id_id_unique
    UNIQUE (vault_id, id);

CREATE TABLE owner_truth.answers (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    context_hash TEXT NOT NULL CHECK (context_hash ~ '^[a-f0-9]{64}$'),
    context_version TEXT NOT NULL CHECK (BTRIM(context_version) <> ''),
    query_hash TEXT CHECK (query_hash IS NULL OR query_hash ~ '^[a-f0-9]{64}$'),
    query_length INTEGER NOT NULL CHECK (query_length >= 0),
    answer_hash TEXT NOT NULL CHECK (answer_hash ~ '^[a-f0-9]{64}$'),
    answer_length INTEGER NOT NULL CHECK (answer_length >= 0),
    authority_epoch BIGINT CHECK (authority_epoch IS NULL OR authority_epoch >= 0),
    projection_checkpoint TEXT CHECK (
        projection_checkpoint IS NULL OR projection_checkpoint ~ '^[a-f0-9]{64}$'
    ),
    fallbacks JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(fallbacks) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.answer_citations (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    answer_id UUID NOT NULL,
    citation_position INTEGER NOT NULL CHECK (citation_position >= 1),
    memory_id UUID NOT NULL,
    memory_version_id UUID NOT NULL,
    memory_version BIGINT NOT NULL CHECK (memory_version >= 1),
    source_id UUID NOT NULL,
    source_version BIGINT NOT NULL CHECK (source_version >= 1),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, answer_id, citation_position),
    FOREIGN KEY (vault_id, answer_id)
        REFERENCES owner_truth.answers(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_answer_receipt()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND
        OR vault_status IS DISTINCT FROM 'active'
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR (
            NEW.authority_epoch IS NOT NULL
            AND NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        )
    THEN
        RAISE EXCEPTION 'owner truth Answer receipt does not match active Vault authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_answers_validate_vault
BEFORE INSERT ON owner_truth.answers
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_answer_receipt();

CREATE OR REPLACE FUNCTION owner_truth.validate_answer_citation()
RETURNS TRIGGER AS $$
DECLARE
    answer_vault_id TEXT;
    answer_owner_subject_id TEXT;
    answer_authority_epoch BIGINT;
    memory_vault_id TEXT;
    memory_owner_subject_id TEXT;
    memory_source_id UUID;
    memory_source_version BIGINT;
    memory_status TEXT;
    memory_authority_epoch BIGINT;
    version_vault_id TEXT;
    version_memory_id UUID;
    version_number BIGINT;
    version_is_current BOOLEAN;
    version_content_hash TEXT;
    source_vault_id TEXT;
    source_owner_subject_id TEXT;
    source_version_value BIGINT;
    source_state TEXT;
    source_authority_epoch BIGINT;
BEGIN
    SELECT vault_id, owner_subject_id, authority_epoch
    INTO answer_vault_id, answer_owner_subject_id, answer_authority_epoch
    FROM owner_truth.answers
    WHERE vault_id = NEW.vault_id AND id = NEW.answer_id;

    SELECT vault_id, owner_subject_id, source_id, source_version, status, authority_epoch
    INTO memory_vault_id, memory_owner_subject_id, memory_source_id,
        memory_source_version, memory_status, memory_authority_epoch
    FROM owner_truth.memories
    WHERE vault_id = NEW.vault_id AND id = NEW.memory_id;

    SELECT vault_id, memory_id, version_number, is_current, content_hash
    INTO version_vault_id, version_memory_id, version_number, version_is_current, version_content_hash
    FROM owner_truth.memory_versions
    WHERE vault_id = NEW.vault_id AND id = NEW.memory_version_id;

    SELECT vault_id, owner_subject_id, source_version, state, authority_epoch
    INTO source_vault_id, source_owner_subject_id, source_version_value,
        source_state, source_authority_epoch
    FROM owner_truth.sources
    WHERE vault_id = NEW.vault_id AND id = NEW.source_id;

    IF NOT FOUND
        OR answer_vault_id IS DISTINCT FROM NEW.vault_id
        OR memory_vault_id IS DISTINCT FROM NEW.vault_id
        OR version_vault_id IS DISTINCT FROM NEW.vault_id
        OR source_vault_id IS DISTINCT FROM NEW.vault_id
        OR memory_owner_subject_id IS DISTINCT FROM answer_owner_subject_id
        OR source_owner_subject_id IS DISTINCT FROM answer_owner_subject_id
        OR memory_status IS DISTINCT FROM 'active'
        OR memory_authority_epoch IS DISTINCT FROM answer_authority_epoch
        OR memory_source_id IS DISTINCT FROM NEW.source_id
        OR memory_source_version IS DISTINCT FROM NEW.source_version
        OR source_state IS DISTINCT FROM 'active'
        OR source_authority_epoch IS DISTINCT FROM answer_authority_epoch
        OR source_version_value IS DISTINCT FROM NEW.source_version
        OR version_memory_id IS DISTINCT FROM NEW.memory_id
        OR version_number IS DISTINCT FROM NEW.memory_version
        OR version_is_current IS DISTINCT FROM TRUE
        OR version_content_hash IS DISTINCT FROM NEW.content_hash
    THEN
        RAISE EXCEPTION 'owner truth Answer citation is not a current authorized MemoryVersion';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_answer_citations_validate_memory
BEFORE INSERT ON owner_truth.answer_citations
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_answer_citation();

CREATE OR REPLACE FUNCTION owner_truth.answer_citation_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth Answer/Citation evidence is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_answers_no_update
BEFORE UPDATE ON owner_truth.answers
FOR EACH ROW EXECUTE FUNCTION owner_truth.answer_citation_append_only();

CREATE TRIGGER owner_truth_answers_no_delete
BEFORE DELETE ON owner_truth.answers
FOR EACH ROW EXECUTE FUNCTION owner_truth.answer_citation_append_only();

CREATE TRIGGER owner_truth_answer_citations_no_update
BEFORE UPDATE ON owner_truth.answer_citations
FOR EACH ROW EXECUTE FUNCTION owner_truth.answer_citation_append_only();

CREATE TRIGGER owner_truth_answer_citations_no_delete
BEFORE DELETE ON owner_truth.answer_citations
FOR EACH ROW EXECUTE FUNCTION owner_truth.answer_citation_append_only();

CREATE INDEX owner_truth_answers_vault_created
    ON owner_truth.answers(vault_id, created_at DESC);

CREATE INDEX owner_truth_answer_citations_memory_version
    ON owner_truth.answer_citations(vault_id, memory_version_id, created_at DESC);
