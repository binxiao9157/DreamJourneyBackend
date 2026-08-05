-- migration:async_effect_business_message_projection_worker_trigger_fix
--
-- 0076 was already applied before the first disposable worker smoke exercised
-- its trigger. Qualify every table column in the trigger function so PL/pgSQL
-- local variables cannot collide with source column names. This is a forward
-- function replacement only: it changes neither historical request rows nor
-- the default-off worker rollout state.

CREATE OR REPLACE FUNCTION async_effects.validate_business_message_projection_request()
RETURNS trigger AS $$
DECLARE
    job_operation_id UUID;
    job_type TEXT;
    job_resource_type TEXT;
    job_resource_id TEXT;
    job_purpose TEXT;
    job_payload_hash TEXT;
    source_operation_row_id UUID;
    source_owner_subject_id TEXT;
    source_vault_id TEXT;
    source_resource_type TEXT;
    source_resource_id TEXT;
    source_resource_version BIGINT;
    source_purpose TEXT;
    source_authority_epoch BIGINT;
    source_stable_key TEXT;
    source_payload_hash TEXT;
    receipt_operation_id UUID;
    receipt_business_target_key TEXT;
    receipt_type TEXT;
    receipt_state TEXT;
    receipt_outcome TEXT;
    receipt_owner_subject_id TEXT;
    receipt_vault_id TEXT;
    receipt_resource_type TEXT;
    receipt_resource_id TEXT;
    receipt_resource_version BIGINT;
    receipt_purpose TEXT;
    receipt_authority_epoch BIGINT;
    receipt_stable_key TEXT;
    receipt_payload_hash TEXT;
    inbox_operation_id UUID;
    inbox_owner_subject_id TEXT;
    inbox_vault_id TEXT;
    inbox_resource_type TEXT;
    inbox_resource_id TEXT;
    inbox_resource_version BIGINT;
    inbox_purpose TEXT;
    inbox_authority_epoch BIGINT;
    inbox_stable_key TEXT;
    inbox_payload_hash TEXT;
    inbox_consumer_name TEXT;
    inbox_state TEXT;
BEGIN
    SELECT job.operation_id, job.job_type, job.resource_type, job.resource_id,
           job.purpose, job.payload_hash
    INTO job_operation_id, job_type, job_resource_type, job_resource_id, job_purpose, job_payload_hash
    FROM async_effects.jobs AS job
    WHERE job.job_id = NEW.job_id
    FOR SHARE;

    IF NOT FOUND
       OR job_operation_id IS DISTINCT FROM NEW.operation_id
       OR job_type IS DISTINCT FROM 'businessMessage.projection'
       OR job_resource_type IS DISTINCT FROM 'businessMessageProjection'
       OR job_resource_id IS DISTINCT FROM NEW.message_id::TEXT
       OR job_purpose IS DISTINCT FROM 'inAppMessageProjection'
       OR job_payload_hash IS DISTINCT FROM NEW.request_hash THEN
        RAISE EXCEPTION 'business message projection request does not match typed job coordinates';
    END IF;

    SELECT source.operation_id, source.owner_subject_id, source.vault_id,
           source.resource_type, source.resource_id, source.resource_version,
           source.purpose, source.authority_epoch, source.stable_key, source.payload_hash
    INTO source_operation_row_id, source_owner_subject_id, source_vault_id,
         source_resource_type, source_resource_id, source_resource_version,
         source_purpose, source_authority_epoch, source_stable_key,
         source_payload_hash
    FROM async_effects.operations AS source
    WHERE source.operation_id = NEW.source_operation_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'business message projection request source operation is missing';
    END IF;

    SELECT receipt.operation_id, receipt.owner_subject_id, receipt.vault_id,
           receipt.resource_type, receipt.resource_id, receipt.resource_version,
           receipt.purpose, receipt.authority_epoch, receipt.stable_key,
           receipt.payload_hash, receipt.receipt_type, receipt.business_target_key,
           receipt.state, receipt.outcome
    INTO receipt_operation_id, receipt_owner_subject_id, receipt_vault_id,
         receipt_resource_type, receipt_resource_id, receipt_resource_version,
         receipt_purpose, receipt_authority_epoch, receipt_stable_key,
         receipt_payload_hash, receipt_type, receipt_business_target_key,
         receipt_state, receipt_outcome
    FROM async_effects.business_receipts AS receipt
    WHERE receipt.receipt_id = NEW.source_business_receipt_id
    FOR SHARE;

    IF NOT FOUND
       OR receipt_operation_id IS DISTINCT FROM NEW.source_operation_id
       OR receipt_owner_subject_id IS DISTINCT FROM source_owner_subject_id
       OR receipt_vault_id IS DISTINCT FROM source_vault_id
       OR receipt_resource_type IS DISTINCT FROM source_resource_type
       OR receipt_resource_id IS DISTINCT FROM source_resource_id
       OR receipt_resource_version IS DISTINCT FROM source_resource_version
       OR receipt_purpose IS DISTINCT FROM source_purpose
       OR receipt_authority_epoch IS DISTINCT FROM source_authority_epoch
       OR receipt_stable_key IS DISTINCT FROM source_stable_key
       OR receipt_payload_hash IS DISTINCT FROM source_payload_hash
       OR receipt_type IS DISTINCT FROM ('consumer.' || NEW.source_consumer_name || '.completion')
       OR receipt_business_target_key IS DISTINCT FROM NEW.source_business_target_key
       OR receipt_state IS DISTINCT FROM 'completed'
       OR receipt_outcome IS DISTINCT FROM 'completed' THEN
        RAISE EXCEPTION 'business message projection request requires a completed source receipt';
    END IF;

    SELECT inbox.operation_id, inbox.owner_subject_id, inbox.vault_id,
           inbox.resource_type, inbox.resource_id, inbox.resource_version,
           inbox.purpose, inbox.authority_epoch, inbox.stable_key, inbox.payload_hash,
           inbox.consumer_name, inbox.state
    INTO inbox_operation_id, inbox_owner_subject_id, inbox_vault_id,
         inbox_resource_type, inbox_resource_id, inbox_resource_version,
         inbox_purpose, inbox_authority_epoch, inbox_stable_key,
         inbox_payload_hash, inbox_consumer_name, inbox_state
    FROM async_effects.consumer_inbox AS inbox
    WHERE inbox.inbox_id = NEW.source_consumer_inbox_id
    FOR SHARE;

    IF NOT FOUND
       OR inbox_operation_id IS DISTINCT FROM NEW.source_operation_id
       OR inbox_owner_subject_id IS DISTINCT FROM source_owner_subject_id
       OR inbox_vault_id IS DISTINCT FROM source_vault_id
       OR inbox_resource_type IS DISTINCT FROM source_resource_type
       OR inbox_resource_id IS DISTINCT FROM source_resource_id
       OR inbox_resource_version IS DISTINCT FROM source_resource_version
       OR inbox_purpose IS DISTINCT FROM source_purpose
       OR inbox_authority_epoch IS DISTINCT FROM source_authority_epoch
       OR inbox_stable_key IS DISTINCT FROM source_stable_key
       OR inbox_payload_hash IS DISTINCT FROM source_payload_hash
       OR inbox_consumer_name IS DISTINCT FROM NEW.source_consumer_name
       OR inbox_state IS DISTINCT FROM 'completed' THEN
        RAISE EXCEPTION 'business message projection request requires a completed source inbox';
    END IF;

    IF receipt_owner_subject_id IS DISTINCT FROM inbox_owner_subject_id
       OR receipt_vault_id IS DISTINCT FROM inbox_vault_id
       OR receipt_resource_type IS DISTINCT FROM inbox_resource_type
       OR receipt_resource_id IS DISTINCT FROM inbox_resource_id
       OR receipt_resource_version IS DISTINCT FROM inbox_resource_version
       OR receipt_purpose IS DISTINCT FROM inbox_purpose
       OR receipt_authority_epoch IS DISTINCT FROM inbox_authority_epoch
       OR receipt_stable_key IS DISTINCT FROM inbox_stable_key
       OR receipt_payload_hash IS DISTINCT FROM inbox_payload_hash THEN
        RAISE EXCEPTION 'business message projection request source receipt and inbox coordinates diverged';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
