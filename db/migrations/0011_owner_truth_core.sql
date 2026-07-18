-- migration:owner_truth_core
--
-- Owner Truth V1 is intentionally isolated from the legacy public.memories
-- compatibility table.  New relations live in owner_truth so this expand-only
-- migration does not change existing Archive, KBLite, Echo, or API traffic.

CREATE SCHEMA IF NOT EXISTS owner_truth;

CREATE TABLE owner_truth.vaults (
    vault_id TEXT PRIMARY KEY CHECK (BTRIM(vault_id) <> ''),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE owner_truth.sources (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL,
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('text', 'archiveItem', 'conversation', 'import')),
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'redacted', 'deleted')),
    source_version BIGINT NOT NULL DEFAULT 1 CHECK (source_version >= 1),
    content_hash TEXT NOT NULL CHECK (BTRIM(content_hash) <> ''),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.source_links (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    source_id UUID NOT NULL,
    linked_source_id UUID NOT NULL,
    relation_type TEXT NOT NULL
        CHECK (relation_type IN ('derivedFrom', 'duplicateOf', 'references')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (source_id <> linked_source_id),
    UNIQUE (vault_id, source_id, linked_source_id, relation_type),
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, linked_source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.extraction_results (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    source_id UUID NOT NULL,
    source_version BIGINT NOT NULL CHECK (source_version >= 1),
    extractor_id TEXT NOT NULL CHECK (BTRIM(extractor_id) <> ''),
    schema_version TEXT NOT NULL CHECK (BTRIM(schema_version) <> ''),
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'succeeded', 'failed', 'quarantined')),
    result_hash TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(payload) = 'object'),
    failure_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.memory_candidates (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL,
    source_id UUID NOT NULL,
    extraction_result_id UUID,
    candidate_kind TEXT NOT NULL
        CHECK (candidate_kind IN ('experience', 'knowledge', 'emotion')),
    perspective_type TEXT NOT NULL
        CHECK (perspective_type IN ('firstPerson', 'reported', 'inferred')),
    epistemic_status TEXT NOT NULL
        CHECK (epistemic_status IN ('observed', 'recalled', 'reported', 'inferred', 'uncertain')),
    sensitivity TEXT NOT NULL DEFAULT 'standard'
        CHECK (sensitivity IN ('standard', 'sensitive', 'restricted')),
    decision_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (decision_status IN ('pending', 'accepted', 'rejected', 'corrected', 'invalidated')),
    quarantine_code TEXT,
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    content_hash TEXT NOT NULL CHECK (BTRIM(content_hash) <> ''),
    payload_schema_version TEXT NOT NULL CHECK (BTRIM(payload_schema_version) <> ''),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, extraction_result_id)
        REFERENCES owner_truth.extraction_results(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (quarantine_code IS NULL OR decision_status = 'pending')
);

CREATE TABLE owner_truth.decision_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    candidate_id UUID NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('accepted', 'rejected', 'corrected', 'invalidated')),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    rationale_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, candidate_id),
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.memories (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL,
    source_id UUID,
    source_version BIGINT CHECK (source_version IS NULL OR source_version >= 1),
    memory_kind TEXT NOT NULL
        CHECK (memory_kind IN ('experience', 'knowledge', 'emotion')),
    perspective_type TEXT NOT NULL
        CHECK (perspective_type IN ('firstPerson', 'reported', 'inferred')),
    epistemic_status TEXT NOT NULL
        CHECK (epistemic_status IN ('observed', 'recalled', 'reported', 'inferred', 'uncertain')),
    sensitivity TEXT NOT NULL DEFAULT 'standard'
        CHECK (sensitivity IN ('standard', 'sensitive', 'restricted')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'redacted', 'invalidated')),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    content_hash TEXT NOT NULL CHECK (BTRIM(content_hash) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT,
    CHECK ((source_id IS NULL AND source_version IS NULL) OR (source_id IS NOT NULL AND source_version IS NOT NULL))
);

CREATE TABLE owner_truth.memory_versions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    memory_id UUID NOT NULL,
    version_number BIGINT NOT NULL CHECK (version_number >= 1),
    is_current BOOLEAN NOT NULL DEFAULT FALSE,
    schema_version TEXT NOT NULL CHECK (BTRIM(schema_version) <> ''),
    content_hash TEXT NOT NULL CHECK (BTRIM(content_hash) <> ''),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, memory_id, version_number),
    FOREIGN KEY (vault_id, memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX owner_truth_memory_versions_one_current
    ON owner_truth.memory_versions(vault_id, memory_id)
    WHERE is_current;

CREATE TABLE owner_truth.memory_relations (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    from_memory_id UUID NOT NULL,
    to_memory_id UUID NOT NULL,
    relation_type TEXT NOT NULL
        CHECK (relation_type IN ('supports', 'contradicts', 'derivesFrom', 'references', 'contains')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (from_memory_id <> to_memory_id),
    UNIQUE (vault_id, from_memory_id, to_memory_id, relation_type),
    FOREIGN KEY (vault_id, from_memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, to_memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.correction_links (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    superseded_memory_id UUID NOT NULL,
    replacement_memory_id UUID NOT NULL,
    decision_receipt_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (superseded_memory_id <> replacement_memory_id),
    UNIQUE (vault_id, superseded_memory_id, replacement_memory_id),
    FOREIGN KEY (vault_id, superseded_memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, replacement_memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, decision_receipt_id)
        REFERENCES owner_truth.decision_receipts(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.bind_vault_authority()
RETURNS TRIGGER AS $$
DECLARE
    canonical_owner_subject_id TEXT;
    canonical_authority_epoch BIGINT;
BEGIN
    SELECT owner_subject_id, authority_epoch
    INTO canonical_owner_subject_id, canonical_authority_epoch
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth vault does not exist';
    END IF;
    IF NEW.owner_subject_id IS DISTINCT FROM canonical_owner_subject_id THEN
        RAISE EXCEPTION 'owner truth record owner does not match vault owner';
    END IF;
    IF NEW.authority_epoch IS DISTINCT FROM canonical_authority_epoch THEN
        RAISE EXCEPTION 'owner truth record authority epoch is stale';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        NEW.row_version := OLD.row_version + 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_sources_bind_vault_authority
BEFORE INSERT OR UPDATE ON owner_truth.sources
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_candidates_bind_vault_authority
BEFORE INSERT OR UPDATE ON owner_truth.memory_candidates
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_memories_bind_vault_authority
BEFORE INSERT OR UPDATE ON owner_truth.memories
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE OR REPLACE FUNCTION owner_truth.guard_terminal_candidate_decision()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.decision_status <> 'pending' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'owner truth terminal candidate decision is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_candidates_terminal_decision
BEFORE UPDATE ON owner_truth.memory_candidates
FOR EACH ROW EXECUTE FUNCTION owner_truth.guard_terminal_candidate_decision();

CREATE OR REPLACE FUNCTION owner_truth.decision_receipts_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth decision receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION owner_truth.validate_decision_receipt()
RETURNS TRIGGER AS $$
DECLARE
    candidate_decision TEXT;
BEGIN
    SELECT decision_status
    INTO candidate_decision
    FROM owner_truth.memory_candidates
    WHERE vault_id = NEW.vault_id AND id = NEW.candidate_id;

    IF candidate_decision IS DISTINCT FROM NEW.decision THEN
        RAISE EXCEPTION 'owner truth decision receipt must match terminal candidate decision';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_decision_receipts_validate_candidate
BEFORE INSERT ON owner_truth.decision_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_decision_receipt();

CREATE TRIGGER owner_truth_decision_receipts_no_update
BEFORE UPDATE ON owner_truth.decision_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.decision_receipts_append_only();

CREATE TRIGGER owner_truth_decision_receipts_no_delete
BEFORE DELETE ON owner_truth.decision_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.decision_receipts_append_only();

CREATE OR REPLACE FUNCTION owner_truth.assert_exactly_one_current_version(
    target_vault_id TEXT,
    target_memory_id UUID
)
RETURNS VOID AS $$
DECLARE
    current_count INTEGER;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM owner_truth.memories
        WHERE vault_id = target_vault_id AND id = target_memory_id
    ) THEN
        RETURN;
    END IF;

    SELECT COUNT(*)
    INTO current_count
    FROM owner_truth.memory_versions
    WHERE vault_id = target_vault_id
      AND memory_id = target_memory_id
      AND is_current;

    IF current_count <> 1 THEN
        RAISE EXCEPTION 'owner truth memory must have exactly one current version';
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION owner_truth.require_memory_current_version()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM owner_truth.assert_exactly_one_current_version(NEW.vault_id, NEW.id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION owner_truth.require_memory_version_current_integrity()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM owner_truth.assert_exactly_one_current_version(OLD.vault_id, OLD.memory_id);
    ELSE
        PERFORM owner_truth.assert_exactly_one_current_version(NEW.vault_id, NEW.memory_id);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER owner_truth_memory_requires_current_version
AFTER INSERT OR UPDATE ON owner_truth.memories
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION owner_truth.require_memory_current_version();

CREATE CONSTRAINT TRIGGER owner_truth_memory_version_current_integrity
AFTER INSERT OR UPDATE OR DELETE ON owner_truth.memory_versions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION owner_truth.require_memory_version_current_integrity();

CREATE OR REPLACE FUNCTION owner_truth.reject_memory_relation_cycle()
RETURNS TRIGGER AS $$
DECLARE
    creates_cycle BOOLEAN;
BEGIN
    WITH RECURSIVE reachable(memory_id, path) AS (
        SELECT relation.to_memory_id,
               ARRAY[relation.from_memory_id, relation.to_memory_id]
        FROM owner_truth.memory_relations AS relation
        WHERE relation.vault_id = NEW.vault_id
          AND relation.from_memory_id = NEW.to_memory_id
          AND (TG_OP <> 'UPDATE' OR relation.id <> NEW.id)
        UNION ALL
        SELECT relation.to_memory_id,
               reachable.path || relation.to_memory_id
        FROM owner_truth.memory_relations AS relation
        JOIN reachable
          ON relation.vault_id = NEW.vault_id
         AND relation.from_memory_id = reachable.memory_id
        WHERE NOT relation.to_memory_id = ANY(reachable.path)
          AND (TG_OP <> 'UPDATE' OR relation.id <> NEW.id)
    )
    SELECT EXISTS (
        SELECT 1
        FROM reachable
        WHERE memory_id = NEW.from_memory_id
    )
    INTO creates_cycle;

    IF creates_cycle THEN
        RAISE EXCEPTION 'owner truth memory relation would create a cycle';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_relations_no_cycle
BEFORE INSERT OR UPDATE ON owner_truth.memory_relations
FOR EACH ROW EXECUTE FUNCTION owner_truth.reject_memory_relation_cycle();

CREATE INDEX owner_truth_sources_vault_state
    ON owner_truth.sources(vault_id, state, created_at DESC);
CREATE INDEX owner_truth_candidates_vault_decision
    ON owner_truth.memory_candidates(vault_id, decision_status, created_at DESC);
CREATE INDEX owner_truth_extractions_vault_source
    ON owner_truth.extraction_results(vault_id, source_id, created_at DESC);
CREATE INDEX owner_truth_memory_relations_vault_from
    ON owner_truth.memory_relations(vault_id, from_memory_id);
