-- migration:publication_public_projector
--
-- Private checkpoint and candidate metadata for a future one-way projector.
-- This is not a public store: it carries no readable publication copy, source
-- payload, object address, index material, route, gateway, or provider effect.

CREATE TABLE publication.projector_checkpoints (
    publication_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    last_event_sequence BIGINT NOT NULL DEFAULT 0 CHECK (last_event_sequence >= 0),
    state TEXT NOT NULL DEFAULT 'pendingIndex'
        CHECK (state IN ('pendingIndex', 'blocked', 'suspended', 'withdrawn')),
    last_event_hash TEXT CHECK (last_event_hash ~ '^[a-f0-9]{64}$'),
    candidate_projection_hash TEXT CHECK (candidate_projection_hash ~ '^[a-f0-9]{64}$'),
    candidate_public_citation_hash TEXT CHECK (candidate_public_citation_hash ~ '^[a-f0-9]{64}$'),
    policy_hash TEXT CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (publication_id, vault_id),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE publication.public_projection_candidates (
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    event_sequence BIGINT NOT NULL CHECK (event_sequence >= 1),
    event_kind TEXT NOT NULL CHECK (event_kind IN ('published', 'suspended', 'withdrawn')),
    event_hash TEXT NOT NULL CHECK (event_hash ~ '^[a-f0-9]{64}$'),
    version_content_hash TEXT NOT NULL CHECK (version_content_hash ~ '^[a-f0-9]{64}$'),
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    candidate_projection_hash TEXT NOT NULL CHECK (candidate_projection_hash ~ '^[a-f0-9]{64}$'),
    candidate_public_citation_hash TEXT NOT NULL
        CHECK (candidate_public_citation_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'pendingIndex'
        CHECK (state IN ('pendingIndex', 'blocked', 'suspended', 'withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (publication_id, publication_version_id, event_sequence),
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION publication.validate_projector_candidate_scope()
RETURNS TRIGGER AS $$
DECLARE
    version_vault_id TEXT;
BEGIN
    SELECT vault_id
    INTO version_vault_id
    FROM publication.publication_versions
    WHERE id = NEW.publication_version_id
      AND publication_id = NEW.publication_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'publication projector version scope does not exist';
    END IF;
    IF version_vault_id IS DISTINCT FROM NEW.vault_id THEN
        RAISE EXCEPTION 'publication projector version belongs to another vault';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_projector_candidates_validate_scope
BEFORE INSERT OR UPDATE OF publication_id, publication_version_id, vault_id
ON publication.public_projection_candidates
FOR EACH ROW EXECUTE FUNCTION publication.validate_projector_candidate_scope();

REVOKE ALL ON TABLE publication.projector_checkpoints FROM PUBLIC;
REVOKE ALL ON TABLE publication.public_projection_candidates FROM PUBLIC;

CREATE INDEX publication_projector_candidates_state
    ON publication.public_projection_candidates(publication_id, state, event_sequence);
