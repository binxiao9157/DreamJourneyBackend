CREATE TABLE narrative.selection_manifests (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    job_id UUID NOT NULL,
    memory_snapshot_id UUID NOT NULL,
    selected_memory_version_ids JSONB NOT NULL
        CHECK (jsonb_typeof(selected_memory_version_ids) = 'array')
        CHECK (jsonb_array_length(selected_memory_version_ids) BETWEEN 1 AND 3),
    selection_hash TEXT NOT NULL CHECK (selection_hash ~ '^[a-f0-9]{64}$'),
    model_id TEXT,
    prompt_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (job_id),
    FOREIGN KEY (vault_id, project_id)
        REFERENCES narrative.book_projects(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, job_id)
        REFERENCES narrative.generation_jobs(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_snapshot_id)
        REFERENCES narrative.memory_snapshots(vault_id, id) ON DELETE RESTRICT
);

CREATE INDEX narrative_selection_manifests_snapshot
    ON narrative.selection_manifests(project_id, memory_snapshot_id);

CREATE TRIGGER narrative_selection_manifests_immutable
BEFORE UPDATE OR DELETE ON narrative.selection_manifests
FOR EACH ROW EXECUTE FUNCTION narrative.reject_immutable_update();
