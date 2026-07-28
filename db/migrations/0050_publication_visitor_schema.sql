-- migration:publication_visitor_schema
--
-- Add a disabled publication domain that is structurally separate from the
-- private Owner Truth schema. These tables contain scope, hashes, policy and
-- lifecycle metadata only. No route, writer, public projection, visitor
-- gateway, public URL or public database role is introduced by this migration.

CREATE SCHEMA IF NOT EXISTS publication;
REVOKE ALL ON SCHEMA publication FROM PUBLIC;

CREATE TABLE publication.publications (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    state TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft', 'confirmed', 'suspended', 'withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, vault_id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION publication.validate_publication_owner()
RETURNS TRIGGER AS $$
DECLARE
    canonical_owner_subject_id TEXT;
    canonical_authority_epoch BIGINT;
BEGIN
    SELECT owner_subject_id, authority_epoch
    INTO canonical_owner_subject_id, canonical_authority_epoch
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'publication vault does not exist';
    END IF;
    IF NEW.owner_subject_id IS DISTINCT FROM canonical_owner_subject_id THEN
        RAISE EXCEPTION 'publication owner does not match vault owner';
    END IF;
    IF NEW.authority_epoch IS DISTINCT FROM canonical_authority_epoch THEN
        RAISE EXCEPTION 'publication authority epoch is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_publications_validate_owner
BEFORE INSERT OR UPDATE OF vault_id, owner_subject_id, authority_epoch
ON publication.publications
FOR EACH ROW EXECUTE FUNCTION publication.validate_publication_owner();

CREATE TABLE publication.publication_versions (
    id UUID PRIMARY KEY,
    publication_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    pinned_memory_version_id UUID NOT NULL,
    version_number BIGINT NOT NULL CHECK (version_number >= 1),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    confirmed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (publication_id, version_number),
    UNIQUE (id, publication_id, vault_id),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (pinned_memory_version_id)
        REFERENCES owner_truth.memory_versions(id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION publication.validate_publication_version_scope()
RETURNS TRIGGER AS $$
DECLARE
    memory_vault_id TEXT;
BEGIN
    SELECT vault_id
    INTO memory_vault_id
    FROM owner_truth.memory_versions
    WHERE id = NEW.pinned_memory_version_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'pinned memory version does not exist';
    END IF;
    IF memory_vault_id IS DISTINCT FROM NEW.vault_id THEN
        RAISE EXCEPTION 'pinned memory version belongs to another vault';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_versions_validate_scope
BEFORE INSERT ON publication.publication_versions
FOR EACH ROW EXECUTE FUNCTION publication.validate_publication_version_scope();

CREATE OR REPLACE FUNCTION publication.publication_version_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'publication versions are immutable; create a new version or suspend the publication';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_versions_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.publication_versions
FOR EACH ROW EXECUTE FUNCTION publication.publication_version_append_only();

CREATE TABLE publication.share_grants (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    grantee_subject_hash TEXT NOT NULL CHECK (grantee_subject_hash ~ '^[a-f0-9]{64}$'),
    token_hash TEXT NOT NULL CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    purpose TEXT NOT NULL CHECK (purpose IN ('read')),
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'revoked', 'expired')),
    use_limit INTEGER NOT NULL DEFAULT 1 CHECK (use_limit BETWEEN 1 AND 100),
    use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count BETWEEN 0 AND use_limit),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (token_hash),
    UNIQUE (id, publication_id, vault_id),
    CHECK (expires_at > created_at),
    CHECK (expires_at <= created_at + INTERVAL '7 days'),
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION publication.validate_share_grant_scope()
RETURNS TRIGGER AS $$
DECLARE
    publication_owner_subject_id TEXT;
BEGIN
    SELECT owner_subject_id
    INTO publication_owner_subject_id
    FROM publication.publications
    WHERE id = NEW.publication_id AND vault_id = NEW.vault_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'share grant publication scope does not exist';
    END IF;
    IF NEW.owner_subject_id IS DISTINCT FROM publication_owner_subject_id THEN
        RAISE EXCEPTION 'share grant owner does not match publication owner';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_share_grants_validate_scope
BEFORE INSERT OR UPDATE OF vault_id, publication_id, publication_version_id, owner_subject_id
ON publication.share_grants
FOR EACH ROW EXECUTE FUNCTION publication.validate_share_grant_scope();

CREATE TABLE publication.visitor_sessions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    share_grant_id UUID NOT NULL,
    visitor_subject_hash TEXT NOT NULL CHECK (visitor_subject_hash ~ '^[a-f0-9]{64}$'),
    session_token_hash TEXT NOT NULL CHECK (session_token_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'closed', 'expired', 'revoked')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_token_hash),
    UNIQUE (id, publication_id, vault_id),
    CHECK (expires_at > created_at),
    CHECK (expires_at <= created_at + INTERVAL '7 days'),
    FOREIGN KEY (share_grant_id, publication_id, vault_id)
        REFERENCES publication.share_grants(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE publication.visitor_feedback (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    visitor_session_id UUID NOT NULL,
    feedback_kind TEXT NOT NULL
        CHECK (feedback_kind IN ('safetyReport', 'correctionSignal', 'accessIssue')),
    feedback_hash TEXT NOT NULL CHECK (feedback_hash ~ '^[a-f0-9]{64}$'),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (visitor_session_id, feedback_hash),
    CHECK (expires_at > created_at),
    FOREIGN KEY (visitor_session_id, publication_id, vault_id)
        REFERENCES publication.visitor_sessions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

REVOKE ALL ON TABLE publication.publications FROM PUBLIC;
REVOKE ALL ON TABLE publication.publication_versions FROM PUBLIC;
REVOKE ALL ON TABLE publication.share_grants FROM PUBLIC;
REVOKE ALL ON TABLE publication.visitor_sessions FROM PUBLIC;
REVOKE ALL ON TABLE publication.visitor_feedback FROM PUBLIC;

CREATE INDEX publication_publications_owner_state
    ON publication.publications(vault_id, owner_subject_id, state, updated_at DESC);
CREATE INDEX publication_share_grants_active_expiry
    ON publication.share_grants(publication_id, state, expires_at);
CREATE INDEX publication_visitor_sessions_active_expiry
    ON publication.visitor_sessions(publication_id, state, expires_at);
CREATE INDEX publication_visitor_feedback_expiry
    ON publication.visitor_feedback(expires_at);
