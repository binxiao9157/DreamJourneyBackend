-- Start the post-close deadlines when the immutable Source is bound. Existing
-- bound runs stay NULL and fail closed for new provider work; their historical
-- deadline cannot be reconstructed from updated_at.
ALTER TABLE owner_truth.live_memory_runs
    ADD COLUMN IF NOT EXISTS organization_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ;

ALTER TABLE owner_truth.live_memory_runs
    ADD CONSTRAINT live_memory_runs_deadline_coordinates_check
    CHECK (
        (organization_started_at IS NULL AND last_progress_at IS NULL)
        OR (organization_started_at IS NOT NULL AND last_progress_at IS NOT NULL)
    );
