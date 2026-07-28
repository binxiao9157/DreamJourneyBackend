-- migration:publication_draft_snapshot
--
-- Add private, hash-only metadata for a future Owner Draft Snapshot. The
-- draft preview and source copy remain inaccessible at G0: this migration
-- introduces no writer, gateway, projection, visitor session, URL or public
-- DTO. A later approved writer must re-check every source at command time.

CREATE TABLE publication.publication_drafts (
    id UUID PRIMARY KEY,
    publication_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    draft_revision BIGINT NOT NULL DEFAULT 1 CHECK (draft_revision >= 1),
    state TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft', 'blocked', 'confirmed', 'cancelled')),
    draft_snapshot_hash TEXT NOT NULL CHECK (draft_snapshot_hash ~ '^[a-f0-9]{64}$'),
    preview_hash TEXT NOT NULL CHECK (preview_hash ~ '^[a-f0-9]{64}$'),
    redaction_diff_hash TEXT CHECK (redaction_diff_hash ~ '^[a-f0-9]{64}$'),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    ai_transformation_present BOOLEAN NOT NULL DEFAULT FALSE,
    confirmed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, vault_id),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION publication.validate_draft_owner()
RETURNS TRIGGER AS $$
DECLARE
    publication_owner_subject_id TEXT;
    publication_authority_epoch BIGINT;
BEGIN
    SELECT owner_subject_id, authority_epoch
    INTO publication_owner_subject_id, publication_authority_epoch
    FROM publication.publications
    WHERE id = NEW.publication_id AND vault_id = NEW.vault_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'publication draft publication scope does not exist';
    END IF;
    IF NEW.owner_subject_id IS DISTINCT FROM publication_owner_subject_id THEN
        RAISE EXCEPTION 'publication draft owner does not match publication owner';
    END IF;
    IF NEW.authority_epoch IS DISTINCT FROM publication_authority_epoch THEN
        RAISE EXCEPTION 'publication draft authority epoch is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_drafts_validate_owner
BEFORE INSERT OR UPDATE OF publication_id, vault_id, owner_subject_id, authority_epoch
ON publication.publication_drafts
FOR EACH ROW EXECUTE FUNCTION publication.validate_draft_owner();

CREATE TABLE publication.publication_draft_memory_versions (
    draft_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    memory_version_id UUID NOT NULL,
    source_citation_hash TEXT NOT NULL CHECK (source_citation_hash ~ '^[a-f0-9]{64}$'),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    source_state TEXT NOT NULL
        CHECK (source_state IN ('active', 'redacted', 'deleted', 'suspended')),
    consent_state TEXT NOT NULL
        CHECK (consent_state IN ('granted', 'missing', 'revoked', 'thirdPartyRestricted')),
    requires_redaction BOOLEAN NOT NULL DEFAULT FALSE,
    redaction_diff_hash TEXT CHECK (redaction_diff_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (draft_id, memory_version_id),
    FOREIGN KEY (draft_id, vault_id)
        REFERENCES publication.publication_drafts(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (memory_version_id)
        REFERENCES owner_truth.memory_versions(id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION publication.validate_draft_memory_version_scope()
RETURNS TRIGGER AS $$
DECLARE
    memory_vault_id TEXT;
    memory_is_current BOOLEAN;
BEGIN
    SELECT vault_id, is_current
    INTO memory_vault_id, memory_is_current
    FROM owner_truth.memory_versions
    WHERE id = NEW.memory_version_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'publication draft memory version does not exist';
    END IF;
    IF memory_vault_id IS DISTINCT FROM NEW.vault_id THEN
        RAISE EXCEPTION 'publication draft memory version belongs to another vault';
    END IF;
    IF memory_is_current IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'publication draft memory version is not current';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_draft_memory_versions_validate_scope
BEFORE INSERT OR UPDATE OF vault_id, memory_version_id
ON publication.publication_draft_memory_versions
FOR EACH ROW EXECUTE FUNCTION publication.validate_draft_memory_version_scope();

REVOKE ALL ON TABLE publication.publication_drafts FROM PUBLIC;
REVOKE ALL ON TABLE publication.publication_draft_memory_versions FROM PUBLIC;

CREATE INDEX publication_drafts_owner_state
    ON publication.publication_drafts(vault_id, owner_subject_id, state, updated_at DESC);
CREATE INDEX publication_draft_memory_versions_lookup
    ON publication.publication_draft_memory_versions(memory_version_id, vault_id);
