-- migration:voice_dh_blocked_sample_intent_receipts
--
-- Extends the V4 Voice/DH Authority boundary with an append-only,
-- hash-only sample intent receipt. This is not a SampleObject, training
-- command, Provider request, or audio retention path. Every inserted sample
-- intent remains blocked and can only bind an already-blocked self-training
-- profile authority.

CREATE OR REPLACE FUNCTION voice_dh.assert_blocked_sample_intent_parent()
RETURNS TRIGGER AS $$
DECLARE
    profile_vault_id TEXT;
    profile_owner_subject_id TEXT;
    profile_actor_subject_id TEXT;
    profile_subject_id TEXT;
    profile_authority_epoch BIGINT;
    profile_purpose TEXT;
    profile_provider TEXT;
    profile_status TEXT;
BEGIN
    SELECT vault_id, owner_subject_id, actor_subject_id, subject_id, authority_epoch,
        purpose, provider, status
    INTO profile_vault_id, profile_owner_subject_id, profile_actor_subject_id,
        profile_subject_id, profile_authority_epoch, profile_purpose, profile_provider, profile_status
    FROM voice_dh.voice_profile_versions
    WHERE id = NEW.profile_version_id;

    IF NOT FOUND
       OR NEW.vault_id IS DISTINCT FROM profile_vault_id
       OR NEW.owner_subject_id IS DISTINCT FROM profile_owner_subject_id
       OR NEW.actor_subject_id IS DISTINCT FROM profile_actor_subject_id
       OR profile_actor_subject_id IS DISTINCT FROM profile_owner_subject_id
       OR profile_subject_id IS DISTINCT FROM profile_owner_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM profile_authority_epoch
       OR NEW.purpose IS DISTINCT FROM profile_purpose
       OR NEW.provider IS DISTINCT FROM profile_provider
       OR profile_purpose IS DISTINCT FROM 'training'
       OR profile_provider IS DISTINCT FROM 'volcengineVoiceClone'
       OR profile_status IS DISTINCT FROM 'blocked'
       OR NEW.status IS DISTINCT FROM 'blocked'
       OR NEW.sample_format NOT IN ('wav', 'mp3', 'm4a')
       OR NEW.duration_millis < 1
    THEN
        RAISE EXCEPTION 'voice_dh sample intent must bind a blocked self-training profile authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION voice_dh.assert_receipt_resource()
RETURNS TRIGGER AS $$
DECLARE
    resource_vault_id TEXT;
    resource_owner_subject_id TEXT;
    resource_actor_subject_id TEXT;
    resource_authority_epoch BIGINT;
    resource_purpose TEXT;
    resource_policy_version TEXT;
    resource_command_id_hash TEXT;
    resource_payload_hash TEXT;
    resource_status TEXT;
    parent_profile_status TEXT;
BEGIN
    IF NEW.resource_kind = 'voiceProfileVersion' THEN
        SELECT vault_id, owner_subject_id, actor_subject_id, authority_epoch, purpose,
            policy_version, command_id_hash, payload_hash, status
        INTO resource_vault_id, resource_owner_subject_id, resource_actor_subject_id,
            resource_authority_epoch, resource_purpose, resource_policy_version,
            resource_command_id_hash, resource_payload_hash, resource_status
        FROM voice_dh.voice_profile_versions
        WHERE id = NEW.resource_id;
    ELSIF NEW.resource_kind = 'sampleIntent' THEN
        SELECT sample.vault_id, sample.owner_subject_id, sample.actor_subject_id,
            sample.authority_epoch, sample.purpose, sample.policy_version,
            sample.command_id_hash, sample.payload_hash, sample.status, profile.status
        INTO resource_vault_id, resource_owner_subject_id, resource_actor_subject_id,
            resource_authority_epoch, resource_purpose, resource_policy_version,
            resource_command_id_hash, resource_payload_hash, resource_status,
            parent_profile_status
        FROM voice_dh.sample_intents AS sample
        JOIN voice_dh.voice_profile_versions AS profile
            ON profile.id = sample.profile_version_id
        WHERE sample.id = NEW.resource_id;
    ELSE
        RAISE EXCEPTION 'voice_dh G0 receipts support only voiceProfileVersion or sampleIntent';
    END IF;

    IF NOT FOUND
       OR NEW.vault_id IS DISTINCT FROM resource_vault_id
       OR NEW.owner_subject_id IS DISTINCT FROM resource_owner_subject_id
       OR NEW.actor_subject_id IS DISTINCT FROM resource_actor_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM resource_authority_epoch
       OR NEW.purpose IS DISTINCT FROM resource_purpose
       OR NEW.policy_version IS DISTINCT FROM resource_policy_version
       OR NEW.command_id_hash IS DISTINCT FROM resource_command_id_hash
       OR NEW.payload_hash IS DISTINCT FROM resource_payload_hash
       OR NEW.operation IS DISTINCT FROM 'blockedAdmission'
       OR resource_status IS DISTINCT FROM 'blocked'
       OR (NEW.resource_kind = 'sampleIntent' AND parent_profile_status IS DISTINCT FROM 'blocked')
    THEN
        RAISE EXCEPTION 'voice_dh receipt must bind the exact blocked authority record';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS voice_dh_sample_intents_bind_profile ON voice_dh.sample_intents;

CREATE TRIGGER voice_dh_sample_intents_bind_blocked_training_profile
BEFORE INSERT ON voice_dh.sample_intents
FOR EACH ROW EXECUTE FUNCTION voice_dh.assert_blocked_sample_intent_parent();
