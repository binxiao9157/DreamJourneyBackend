CREATE OR REPLACE FUNCTION resource_owner_claims(candidate JSONB, resource_kind TEXT)
RETURNS TEXT[] AS $$
    WITH RECURSIVE nodes(node) AS (
        SELECT COALESCE(candidate, '{}'::JSONB)
        UNION ALL
        SELECT children.value
        FROM nodes
        CROSS JOIN LATERAL (
            SELECT value
            FROM jsonb_each(
                CASE WHEN jsonb_typeof(nodes.node) = 'object' THEN nodes.node ELSE '{}'::JSONB END
            )
            UNION ALL
            SELECT value
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(nodes.node) = 'array' THEN nodes.node ELSE '[]'::JSONB END
            )
        ) AS children
    ), claims AS (
        SELECT BTRIM(pair.value #>> '{}') AS claim
        FROM nodes
        CROSS JOIN LATERAL jsonb_each(
            CASE WHEN jsonb_typeof(nodes.node) = 'object' THEN nodes.node ELSE '{}'::JSONB END
        ) AS pair
        WHERE (
            (resource_kind = 'mailbox_letters' AND pair.key IN ('userId', 'recipientUserId'))
            OR (
                resource_kind <> 'mailbox_letters'
                AND pair.key IN (
                    'authenticatedUserId', 'ownerId', 'ownerUserId', 'requesterUserId',
                    'uploadedByUserId', 'uploaderUserId', 'userId'
                )
            )
        )
          AND jsonb_typeof(pair.value) IN ('string', 'number')
    )
    SELECT COALESCE(
        ARRAY_AGG(DISTINCT claim ORDER BY claim) FILTER (WHERE claim <> ''),
        ARRAY[]::TEXT[]
    )
    FROM claims;
$$ LANGUAGE SQL IMMUTABLE;

CREATE TABLE resource_authority_incidents (
    id BIGSERIAL PRIMARY KEY,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    canonical_user_id TEXT NOT NULL,
    observed_owner_claims JSONB NOT NULL,
    incident_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('quarantined', 'resolved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (resource_type, resource_id, incident_code)
);

CREATE OR REPLACE FUNCTION enforce_resource_owner_authority()
RETURNS TRIGGER AS $$
DECLARE
    conflicting_claim TEXT;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.user_id IS DISTINCT FROM OLD.user_id THEN
        RAISE EXCEPTION 'resource owner is immutable';
    END IF;

    NEW.vault_id := NEW.user_id;
    NEW.owner_subject_id := NEW.user_id;
    SELECT claim INTO conflicting_claim
    FROM unnest(resource_owner_claims(NEW.payload, TG_TABLE_NAME)) AS claim
    WHERE claim <> NEW.user_id
    LIMIT 1;
    IF conflicting_claim IS NOT NULL THEN
        RAISE EXCEPTION 'resource payload owner claim conflicts with canonical owner';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        NEW.row_version := OLD.row_version + 1;
    ELSE
        NEW.row_version := COALESCE(NEW.row_version, 1);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

ALTER TABLE memories
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE archive_items
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE mailbox_letters
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE echo_delayed_replies
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE push_device_tokens
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE voice_profiles
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE family_members
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE care_snapshots
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));
ALTER TABLE digital_human_sessions
    ADD COLUMN vault_id TEXT,
    ADD COLUMN owner_subject_id TEXT,
    ADD COLUMN row_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN authority_state TEXT NOT NULL DEFAULT 'active' CHECK (authority_state IN ('active', 'quarantined'));

UPDATE memories SET vault_id = user_id, owner_subject_id = user_id;
UPDATE archive_items SET vault_id = user_id, owner_subject_id = user_id;
UPDATE mailbox_letters SET vault_id = user_id, owner_subject_id = user_id;
UPDATE echo_delayed_replies SET vault_id = user_id, owner_subject_id = user_id;
UPDATE push_device_tokens SET vault_id = user_id, owner_subject_id = user_id;
UPDATE voice_profiles SET vault_id = user_id, owner_subject_id = user_id;
UPDATE family_members SET vault_id = user_id, owner_subject_id = user_id;
UPDATE care_snapshots SET vault_id = user_id, owner_subject_id = user_id;
UPDATE digital_human_sessions SET vault_id = user_id, owner_subject_id = user_id;

CREATE OR REPLACE FUNCTION quarantine_resource_owner_conflicts(
    target_table REGCLASS,
    target_resource_type TEXT
)
RETURNS VOID AS $$
BEGIN
    EXECUTE format(
        'INSERT INTO resource_authority_incidents (
            resource_type, resource_id, canonical_user_id, observed_owner_claims,
            incident_code, status
        )
        SELECT %L, id, user_id, to_jsonb(resource_owner_claims(payload, %L)),
               ''legacyOwnerClaimMismatch'', ''quarantined''
        FROM %s
        WHERE EXISTS (
            SELECT 1 FROM unnest(resource_owner_claims(payload, %L)) AS claim
            WHERE claim <> user_id
        )
        ON CONFLICT (resource_type, resource_id, incident_code) DO NOTHING',
        target_resource_type,
        target_table::TEXT,
        target_table,
        target_table::TEXT
    );
    EXECUTE format(
        'UPDATE %s SET authority_state = ''quarantined''
         WHERE EXISTS (
             SELECT 1 FROM unnest(resource_owner_claims(payload, %L)) AS claim
             WHERE claim <> user_id
         )',
        target_table,
        target_table::TEXT
    );
END;
$$ LANGUAGE PLPGSQL;

SELECT quarantine_resource_owner_conflicts('memories', 'memory');
SELECT quarantine_resource_owner_conflicts('archive_items', 'archiveItem');
SELECT quarantine_resource_owner_conflicts('mailbox_letters', 'mailboxLetter');
SELECT quarantine_resource_owner_conflicts('echo_delayed_replies', 'echoDelayedReply');
SELECT quarantine_resource_owner_conflicts('push_device_tokens', 'pushDeviceToken');
SELECT quarantine_resource_owner_conflicts('voice_profiles', 'voiceProfile');
SELECT quarantine_resource_owner_conflicts('family_members', 'familyMember');
SELECT quarantine_resource_owner_conflicts('care_snapshots', 'careSnapshot');
SELECT quarantine_resource_owner_conflicts('digital_human_sessions', 'digitalHumanSession');
DROP FUNCTION quarantine_resource_owner_conflicts(REGCLASS, TEXT);

ALTER TABLE memories ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE archive_items ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE mailbox_letters ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE echo_delayed_replies ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE push_device_tokens ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE voice_profiles ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE family_members ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE care_snapshots ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;
ALTER TABLE digital_human_sessions ALTER COLUMN vault_id SET NOT NULL, ALTER COLUMN owner_subject_id SET NOT NULL;

CREATE UNIQUE INDEX idx_memories_vault_resource ON memories(vault_id, id);
CREATE UNIQUE INDEX idx_archive_items_vault_resource ON archive_items(vault_id, id);
CREATE UNIQUE INDEX idx_mailbox_letters_vault_resource ON mailbox_letters(vault_id, id);
CREATE UNIQUE INDEX idx_echo_delayed_replies_vault_resource ON echo_delayed_replies(vault_id, id);
CREATE UNIQUE INDEX idx_push_device_tokens_vault_resource ON push_device_tokens(vault_id, id);
CREATE UNIQUE INDEX idx_voice_profiles_vault_resource ON voice_profiles(vault_id, id);
CREATE UNIQUE INDEX idx_family_members_vault_resource ON family_members(vault_id, id);
CREATE UNIQUE INDEX idx_care_snapshots_vault_resource ON care_snapshots(vault_id, id);
CREATE UNIQUE INDEX idx_digital_human_sessions_vault_resource ON digital_human_sessions(vault_id, id);

CREATE TRIGGER memories_owner_authority
BEFORE INSERT OR UPDATE ON memories
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER archive_items_owner_authority
BEFORE INSERT OR UPDATE ON archive_items
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER mailbox_letters_owner_authority
BEFORE INSERT OR UPDATE ON mailbox_letters
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER echo_delayed_replies_owner_authority
BEFORE INSERT OR UPDATE ON echo_delayed_replies
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER push_device_tokens_owner_authority
BEFORE INSERT OR UPDATE ON push_device_tokens
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER voice_profiles_owner_authority
BEFORE INSERT OR UPDATE ON voice_profiles
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER family_members_owner_authority
BEFORE INSERT OR UPDATE ON family_members
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER care_snapshots_owner_authority
BEFORE INSERT OR UPDATE ON care_snapshots
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
CREATE TRIGGER digital_human_sessions_owner_authority
BEFORE INSERT OR UPDATE ON digital_human_sessions
FOR EACH ROW EXECUTE FUNCTION enforce_resource_owner_authority();
