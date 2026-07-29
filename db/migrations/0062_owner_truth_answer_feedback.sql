-- migration:owner_truth_answer_feedback
--
-- Add a default-off, value-free Owner QA feedback receipt.  This does not
-- expose public Echo feedback or compute product metrics.  It binds one
-- explicit helpful/not-helpful signal to an already immutable Answer receipt,
-- and only permits metric eligibility when every cited MemoryVersion remains
-- current and authorized at receipt time.

CREATE TABLE owner_truth.answer_feedback (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    answer_id UUID NOT NULL,
    helpful BOOLEAN NOT NULL,
    citation_count INTEGER NOT NULL CHECK (citation_count >= 0),
    eligible_citation_count INTEGER NOT NULL CHECK (
        eligible_citation_count >= 0
        AND eligible_citation_count <= citation_count
    ),
    metric_eligible BOOLEAN NOT NULL,
    eligibility_reason TEXT NOT NULL CHECK (
        eligibility_reason IN (
            'eligible',
            'notHelpful',
            'noCitations',
            'projectionUnavailable',
            'citationNotCurrent'
        )
    ),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, answer_id),
    FOREIGN KEY (vault_id, answer_id)
        REFERENCES owner_truth.answers(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_answer_feedback_receipt()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    answer_owner_subject_id TEXT;
    answer_authority_epoch BIGINT;
    total_citation_count INTEGER;
    current_citation_count INTEGER;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT owner_subject_id, authority_epoch
    INTO answer_owner_subject_id, answer_authority_epoch
    FROM owner_truth.answers
    WHERE vault_id = NEW.vault_id AND id = NEW.answer_id;

    SELECT COUNT(*)
    INTO total_citation_count
    FROM owner_truth.answer_citations
    WHERE vault_id = NEW.vault_id AND answer_id = NEW.answer_id;

    SELECT COUNT(*)
    INTO current_citation_count
    FROM owner_truth.answer_citations AS citation
    JOIN owner_truth.memories AS memory
      ON memory.vault_id = citation.vault_id
     AND memory.id = citation.memory_id
    JOIN owner_truth.memory_versions AS memory_version
      ON memory_version.vault_id = citation.vault_id
     AND memory_version.id = citation.memory_version_id
    JOIN owner_truth.sources AS source
      ON source.vault_id = citation.vault_id
     AND source.id = citation.source_id
    WHERE citation.vault_id = NEW.vault_id
      AND citation.answer_id = NEW.answer_id
      AND memory.owner_subject_id = NEW.owner_subject_id
      AND memory.status = 'active'
      AND memory.authority_epoch = NEW.authority_epoch
      AND memory_version.memory_id = citation.memory_id
      AND memory_version.version_number = citation.memory_version
      AND memory_version.is_current = TRUE
      AND memory_version.content_hash = citation.content_hash
      AND memory_version.source_id = citation.source_id
      AND memory_version.source_version = citation.source_version
      AND source.owner_subject_id = NEW.owner_subject_id
      AND source.state = 'active'
      AND source.source_version = citation.source_version
      AND source.authority_epoch = NEW.authority_epoch;

    IF vault_status IS DISTINCT FROM 'active'
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR answer_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR vault_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR NEW.citation_count IS DISTINCT FROM total_citation_count
        OR (
            NEW.eligibility_reason <> 'projectionUnavailable'
            AND NEW.eligible_citation_count IS DISTINCT FROM current_citation_count
        )
    THEN
        RAISE EXCEPTION 'owner truth Answer feedback does not match current Owner authority';
    END IF;

    IF NEW.metric_eligible AND (
        NEW.helpful IS DISTINCT FROM TRUE
        OR NEW.citation_count = 0
        OR NEW.eligible_citation_count <> NEW.citation_count
        OR NEW.eligibility_reason <> 'eligible'
        OR answer_authority_epoch IS DISTINCT FROM NEW.authority_epoch
    ) THEN
        RAISE EXCEPTION 'owner truth Answer feedback metric eligibility is invalid';
    END IF;

    IF NEW.eligibility_reason = 'eligible' AND NOT NEW.metric_eligible THEN
        RAISE EXCEPTION 'owner truth Answer feedback eligible reason requires a metric signal';
    END IF;
    IF NEW.eligibility_reason = 'projectionUnavailable' AND (
        NEW.metric_eligible OR NEW.eligible_citation_count <> 0
    ) THEN
        RAISE EXCEPTION 'owner truth Answer feedback unavailable projection must not become a metric';
    END IF;
    IF NEW.eligibility_reason = 'noCitations' AND (
        NEW.citation_count <> 0
        OR NEW.eligible_citation_count <> 0
        OR NEW.metric_eligible
    ) THEN
        RAISE EXCEPTION 'owner truth Answer feedback no-citation receipt is inconsistent';
    END IF;
    IF NEW.eligibility_reason = 'notHelpful' AND NEW.helpful IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'owner truth Answer feedback not-helpful receipt is inconsistent';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_answer_feedback_validate_insert
BEFORE INSERT ON owner_truth.answer_feedback
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_answer_feedback_receipt();

CREATE OR REPLACE FUNCTION owner_truth.answer_feedback_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth Answer feedback is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_answer_feedback_reject_mutation
BEFORE UPDATE OR DELETE ON owner_truth.answer_feedback
FOR EACH ROW EXECUTE FUNCTION owner_truth.answer_feedback_append_only();

CREATE INDEX owner_truth_answer_feedback_vault_created
    ON owner_truth.answer_feedback(vault_id, created_at DESC);

CREATE INDEX owner_truth_answer_feedback_metric_eligible
    ON owner_truth.answer_feedback(vault_id, metric_eligible, created_at DESC);
