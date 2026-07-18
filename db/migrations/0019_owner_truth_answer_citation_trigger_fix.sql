-- migration:owner_truth_answer_citation_trigger_fix
--
-- 0018 is already applied as the additive Answer/Citation shadow ledger.
-- Preserve its checksum and replace only the citation-validation function.
-- The original function selected an unqualified version_number, which clashes
-- with PL/pgSQL's local variable namespace on a real Postgres execution.

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
    version_number_value BIGINT;
    version_is_current BOOLEAN;
    version_content_hash TEXT;
    source_vault_id TEXT;
    source_owner_subject_id TEXT;
    source_version_value BIGINT;
    source_state TEXT;
    source_authority_epoch BIGINT;
BEGIN
    SELECT answer.vault_id, answer.owner_subject_id, answer.authority_epoch
    INTO answer_vault_id, answer_owner_subject_id, answer_authority_epoch
    FROM owner_truth.answers AS answer
    WHERE answer.vault_id = NEW.vault_id AND answer.id = NEW.answer_id;

    SELECT
        memory.vault_id,
        memory.owner_subject_id,
        memory.source_id,
        memory.source_version,
        memory.status,
        memory.authority_epoch
    INTO
        memory_vault_id,
        memory_owner_subject_id,
        memory_source_id,
        memory_source_version,
        memory_status,
        memory_authority_epoch
    FROM owner_truth.memories AS memory
    WHERE memory.vault_id = NEW.vault_id AND memory.id = NEW.memory_id;

    SELECT
        memory_version.vault_id,
        memory_version.memory_id,
        memory_version.version_number,
        memory_version.is_current,
        memory_version.content_hash
    INTO
        version_vault_id,
        version_memory_id,
        version_number_value,
        version_is_current,
        version_content_hash
    FROM owner_truth.memory_versions AS memory_version
    WHERE memory_version.vault_id = NEW.vault_id
      AND memory_version.id = NEW.memory_version_id;

    SELECT
        source.vault_id,
        source.owner_subject_id,
        source.source_version,
        source.state,
        source.authority_epoch
    INTO
        source_vault_id,
        source_owner_subject_id,
        source_version_value,
        source_state,
        source_authority_epoch
    FROM owner_truth.sources AS source
    WHERE source.vault_id = NEW.vault_id AND source.id = NEW.source_id;

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
        OR version_number_value IS DISTINCT FROM NEW.memory_version
        OR version_is_current IS DISTINCT FROM TRUE
        OR version_content_hash IS DISTINCT FROM NEW.content_hash
    THEN
        RAISE EXCEPTION 'owner truth Answer citation is not a current authorized MemoryVersion';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
