-- migration:async_effect_business_message_projection_worker_inputs
--
-- A background message projection must not pack private message metadata into
-- async_effect payloads. This append-only input table binds one typed job to a
-- previously completed business receipt and an explicit active inbox snapshot.
-- It remains internal: no public mailbox or notification route reads it.

CREATE TABLE async_effects.business_message_projection_requests (
    job_id UUID PRIMARY KEY
        REFERENCES async_effects.jobs(job_id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL
        REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    source_operation_id UUID NOT NULL
        REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    source_business_receipt_id UUID NOT NULL
        REFERENCES async_effects.business_receipts(receipt_id) ON DELETE RESTRICT,
    source_consumer_inbox_id UUID NOT NULL
        REFERENCES async_effects.consumer_inbox(inbox_id) ON DELETE RESTRICT,
    source_consumer_name TEXT NOT NULL CHECK (BTRIM(source_consumer_name) <> ''),
    source_business_target_key TEXT NOT NULL
        CHECK (source_business_target_key ~ '^[0-9a-f]{64}$'),
    message_id UUID NOT NULL UNIQUE,
    message_kind TEXT NOT NULL CHECK (
        message_kind IN ('timeLetter', 'echoReply', 'careSignal', 'familyInvitation', 'systemNotice')
    ),
    inbox_subject_id TEXT NOT NULL CHECK (BTRIM(inbox_subject_id) <> ''),
    inbox_vault_id TEXT NOT NULL CHECK (BTRIM(inbox_vault_id) <> ''),
    inbox_account_epoch BIGINT NOT NULL CHECK (inbox_account_epoch >= 0),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'business-message-projection-effect-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (
        source_business_receipt_id, message_kind, inbox_subject_id, inbox_vault_id
    )
);

CREATE INDEX async_effects_business_message_projection_requests_source_idx
    ON async_effects.business_message_projection_requests(
        source_operation_id, source_business_receipt_id, created_at DESC
    );

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
    SELECT operation_id, job_type, resource_type, resource_id, purpose, payload_hash
    INTO job_operation_id, job_type, job_resource_type, job_resource_id, job_purpose, job_payload_hash
    FROM async_effects.jobs
    WHERE job_id = NEW.job_id
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

    SELECT operation_id, owner_subject_id, vault_id, resource_type, resource_id,
           resource_version, purpose, authority_epoch, stable_key, payload_hash
    INTO source_operation_row_id, source_owner_subject_id, source_vault_id,
         source_resource_type, source_resource_id, source_resource_version,
         source_purpose, source_authority_epoch, source_stable_key,
         source_payload_hash
    FROM async_effects.operations
    WHERE operation_id = NEW.source_operation_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'business message projection request source operation is missing';
    END IF;

    SELECT operation_id, owner_subject_id, vault_id, resource_type, resource_id,
           resource_version, purpose, authority_epoch, stable_key, payload_hash,
           receipt_type, business_target_key, state, outcome
    INTO receipt_operation_id, receipt_owner_subject_id, receipt_vault_id,
         receipt_resource_type, receipt_resource_id, receipt_resource_version,
         receipt_purpose, receipt_authority_epoch, receipt_stable_key,
         receipt_payload_hash, receipt_type, receipt_business_target_key,
         receipt_state, receipt_outcome
    FROM async_effects.business_receipts
    WHERE receipt_id = NEW.source_business_receipt_id
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

    SELECT operation_id, owner_subject_id, vault_id, resource_type, resource_id,
           resource_version, purpose, authority_epoch, stable_key, payload_hash,
           consumer_name, state
    INTO inbox_operation_id, inbox_owner_subject_id, inbox_vault_id,
         inbox_resource_type, inbox_resource_id, inbox_resource_version,
         inbox_purpose, inbox_authority_epoch, inbox_stable_key,
         inbox_payload_hash, inbox_consumer_name, inbox_state
    FROM async_effects.consumer_inbox
    WHERE inbox_id = NEW.source_consumer_inbox_id
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

CREATE TRIGGER async_effects_business_message_projection_requests_validate
BEFORE INSERT ON async_effects.business_message_projection_requests
FOR EACH ROW EXECUTE FUNCTION async_effects.validate_business_message_projection_request();

CREATE TRIGGER async_effects_business_message_projection_requests_no_update
BEFORE UPDATE ON async_effects.business_message_projection_requests
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();

CREATE TRIGGER async_effects_business_message_projection_requests_no_delete
BEFORE DELETE ON async_effects.business_message_projection_requests
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
