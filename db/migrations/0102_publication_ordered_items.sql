-- migration:publication_ordered_items
--
-- Add immutable, ordered item snapshots beneath the existing publication
-- aggregate. Existing single-memory publications are backfilled as item 0;
-- parent rows remain intact so v1 readers and lifecycle triggers keep working.

CREATE TABLE publication.publication_draft_items (
    draft_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    item_index INTEGER NOT NULL CHECK (item_index BETWEEN 0 AND 19),
    memory_version_id UUID NOT NULL,
    memory_content_hash TEXT NOT NULL CHECK (memory_content_hash ~ '^[a-f0-9]{64}$'),
    item_snapshot_hash TEXT NOT NULL CHECK (item_snapshot_hash ~ '^[a-f0-9]{64}$'),
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
    PRIMARY KEY (draft_id, item_index),
    UNIQUE (draft_id, memory_version_id),
    FOREIGN KEY (draft_id, vault_id)
        REFERENCES publication.publication_drafts(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT publication_draft_items_no_direct_identifiers CHECK (
        display_title !~ '1[3-9][0-9]{9}'
        AND display_body !~ '1[3-9][0-9]{9}'
        AND display_title !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_body !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_title !~ '[0-9]{15,18}[0-9Xx]'
        AND display_body !~ '[0-9]{15,18}[0-9Xx]'
    )
);

INSERT INTO publication.publication_draft_items (
    draft_id, vault_id, item_index, memory_version_id, memory_content_hash,
    item_snapshot_hash, display_title, display_body, content_hash,
    preview_title, preview_body, preview_hash, redaction_diff_hash,
    third_party_review_required, ai_disclosure, created_at
)
SELECT
    content.draft_id,
    content.vault_id,
    0,
    version.memory_version_id,
    version.content_hash,
    draft.draft_snapshot_hash,
    content.display_title,
    content.display_body,
    content.content_hash,
    content.preview_title,
    content.preview_body,
    content.preview_hash,
    content.redaction_diff_hash,
    content.third_party_review_required,
    content.ai_disclosure,
    content.created_at
FROM publication.publication_draft_public_contents AS content
JOIN publication.publication_drafts AS draft
  ON draft.id = content.draft_id AND draft.vault_id = content.vault_id
JOIN LATERAL (
    SELECT memory_version_id, content_hash
    FROM publication.publication_draft_memory_versions
    WHERE draft_id = content.draft_id AND vault_id = content.vault_id
    ORDER BY memory_version_id ASC
    LIMIT 1
) AS version ON TRUE;

CREATE OR REPLACE FUNCTION publication.publication_ordered_item_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'publication ordered item snapshots are immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_draft_items_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.publication_draft_items
FOR EACH ROW EXECUTE FUNCTION publication.publication_ordered_item_immutable();

CREATE TABLE publication.publication_version_items (
    publication_version_id UUID NOT NULL,
    publication_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    item_index INTEGER NOT NULL CHECK (item_index BETWEEN 0 AND 19),
    memory_version_id UUID NOT NULL,
    memory_content_hash TEXT NOT NULL CHECK (memory_content_hash ~ '^[a-f0-9]{64}$'),
    item_snapshot_hash TEXT NOT NULL CHECK (item_snapshot_hash ~ '^[a-f0-9]{64}$'),
    display_title TEXT NOT NULL CHECK (CHAR_LENGTH(display_title) BETWEEN 1 AND 120),
    display_body TEXT NOT NULL CHECK (CHAR_LENGTH(display_body) BETWEEN 1 AND 12000),
    ai_disclosure TEXT NOT NULL CHECK (CHAR_LENGTH(ai_disclosure) BETWEEN 1 AND 300),
    projection_hash TEXT NOT NULL CHECK (projection_hash ~ '^[a-f0-9]{64}$'),
    public_citation_hash TEXT NOT NULL CHECK (public_citation_hash ~ '^[a-f0-9]{64}$'),
    redaction_diff_hash TEXT CHECK (redaction_diff_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (publication_version_id, item_index),
    UNIQUE (publication_version_id, memory_version_id),
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TRIGGER publication_version_items_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.publication_version_items
FOR EACH ROW EXECUTE FUNCTION publication.publication_ordered_item_immutable();

CREATE TABLE publication.public_projection_items (
    public_projection_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    publication_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    item_index INTEGER NOT NULL CHECK (item_index BETWEEN 0 AND 19),
    display_title TEXT NOT NULL CHECK (CHAR_LENGTH(display_title) BETWEEN 1 AND 120),
    display_body TEXT NOT NULL CHECK (CHAR_LENGTH(display_body) BETWEEN 1 AND 12000),
    ai_disclosure TEXT NOT NULL CHECK (CHAR_LENGTH(ai_disclosure) BETWEEN 1 AND 300),
    projection_hash TEXT NOT NULL CHECK (projection_hash ~ '^[a-f0-9]{64}$'),
    public_citation_hash TEXT NOT NULL CHECK (public_citation_hash ~ '^[a-f0-9]{64}$'),
    redaction_diff_hash TEXT CHECK (redaction_diff_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (public_projection_id, item_index),
    UNIQUE (publication_version_id, item_index),
    FOREIGN KEY (public_projection_id, publication_id, vault_id)
        REFERENCES publication.public_projections(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    CONSTRAINT publication_public_projection_items_no_direct_identifiers CHECK (
        display_title !~ '1[3-9][0-9]{9}'
        AND display_body !~ '1[3-9][0-9]{9}'
        AND display_title !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_body !~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}'
        AND display_title !~ '[0-9]{15,18}[0-9Xx]'
        AND display_body !~ '[0-9]{15,18}[0-9Xx]'
    )
);

INSERT INTO publication.publication_version_items (
    publication_version_id, publication_id, vault_id, item_index,
    memory_version_id, memory_content_hash, item_snapshot_hash,
    display_title, display_body, ai_disclosure, projection_hash,
    public_citation_hash, redaction_diff_hash, created_at
)
SELECT
    version.id,
    version.publication_id,
    version.vault_id,
    0,
    version.pinned_memory_version_id,
    memory.content_hash,
    version.content_hash,
    projection.display_title,
    projection.display_body,
    projection.ai_disclosure,
    projection.projection_hash,
    projection.public_citation_hash,
    projection.redaction_diff_hash,
    version.created_at
FROM publication.publication_versions AS version
JOIN owner_truth.memory_versions AS memory
  ON memory.vault_id = version.vault_id AND memory.id = version.pinned_memory_version_id
JOIN publication.public_projections AS projection
  ON projection.publication_version_id = version.id
 AND projection.publication_id = version.publication_id
 AND projection.vault_id = version.vault_id;

INSERT INTO publication.public_projection_items (
    public_projection_id, publication_version_id, publication_id, vault_id,
    item_index, display_title, display_body, ai_disclosure, projection_hash,
    public_citation_hash, redaction_diff_hash, created_at
)
SELECT
    projection.id,
    projection.publication_version_id,
    projection.publication_id,
    projection.vault_id,
    0,
    projection.display_title,
    projection.display_body,
    projection.ai_disclosure,
    projection.projection_hash,
    projection.public_citation_hash,
    projection.redaction_diff_hash,
    projection.created_at
FROM publication.public_projections AS projection;

CREATE TRIGGER publication_projection_items_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.public_projection_items
FOR EACH ROW EXECUTE FUNCTION publication.publication_ordered_item_immutable();

CREATE INDEX publication_draft_items_memory_lookup
    ON publication.publication_draft_items(vault_id, memory_version_id);
CREATE INDEX publication_version_items_memory_lookup
    ON publication.publication_version_items(vault_id, memory_version_id);

-- Any source or Memory change must invalidate a publication when any ordered
-- item, not only the compatibility anchor, references that authority row.
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
            SELECT DISTINCT item.publication_version_id AS id
            FROM publication.publication_version_items AS item
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = item.vault_id AND version.id = item.memory_version_id
            WHERE version.vault_id = NEW.vault_id AND version.source_id = NEW.id
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

CREATE OR REPLACE FUNCTION publication.block_projections_for_memory_change()
RETURNS TRIGGER AS $$
DECLARE
    version_row RECORD;
BEGIN
    IF NEW.status IS DISTINCT FROM 'active'
        OR NEW.authority_epoch IS DISTINCT FROM OLD.authority_epoch
    THEN
        FOR version_row IN
            SELECT DISTINCT item.publication_version_id AS id
            FROM publication.publication_version_items AS item
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = item.vault_id AND version.id = item.memory_version_id
            WHERE version.vault_id = NEW.vault_id AND version.memory_id = NEW.id
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

CREATE OR REPLACE FUNCTION publication.block_projections_for_current_version_change()
RETURNS TRIGGER AS $$
DECLARE
    version_row RECORD;
BEGIN
    IF OLD.is_current = TRUE AND NEW.is_current = FALSE THEN
        FOR version_row IN
            SELECT DISTINCT item.publication_version_id AS id
            FROM publication.publication_version_items AS item
            WHERE item.vault_id = OLD.vault_id AND item.memory_version_id = OLD.id
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

REVOKE ALL ON TABLE publication.publication_draft_items FROM PUBLIC;
REVOKE ALL ON TABLE publication.publication_version_items FROM PUBLIC;
REVOKE ALL ON TABLE publication.public_projection_items FROM PUBLIC;
