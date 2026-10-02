-- Same immutable candidate may remain in successive revisions of one theme.
ALTER TABLE owner_truth.live_memory_topic_members DROP CONSTRAINT live_memory_topic_members_candidate_id_key;
CREATE UNIQUE INDEX live_memory_topic_member_revision_candidate ON owner_truth.live_memory_topic_members(topic_id,version,candidate_id);
