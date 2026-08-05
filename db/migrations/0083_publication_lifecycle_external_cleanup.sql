-- migration:publication_lifecycle_external_cleanup
--
-- P2-S4C separates the already-completed local access-deny boundary from
-- subsequent external cleanup.  It stores only opaque lifecycle/effect
-- coordinates and redacted state evidence.  No provider credential, object
-- key, URL, public text, or raw provider response is persisted here.
--
-- This migration is additive and does not enable a worker.  A lifecycle
-- receipt must first prove local access denial before application code may bind
-- one of these rows to the generic async-effect kernel.

CREATE TABLE publication.lifecycle_external_cleanup_effects (
    effect_id UUID PRIMARY KEY,
    lifecycle_receipt_id UUID NOT NULL
        REFERENCES publication.publication_lifecycle_receipts(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    domain TEXT NOT NULL CHECK (domain IN (
        'publicIndex',
        'cache',
        'digitalHumanSession',
        'providerVoice',
        'objectStorage'
    )),
    operation_id UUID NOT NULL
        REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    provider_effect_id UUID NOT NULL
        REFERENCES async_effects.provider_effects(effect_id) ON DELETE RESTRICT,
    effect_identity_hash TEXT NOT NULL CHECK (effect_identity_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('pending', 'partial', 'completed', 'unsupported')),
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    provider_receipt_present BOOLEAN NOT NULL DEFAULT FALSE,
    provider_receipt_hash TEXT CHECK (provider_receipt_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (lifecycle_receipt_id, domain),
    UNIQUE (operation_id),
    UNIQUE (provider_effect_id),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id) ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id) ON DELETE RESTRICT,
    CHECK (provider_receipt_present = (provider_receipt_hash IS NOT NULL)),
    CHECK (state <> 'completed' OR (provider_receipt_present AND provider_receipt_hash IS NOT NULL))
);

CREATE TABLE publication.lifecycle_external_cleanup_receipts (
    id UUID PRIMARY KEY,
    effect_id UUID NOT NULL
        REFERENCES publication.lifecycle_external_cleanup_effects(effect_id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'partial', 'completed', 'unsupported')),
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    provider_receipt_present BOOLEAN NOT NULL DEFAULT FALSE,
    provider_receipt_hash TEXT CHECK (provider_receipt_hash ~ '^[a-f0-9]{64}$'),
    observation_hash TEXT NOT NULL CHECK (observation_hash ~ '^[a-f0-9]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (effect_id, observation_hash),
    CHECK (provider_receipt_present = (provider_receipt_hash IS NOT NULL)),
    CHECK (state <> 'completed' OR (provider_receipt_present AND provider_receipt_hash IS NOT NULL))
);

CREATE OR REPLACE FUNCTION publication.lifecycle_external_cleanup_receipts_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'publication lifecycle external cleanup receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_lifecycle_external_cleanup_receipts_no_update_or_delete
BEFORE UPDATE OR DELETE ON publication.lifecycle_external_cleanup_receipts
FOR EACH ROW EXECUTE FUNCTION publication.lifecycle_external_cleanup_receipts_append_only();

CREATE INDEX publication_lifecycle_external_cleanup_effects_receipt_idx
    ON publication.lifecycle_external_cleanup_effects(lifecycle_receipt_id, created_at ASC);
CREATE INDEX publication_lifecycle_external_cleanup_effects_state_idx
    ON publication.lifecycle_external_cleanup_effects(state, updated_at ASC);
CREATE INDEX publication_lifecycle_external_cleanup_receipts_effect_idx
    ON publication.lifecycle_external_cleanup_receipts(effect_id, observed_at ASC);

REVOKE ALL ON TABLE publication.lifecycle_external_cleanup_effects FROM PUBLIC;
REVOKE ALL ON TABLE publication.lifecycle_external_cleanup_receipts FROM PUBLIC;
