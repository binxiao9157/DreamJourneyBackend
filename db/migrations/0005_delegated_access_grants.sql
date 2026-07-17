-- migration:delegated_access_grants

CREATE TABLE family_relationships (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL,
    family_member_id TEXT NOT NULL,
    member_subject_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'paused', 'revoked')),
    relationship_epoch BIGINT NOT NULL DEFAULT 1 CHECK (relationship_epoch >= 1),
    grant_epoch BIGINT NOT NULL DEFAULT 0 CHECK (grant_epoch >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, family_member_id),
    UNIQUE (id, vault_id, owner_subject_id, member_subject_id)
);

CREATE INDEX idx_family_relationships_member_subject
    ON family_relationships(member_subject_id, status);

CREATE TABLE access_grants (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    grantor_subject_id TEXT NOT NULL,
    grantee_subject_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('family.persona', 'care.snapshot', 'timeLetter.read')),
    resource_type TEXT NOT NULL CHECK (resource_type IN ('familyMember', 'careSnapshot', 'timeLetter')),
    resource_id TEXT,
    operations JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        jsonb_typeof(operations) = 'array'
        AND operations <@ '["read"]'::jsonb
        AND operations @> '["read"]'::jsonb
    ),
    CHECK (vault_id = grantor_subject_id),
    CHECK (grantor_subject_id <> grantee_subject_id),
    FOREIGN KEY (
        relationship_id,
        vault_id,
        grantor_subject_id,
        grantee_subject_id
    ) REFERENCES family_relationships(
        id,
        vault_id,
        owner_subject_id,
        member_subject_id
    ),
    CHECK (
        (purpose = 'family.persona' AND resource_type = 'familyMember')
        OR (purpose = 'care.snapshot' AND resource_type = 'careSnapshot')
        OR (purpose = 'timeLetter.read' AND resource_type = 'timeLetter')
    ),
    CHECK (
        purpose NOT IN ('family.persona', 'timeLetter.read')
        OR NULLIF(resource_id, '') IS NOT NULL
    ),
    CHECK (expires_at IS NULL OR expires_at > created_at)
);

CREATE INDEX idx_access_grants_relationship
    ON access_grants(relationship_id, status, purpose);

CREATE INDEX idx_access_grants_grantee
    ON access_grants(grantee_subject_id, status, expires_at);

CREATE UNIQUE INDEX idx_access_grants_active_scope
    ON access_grants(
        relationship_id,
        grantee_subject_id,
        purpose,
        resource_type,
        COALESCE(resource_id, '')
    )
    WHERE status = 'active';

CREATE TABLE grant_events (
    id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL REFERENCES access_grants(id),
    relationship_id TEXT NOT NULL REFERENCES family_relationships(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('granted', 'revoked', 'accessed')),
    actor_subject_id TEXT NOT NULL,
    grant_version BIGINT NOT NULL CHECK (grant_version >= 1),
    reason TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_grant_events_grant_time
    ON grant_events(grant_id, occurred_at ASC);

CREATE OR REPLACE FUNCTION reject_grant_event_mutation()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('dreamjourney.grant_event_purge_scope', true)
           = OLD.relationship_id THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'grant_events are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER grant_events_no_update
BEFORE UPDATE OR DELETE ON grant_events
FOR EACH ROW EXECUTE FUNCTION reject_grant_event_mutation();

CREATE OR REPLACE FUNCTION purge_delegated_access_for_subject(target_subject_id TEXT)
RETURNS TABLE (
    deleted_grant_events BIGINT,
    deleted_access_grants BIGINT,
    deleted_relationships BIGINT
)
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    scoped_relationship_id TEXT;
    event_count BIGINT := 0;
    grant_count BIGINT := 0;
    relationship_count BIGINT := 0;
    affected BIGINT := 0;
BEGIN
    IF NULLIF(BTRIM(target_subject_id), '') IS NULL THEN
        RAISE EXCEPTION 'target_subject_id is required';
    END IF;

    FOR scoped_relationship_id IN
        SELECT DISTINCT relationship_id
        FROM public.access_grants
        WHERE grantor_subject_id = target_subject_id
           OR grantee_subject_id = target_subject_id
        UNION
        SELECT id
        FROM public.family_relationships
        WHERE owner_subject_id = target_subject_id
           OR member_subject_id = target_subject_id
    LOOP
        PERFORM set_config(
            'dreamjourney.grant_event_purge_scope',
            scoped_relationship_id,
            true
        );
        DELETE FROM public.grant_events
        WHERE relationship_id = scoped_relationship_id;
        GET DIAGNOSTICS affected = ROW_COUNT;
        event_count := event_count + affected;
    END LOOP;

    PERFORM set_config('dreamjourney.grant_event_purge_scope', '', true);
    DELETE FROM public.access_grants
    WHERE grantor_subject_id = target_subject_id
       OR grantee_subject_id = target_subject_id
       OR relationship_id IN (
           SELECT id
           FROM public.family_relationships
           WHERE owner_subject_id = target_subject_id
              OR member_subject_id = target_subject_id
       );
    GET DIAGNOSTICS grant_count = ROW_COUNT;

    DELETE FROM public.family_relationships
    WHERE owner_subject_id = target_subject_id
       OR member_subject_id = target_subject_id;
    GET DIAGNOSTICS relationship_count = ROW_COUNT;

    RETURN QUERY SELECT event_count, grant_count, relationship_count;
END;
$$ LANGUAGE plpgsql;

REVOKE ALL ON FUNCTION purge_delegated_access_for_subject(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION purge_delegated_access_for_subject(TEXT) TO CURRENT_USER;

-- Existing family records become relationships only. No access grant is inferred.
INSERT INTO family_relationships (
    id,
    vault_id,
    owner_subject_id,
    family_member_id,
    member_subject_id,
    status,
    relationship_epoch,
    grant_epoch,
    created_at,
    updated_at
)
SELECT
    'relationship_legacy_' || id,
    vault_id,
    owner_subject_id,
    id,
    COALESCE(
        NULLIF(payload->>'memberUserId', ''),
        NULLIF(payload->>'acceptedUserId', ''),
        NULLIF(payload->>'recipientUserId', ''),
        'legacy-unverified:' || id
    ),
    CASE
        WHEN payload->>'accessStatus' = 'revoked'
          OR payload->>'invitationStatus' = 'revoked' THEN 'revoked'
        WHEN payload->>'accessStatus' = 'active'
          AND payload->>'invitationStatus' = 'accepted'
          AND COALESCE(
              NULLIF(payload->>'memberUserId', ''),
              NULLIF(payload->>'acceptedUserId', ''),
              NULLIF(payload->>'recipientUserId', '')
          ) IS NOT NULL THEN 'accepted'
        ELSE 'pending'
    END,
    1,
    0,
    created_at,
    NOW()
FROM family_members
WHERE authority_state = 'active'
ON CONFLICT (vault_id, family_member_id) DO NOTHING;
