-- migration:family_relationship_termination
-- A relationship can be ended by either participant without deleting either
-- account. Receipts are append-only evidence; publication grants remain under
-- their independent Owner-controlled lifecycle.

CREATE TABLE family_relationship_termination_receipts (
    receipt_id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL REFERENCES family_relationships(id) ON DELETE RESTRICT,
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    actor_role TEXT NOT NULL CHECK (actor_role IN ('owner', 'member')),
    outcome TEXT NOT NULL CHECK (outcome IN ('terminated', 'alreadyTerminated')),
    receipt JSONB NOT NULL,
    terminated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (relationship_id, command_id_hash)
);

CREATE TABLE owner_truth.family_contribution_disposal_queue (
    submission_id UUID PRIMARY KEY
        REFERENCES owner_truth.family_contribution_submissions(id) ON DELETE RESTRICT,
    relationship_id TEXT NOT NULL
        REFERENCES family_relationships(id) ON DELETE RESTRICT,
    grant_id UUID NOT NULL
        REFERENCES owner_truth.family_contribution_grants(id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    contributor_subject_id TEXT NOT NULL CHECK (BTRIM(contributor_subject_id) <> ''),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'completed', 'manualReview')),
    reason TEXT NOT NULL CHECK (reason = 'relationshipTerminated'),
    enqueued_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (state = 'completed' AND completed_at IS NOT NULL)
        OR (state <> 'completed')
    )
);

CREATE INDEX family_relationship_termination_actor_time
    ON family_relationship_termination_receipts(actor_subject_id, terminated_at DESC);

CREATE INDEX owner_truth_family_contribution_disposal_pending
    ON owner_truth.family_contribution_disposal_queue(state, enqueued_at, submission_id);

CREATE OR REPLACE FUNCTION reject_family_relationship_termination_receipt_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'family relationship termination receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER family_relationship_termination_receipts_no_mutation
BEFORE UPDATE OR DELETE ON family_relationship_termination_receipts
FOR EACH ROW EXECUTE FUNCTION reject_family_relationship_termination_receipt_mutation();

REVOKE ALL ON TABLE family_relationship_termination_receipts FROM PUBLIC;
REVOKE ALL ON TABLE owner_truth.family_contribution_disposal_queue FROM PUBLIC;
