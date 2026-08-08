-- migration:identity_challenge_delivery_state
-- Additive provider-neutral OTP delivery and recovery evidence. Provider
-- receipt identifiers remain transient; only keyed hashes may be persisted.

ALTER TABLE auth_challenges
    ADD COLUMN delivery_state TEXT NOT NULL DEFAULT 'accepted',
    ADD COLUMN recovery_state TEXT NOT NULL DEFAULT 'unsupported',
    ADD COLUMN provider_receipt_hash TEXT,
    ADD COLUMN provider_retry_after_seconds INTEGER,
    ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN provider_checked_at TIMESTAMPTZ,
    ADD COLUMN provider_delivered_at TIMESTAMPTZ;

ALTER TABLE auth_challenges
    ADD CONSTRAINT auth_challenges_delivery_state_check
        CHECK (delivery_state IN (
            'accepted', 'delivered', 'undeliverable', 'unknown'
        )),
    ADD CONSTRAINT auth_challenges_recovery_state_check
        CHECK (recovery_state IN (
            'available', 'notRequired', 'pending', 'terminal', 'unsupported'
        )),
    ADD CONSTRAINT auth_challenges_provider_receipt_hash_check
        CHECK (
            provider_receipt_hash IS NULL
            OR provider_receipt_hash ~ '^[0-9a-f]{64}$'
        ),
    ADD CONSTRAINT auth_challenges_provider_retry_after_check
        CHECK (
            provider_retry_after_seconds IS NULL
            OR provider_retry_after_seconds BETWEEN 0 AND 86400
        ),
    ADD CONSTRAINT auth_challenges_recovery_attempts_check
        CHECK (recovery_attempts >= 0),
    ADD CONSTRAINT auth_challenges_delivery_terminal_check
        CHECK (
            (delivery_state <> 'delivered' OR recovery_state = 'notRequired')
            AND (delivery_state <> 'undeliverable' OR recovery_state = 'terminal')
        );

CREATE INDEX auth_challenges_delivery_recovery_idx
    ON auth_challenges(delivery_state, recovery_state, updated_at ASC);

REVOKE ALL ON TABLE auth_challenges FROM PUBLIC;
