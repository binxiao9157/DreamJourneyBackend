-- migration:owner_truth_changeset_proposals
--
-- A V5 Candidate must show its intended formal-memory mutation before the
-- Owner makes a terminal decision.  The preview is immutable audit material:
-- the receipt binds the exact ChangeSet ID and digest that was rendered, while
-- the write path still recomputes it under the current revision lock.

CREATE TABLE owner_truth.memory_changeset_proposals (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    candidate_id UUID NOT NULL,
    candidate_content_hash TEXT NOT NULL CHECK (BTRIM(candidate_content_hash) <> ''),
    candidate_row_version BIGINT NOT NULL CHECK (candidate_row_version >= 1),
    base_memory_revision BIGINT NOT NULL CHECK (base_memory_revision >= 0),
    change_set_id UUID NOT NULL,
    proposal_hash TEXT NOT NULL CHECK (proposal_hash ~ '^[0-9a-f]{64}$'),
    schema_version TEXT NOT NULL CHECK (BTRIM(schema_version) <> ''),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, change_set_id, proposal_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.memory_changeset_proposal_operations (
    proposal_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    operation_index SMALLINT NOT NULL CHECK (operation_index >= 0),
    operation_kind TEXT NOT NULL CHECK (operation_kind IN (
        'add', 'addEvidence', 'refine', 'temporalChange', 'correct',
        'dispute', 'duplicate', 'noPersonalFact'
    )),
    candidate_id UUID NOT NULL,
    target_memory_id UUID,
    target_memory_version_id UUID,
    target_memory_version BIGINT CHECK (
        target_memory_version IS NULL OR target_memory_version >= 1
    ),
    candidate_assertion JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(candidate_assertion) = 'array'),
    changed_fields JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(changed_fields) = 'array'),
    added_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (added_evidence_count >= 0),
    reason TEXT NOT NULL CHECK (BTRIM(reason) <> ''),
    depends_on_operation_indexes INTEGER[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, proposal_id, operation_index),
    FOREIGN KEY (vault_id, proposal_id)
        REFERENCES owner_truth.memory_changeset_proposals(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (target_memory_id IS NULL AND target_memory_version_id IS NULL AND target_memory_version IS NULL)
        OR
        (target_memory_id IS NOT NULL AND target_memory_version_id IS NOT NULL AND target_memory_version IS NOT NULL)
    )
);

CREATE INDEX owner_truth_memory_changeset_proposals_candidate
    ON owner_truth.memory_changeset_proposals(
        vault_id, candidate_id, candidate_row_version, base_memory_revision DESC
    );

CREATE OR REPLACE FUNCTION owner_truth.memory_changeset_proposal_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth MemoryChangeSet proposal is immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_changeset_proposals_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changeset_proposals
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_proposal_immutable();

CREATE TRIGGER owner_truth_memory_changeset_proposal_operations_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changeset_proposal_operations
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_proposal_immutable();

ALTER TABLE owner_truth.decision_receipts
    ADD COLUMN expected_change_set_id UUID,
    ADD COLUMN expected_proposal_hash TEXT;

ALTER TABLE owner_truth.decision_receipts
    ADD CONSTRAINT owner_truth_decision_receipts_changeset_binding_paired
    CHECK (
        (expected_change_set_id IS NULL AND expected_proposal_hash IS NULL)
        OR
        (expected_change_set_id IS NOT NULL AND expected_proposal_hash ~ '^[0-9a-f]{64}$')
    ) NOT VALID;

ALTER TABLE owner_truth.decision_receipts
    ADD CONSTRAINT owner_truth_decision_receipts_changeset_binding_exists
    FOREIGN KEY (vault_id, expected_change_set_id, expected_proposal_hash)
    REFERENCES owner_truth.memory_changeset_proposals(
        vault_id, change_set_id, proposal_hash
    )
    DEFERRABLE INITIALLY IMMEDIATE
    NOT VALID;

ALTER TABLE owner_truth.decision_receipts
    VALIDATE CONSTRAINT owner_truth_decision_receipts_changeset_binding_paired;

ALTER TABLE owner_truth.decision_receipts
    VALIDATE CONSTRAINT owner_truth_decision_receipts_changeset_binding_exists;
