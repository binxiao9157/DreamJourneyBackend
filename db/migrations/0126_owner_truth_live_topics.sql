-- Theme cards compose immutable V5 members. No historical candidates change.
CREATE TABLE owner_truth.live_memory_topics (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES owner_truth.vaults(vault_id) ON DELETE CASCADE,
    owner_subject_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK(authority_epoch>=0),
    current_version INTEGER NOT NULL CHECK(current_version>0),
    state TEXT NOT NULL CHECK(state IN ('pending','accepted','rejected')),
    linked_topic_id UUID REFERENCES owner_truth.live_memory_topics(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX live_memory_topics_pending ON owner_truth.live_memory_topics(vault_id,state,created_at);
CREATE TABLE owner_truth.live_memory_topic_revisions (
    topic_id UUID NOT NULL REFERENCES owner_truth.live_memory_topics(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK(version>0),
    snapshot_id UUID NOT NULL REFERENCES owner_truth.live_recovery_snapshots(id),
    source_id UUID NOT NULL REFERENCES owner_truth.sources(id),
    proposal_hash CHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY(topic_id,version), UNIQUE(topic_id,snapshot_id)
);
CREATE TABLE owner_truth.live_memory_topic_members (
    topic_id UUID NOT NULL,
    version INTEGER NOT NULL,
    atom_id TEXT NOT NULL,
    candidate_id UUID NOT NULL REFERENCES owner_truth.memory_candidates(id),
    candidate_proposal_hash CHAR(64) NOT NULL,
    PRIMARY KEY(topic_id,version,atom_id),
    UNIQUE(candidate_id),
    FOREIGN KEY(topic_id,version) REFERENCES owner_truth.live_memory_topic_revisions(topic_id,version) ON DELETE CASCADE
);
CREATE OR REPLACE FUNCTION owner_truth.live_topic_revision_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'immutable Live topic revision' USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER live_topic_revision_immutable BEFORE UPDATE ON owner_truth.live_memory_topic_revisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.live_topic_revision_immutable();
CREATE TRIGGER live_topic_member_immutable BEFORE UPDATE ON owner_truth.live_memory_topic_members
FOR EACH ROW EXECUTE FUNCTION owner_truth.live_topic_revision_immutable();

-- A once-written manifest cannot change after its candidate transaction commits.
ALTER TABLE owner_truth.live_recovery_snapshots ADD COLUMN publication_manifest JSONB;
CREATE OR REPLACE FUNCTION owner_truth.live_publication_manifest_immutable() RETURNS trigger AS $$
BEGIN
    IF OLD.publication_manifest IS NOT NULL AND NEW.publication_manifest IS DISTINCT FROM OLD.publication_manifest THEN
        RAISE EXCEPTION 'immutable Live publication manifest' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER live_publication_manifest_immutable BEFORE UPDATE ON owner_truth.live_recovery_snapshots
FOR EACH ROW EXECUTE FUNCTION owner_truth.live_publication_manifest_immutable();
