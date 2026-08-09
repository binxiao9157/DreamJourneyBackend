-- migration:owner_truth_family_contribution_review
--
-- Family material remains inert until the Vault Owner accepts it. A
-- contributor receives no Vault read, Candidate decision, Voice or Digital
-- Human authority from this table.

CREATE TABLE owner_truth.family_contribution_submissions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    grant_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    contributor_subject_id TEXT NOT NULL CHECK (BTRIM(contributor_subject_id) <> ''),
    relationship_id TEXT NOT NULL,
    relationship_epoch BIGINT NOT NULL CHECK (relationship_epoch >= 1),
    grant_version BIGINT NOT NULL CHECK (grant_version >= 1),
    material_kind TEXT NOT NULL CHECK (material_kind IN ('text', 'image')),
    text_content TEXT,
    source_object_id UUID,
    source_id UUID,
    status TEXT NOT NULL CHECK (
        status IN ('pendingReview', 'accepted', 'rejected', 'withdrawn')
    ),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    create_command_id_hash TEXT NOT NULL CHECK (create_command_id_hash ~ '^[a-f0-9]{64}$'),
    create_payload_hash TEXT NOT NULL CHECK (create_payload_hash ~ '^[a-f0-9]{64}$'),
    decision_command_id_hash TEXT CHECK (
        decision_command_id_hash IS NULL OR decision_command_id_hash ~ '^[a-f0-9]{64}$'
    ),
    decision_payload_hash TEXT CHECK (
        decision_payload_hash IS NULL OR decision_payload_hash ~ '^[a-f0-9]{64}$'
    ),
    decided_at TIMESTAMPTZ,
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, create_command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (grant_id)
        REFERENCES owner_truth.family_contribution_grants(id)
        ON DELETE RESTRICT,
    FOREIGN KEY (relationship_id)
        REFERENCES public.family_relationships(id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_object_id)
        REFERENCES owner_truth.media_source_objects(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (owner_subject_id <> contributor_subject_id),
    CHECK (
        (material_kind = 'text' AND NULLIF(BTRIM(text_content), '') IS NOT NULL
            AND source_object_id IS NULL)
        OR (material_kind = 'image' AND text_content IS NULL
            AND source_object_id IS NOT NULL)
    ),
    CHECK (
        (status = 'pendingReview' AND decided_at IS NULL
            AND decision_reason IS NULL
            AND decision_command_id_hash IS NULL
            AND decision_payload_hash IS NULL
            AND source_id IS NULL)
        OR (status IN ('accepted', 'rejected') AND decided_at IS NOT NULL
            AND NULLIF(BTRIM(decision_reason), '') IS NOT NULL
            AND decision_command_id_hash IS NOT NULL
            AND decision_payload_hash IS NOT NULL)
        OR (status = 'withdrawn')
    ),
    CHECK (status <> 'accepted' OR material_kind <> 'text' OR source_id IS NOT NULL),
    CHECK (status <> 'rejected' OR source_id IS NULL)
);

CREATE INDEX owner_truth_family_contribution_submissions_owner_review
    ON owner_truth.family_contribution_submissions(
        owner_subject_id, status, updated_at DESC
    );

CREATE INDEX owner_truth_family_contribution_submissions_contributor_status
    ON owner_truth.family_contribution_submissions(
        contributor_subject_id, status, updated_at DESC
    );

REVOKE ALL ON TABLE owner_truth.family_contribution_submissions FROM PUBLIC;
