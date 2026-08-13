-- migration:realtime_voice_subject_authority
--
-- 0091 bound realtime voice admission tickets to the legacy users table even
-- though the typed V4 login flow issues Subject IDs. Keep the applied 0091
-- checksum immutable and move the short-lived ticket ownership boundary to
-- the strong-identity Subject authority.
--
-- The replacement constraint is NOT VALID so a ticket issued by a retired
-- legacy login can finish naturally. PostgreSQL still validates every new or
-- updated row, so all newly issued tickets must belong to a current Subject.

ALTER TABLE realtime_voice_session_tickets
    DROP CONSTRAINT realtime_voice_session_tickets_user_id_fkey;

ALTER TABLE realtime_voice_session_tickets
    ADD CONSTRAINT realtime_voice_session_tickets_subject_id_fkey
    FOREIGN KEY (user_id) REFERENCES subjects(id) ON DELETE CASCADE
    NOT VALID;
