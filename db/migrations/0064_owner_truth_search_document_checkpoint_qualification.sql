-- migration:owner_truth_search_document_checkpoint_qualification
--
-- Migration 0046 defined a PL/pgSQL local named projection_source and then
-- selected an unqualified column of the same name. PostgreSQL correctly
-- rejects that branch as ambiguous when a SearchDocument checkpoint is
-- rebuilt. Keep the applied 0046 checksum immutable and replace only the
-- trigger function with qualified relation columns and distinct local names.
-- No Source, Candidate, MemoryVersion, Projection, or SearchDocument rows are
-- rewritten by this compatibility fix.

CREATE OR REPLACE FUNCTION owner_truth.validate_search_document_checkpoint()
RETURNS TRIGGER AS $$
DECLARE
    v_vault_owner_subject_id TEXT;
    v_vault_authority_epoch BIGINT;
    v_vault_status TEXT;
    v_projection_owner_subject_id TEXT;
    v_projection_source TEXT;
    v_projection_state TEXT;
    v_projection_hash TEXT;
BEGIN
    SELECT vault.owner_subject_id, vault.authority_epoch, vault.status
    INTO v_vault_owner_subject_id, v_vault_authority_epoch, v_vault_status
    FROM owner_truth.vaults AS vault
    WHERE vault.vault_id = NEW.vault_id;

    SELECT checkpoint.owner_subject_id,
           checkpoint.projection_source,
           checkpoint.state,
           checkpoint.projection_hash
    INTO v_projection_owner_subject_id,
         v_projection_source,
         v_projection_state,
         v_projection_hash
    FROM owner_truth.memory_projection_checkpoints AS checkpoint
    WHERE checkpoint.vault_id = NEW.vault_id
      AND checkpoint.authority_epoch = NEW.authority_epoch;

    IF NOT FOUND
       OR v_vault_status IS DISTINCT FROM 'active'
       OR NEW.owner_subject_id IS DISTINCT FROM v_vault_owner_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM v_vault_authority_epoch
       OR v_projection_owner_subject_id IS DISTINCT FROM v_vault_owner_subject_id
       OR v_projection_source IS DISTINCT FROM 'v4'
       OR v_projection_state IS DISTINCT FROM 'ready'
       OR NEW.source_projection_checkpoint IS DISTINCT FROM v_projection_hash
    THEN
        RAISE EXCEPTION 'owner truth search document checkpoint is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
