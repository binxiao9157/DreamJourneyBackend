-- Capability fence for private units as well as public snapshot jobs.
-- An old binary may still issue the old unfiltered claim SQL. Reject that
-- transition atomically; never let it consume new-protocol private work.
CREATE OR REPLACE FUNCTION owner_truth.live_recovery_unit_claim_fence() RETURNS trigger AS $$
BEGIN
    IF NEW.state='running' AND (OLD.state IS DISTINCT FROM NEW.state
        OR OLD.lease_generation IS DISTINCT FROM NEW.lease_generation)
       AND current_setting('dreamjourney.live_recovery_worker', true) IS DISTINCT FROM 'v1'
       AND EXISTS (
          SELECT 1 FROM owner_truth.live_memory_runs run
          JOIN owner_truth.interview_sessions i ON i.product_session_id=run.product_session_id
             AND i.vault_id=run.vault_id AND i.owner_subject_id=run.owner_subject_id
          JOIN owner_truth.live_recovery_sessions r ON r.session_id=i.id
             AND r.authority_epoch=run.authority_epoch
          WHERE run.id=NEW.run_id
       ) THEN
        RAISE EXCEPTION 'Live recovery worker capability required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER live_recovery_unit_claim_fence BEFORE UPDATE ON owner_truth.live_memory_work_units
FOR EACH ROW EXECUTE FUNCTION owner_truth.live_recovery_unit_claim_fence();
