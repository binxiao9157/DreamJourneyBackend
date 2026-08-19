-- migration:publication_version_revision
--
-- Bind every revision draft to the immutable publication version it was
-- copied from. Confirmation can then reject stale drafts and atomically make
-- exactly one newer public projection active without mutating prior versions.

ALTER TABLE publication.publication_drafts
    ADD COLUMN base_publication_version_id UUID,
    ADD COLUMN target_version_number BIGINT NOT NULL DEFAULT 1
        CHECK (target_version_number >= 1),
    ADD CONSTRAINT publication_drafts_revision_version_shape CHECK (
        (base_publication_version_id IS NULL AND target_version_number = 1)
        OR (base_publication_version_id IS NOT NULL AND target_version_number >= 2)
    ),
    ADD CONSTRAINT publication_drafts_base_version_scope
        FOREIGN KEY (base_publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT;

ALTER TABLE publication.public_projections
    DROP CONSTRAINT public_projections_state_check,
    ADD CONSTRAINT publication_public_projections_state_check
        CHECK (state IN ('active', 'blocked', 'suspended', 'withdrawn', 'superseded'));

CREATE UNIQUE INDEX publication_one_active_projection_per_publication
    ON publication.public_projections(publication_id)
    WHERE state = 'active';

CREATE INDEX publication_drafts_revision_base_lookup
    ON publication.publication_drafts(
        publication_id,
        base_publication_version_id,
        target_version_number,
        state
    );
