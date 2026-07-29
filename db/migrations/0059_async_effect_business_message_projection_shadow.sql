-- migration:async_effect_business_message_projection_shadow
--
-- Stores a metadata-only, append-only shadow of a completed business message.
-- This table is intentionally separate from mailbox_letters: it preserves the
-- resource owner and the recipient inbox as distinct coordinates, has no
-- public route, and cannot dispatch notifications or alter a mailbox row.

CREATE TABLE async_effects.business_message_projections (
    message_id UUID PRIMARY KEY,
    business_receipt_id UUID NOT NULL
        REFERENCES async_effects.business_receipts(receipt_id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL
        REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    resource_owner_subject_id TEXT NOT NULL CHECK (BTRIM(resource_owner_subject_id) <> ''),
    resource_vault_id TEXT NOT NULL CHECK (BTRIM(resource_vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    resource_authority_epoch BIGINT NOT NULL CHECK (resource_authority_epoch >= 0),
    purpose TEXT NOT NULL CHECK (purpose = 'businessCompletionMessage'),
    business_target_key TEXT NOT NULL CHECK (business_target_key ~ '^[0-9a-f]{64}$'),
    inbox_subject_id TEXT NOT NULL CHECK (BTRIM(inbox_subject_id) <> ''),
    inbox_vault_id TEXT NOT NULL CHECK (BTRIM(inbox_vault_id) <> ''),
    inbox_account_epoch BIGINT NOT NULL CHECK (inbox_account_epoch >= 0),
    message_kind TEXT NOT NULL CHECK (
        message_kind IN ('timeLetter', 'echoReply', 'careSignal', 'familyInvitation', 'systemNotice')
    ),
    state TEXT NOT NULL CHECK (state = 'unread'),
    projection_hash TEXT NOT NULL CHECK (projection_hash ~ '^[0-9a-f]{64}$'),
    schema_version TEXT NOT NULL CHECK (schema_version = 'business-message-projection-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (business_receipt_id, message_kind, inbox_subject_id, inbox_vault_id)
);

CREATE INDEX async_effects_business_message_projections_inbox_idx
    ON async_effects.business_message_projections(
        inbox_subject_id, inbox_vault_id, created_at DESC
    );

CREATE OR REPLACE FUNCTION async_effects.validate_business_message_projection()
RETURNS trigger AS $$
DECLARE
    receipt_operation_id UUID;
    receipt_owner_subject_id TEXT;
    receipt_vault_id TEXT;
    receipt_resource_type TEXT;
    receipt_resource_id TEXT;
    receipt_resource_version BIGINT;
    receipt_authority_epoch BIGINT;
    receipt_business_target_key TEXT;
    receipt_state TEXT;
    receipt_outcome TEXT;
BEGIN
    SELECT operation_id, owner_subject_id, vault_id, resource_type, resource_id,
           resource_version, authority_epoch, business_target_key, state, outcome
    INTO receipt_operation_id, receipt_owner_subject_id, receipt_vault_id,
         receipt_resource_type, receipt_resource_id, receipt_resource_version,
         receipt_authority_epoch, receipt_business_target_key, receipt_state,
         receipt_outcome
    FROM async_effects.business_receipts
    WHERE receipt_id = NEW.business_receipt_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'business message projection references a missing receipt';
    END IF;
    IF receipt_state IS DISTINCT FROM 'completed'
       OR receipt_outcome IS DISTINCT FROM 'completed' THEN
        RAISE EXCEPTION 'business message projection requires a completed receipt';
    END IF;
    IF NEW.operation_id IS DISTINCT FROM receipt_operation_id
       OR NEW.resource_owner_subject_id IS DISTINCT FROM receipt_owner_subject_id
       OR NEW.resource_vault_id IS DISTINCT FROM receipt_vault_id
       OR NEW.resource_type IS DISTINCT FROM receipt_resource_type
       OR NEW.resource_id IS DISTINCT FROM receipt_resource_id
       OR NEW.resource_version IS DISTINCT FROM receipt_resource_version
       OR NEW.resource_authority_epoch IS DISTINCT FROM receipt_authority_epoch
       OR NEW.business_target_key IS DISTINCT FROM receipt_business_target_key THEN
        RAISE EXCEPTION 'business message projection does not match immutable receipt coordinates';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER async_effects_business_message_projections_validate_receipt
BEFORE INSERT ON async_effects.business_message_projections
FOR EACH ROW EXECUTE FUNCTION async_effects.validate_business_message_projection();

CREATE TRIGGER async_effects_business_message_projections_no_update
BEFORE UPDATE ON async_effects.business_message_projections
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();

CREATE TRIGGER async_effects_business_message_projections_no_delete
BEFORE DELETE ON async_effects.business_message_projections
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
