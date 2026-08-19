-- migration:owner_truth_echo_grounding_audit
-- Bind a public Owner Echo answer to its runtime Context trace without storing
-- the raw trace identifier, query, answer, or memory text.

ALTER TABLE owner_truth.answers
    ADD COLUMN context_trace_id_hash TEXT
    CHECK (
        context_trace_id_hash IS NULL
        OR context_trace_id_hash ~ '^[a-f0-9]{64}$'
    );

REVOKE ALL ON TABLE owner_truth.answers FROM PUBLIC;
