-- migration:owner_truth_media_processing
--
-- Stage 2 turns verified private SourceObjects into bounded processing work.
-- Result rows retain only hashes, processor identity, ownership and lifecycle
-- evidence. Parsed text remains private Source content and never belongs in
-- this processing ledger or an async-effect payload.

ALTER TABLE owner_truth.media_source_objects
    ADD COLUMN external_processing_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN processing_generation BIGINT NOT NULL DEFAULT 0
        CHECK (processing_generation >= 0),
    ADD COLUMN derived_source_id UUID,
    ADD COLUMN last_processing_result_id UUID;

CREATE TABLE owner_truth.media_source_object_processing_results (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    source_object_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    processor_id TEXT NOT NULL CHECK (BTRIM(processor_id) <> ''),
    processor_version TEXT NOT NULL CHECK (BTRIM(processor_version) <> ''),
    state TEXT NOT NULL CHECK (state IN ('succeeded', 'retryableFailed', 'failed', 'notApplicable')),
    processing_generation BIGINT NOT NULL CHECK (processing_generation >= 0),
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    result_hash TEXT NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
    extracted_text_sha256 TEXT CHECK (
        extracted_text_sha256 IS NULL OR extracted_text_sha256 ~ '^[0-9a-f]{64}$'
    ),
    derived_source_id UUID,
    failure_code TEXT,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (
        vault_id, source_object_id, processor_id, processor_version,
        processing_generation, attempt
    ),
    FOREIGN KEY (vault_id, source_object_id)
        REFERENCES owner_truth.media_source_objects(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    CHECK (
        (state = 'succeeded' AND extracted_text_sha256 IS NOT NULL AND derived_source_id IS NOT NULL AND failure_code IS NULL)
        OR (state = 'retryableFailed' AND extracted_text_sha256 IS NULL AND derived_source_id IS NULL AND failure_code IS NOT NULL)
        OR (state = 'failed' AND extracted_text_sha256 IS NULL AND derived_source_id IS NULL AND failure_code IS NOT NULL)
        OR (state = 'notApplicable' AND extracted_text_sha256 IS NULL AND derived_source_id IS NULL AND failure_code IS NULL)
    )
);

ALTER TABLE owner_truth.media_source_objects
    ADD CONSTRAINT owner_truth_media_source_objects_derived_source_fk
    FOREIGN KEY (vault_id, derived_source_id)
    REFERENCES owner_truth.sources(vault_id, id)
    ON DELETE RESTRICT;

ALTER TABLE owner_truth.media_source_objects
    ADD CONSTRAINT owner_truth_media_source_objects_last_result_fk
    FOREIGN KEY (last_processing_result_id)
    REFERENCES owner_truth.media_source_object_processing_results(id)
    ON DELETE RESTRICT;

CREATE INDEX owner_truth_media_processing_results_source_idx
    ON owner_truth.media_source_object_processing_results(vault_id, source_object_id, completed_at DESC);

CREATE INDEX owner_truth_media_processing_results_owner_state_idx
    ON owner_truth.media_source_object_processing_results(vault_id, owner_subject_id, state, completed_at DESC);
