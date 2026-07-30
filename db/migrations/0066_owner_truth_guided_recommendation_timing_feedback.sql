-- migration:owner_truth_guided_recommendation_timing_feedback
--
-- The formal, default-off guided presentation may now record one explicit
-- ``defer`` + ``timing`` action.  It stays an append-only, value-free receipt:
-- the session cooldown and continuation pointer are written by their existing
-- Owner-controlled services in the same request transaction.  No private text,
-- evidence payload, provider result or client-supplied authority field is added.
--
-- Migration 0045 deliberately left its CHECK constraints unnamed.  Replace only
-- the checks whose definitions govern feedback action/reason/scope, preserving
-- all owner, session, facet, foreign-key, trigger and append-only constraints.

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    FOR constraint_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'owner_truth.knowledge_recommendation_feedback_receipts'::regclass
          AND contype = 'c'
          AND (
              pg_get_constraintdef(oid) LIKE '%feedback_action%'
              OR pg_get_constraintdef(oid) LIKE '%feedback_reason%'
              OR pg_get_constraintdef(oid) LIKE '%feedback_scope%'
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE owner_truth.knowledge_recommendation_feedback_receipts DROP CONSTRAINT %I',
            constraint_name
        );
    END LOOP;
END;
$$;

ALTER TABLE owner_truth.knowledge_recommendation_feedback_receipts
    ADD CONSTRAINT knowledge_recommendation_feedback_action_v2_check
        CHECK (feedback_action IN ('replace', 'notInterested', 'defer')),
    ADD CONSTRAINT knowledge_recommendation_feedback_reason_v2_check
        CHECK (feedback_reason IN (
            'questionWording', 'topicPreference', 'recommendationType', 'timing'
        )),
    ADD CONSTRAINT knowledge_recommendation_feedback_scope_v2_check
        CHECK (feedback_scope IN ('candidate', 'dimension', 'questionTemplate')),
    ADD CONSTRAINT knowledge_recommendation_feedback_combination_v2_check
        CHECK (
            (feedback_action = 'replace'
                AND feedback_reason = 'questionWording'
                AND feedback_scope = 'candidate')
            OR (feedback_action = 'notInterested'
                AND feedback_reason = 'topicPreference'
                AND feedback_scope = 'dimension')
            OR (feedback_action = 'notInterested'
                AND feedback_reason = 'recommendationType'
                AND feedback_scope = 'questionTemplate')
            OR (feedback_action = 'defer'
                AND feedback_reason = 'timing'
                AND feedback_scope = 'candidate')
        );
