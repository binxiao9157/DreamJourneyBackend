-- Additive opt-in recovery protocol. No historical session is enrolled.
CREATE TABLE IF NOT EXISTS owner_truth.live_recovery_sessions (
    session_id UUID PRIMARY KEY REFERENCES owner_truth.interview_sessions(id) ON DELETE CASCADE,
    vault_id TEXT NOT NULL REFERENCES owner_truth.vaults(vault_id) ON DELETE CASCADE,
    owner_subject_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    protocol TEXT NOT NULL CHECK (protocol = 'live-recovery-v1'),
    coordinates JSONB NOT NULL,
    publication_authorization JSONB,
    publication_authorized_at TIMESTAMPTZ,
    next_scan_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS live_recovery_scan_idx ON owner_truth.live_recovery_sessions(next_scan_at);
CREATE TABLE IF NOT EXISTS owner_truth.live_recovery_snapshots (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES owner_truth.live_recovery_sessions(session_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    snapshot_hash CHAR(64) NOT NULL,
    snapshot JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','published','failed','noChange')),
    source_id UUID,
    failure_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, revision)
);

-- Snapshot evidence never changes when delivery or publication state advances.
CREATE OR REPLACE FUNCTION owner_truth.live_recovery_snapshot_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.revision IS DISTINCT FROM OLD.revision OR NEW.snapshot_hash IS DISTINCT FROM OLD.snapshot_hash
       OR NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (OLD.source_id IS NOT NULL AND NEW.source_id IS DISTINCT FROM OLD.source_id) THEN
        RAISE EXCEPTION 'immutable Live recovery snapshot' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER live_recovery_snapshot_immutable BEFORE UPDATE ON owner_truth.live_recovery_snapshots
FOR EACH ROW EXECUTE FUNCTION owner_truth.live_recovery_snapshot_immutable();
