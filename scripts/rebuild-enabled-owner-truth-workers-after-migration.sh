#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-live}"
VERIFY_DELAY_SECONDS="${DJ_OWNER_TRUTH_WORKER_VERIFY_DELAY_SECONDS:-5}"
DEPLOY_BUILD_ID="${DEPLOY_BUILD_ID:-unknown}"

fail() {
  printf 'ownerTruthWorkerImageAlignment=failed reason=%s\n' "$1" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || fail "missingFile:$1"
}

worker_rows() {
  cat <<'EOF'
ownerTruthCandidateExtraction|owner_truth_candidate_extraction_worker_enabled|owner-truth-candidate-extraction-worker
ownerTruthMemoryProjection|owner_truth_memory_projection_worker_enabled|owner-truth-memory-projection-worker
ownerTruthMediaProcessing|owner_truth_media_processing_worker_enabled|owner-truth-media-processing-worker
ownerTruthMediaDeletion|owner_truth_media_deletion_worker_enabled|owner-truth-media-deletion-worker
EOF
}

contract_only() {
  require_file "$ROOT/docker-compose.yml"
  require_file "$ROOT/scripts/migrate_db.py"
  require_file "$ROOT/app/async_effects/owner_truth_worker_activation.py"

  while IFS='|' read -r worker_kind flag_name service_name; do
    grep -q "^[[:space:]]*${service_name}:" "$ROOT/docker-compose.yml" \
      || fail "composeServiceMissing:$service_name"
    grep -q "${flag_name}" "$ROOT/app/core/config.py" \
      || fail "workerFlagMissing:$worker_kind"
  done < <(worker_rows)

  printf '{"schemaVersion":"dreamjourney-owner-truth-worker-image-alignment-v1","mode":"contract","status":"passed","workerCount":4}\n'
}

[[ "$MODE" == "--contract-only" || "$MODE" == "live" ]] || fail "unsupportedMode"
if [[ "$MODE" == "--contract-only" ]]; then
  contract_only
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "dockerUnavailable"
command -v python3 >/dev/null 2>&1 || fail "pythonUnavailable"
require_file "$ROOT/.env"

cd "$ROOT"
docker compose version >/dev/null || fail "dockerComposeUnavailable"
docker compose config --quiet || fail "composeConfigurationInvalid"

expected_head="$(
  find "$ROOT/db/migrations" -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9]_*.sql' -print \
    | sed -E 's#^.*/([0-9]{4})_.*#\1#' \
    | sort -u \
    | tail -1
)"
[[ -n "$expected_head" ]] || fail "repositoryMigrationHeadMissing"

api_image_head="$(
  docker compose run --rm --no-deps -T --entrypoint python api -c \
    'from app.db.migrator import default_migrations_dir, load_migrations; print(load_migrations(default_migrations_dir())[-1].version)'
)"
[[ "$api_image_head" == "$expected_head" ]] || fail "apiImageMigrationHeadMismatch"

inventory="$(
  docker compose run --rm --no-deps -T --entrypoint python api -c '
from app.core.config import Settings

settings = Settings.from_env()
workers = (
    ("ownerTruthCandidateExtraction", "owner_truth_candidate_extraction_worker_enabled", "owner-truth-candidate-extraction-worker"),
    ("ownerTruthMemoryProjection", "owner_truth_memory_projection_worker_enabled", "owner-truth-memory-projection-worker"),
    ("ownerTruthMediaProcessing", "owner_truth_media_processing_worker_enabled", "owner-truth-media-processing-worker"),
    ("ownerTruthMediaDeletion", "owner_truth_media_deletion_worker_enabled", "owner-truth-media-deletion-worker"),
)
for worker_kind, flag_name, service_name in workers:
    if bool(getattr(settings, flag_name)):
        print("|".join((worker_kind, flag_name, service_name)))
'
)"

enabled_services=()
while IFS='|' read -r worker_kind flag_name service_name; do
  [[ -n "$worker_kind" ]] || continue
  enabled_services+=("$service_name")

  docker compose build "$service_name"

  worker_image_head="$(
    docker compose run --rm --no-deps -T --entrypoint python "$service_name" -c \
      'from app.db.migrator import default_migrations_dir, load_migrations; print(load_migrations(default_migrations_dir())[-1].version)'
  )"
  [[ "$worker_image_head" == "$expected_head" ]] \
    || fail "workerImageMigrationHeadMismatch:$service_name"

  migration_output="$(
    docker compose run --rm --no-deps -T --entrypoint python "$service_name" \
      scripts/migrate_db.py --verify --build-id "$DEPLOY_BUILD_ID"
  )"
  MIGRATION_OUTPUT="$migration_output" EXPECTED_HEAD="$expected_head" python3 - <<'PY' \
    || fail "workerDatabaseMigrationHeadMismatch:$service_name"
import json
import os

payload = json.loads(os.environ["MIGRATION_OUTPUT"])
expected = os.environ["EXPECTED_HEAD"]
if (
    payload.get("status") != "ready"
    or payload.get("expectedHead") != expected
    or payload.get("appliedHead") != expected
    or payload.get("pendingVersions")
):
    raise SystemExit(1)
PY

  activation_output=""
  if ! activation_output="$(
    docker compose run --rm --no-deps -T --entrypoint python "$service_name" \
      -m app.async_effects.owner_truth_worker_activation --worker "$worker_kind"
  )"; then
    fail "workerActivationFailed:$worker_kind"
  fi
  ACTIVATION_OUTPUT="$activation_output" EXPECTED_WORKER="$worker_kind" python3 - <<'PY' \
    || fail "workerActivationDescriptorInvalid:$worker_kind"
import json
import os

payload = json.loads(os.environ["ACTIVATION_OUTPUT"])
if payload.get("worker") != os.environ["EXPECTED_WORKER"] or payload.get("ready") is not True:
    raise SystemExit(1)
PY

  docker compose up -d --no-deps --force-recreate "$service_name"
  sleep "$VERIFY_DELAY_SECONDS"

  container_id="$(docker compose ps -q "$service_name")"
  [[ -n "$container_id" ]] || fail "workerContainerMissing:$service_name"
  container_state="$(
    docker inspect --format '{{.State.Status}}|{{.State.Restarting}}|{{.RestartCount}}' "$container_id"
  )"
  [[ "$container_state" == "running|false|0" ]] \
    || fail "workerContainerUnstable:$service_name:$container_state"
done <<< "$inventory"

services_csv="$(IFS=,; printf '%s' "${enabled_services[*]-}")"
ENABLED_SERVICES="$services_csv" EXPECTED_HEAD="$expected_head" python3 - <<'PY'
import json
import os

services = [item for item in os.environ.get("ENABLED_SERVICES", "").split(",") if item]
print(
    json.dumps(
        {
            "schemaVersion": "dreamjourney-owner-truth-worker-image-alignment-v1",
            "mode": "live",
            "status": "passed",
            "migrationHead": os.environ["EXPECTED_HEAD"],
            "rebuiltWorkers": services,
            "workerCount": len(services),
        },
        sort_keys=True,
    )
)
PY
