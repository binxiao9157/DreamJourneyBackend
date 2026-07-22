-- migration:owner_truth_interview_decision_audits
--
-- Guided-interview policy actions need a minimal immutable audit link, but the
-- audit lane must not copy a transcript, topic label, model output, or any
-- Candidate/Memory authority.  One Owner narrative can therefore bind one
-- value-free decision only.

CREATE TABLE owner_truth.interview_decisions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    thread_id UUID NOT NULL,
    session_id UUID NOT NULL,
    message_id UUID NOT NULL,
    session_version BIGINT NOT NULL CHECK (session_version >= 1),
    action TEXT NOT NULL
        CHECK (action IN ('listen', 'deepen', 'clarify', 'broaden', 'summarize', 'pause')),
    reason_code TEXT NOT NULL
        CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    policy_schema_version TEXT NOT NULL
        CHECK (policy_schema_version = 'owner-truth-interview-orchestration-v1'),
    target_dimension TEXT,
    missing_facet TEXT,
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    request_payload_hash TEXT NOT NULL CHECK (request_payload_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-interview-decision-audit-v1'),
    ui_schema_version TEXT NOT NULL
        CHECK (ui_schema_version = 'interview-decision-audit-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, message_id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, session_id)
        REFERENCES owner_truth.interview_sessions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, message_id)
        REFERENCES owner_truth.conversation_messages(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (target_dimension IS NULL AND missing_facet IS NULL)
        OR (
            target_dimension ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
            AND missing_facet ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
        )
    )
);

CREATE OR REPLACE FUNCTION owner_truth.validate_interview_decision_audit()
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
    session_current_thread_id UUID;
    session_state TEXT;
    session_row_version BIGINT;
    message_owner_subject_id TEXT;
    message_authority_epoch BIGINT;
    message_thread_id UUID;
    message_session_id UUID;
    message_author TEXT;
    message_kind TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT owner_subject_id, authority_epoch, state
    INTO thread_owner_subject_id, thread_authority_epoch, thread_state
    FROM owner_truth.conversation_threads
    WHERE vault_id = NEW.vault_id AND id = NEW.thread_id;

    SELECT owner_subject_id, authority_epoch, current_thread_id, state, row_version
    INTO session_owner_subject_id, session_authority_epoch, session_current_thread_id,
        session_state, session_row_version
    FROM owner_truth.interview_sessions
    WHERE vault_id = NEW.vault_id AND id = NEW.session_id;

    SELECT owner_subject_id, authority_epoch, thread_id, session_id, author, kind
    INTO message_owner_subject_id, message_authority_epoch, message_thread_id,
        message_session_id, message_author, message_kind
    FROM owner_truth.conversation_messages
    WHERE vault_id = NEW.vault_id AND id = NEW.message_id;

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
       OR session_current_thread_id IS DISTINCT FROM NEW.thread_id
       OR session_state IS DISTINCT FROM 'active'
       OR session_row_version IS DISTINCT FROM NEW.session_version
       OR message_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
       OR message_authority_epoch IS DISTINCT FROM vault_authority_epoch
       OR message_thread_id IS DISTINCT FROM NEW.thread_id
       OR message_session_id IS DISTINCT FROM NEW.session_id
       OR message_author IS DISTINCT FROM 'owner'
       OR message_kind IS DISTINCT FROM 'narrative'
    THEN
        RAISE EXCEPTION 'interview decision audit must bind one current Owner narrative and active session authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_interview_decisions_bind_vault_authority
BEFORE INSERT ON owner_truth.interview_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_interview_decisions_validate
BEFORE INSERT ON owner_truth.interview_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_interview_decision_audit();

CREATE TRIGGER owner_truth_interview_decisions_no_update
BEFORE UPDATE ON owner_truth.interview_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_interview_decisions_no_delete
BEFORE DELETE ON owner_truth.interview_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE INDEX owner_truth_interview_decisions_session_created
    ON owner_truth.interview_decisions(vault_id, session_id, created_at DESC);
