-- migration:owner_truth_thread_preferences
--
-- Owner-controlled conversation preferences are separate from a single
-- interview-session boundary.  They retain only the opaque ConversationThread
-- identity and policy state; no topic title, transcript, model output, or
-- provider payload may enter this lane.

CREATE TABLE owner_truth.thread_preferences (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    thread_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    preference TEXT NOT NULL CHECK (preference IN ('open', 'cooldown', 'doNotAsk')),
    cooldown_until TIMESTAMPTZ,
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, thread_id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (preference = 'cooldown' AND cooldown_until IS NOT NULL)
        OR (preference IN ('open', 'doNotAsk') AND cooldown_until IS NULL)
    )
);

CREATE TABLE owner_truth.thread_preference_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    thread_id UUID NOT NULL,
    session_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    operation TEXT NOT NULL CHECK (operation IN ('set', 'restore')),
    preference TEXT NOT NULL CHECK (preference IN ('open', 'cooldown', 'doNotAsk')),
    previous_preference TEXT CHECK (previous_preference IN ('cooldown', 'doNotAsk')),
    cooldown_until TIMESTAMPTZ,
    expected_session_version BIGINT NOT NULL CHECK (expected_session_version >= 1),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-thread-preference-v1'),
    ui_schema_version TEXT NOT NULL
        CHECK (ui_schema_version = 'thread-preference-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, session_id)
        REFERENCES owner_truth.interview_sessions(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (operation = 'set'
            AND preference IN ('cooldown', 'doNotAsk')
            AND previous_preference IS NULL
            AND ((preference = 'cooldown' AND cooldown_until IS NOT NULL)
                OR (preference = 'doNotAsk' AND cooldown_until IS NULL)))
        OR
        (operation = 'restore'
            AND preference = 'open'
            AND previous_preference IN ('cooldown', 'doNotAsk')
            AND cooldown_until IS NULL)
    )
);

CREATE TRIGGER owner_truth_thread_preferences_bind_vault_authority
BEFORE INSERT OR UPDATE ON owner_truth.thread_preferences
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE OR REPLACE FUNCTION owner_truth.validate_thread_preference_receipt()
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
    current_preference TEXT;
    current_cooldown_until TIMESTAMPTZ;
    current_preference_epoch BIGINT;
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

    SELECT preference, cooldown_until, authority_epoch
    INTO current_preference, current_cooldown_until, current_preference_epoch
    FROM owner_truth.thread_preferences
    WHERE vault_id = NEW.vault_id AND thread_id = NEW.thread_id;

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
        OR current_preference_epoch IS DISTINCT FROM vault_authority_epoch
        OR current_preference IS DISTINCT FROM NEW.preference
        OR current_cooldown_until IS DISTINCT FROM NEW.cooldown_until
        OR session_row_version IS DISTINCT FROM NEW.expected_session_version
    THEN
        RAISE EXCEPTION 'thread preference receipt must bind current Owner Vault, thread, session, and preference authority';
    END IF;

    IF NEW.operation = 'set' THEN
        IF session_state IS DISTINCT FROM 'paused'
            OR session_boundary IS DISTINCT FROM NEW.preference
        THEN
            RAISE EXCEPTION 'thread preference set receipt must bind paused matching interview boundary';
        END IF;
    ELSIF NEW.operation = 'restore' THEN
        IF session_state IS DISTINCT FROM 'active'
            OR session_boundary IS DISTINCT FROM 'open'
        THEN
            RAISE EXCEPTION 'thread preference restore receipt must bind explicit active open interview boundary';
        END IF;
    ELSE
        RAISE EXCEPTION 'thread preference receipt operation is not supported';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_thread_preference_receipts_validate
BEFORE INSERT ON owner_truth.thread_preference_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_thread_preference_receipt();

CREATE TRIGGER owner_truth_thread_preference_receipts_no_update
BEFORE UPDATE ON owner_truth.thread_preference_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_thread_preference_receipts_no_delete
BEFORE DELETE ON owner_truth.thread_preference_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE INDEX owner_truth_thread_preferences_recommendation_lookup
    ON owner_truth.thread_preferences(vault_id, owner_subject_id, authority_epoch, preference);

CREATE INDEX owner_truth_thread_preference_receipts_thread_lookup
    ON owner_truth.thread_preference_receipts(vault_id, thread_id, created_at DESC);
