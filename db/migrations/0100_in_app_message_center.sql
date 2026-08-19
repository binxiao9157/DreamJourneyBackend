-- migration:in_app_message_center
--
-- Adds mutable, principal-scoped lifecycle state beside the immutable
-- business-message projection. Message bodies remain outside this lane.

ALTER TABLE async_effects.business_message_projections
    DROP CONSTRAINT business_message_projections_message_kind_check;

ALTER TABLE async_effects.business_message_projections
    ADD CONSTRAINT business_message_projections_message_kind_check CHECK (
        message_kind IN (
            'timeLetter', 'echoReply', 'careSignal', 'familyInvitation', 'systemNotice',
            'candidateReady', 'projectionStatus', 'exportStatus', 'familyContribution',
            'authorizationRevoked', 'accountSecurity', 'taskRetryRequired'
        )
    );

ALTER TABLE async_effects.business_message_projection_requests
    DROP CONSTRAINT business_message_projection_requests_message_kind_check;

ALTER TABLE async_effects.business_message_projection_requests
    ADD CONSTRAINT business_message_projection_requests_message_kind_check CHECK (
        message_kind IN (
            'timeLetter', 'echoReply', 'careSignal', 'familyInvitation', 'systemNotice',
            'candidateReady', 'projectionStatus', 'exportStatus', 'familyContribution',
            'authorizationRevoked', 'accountSecurity', 'taskRetryRequired'
        )
    );

CREATE TABLE async_effects.in_app_message_lifecycle (
    message_id UUID PRIMARY KEY
        REFERENCES async_effects.business_message_projections(message_id) ON DELETE RESTRICT,
    inbox_subject_id TEXT NOT NULL CHECK (BTRIM(inbox_subject_id) <> ''),
    inbox_vault_id TEXT NOT NULL CHECK (BTRIM(inbox_vault_id) <> ''),
    state TEXT NOT NULL CHECK (state IN ('read', 'deleted')),
    read_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ,
    state_version BIGINT NOT NULL CHECK (state_version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (state = 'read' AND deleted_at IS NULL)
        OR (state = 'deleted' AND deleted_at IS NOT NULL)
    )
);

CREATE INDEX async_effects_in_app_message_lifecycle_inbox_idx
    ON async_effects.in_app_message_lifecycle(inbox_subject_id, state, updated_at DESC);

CREATE OR REPLACE FUNCTION async_effects.validate_in_app_message_lifecycle()
RETURNS trigger AS $$
DECLARE
    projection_subject_id TEXT;
    projection_vault_id TEXT;
    projection_created_at TIMESTAMPTZ;
BEGIN
    SELECT inbox_subject_id, inbox_vault_id, created_at
    INTO projection_subject_id, projection_vault_id, projection_created_at
    FROM async_effects.business_message_projections
    WHERE message_id = NEW.message_id
    FOR SHARE;

    IF NOT FOUND
       OR NEW.inbox_subject_id IS DISTINCT FROM projection_subject_id
       OR NEW.inbox_vault_id IS DISTINCT FROM projection_vault_id
       OR NEW.read_at < projection_created_at THEN
        RAISE EXCEPTION 'in-app message lifecycle does not match immutable inbox coordinates';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.message_id IS DISTINCT FROM OLD.message_id
           OR NEW.inbox_subject_id IS DISTINCT FROM OLD.inbox_subject_id
           OR NEW.inbox_vault_id IS DISTINCT FROM OLD.inbox_vault_id
           OR NEW.read_at IS DISTINCT FROM OLD.read_at
           OR NEW.state_version IS DISTINCT FROM OLD.state_version + 1
           OR OLD.state = 'deleted'
           OR (OLD.state = 'read' AND NEW.state <> 'deleted') THEN
            RAISE EXCEPTION 'in-app message lifecycle transition is invalid';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER async_effects_in_app_message_lifecycle_validate
BEFORE INSERT OR UPDATE ON async_effects.in_app_message_lifecycle
FOR EACH ROW EXECUTE FUNCTION async_effects.validate_in_app_message_lifecycle();

CREATE TRIGGER async_effects_in_app_message_lifecycle_no_delete
BEFORE DELETE ON async_effects.in_app_message_lifecycle
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();

CREATE TABLE async_effects.in_app_message_commands (
    command_id UUID PRIMARY KEY,
    inbox_subject_id TEXT NOT NULL CHECK (BTRIM(inbox_subject_id) <> ''),
    command_kind TEXT NOT NULL CHECK (
        command_kind IN ('markRead', 'markAllRead', 'deleteRead')
    ),
    message_id UUID
        REFERENCES async_effects.business_message_projections(message_id) ON DELETE RESTRICT,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    affected_count INTEGER NOT NULL CHECK (affected_count >= 0),
    schema_version TEXT NOT NULL CHECK (schema_version = 'in-app-message-command-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (command_kind = 'markRead' AND message_id IS NOT NULL)
        OR (command_kind IN ('markAllRead', 'deleteRead') AND message_id IS NULL)
    )
);

CREATE INDEX async_effects_in_app_message_commands_inbox_idx
    ON async_effects.in_app_message_commands(inbox_subject_id, created_at DESC);

CREATE TRIGGER async_effects_in_app_message_commands_no_update
BEFORE UPDATE ON async_effects.in_app_message_commands
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();

CREATE TRIGGER async_effects_in_app_message_commands_no_delete
BEFORE DELETE ON async_effects.in_app_message_commands
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
