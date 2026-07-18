-- migration:operation_metric_evidence
--
-- This is an additive expansion of the evidence envelope. Existing event
-- types remain valid; operationMetric is used only by shadow observability.

ALTER TABLE evidence_events
    DROP CONSTRAINT IF EXISTS evidence_events_event_type_check;

ALTER TABLE evidence_events
    ADD CONSTRAINT evidence_events_event_type_check
    CHECK (
        event_type IN (
            'operation',
            'operationMetric',
            'rights',
            'incident',
            'providerCost'
        )
    );
