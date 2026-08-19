-- migration:voice_profile_creation_quota
-- Cumulative, non-refundable creation receipts for each authenticated voice
-- subject. Provider retries and lifecycle changes never rewrite this ledger.

CREATE TABLE voice_profile_creation_quotas (
    subject_id TEXT PRIMARY KEY,
    creation_count INTEGER NOT NULL DEFAULT 0 CHECK (creation_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE voice_profile_creation_commands (
    subject_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    voice_profile_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL UNIQUE,
    creation_ordinal INTEGER NOT NULL CHECK (creation_ordinal >= 1),
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (subject_id, command_id),
    UNIQUE (subject_id, voice_profile_id)
);

WITH ranked_profiles AS (
    SELECT
        user_id,
        id,
        updated_at,
        ROW_NUMBER() OVER (
            PARTITION BY user_id
            ORDER BY updated_at ASC, id ASC
        ) AS creation_ordinal
    FROM voice_profiles
)
INSERT INTO voice_profile_creation_commands (
    subject_id,
    command_id,
    voice_profile_id,
    receipt_id,
    creation_ordinal,
    accepted_at
)
SELECT
    user_id,
    'legacy-' || MD5(user_id || ':' || id),
    id,
    'vpcr_' || MD5(user_id || ':legacy:' || id),
    creation_ordinal,
    updated_at
FROM ranked_profiles
ON CONFLICT DO NOTHING;

INSERT INTO voice_profile_creation_quotas (subject_id, creation_count, updated_at)
SELECT user_id, COUNT(*), NOW()
FROM voice_profiles
GROUP BY user_id
ON CONFLICT (subject_id) DO UPDATE SET
    creation_count = GREATEST(
        voice_profile_creation_quotas.creation_count,
        EXCLUDED.creation_count
    ),
    updated_at = NOW();

CREATE INDEX voice_profile_creation_commands_subject_ordinal
    ON voice_profile_creation_commands(subject_id, creation_ordinal DESC);

REVOKE ALL ON TABLE voice_profile_creation_quotas FROM PUBLIC;
REVOKE ALL ON TABLE voice_profile_creation_commands FROM PUBLIC;
