-- migration:evidence_manifest
--
-- Acceptance manifests are value-free append-only evidence rows. The payload
-- contains only versioned metadata and artifact SHA-256 values; report bodies,
-- user data, provider payloads, local paths, and secrets remain outside this
-- store.

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
            'providerCost',
            'evidenceManifest'
        )
    );

CREATE INDEX idx_evidence_events_manifest_type_expiry
    ON evidence_events ((payload->>'manifestType'), expires_at DESC)
    WHERE event_type = 'evidenceManifest';
