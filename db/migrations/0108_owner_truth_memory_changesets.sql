-- migration:owner_truth_memory_changesets
--
-- A reviewed Candidate is not allowed to silently create another independent
-- formal-memory record when it only adds evidence or clarifies an existing
-- fact.  This additive migration stores the Vault-wide formal-memory revision
-- and one immutable, auditable change set per DecisionReceipt.

CREATE TABLE owner_truth.memory_revisions (
    vault_id TEXT PRIMARY KEY,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.memory_changesets (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    decision_receipt_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    base_memory_revision BIGINT NOT NULL CHECK (base_memory_revision >= 0),
    applied_memory_revision BIGINT NOT NULL CHECK (applied_memory_revision >= 0),
    operation_kind TEXT NOT NULL CHECK (operation_kind IN (
        'add', 'addEvidence', 'refine', 'temporalChange', 'correct',
        'dispute', 'duplicate', 'noPersonalFact'
    )),
    activation_outcome TEXT NOT NULL CHECK (activation_outcome IN (
        'created', 'revised', 'duplicate', 'notApplicable'
    )),
    target_memory_id UUID,
    target_memory_version_id UUID,
    target_memory_version BIGINT CHECK (
        target_memory_version IS NULL OR target_memory_version >= 1
    ),
    candidate_assertion JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(candidate_assertion) = 'array'),
    reason TEXT NOT NULL CHECK (BTRIM(reason) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, decision_receipt_id),
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id, decision_receipt_id)
        REFERENCES owner_truth.decision_receipts(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, target_memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, target_memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (target_memory_id IS NULL AND target_memory_version_id IS NULL AND target_memory_version IS NULL)
        OR
        (target_memory_id IS NOT NULL AND target_memory_version_id IS NOT NULL AND target_memory_version IS NOT NULL)
    )
);

CREATE TABLE owner_truth.memory_changeset_operations (
    changeset_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    operation_index SMALLINT NOT NULL CHECK (operation_index = 1),
    operation_kind TEXT NOT NULL CHECK (operation_kind IN (
        'add', 'addEvidence', 'refine', 'temporalChange', 'correct',
        'dispute', 'duplicate', 'noPersonalFact'
    )),
    changed_fields JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(changed_fields) = 'array'),
    added_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (added_evidence_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, changeset_id, operation_index),
    FOREIGN KEY (vault_id, changeset_id)
        REFERENCES owner_truth.memory_changesets(vault_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX owner_truth_memory_changesets_vault_created
    ON owner_truth.memory_changesets(vault_id, created_at DESC);

CREATE INDEX owner_truth_memory_changesets_target
    ON owner_truth.memory_changesets(vault_id, target_memory_id, target_memory_version_id)
    WHERE target_memory_id IS NOT NULL;

CREATE OR REPLACE FUNCTION owner_truth.memory_changeset_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth MemoryChangeSet is immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_changesets_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changesets
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_immutable();

CREATE TRIGGER owner_truth_memory_changeset_operations_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changeset_operations
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_immutable();
