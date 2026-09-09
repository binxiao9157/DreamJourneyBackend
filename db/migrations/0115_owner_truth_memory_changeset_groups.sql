-- migration:owner_truth_memory_changeset_groups
--
-- Related V5 Candidates may require one Owner-visible proposal and one
-- all-or-nothing confirmation.  Existing 0108/0112 records remain the
-- authoritative child ChangeSets and receipts; this additive schema binds
-- their ordered preview and root command without creating another fact store.

CREATE TABLE owner_truth.memory_changeset_group_proposals (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    base_memory_revision BIGINT NOT NULL CHECK (base_memory_revision >= 0),
    proposal_hash TEXT NOT NULL CHECK (proposal_hash ~ '^[0-9a-f]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-memory-changeset-group-v1'),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, proposal_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.memory_changeset_group_proposal_members (
    group_proposal_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    operation_index SMALLINT NOT NULL CHECK (operation_index >= 0),
    candidate_id UUID NOT NULL,
    candidate_row_version BIGINT NOT NULL CHECK (candidate_row_version >= 1),
    child_proposal_id UUID NOT NULL,
    child_change_set_id UUID NOT NULL,
    requested_action TEXT NOT NULL CHECK (requested_action IN ('accept', 'correct', 'reject')),
    anticipated_outcome TEXT NOT NULL CHECK (anticipated_outcome IN (
        'created', 'revised', 'duplicate', 'notApplicable'
    )),
    applied_memory_revision BIGINT NOT NULL CHECK (applied_memory_revision >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, group_proposal_id, operation_index),
    UNIQUE (vault_id, group_proposal_id, candidate_id),
    FOREIGN KEY (vault_id, group_proposal_id)
        REFERENCES owner_truth.memory_changeset_group_proposals(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, child_proposal_id)
        REFERENCES owner_truth.memory_changeset_proposals(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.memory_changeset_group_proposal_dependencies (
    group_proposal_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    before_operation_index SMALLINT NOT NULL CHECK (before_operation_index >= 0),
    after_operation_index SMALLINT NOT NULL CHECK (after_operation_index >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        vault_id, group_proposal_id, before_operation_index, after_operation_index
    ),
    FOREIGN KEY (vault_id, group_proposal_id)
        REFERENCES owner_truth.memory_changeset_group_proposals(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (before_operation_index <> after_operation_index)
);

CREATE TABLE owner_truth.memory_changeset_group_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    group_proposal_id UUID NOT NULL,
    group_proposal_hash TEXT NOT NULL CHECK (group_proposal_hash ~ '^[0-9a-f]{64}$'),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[0-9a-f]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    base_memory_revision BIGINT NOT NULL CHECK (base_memory_revision >= 0),
    applied_memory_revision BIGINT NOT NULL CHECK (applied_memory_revision >= 0),
    result_payload JSONB NOT NULL CHECK (jsonb_typeof(result_payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id, group_proposal_id)
        REFERENCES owner_truth.memory_changeset_group_proposals(vault_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX owner_truth_memory_changeset_group_proposals_vault_created
    ON owner_truth.memory_changeset_group_proposals(vault_id, created_at DESC);

CREATE INDEX owner_truth_memory_changeset_group_receipts_vault_created
    ON owner_truth.memory_changeset_group_receipts(vault_id, created_at DESC);

CREATE TRIGGER owner_truth_memory_changeset_group_proposals_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changeset_group_proposals
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_proposal_immutable();

CREATE TRIGGER owner_truth_memory_changeset_group_proposal_members_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changeset_group_proposal_members
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_proposal_immutable();

CREATE TRIGGER owner_truth_memory_changeset_group_proposal_dependencies_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changeset_group_proposal_dependencies
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_proposal_immutable();

CREATE TRIGGER owner_truth_memory_changeset_group_receipts_immutable
BEFORE UPDATE OR DELETE ON owner_truth.memory_changeset_group_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_changeset_proposal_immutable();
