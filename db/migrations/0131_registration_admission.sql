-- Additive: preserves identities, sessions, credentials, memories and old invitation values.
CREATE TABLE registration_policy (
    id integer PRIMARY KEY CHECK (id=1),
    payload jsonb NOT NULL
);
CREATE TABLE registration_records (
    kind text NOT NULL CHECK (kind IN ('codes','attempts','seats','operations','events','adminSessions','adminAttempts')),
    id text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(kind,id)
);
INSERT INTO registration_policy VALUES (1,'{"cap":null,"paused":true,"revision":0,"smsBudget":0,"smsUsed":0}');
-- Conservative inventory: include all subjects, legacy users, and session owners.
INSERT INTO registration_records(kind,id,payload)
SELECT 'seats',id,jsonb_build_object('status','occupied','source','migration0131') FROM (
    SELECT id FROM subjects UNION SELECT id FROM users UNION SELECT user_id FROM auth_sessions
) owners WHERE id IS NOT NULL;
-- Import old reservations at activation with application HMAC/encryption keys. Until then
-- all new creation is closed. Retain family rows and expiry unchanged.
CREATE FUNCTION registration_guard_new_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM registration_records WHERE kind='seats' AND id=NEW.subject_id
                   AND payload->>'status'='occupied') THEN
        RAISE EXCEPTION 'registration admission required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER registration_identity_guard BEFORE INSERT ON identity_bindings
FOR EACH ROW EXECUTE FUNCTION registration_guard_new_identity();
CREATE FUNCTION registration_guard_new_user() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM registration_records WHERE kind='seats' AND id=NEW.id
                   AND payload->>'status'='occupied') THEN
        RAISE EXCEPTION 'registration admission required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER registration_user_guard BEFORE INSERT ON users
FOR EACH ROW EXECUTE FUNCTION registration_guard_new_user();

CREATE FUNCTION registration_guard_new_session() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM registration_records WHERE kind='seats' AND id=NEW.user_id
                   AND payload->>'status'='occupied') THEN
        RAISE EXCEPTION 'registration admission required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER registration_session_guard BEFORE INSERT ON auth_sessions
FOR EACH ROW EXECUTE FUNCTION registration_guard_new_session();
