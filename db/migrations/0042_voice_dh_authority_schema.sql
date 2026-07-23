-- migration:voice_dh_authority_schema
--
-- Voice clone and Digital Human runtime state currently lives in legacy
-- profile/session adapters.  This additive schema establishes a separate,
-- default-deny Authority boundary for the future V4 path.  It deliberately
-- records only opaque identifiers and hashes: no audio, text, object URL,
-- provider speaker ID, session credential, or provider token belongs here.

CREATE SCHEMA IF NOT EXISTS voice_dh;

CREATE OR REPLACE FUNCTION voice_dh.bind_vault_authority()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND
       OR vault_status IS DISTINCT FROM 'active'
       OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
    THEN
        RAISE EXCEPTION 'voice_dh record must bind the current active Vault authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION voice_dh.assert_profile_parent()
RETURNS TRIGGER AS $$
DECLARE
    profile_vault_id TEXT;
    profile_owner_subject_id TEXT;
    profile_authority_epoch BIGINT;
    profile_purpose TEXT;
    profile_provider TEXT;
BEGIN
    SELECT vault_id, owner_subject_id, authority_epoch, purpose, provider
    INTO profile_vault_id, profile_owner_subject_id, profile_authority_epoch,
        profile_purpose, profile_provider
    FROM voice_dh.voice_profile_versions
    WHERE id = NEW.profile_version_id;

    IF NOT FOUND
       OR NEW.vault_id IS DISTINCT FROM profile_vault_id
       OR NEW.owner_subject_id IS DISTINCT FROM profile_owner_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM profile_authority_epoch
       OR NEW.purpose IS DISTINCT FROM profile_purpose
       OR NEW.provider IS DISTINCT FROM profile_provider
    THEN
        RAISE EXCEPTION 'voice_dh child record must bind its parent profile authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION voice_dh.append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'voice_dh authority records are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TABLE voice_dh.voice_profile_versions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    subject_id TEXT NOT NULL CHECK (BTRIM(subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    profile_id TEXT NOT NULL CHECK (BTRIM(profile_id) <> ''),
    version_number BIGINT NOT NULL CHECK (version_number >= 1),
    purpose TEXT NOT NULL CHECK (purpose IN (
        'training', 'preview', 'private_synthesis', 'memoir',
        'dh_audio_drive', 'visitor_public_voice'
    )),
    provider TEXT NOT NULL CHECK (provider IN ('volcengineVoiceClone', 'tencentDigitalHuman')),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    consent_receipt_hash TEXT NOT NULL CHECK (consent_receipt_hash ~ '^[a-f0-9]{64}$'),
    purpose_grant_hash TEXT NOT NULL CHECK (purpose_grant_hash ~ '^[a-f0-9]{64}$'),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('blocked', 'notAccepted', 'revoked', 'legacyObserved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, profile_id, version_number),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE INDEX voice_dh_profile_versions_owner_created
    ON voice_dh.voice_profile_versions(vault_id, owner_subject_id, created_at DESC);

CREATE OR REPLACE FUNCTION voice_dh.assert_receipt_resource()
RETURNS TRIGGER AS $$
DECLARE
    profile_vault_id TEXT;
    profile_owner_subject_id TEXT;
    profile_actor_subject_id TEXT;
    profile_authority_epoch BIGINT;
    profile_purpose TEXT;
    profile_policy_version TEXT;
    profile_command_id_hash TEXT;
    profile_payload_hash TEXT;
BEGIN
    -- G0 intentionally admits receipts only for the single profile record it
    -- writes. Later resource kinds need their own explicit authority rule.
    IF NEW.resource_kind <> 'voiceProfileVersion' THEN
        RAISE EXCEPTION 'voice_dh G0 receipts only support voiceProfileVersion';
    END IF;

    SELECT vault_id, owner_subject_id, actor_subject_id, authority_epoch, purpose,
        policy_version, command_id_hash, payload_hash
    INTO profile_vault_id, profile_owner_subject_id, profile_actor_subject_id,
        profile_authority_epoch, profile_purpose, profile_policy_version,
        profile_command_id_hash, profile_payload_hash
    FROM voice_dh.voice_profile_versions
    WHERE id = NEW.resource_id;

    IF NOT FOUND
       OR NEW.vault_id IS DISTINCT FROM profile_vault_id
       OR NEW.owner_subject_id IS DISTINCT FROM profile_owner_subject_id
       OR NEW.actor_subject_id IS DISTINCT FROM profile_actor_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM profile_authority_epoch
       OR NEW.purpose IS DISTINCT FROM profile_purpose
       OR NEW.policy_version IS DISTINCT FROM profile_policy_version
       OR NEW.command_id_hash IS DISTINCT FROM profile_command_id_hash
       OR NEW.payload_hash IS DISTINCT FROM profile_payload_hash
    THEN
        RAISE EXCEPTION 'voice_dh receipt must bind the exact profile authority record';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE voice_dh.sample_intents (
    id UUID PRIMARY KEY,
    profile_version_id UUID NOT NULL REFERENCES voice_dh.voice_profile_versions(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    purpose TEXT NOT NULL CHECK (purpose IN (
        'training', 'preview', 'private_synthesis', 'memoir',
        'dh_audio_drive', 'visitor_public_voice'
    )),
    provider TEXT NOT NULL CHECK (provider IN ('volcengineVoiceClone', 'tencentDigitalHuman')),
    sample_hash TEXT NOT NULL CHECK (sample_hash ~ '^[a-f0-9]{64}$'),
    sample_format TEXT NOT NULL CHECK (BTRIM(sample_format) <> ''),
    duration_millis BIGINT NOT NULL CHECK (duration_millis >= 0),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('blocked', 'notAccepted', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE voice_dh.generated_audio_intents (
    id UUID PRIMARY KEY,
    profile_version_id UUID NOT NULL REFERENCES voice_dh.voice_profile_versions(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    purpose TEXT NOT NULL CHECK (purpose IN (
        'training', 'preview', 'private_synthesis', 'memoir',
        'dh_audio_drive', 'visitor_public_voice'
    )),
    provider TEXT NOT NULL CHECK (provider IN ('volcengineVoiceClone', 'tencentDigitalHuman')),
    text_hash TEXT NOT NULL CHECK (text_hash ~ '^[a-f0-9]{64}$'),
    output_mode TEXT NOT NULL CHECK (output_mode IN ('preview', 'private_synthesis', 'dh_audio_drive')),
    expires_at TIMESTAMPTZ,
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('blocked', 'notGenerated', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE voice_dh.dh_session_admissions (
    id UUID PRIMARY KEY,
    profile_version_id UUID REFERENCES voice_dh.voice_profile_versions(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    purpose TEXT NOT NULL CHECK (purpose IN (
        'training', 'preview', 'private_synthesis', 'memoir',
        'dh_audio_drive', 'visitor_public_voice'
    )),
    provider TEXT NOT NULL CHECK (provider = 'tencentDigitalHuman'),
    asset_hash TEXT NOT NULL CHECK (asset_hash ~ '^[a-f0-9]{64}$'),
    session_intent_hash TEXT NOT NULL CHECK (session_intent_hash ~ '^[a-f0-9]{64}$'),
    expires_at TIMESTAMPTZ,
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('blocked', 'notAccepted', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE voice_dh.authority_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    resource_kind TEXT NOT NULL CHECK (resource_kind IN (
        'voiceProfileVersion', 'sampleIntent', 'generatedAudioIntent', 'dhSessionAdmission'
    )),
    resource_id UUID NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN (
        'training', 'preview', 'private_synthesis', 'memoir',
        'dh_audio_drive', 'visitor_public_voice'
    )),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    operation TEXT NOT NULL CHECK (operation IN ('blockedAdmission', 'revocationObserved')),
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, resource_kind, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE INDEX voice_dh_authority_receipts_owner_created
    ON voice_dh.authority_receipts(vault_id, owner_subject_id, created_at DESC);

CREATE TRIGGER voice_dh_profile_versions_bind_vault_authority
BEFORE INSERT ON voice_dh.voice_profile_versions
FOR EACH ROW EXECUTE FUNCTION voice_dh.bind_vault_authority();

CREATE TRIGGER voice_dh_sample_intents_bind_vault_authority
BEFORE INSERT ON voice_dh.sample_intents
FOR EACH ROW EXECUTE FUNCTION voice_dh.bind_vault_authority();

CREATE TRIGGER voice_dh_sample_intents_bind_profile
BEFORE INSERT ON voice_dh.sample_intents
FOR EACH ROW EXECUTE FUNCTION voice_dh.assert_profile_parent();

CREATE TRIGGER voice_dh_generated_audio_intents_bind_vault_authority
BEFORE INSERT ON voice_dh.generated_audio_intents
FOR EACH ROW EXECUTE FUNCTION voice_dh.bind_vault_authority();

CREATE TRIGGER voice_dh_generated_audio_intents_bind_profile
BEFORE INSERT ON voice_dh.generated_audio_intents
FOR EACH ROW EXECUTE FUNCTION voice_dh.assert_profile_parent();

CREATE TRIGGER voice_dh_dh_session_admissions_bind_vault_authority
BEFORE INSERT ON voice_dh.dh_session_admissions
FOR EACH ROW EXECUTE FUNCTION voice_dh.bind_vault_authority();

CREATE TRIGGER voice_dh_dh_session_admissions_bind_profile
BEFORE INSERT ON voice_dh.dh_session_admissions
FOR EACH ROW WHEN (NEW.profile_version_id IS NOT NULL)
EXECUTE FUNCTION voice_dh.assert_profile_parent();

CREATE TRIGGER voice_dh_authority_receipts_bind_vault_authority
BEFORE INSERT ON voice_dh.authority_receipts
FOR EACH ROW EXECUTE FUNCTION voice_dh.bind_vault_authority();

CREATE TRIGGER voice_dh_authority_receipts_bind_resource
BEFORE INSERT ON voice_dh.authority_receipts
FOR EACH ROW EXECUTE FUNCTION voice_dh.assert_receipt_resource();

CREATE TRIGGER voice_dh_profile_versions_no_update
BEFORE UPDATE OR DELETE ON voice_dh.voice_profile_versions
FOR EACH ROW EXECUTE FUNCTION voice_dh.append_only();

CREATE TRIGGER voice_dh_sample_intents_no_update
BEFORE UPDATE OR DELETE ON voice_dh.sample_intents
FOR EACH ROW EXECUTE FUNCTION voice_dh.append_only();

CREATE TRIGGER voice_dh_generated_audio_intents_no_update
BEFORE UPDATE OR DELETE ON voice_dh.generated_audio_intents
FOR EACH ROW EXECUTE FUNCTION voice_dh.append_only();

CREATE TRIGGER voice_dh_dh_session_admissions_no_update
BEFORE UPDATE OR DELETE ON voice_dh.dh_session_admissions
FOR EACH ROW EXECUTE FUNCTION voice_dh.append_only();

CREATE TRIGGER voice_dh_authority_receipts_no_update
BEFORE UPDATE OR DELETE ON voice_dh.authority_receipts
FOR EACH ROW EXECUTE FUNCTION voice_dh.append_only();
