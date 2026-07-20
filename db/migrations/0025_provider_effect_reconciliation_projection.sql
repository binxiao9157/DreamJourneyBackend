-- migration:provider_effect_reconciliation_projection
--
-- Default-off provider-effect persistence.  The durable base effect keeps its
-- first uncertain outcome; later provider query observations are appended as
-- receipts and projected through a read-only view.  No provider request,
-- retry worker, or public endpoint is enabled by this migration.

ALTER TABLE async_effects.provider_effects
    ADD COLUMN capability TEXT NOT NULL DEFAULT 'legacyObserved'
        CHECK (BTRIM(capability) <> ''),
    ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'legacyObserved'
        CHECK (BTRIM(contract_version) <> ''),
    ADD COLUMN provider_request_id_hash TEXT NOT NULL DEFAULT repeat('0', 64)
        CHECK (provider_request_id_hash ~ '^[0-9a-f]{64}$');

ALTER TABLE async_effects.provider_receipts
    ADD COLUMN reason_code TEXT NOT NULL DEFAULT 'legacyObserved'
        CHECK (BTRIM(reason_code) <> ''),
    ADD COLUMN observation_origin TEXT NOT NULL DEFAULT 'legacyObserved'
        CHECK (BTRIM(observation_origin) <> '');

CREATE INDEX async_effects_provider_effect_operation_capability_idx
    ON async_effects.provider_effects(operation_id, provider_name, capability);

CREATE INDEX async_effects_provider_receipts_projection_idx
    ON async_effects.provider_receipts(effect_id, observation_origin, state, observed_at DESC);

CREATE OR REPLACE VIEW async_effects.provider_effect_reconciliation_projection AS
WITH query_evidence AS (
    SELECT
        effect_id,
        COUNT(*) FILTER (
            WHERE observation_origin = 'providerQuery'
        ) AS query_observation_count,
        COUNT(*) FILTER (
            WHERE observation_origin = 'providerQuery' AND state = 'completed'
        ) AS completed_query_count,
        COUNT(*) FILTER (
            WHERE observation_origin = 'providerQuery' AND state = 'failed'
        ) AS failed_query_count
    FROM async_effects.provider_receipts
    GROUP BY effect_id
)
SELECT
    effect.effect_id,
    effect.state AS recorded_state,
    CASE
        WHEN effect.state <> 'unknown' THEN effect.state
        WHEN COALESCE(evidence.completed_query_count, 0) > 0
             AND COALESCE(evidence.failed_query_count, 0) > 0 THEN 'unknown'
        WHEN COALESCE(evidence.completed_query_count, 0) > 0 THEN 'completed'
        WHEN COALESCE(evidence.failed_query_count, 0) > 0 THEN 'failed'
        ELSE 'unknown'
    END AS effective_state,
    CASE
        WHEN effect.state <> 'unknown' THEN 'notReconciled'
        WHEN COALESCE(evidence.completed_query_count, 0) > 0
             AND COALESCE(evidence.failed_query_count, 0) > 0 THEN 'reconciliationConflict'
        WHEN COALESCE(evidence.completed_query_count, 0) > 0 THEN 'reconciledCompleted'
        WHEN COALESCE(evidence.failed_query_count, 0) > 0 THEN 'reconciledFailed'
        WHEN COALESCE(evidence.query_observation_count, 0) > 0 THEN 'manualReview'
        ELSE 'pendingReconcile'
    END AS reconciliation_status,
    (
        effect.state = 'unknown'
        AND (
            (COALESCE(evidence.completed_query_count, 0) > 0
             AND COALESCE(evidence.failed_query_count, 0) > 0)
            OR (
                COALESCE(evidence.completed_query_count, 0) = 0
                AND COALESCE(evidence.failed_query_count, 0) = 0
            )
        )
    ) AS requires_manual_review
FROM async_effects.provider_effects AS effect
LEFT JOIN query_evidence AS evidence ON evidence.effect_id = effect.effect_id;
