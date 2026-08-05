-- migration:publication_lifecycle_execution
--
-- P2-S4A adds only the local, fail-closed execution boundary for publication
-- withdrawal and third-party objection handling. It makes access denial and
-- audit receipts durable, but deliberately does not claim completion for any
-- public index, cache, CDN, object store, provider, or Digital Human cleanup.

ALTER TABLE publication.publications
    ADD COLUMN IF NOT EXISTS conflict_hold BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE publication.publication_lifecycle_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    action TEXT NOT NULL CHECK (action IN ('withdraw', 'suspend', 'systemSuspend')),
    origin TEXT NOT NULL CHECK (origin IN ('ownerCommand', 'authorityTrigger')),
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    command_id_hash TEXT CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    publication_state TEXT NOT NULL
        CHECK (publication_state IN ('suspended', 'withdrawn')),
    projection_state TEXT NOT NULL
        CHECK (projection_state IN ('blocked', 'suspended', 'withdrawn')),
    conflict_hold BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_grant_count INTEGER NOT NULL DEFAULT 0 CHECK (revoked_grant_count >= 0),
    revoked_visitor_session_count INTEGER NOT NULL DEFAULT 0
        CHECK (revoked_visitor_session_count >= 0),
    access_deny_state TEXT NOT NULL CHECK (access_deny_state = 'completed'),
    public_index_cleanup_state TEXT NOT NULL CHECK (public_index_cleanup_state = 'pending'),
    runtime_cleanup_state TEXT NOT NULL CHECK (runtime_cleanup_state = 'notApplicable'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (publication_version_id, reason_code, origin),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    CHECK (
        (origin = 'ownerCommand' AND command_id_hash IS NOT NULL AND command_payload_hash IS NOT NULL)
        OR (origin = 'authorityTrigger' AND command_id_hash IS NULL AND command_payload_hash IS NULL)
    )
);

CREATE OR REPLACE FUNCTION publication.publication_lifecycle_receipts_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'publication lifecycle receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_lifecycle_receipts_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.publication_lifecycle_receipts
FOR EACH ROW EXECUTE FUNCTION publication.publication_lifecycle_receipts_append_only();

-- Existing Owner Truth authority triggers already block the independent public
-- projection. Expand that local transaction to revoke any active ShareGrant
-- and Visitor session and append a value-free system receipt. External
-- cleanup remains explicitly pending.
CREATE OR REPLACE FUNCTION publication.block_public_projection_version(
    target_publication_version_id UUID,
    target_reason_code TEXT
)
RETURNS VOID AS $$
DECLARE
    target RECORD;
    revoked_grant_count INTEGER;
    revoked_visitor_session_count INTEGER;
    trigger_receipt_id UUID;
BEGIN
    FOR target IN
        SELECT projection.vault_id, projection.publication_id, projection.publication_version_id,
            publication.owner_subject_id, publication.authority_epoch
        FROM publication.public_projections AS projection
        JOIN publication.publications AS publication
          ON publication.id = projection.publication_id
         AND publication.vault_id = projection.vault_id
        WHERE projection.publication_version_id = target_publication_version_id
          AND projection.state = 'active'
        FOR UPDATE OF projection, publication
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
            conflict_hold = conflict_hold OR target_reason_code = 'thirdPartyObjection',
            updated_at = NOW()
        WHERE id = target.publication_id
          AND vault_id = target.vault_id
          AND state IN ('draft', 'confirmed');

        UPDATE publication.visitor_sessions
        SET state = 'revoked',
            updated_at = NOW()
        WHERE vault_id = target.vault_id
          AND publication_id = target.publication_id
          AND publication_version_id = target.publication_version_id
          AND state = 'active';
        GET DIAGNOSTICS revoked_visitor_session_count = ROW_COUNT;

        UPDATE publication.share_grants
        SET state = 'revoked',
            revoked_at = NOW(),
            updated_at = NOW()
        WHERE vault_id = target.vault_id
          AND publication_id = target.publication_id
          AND publication_version_id = target.publication_version_id
          AND state = 'active';
        GET DIAGNOSTICS revoked_grant_count = ROW_COUNT;

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

        trigger_receipt_id := (
            SUBSTRING(
                md5(
                    'publication-lifecycle-trigger:' || target.publication_version_id::text
                    || ':' || target_reason_code
                ), 1, 8
            ) || '-' || SUBSTRING(
                md5(
                    'publication-lifecycle-trigger:' || target.publication_version_id::text
                    || ':' || target_reason_code
                ), 9, 4
            ) || '-' || SUBSTRING(
                md5(
                    'publication-lifecycle-trigger:' || target.publication_version_id::text
                    || ':' || target_reason_code
                ), 13, 4
            ) || '-' || SUBSTRING(
                md5(
                    'publication-lifecycle-trigger:' || target.publication_version_id::text
                    || ':' || target_reason_code
                ), 17, 4
            ) || '-' || SUBSTRING(
                md5(
                    'publication-lifecycle-trigger:' || target.publication_version_id::text
                    || ':' || target_reason_code
                ), 21, 12
        ))::UUID;

        INSERT INTO publication.publication_lifecycle_receipts (
            id, vault_id, publication_id, publication_version_id, owner_subject_id,
            authority_epoch, action, origin, reason_code, publication_state,
            projection_state, conflict_hold, revoked_grant_count,
            revoked_visitor_session_count, access_deny_state,
            public_index_cleanup_state, runtime_cleanup_state
        ) VALUES (
            trigger_receipt_id,
            target.vault_id,
            target.publication_id,
            target.publication_version_id,
            target.owner_subject_id,
            target.authority_epoch,
            'systemSuspend',
            'authorityTrigger',
            target_reason_code,
            'suspended',
            'blocked',
            target_reason_code = 'thirdPartyObjection',
            revoked_grant_count,
            revoked_visitor_session_count,
            'completed',
            'pending',
            'notApplicable'
        )
        ON CONFLICT (publication_version_id, reason_code, origin) DO NOTHING;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

REVOKE ALL ON TABLE publication.publication_lifecycle_receipts FROM PUBLIC;

CREATE INDEX publication_lifecycle_receipts_owner_lookup
    ON publication.publication_lifecycle_receipts(vault_id, owner_subject_id, created_at DESC);
CREATE INDEX publication_lifecycle_receipts_command_lookup
    ON publication.publication_lifecycle_receipts(vault_id, command_id_hash)
    WHERE command_id_hash IS NOT NULL;
