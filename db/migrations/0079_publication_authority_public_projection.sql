-- migration:publication_authority_public_projection
--
-- P2-S1 adds an Owner-confirmed write lane to the structurally separate
-- publication schema.  The stored display copy is written only after the
-- Owner supplies an explicit draft and confirms its exact snapshot.  It is
-- not populated from a private read model, media address, or inference cache.
-- All release flags remain disabled; no visitor-facing read route is created.

CREATE TABLE publication.publication_authority_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    draft_id UUID,
    publication_version_id UUID,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    command_kind TEXT NOT NULL
        CHECK (command_kind IN ('draftCreated', 'publicationConfirmed')),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    memory_version_id UUID NOT NULL,
    draft_snapshot_hash TEXT NOT NULL CHECK (draft_snapshot_hash ~ '^[a-f0-9]{64}$'),
    preview_hash TEXT NOT NULL CHECK (preview_hash ~ '^[a-f0-9]{64}$'),
    redaction_diff_hash TEXT CHECK (redaction_diff_hash ~ '^[a-f0-9]{64}$'),
    third_party_review_state TEXT NOT NULL
        CHECK (third_party_review_state IN ('noneDetected', 'reviewRequired')),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (publication_version_id),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (draft_id, vault_id)
        REFERENCES publication.publication_drafts(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (command_kind = 'draftCreated' AND draft_id IS NOT NULL AND publication_version_id IS NULL)
        OR (command_kind = 'publicationConfirmed' AND draft_id IS NOT NULL AND publication_version_id IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION publication.validate_publication_authority_receipt()
RETURNS TRIGGER AS $$
DECLARE
    memory_owner_subject_id TEXT;
    memory_authority_epoch BIGINT;
    memory_state TEXT;
    memory_is_current BOOLEAN;
    source_owner_subject_id TEXT;
    source_authority_epoch BIGINT;
    source_state TEXT;
    receipt_decision TEXT;
    receipt_authority_epoch BIGINT;
    candidate_owner_subject_id TEXT;
    candidate_authority_epoch BIGINT;
    candidate_decision_status TEXT;
    publication_owner_subject_id TEXT;
    publication_authority_epoch BIGINT;
    draft_owner_subject_id TEXT;
    draft_authority_epoch BIGINT;
    pinned_memory_version_id UUID;
BEGIN
    SELECT
        memory.owner_subject_id,
        memory.authority_epoch,
        memory.status,
        version.is_current,
        source.owner_subject_id,
        source.authority_epoch,
        source.state,
        decision_receipt.decision,
        decision_receipt.authority_epoch,
        candidate.owner_subject_id,
        candidate.authority_epoch,
        candidate.decision_status
    INTO
        memory_owner_subject_id,
        memory_authority_epoch,
        memory_state,
        memory_is_current,
        source_owner_subject_id,
        source_authority_epoch,
        source_state,
        receipt_decision,
        receipt_authority_epoch,
        candidate_owner_subject_id,
        candidate_authority_epoch,
        candidate_decision_status
    FROM owner_truth.memory_versions AS version
    JOIN owner_truth.memories AS memory
      ON memory.vault_id = version.vault_id
     AND memory.id = version.memory_id
    JOIN owner_truth.sources AS source
      ON source.vault_id = version.vault_id
     AND source.id = version.source_id
    JOIN owner_truth.decision_receipts AS decision_receipt
      ON decision_receipt.vault_id = version.vault_id
     AND decision_receipt.id = version.decision_receipt_id
    JOIN owner_truth.memory_candidates AS candidate
      ON candidate.vault_id = decision_receipt.vault_id
     AND candidate.id = decision_receipt.candidate_id
    WHERE version.vault_id = NEW.vault_id
      AND version.id = NEW.memory_version_id;

    IF NOT FOUND
        OR memory_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR memory_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR memory_state IS DISTINCT FROM 'active'
        OR memory_is_current IS DISTINCT FROM TRUE
        OR source_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR source_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR source_state IS DISTINCT FROM 'active'
        OR receipt_decision NOT IN ('accepted', 'corrected')
        OR receipt_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR candidate_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR candidate_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR candidate_decision_status IS DISTINCT FROM receipt_decision
    THEN
        RAISE EXCEPTION 'publication authority receipt must bind one current Owner-confirmed MemoryVersion';
    END IF;

    SELECT owner_subject_id, authority_epoch
    INTO publication_owner_subject_id, publication_authority_epoch
    FROM publication.publications
    WHERE id = NEW.publication_id
      AND vault_id = NEW.vault_id;

    IF NOT FOUND
        OR publication_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR publication_authority_epoch IS DISTINCT FROM NEW.authority_epoch
    THEN
        RAISE EXCEPTION 'publication authority receipt must match its publication owner and epoch';
    END IF;

    SELECT owner_subject_id, authority_epoch
    INTO draft_owner_subject_id, draft_authority_epoch
    FROM publication.publication_drafts
    WHERE id = NEW.draft_id
      AND vault_id = NEW.vault_id
      AND publication_id = NEW.publication_id;

    IF NOT FOUND
        OR draft_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR draft_authority_epoch IS DISTINCT FROM NEW.authority_epoch
    THEN
        RAISE EXCEPTION 'publication authority receipt must match its draft owner and epoch';
    END IF;

    IF NEW.publication_version_id IS NOT NULL THEN
        SELECT pinned_memory_version_id
        INTO pinned_memory_version_id
        FROM publication.publication_versions
        WHERE id = NEW.publication_version_id
          AND publication_id = NEW.publication_id
          AND vault_id = NEW.vault_id;

        IF NOT FOUND OR pinned_memory_version_id IS DISTINCT FROM NEW.memory_version_id THEN
            RAISE EXCEPTION 'publication authority receipt must bind the publication version authority anchor';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_authority_receipts_validate_authority
BEFORE INSERT ON publication.publication_authority_receipts
FOR EACH ROW EXECUTE FUNCTION publication.validate_publication_authority_receipt();

CREATE TABLE publication.publication_draft_public_contents (
    draft_id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    display_title TEXT NOT NULL CHECK (CHAR_LENGTH(display_title) BETWEEN 1 AND 120),
    display_body TEXT NOT NULL CHECK (CHAR_LENGTH(display_body) BETWEEN 1 AND 12000),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    preview_title TEXT NOT NULL CHECK (CHAR_LENGTH(preview_title) BETWEEN 1 AND 120),
    preview_body TEXT NOT NULL CHECK (CHAR_LENGTH(preview_body) BETWEEN 1 AND 12000),
    preview_hash TEXT NOT NULL CHECK (preview_hash ~ '^[a-f0-9]{64}$'),
    redaction_diff_hash TEXT CHECK (redaction_diff_hash ~ '^[a-f0-9]{64}$'),
    third_party_review_required BOOLEAN NOT NULL DEFAULT FALSE,
    ai_disclosure TEXT NOT NULL CHECK (CHAR_LENGTH(ai_disclosure) BETWEEN 1 AND 300),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (draft_id, vault_id)
        REFERENCES publication.publication_drafts(id, vault_id)
        ON DELETE RESTRICT,
    CONSTRAINT publication_draft_public_contents_no_direct_identifiers CHECK (
        display_title !~ '1[3-9][0-9]{9}'
        AND display_body !~ '1[3-9][0-9]{9}'
        AND display_title !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_body !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_title !~ '[0-9]{15,18}[0-9Xx]'
        AND display_body !~ '[0-9]{15,18}[0-9Xx]'
    )
);

CREATE OR REPLACE FUNCTION publication.publication_draft_public_content_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'publication draft public content is immutable; create a new draft';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_draft_public_contents_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.publication_draft_public_contents
FOR EACH ROW EXECUTE FUNCTION publication.publication_draft_public_content_immutable();

CREATE OR REPLACE FUNCTION publication.publication_authority_receipt_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'publication authority receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_authority_receipts_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.publication_authority_receipts
FOR EACH ROW EXECUTE FUNCTION publication.publication_authority_receipt_append_only();

CREATE TABLE publication.public_projections (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'blocked', 'suspended', 'withdrawn')),
    display_title TEXT NOT NULL CHECK (CHAR_LENGTH(display_title) BETWEEN 1 AND 120),
    display_body TEXT NOT NULL CHECK (CHAR_LENGTH(display_body) BETWEEN 1 AND 12000),
    ai_disclosure TEXT NOT NULL CHECK (CHAR_LENGTH(ai_disclosure) BETWEEN 1 AND 300),
    projection_hash TEXT NOT NULL CHECK (projection_hash ~ '^[a-f0-9]{64}$'),
    public_citation_hash TEXT NOT NULL CHECK (public_citation_hash ~ '^[a-f0-9]{64}$'),
    redaction_diff_hash TEXT CHECK (redaction_diff_hash ~ '^[a-f0-9]{64}$'),
    block_reason_code TEXT CHECK (block_reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    blocked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (publication_version_id),
    UNIQUE (id, publication_id, vault_id),
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    CONSTRAINT publication_public_projections_no_direct_identifiers CHECK (
        display_title !~ '1[3-9][0-9]{9}'
        AND display_body !~ '1[3-9][0-9]{9}'
        AND display_title !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_body !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_title !~ '[0-9]{15,18}[0-9Xx]'
        AND display_body !~ '[0-9]{15,18}[0-9Xx]'
    ),
    CHECK (
        (state = 'active' AND blocked_at IS NULL AND block_reason_code IS NULL)
        OR (state <> 'active' AND blocked_at IS NOT NULL AND block_reason_code IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION publication.publication_public_projections_content_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.vault_id IS DISTINCT FROM OLD.vault_id
        OR NEW.publication_id IS DISTINCT FROM OLD.publication_id
        OR NEW.publication_version_id IS DISTINCT FROM OLD.publication_version_id
        OR NEW.display_title IS DISTINCT FROM OLD.display_title
        OR NEW.display_body IS DISTINCT FROM OLD.display_body
        OR NEW.ai_disclosure IS DISTINCT FROM OLD.ai_disclosure
        OR NEW.projection_hash IS DISTINCT FROM OLD.projection_hash
        OR NEW.public_citation_hash IS DISTINCT FROM OLD.public_citation_hash
        OR NEW.redaction_diff_hash IS DISTINCT FROM OLD.redaction_diff_hash
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'publication public projection content is immutable';
    END IF;
    IF OLD.state <> 'active' AND NEW.state IS DISTINCT FROM OLD.state THEN
        RAISE EXCEPTION 'publication public projection cannot resume after access is blocked';
    END IF;
    IF NEW.state = 'active'
        OR NEW.blocked_at IS NULL
        OR NEW.block_reason_code IS NULL
    THEN
        RAISE EXCEPTION 'publication public projection state transition is invalid';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_public_projections_content_immutable
BEFORE UPDATE ON publication.public_projections
FOR EACH ROW EXECUTE FUNCTION publication.publication_public_projections_content_immutable();

CREATE OR REPLACE FUNCTION publication.publication_public_projections_no_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'publication public projections are retained as lifecycle evidence';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_public_projections_no_delete
BEFORE DELETE ON publication.public_projections
FOR EACH ROW EXECUTE FUNCTION publication.publication_public_projections_no_delete();

CREATE TABLE publication.projection_invalidation_requests (
    id TEXT PRIMARY KEY CHECK (id ~ '^piv_[0-9a-f]{32}$'),
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'processing', 'completed', 'failed')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (publication_version_id, reason_code),
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    CHECK ((state = 'completed' AND completed_at IS NOT NULL) OR (state <> 'completed'))
);

CREATE OR REPLACE FUNCTION publication.block_public_projection_version(
    target_publication_version_id UUID,
    target_reason_code TEXT
)
RETURNS VOID AS $$
DECLARE
    target RECORD;
BEGIN
    FOR target IN
        SELECT projection.vault_id, projection.publication_id, projection.publication_version_id
        FROM publication.public_projections AS projection
        WHERE projection.publication_version_id = target_publication_version_id
          AND projection.state = 'active'
        FOR UPDATE
    LOOP
        UPDATE publication.public_projections
        SET state = 'blocked',
            blocked_at = NOW(),
            block_reason_code = target_reason_code,
            updated_at = NOW()
        WHERE publication_version_id = target.publication_version_id
          AND state = 'active';

        UPDATE publication.publications
        SET state = 'suspended',
            updated_at = NOW()
        WHERE id = target.publication_id
          AND vault_id = target.vault_id
          AND state IN ('draft', 'confirmed');

        UPDATE publication.projector_checkpoints
        SET state = 'blocked',
            updated_at = NOW()
        WHERE publication_id = target.publication_id
          AND vault_id = target.vault_id
          AND state = 'pendingIndex';

        INSERT INTO publication.projection_invalidation_requests (
            id, vault_id, publication_id, publication_version_id, reason_code
        ) VALUES (
            'piv_' || md5(target.publication_version_id::text || ':' || target_reason_code),
            target.vault_id,
            target.publication_id,
            target.publication_version_id,
            target_reason_code
        )
        ON CONFLICT (publication_version_id, reason_code) DO NOTHING;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION publication.block_projections_for_source_change()
RETURNS TRIGGER AS $$
DECLARE
    version_row RECORD;
BEGIN
    IF NEW.state IS DISTINCT FROM 'active'
        OR NEW.source_version IS DISTINCT FROM OLD.source_version
        OR NEW.authority_epoch IS DISTINCT FROM OLD.authority_epoch
    THEN
        FOR version_row IN
            SELECT publication_version.id
            FROM publication.publication_versions AS publication_version
            JOIN owner_truth.memory_versions AS version
              ON version.id = publication_version.pinned_memory_version_id
            WHERE version.vault_id = NEW.vault_id
              AND version.source_id = NEW.id
        LOOP
            PERFORM publication.block_public_projection_version(
                version_row.id,
                'sourceAuthorityChanged'
            );
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_block_projections_for_source_change
AFTER UPDATE OF state, source_version, authority_epoch ON owner_truth.sources
FOR EACH ROW EXECUTE FUNCTION publication.block_projections_for_source_change();

CREATE OR REPLACE FUNCTION publication.block_projections_for_memory_change()
RETURNS TRIGGER AS $$
DECLARE
    version_row RECORD;
BEGIN
    IF NEW.status IS DISTINCT FROM 'active'
        OR NEW.authority_epoch IS DISTINCT FROM OLD.authority_epoch
    THEN
        FOR version_row IN
            SELECT publication_version.id
            FROM publication.publication_versions AS publication_version
            JOIN owner_truth.memory_versions AS version
              ON version.id = publication_version.pinned_memory_version_id
            WHERE version.vault_id = NEW.vault_id
              AND version.memory_id = NEW.id
        LOOP
            PERFORM publication.block_public_projection_version(
                version_row.id,
                'memoryAuthorityChanged'
            );
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_block_projections_for_memory_change
AFTER UPDATE OF status, authority_epoch ON owner_truth.memories
FOR EACH ROW EXECUTE FUNCTION publication.block_projections_for_memory_change();

CREATE OR REPLACE FUNCTION publication.block_projections_for_current_version_change()
RETURNS TRIGGER AS $$
DECLARE
    version_row RECORD;
BEGIN
    IF OLD.is_current = TRUE AND NEW.is_current = FALSE THEN
        FOR version_row IN
            SELECT publication_version.id
            FROM publication.publication_versions AS publication_version
            WHERE publication_version.vault_id = OLD.vault_id
              AND publication_version.pinned_memory_version_id = OLD.id
        LOOP
            PERFORM publication.block_public_projection_version(
                version_row.id,
                'memoryVersionSuperseded'
            );
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_block_projections_for_current_version_change
AFTER UPDATE OF is_current ON owner_truth.memory_versions
FOR EACH ROW EXECUTE FUNCTION publication.block_projections_for_current_version_change();

CREATE OR REPLACE FUNCTION publication.block_projections_for_vault_change()
RETURNS TRIGGER AS $$
DECLARE
    version_row RECORD;
BEGIN
    IF NEW.status IS DISTINCT FROM 'active'
        OR NEW.owner_subject_id IS DISTINCT FROM OLD.owner_subject_id
        OR NEW.authority_epoch IS DISTINCT FROM OLD.authority_epoch
    THEN
        FOR version_row IN
            SELECT publication_version.id
            FROM publication.publication_versions AS publication_version
            WHERE publication_version.vault_id = NEW.vault_id
        LOOP
            PERFORM publication.block_public_projection_version(
                version_row.id,
                'vaultAuthorityChanged'
            );
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_block_projections_for_vault_change
AFTER UPDATE OF status, owner_subject_id, authority_epoch ON owner_truth.vaults
FOR EACH ROW EXECUTE FUNCTION publication.block_projections_for_vault_change();

REVOKE ALL ON TABLE publication.publication_authority_receipts FROM PUBLIC;
REVOKE ALL ON TABLE publication.publication_draft_public_contents FROM PUBLIC;
REVOKE ALL ON TABLE publication.public_projections FROM PUBLIC;
REVOKE ALL ON TABLE publication.projection_invalidation_requests FROM PUBLIC;

CREATE INDEX publication_authority_receipts_owner_lookup
    ON publication.publication_authority_receipts(vault_id, owner_subject_id, created_at DESC);
CREATE INDEX publication_public_projections_state_lookup
    ON publication.public_projections(publication_id, state, created_at DESC);
CREATE INDEX publication_projection_invalidation_requests_state_lookup
    ON publication.projection_invalidation_requests(state, requested_at ASC);
