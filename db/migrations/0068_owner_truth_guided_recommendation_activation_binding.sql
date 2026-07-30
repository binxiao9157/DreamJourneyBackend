-- migration:owner_truth_guided_recommendation_activation_binding
--
-- The formal default-off guided presentation can activate a rendered prompt
-- without exposing private planner identifiers to the client. This additive
-- binding records only the opaque recommendation-set digest needed for safe
-- replay. It deliberately stores no question text, Owner narrative, evidence,
-- provider output, or conversation payload.

ALTER TABLE owner_truth.knowledge_recommendation_activation_receipts
    ADD COLUMN guided_recommendation_set_id TEXT NULL
        CHECK (
            guided_recommendation_set_id IS NULL
            OR guided_recommendation_set_id ~ '^[a-f0-9]{64}$'
        );
