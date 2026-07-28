-- migration:owner_truth_knowledge_recommendation_feedback_receipts
--
-- M0-B feedback is a private, value-free control record. It is not a
-- ConversationMessage, Candidate, DecisionReceipt, MemoryVersion, or a new
-- topic authority. The server validates a current selected recommendation
-- before inserting this append-only receipt. ``replace`` only suppresses the
-- candidate identified by its short-lived plan ID; ``notInterested`` can lower
-- a dimension or policy question-template rank without becoming do-not-ask.

CREATE TABLE owner_truth.knowledge_recommendation_feedback_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    candidate_id TEXT NOT NULL CHECK (candidate_id ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    feedback_action TEXT NOT NULL CHECK (feedback_action IN ('replace', 'notInterested')),
    feedback_reason TEXT NOT NULL CHECK (feedback_reason IN (
        'questionWording', 'topicPreference', 'recommendationType'
    )),
    feedback_scope TEXT NOT NULL CHECK (feedback_scope IN (
        'candidate', 'dimension', 'questionTemplate'
    )),
    slot TEXT NOT NULL CHECK (slot IN ('continuity', 'breadth')),
    thread_id UUID NOT NULL,
    session_id UUID NOT NULL,
    expected_session_version BIGINT NOT NULL CHECK (expected_session_version >= 1),
    target_dimension TEXT NOT NULL CHECK (target_dimension IN (
        'lifeStage',
        'importantPeople',
        'keyDecisions',
        'professionalExperience',
        'values',
        'aspirationsAndBoundaries'
    )),
    missing_facet TEXT NOT NULL,
    question_template_id TEXT NOT NULL
        CHECK (question_template_id ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    selection_policy_version TEXT NOT NULL CHECK (BTRIM(selection_policy_version) <> ''),
    evidence_ref_count INTEGER NOT NULL CHECK (evidence_ref_count >= 0),
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-knowledge-recommendation-feedback-v1'),
    ui_schema_version TEXT NOT NULL
        CHECK (ui_schema_version = 'knowledge-recommendation-feedback-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, candidate_id),
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
        (feedback_action = 'replace'
            AND feedback_reason = 'questionWording'
            AND feedback_scope = 'candidate')
        OR (feedback_action = 'notInterested'
            AND feedback_reason = 'topicPreference'
            AND feedback_scope = 'dimension')
        OR (feedback_action = 'notInterested'
            AND feedback_reason = 'recommendationType'
            AND feedback_scope = 'questionTemplate')
    ),
    CHECK (
        (target_dimension = 'lifeStage' AND missing_facet IN ('timeContext', 'experience'))
        OR (target_dimension = 'importantPeople' AND missing_facet IN ('person', 'relationshipChange'))
        OR (target_dimension = 'keyDecisions' AND missing_facet IN ('choice', 'reason', 'outcome'))
        OR (target_dimension = 'professionalExperience' AND missing_facet IN ('practice', 'judgment'))
        OR (target_dimension = 'values' AND missing_facet IN ('priority', 'reflection'))
        OR (target_dimension = 'aspirationsAndBoundaries' AND missing_facet IN ('aspiration', 'boundary'))
    )
);

CREATE OR REPLACE FUNCTION owner_truth.validate_knowledge_recommendation_feedback_receipt()
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
    session_boundary TEXT;
    session_row_version BIGINT;
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
    INTO session_owner_subject_id, session_authority_epoch, session_current_thread_id,
        session_state, session_boundary, session_row_version
    FROM owner_truth.interview_sessions
    WHERE vault_id = NEW.vault_id AND id = NEW.session_id;

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
       OR session_boundary IS DISTINCT FROM 'open'
       OR session_row_version IS DISTINCT FROM NEW.expected_session_version
    THEN
        RAISE EXCEPTION 'recommendation feedback must bind current Owner active open interview authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_knowledge_recommendation_feedback_receipts_validate
BEFORE INSERT ON owner_truth.knowledge_recommendation_feedback_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_knowledge_recommendation_feedback_receipt();

CREATE OR REPLACE FUNCTION owner_truth.knowledge_recommendation_feedback_receipt_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'knowledge recommendation feedback receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_knowledge_recommendation_feedback_receipts_no_update
BEFORE UPDATE ON owner_truth.knowledge_recommendation_feedback_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.knowledge_recommendation_feedback_receipt_append_only();

CREATE TRIGGER owner_truth_knowledge_recommendation_feedback_receipts_no_delete
BEFORE DELETE ON owner_truth.knowledge_recommendation_feedback_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.knowledge_recommendation_feedback_receipt_append_only();

CREATE INDEX owner_truth_knowledge_recommendation_feedback_receipts_policy_lookup
    ON owner_truth.knowledge_recommendation_feedback_receipts(
        vault_id,
        owner_subject_id,
        authority_epoch,
        created_at
    );
