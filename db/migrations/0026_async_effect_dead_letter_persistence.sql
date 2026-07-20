-- migration:async_effect_dead_letter_persistence
--
-- Adds the final value-free receipt coordinate required to reconstruct a
-- dead-letter admission. The existing kernel remains disabled: this migration
-- neither re-enqueues jobs nor enables a worker or Provider replay path.

ALTER TABLE async_effects.dead_letters
    ADD COLUMN IF NOT EXISTS last_receipt_hash TEXT;

ALTER TABLE async_effects.dead_letters
    DROP CONSTRAINT IF EXISTS async_effects_dead_letters_last_receipt_hash_check;

ALTER TABLE async_effects.dead_letters
    ADD CONSTRAINT async_effects_dead_letters_last_receipt_hash_check
    CHECK (last_receipt_hash IS NULL OR last_receipt_hash ~ '^[0-9a-f]{64}$');
