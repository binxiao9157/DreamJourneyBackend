#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-live}"
VERIFY_DELAY_SECONDS="${DJ_WORKER_VERIFY_DELAY_SECONDS:-5}"
STABILITY_DELAY_SECONDS="${DJ_WORKER_STABILITY_DELAY_SECONDS:-5}"
DEPLOY_BUILD_ID="${DEPLOY_BUILD_ID:-unknown}"

fail() {
  printf 'workerImageAlignment=failed reason=%s\n' "$1" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || fail "missingFile:$1"
}

registry_rows() {
  PYTHONPATH="$ROOT" python3 -m app.async_effects.worker_deployment_registry \
    --format lines
}

contract_only() {
  require_file "$ROOT/docker-compose.yml"
  require_file "$ROOT/scripts/migrate_db.py"
  require_file "$ROOT/app/async_effects/worker_activation.py"
  require_file "$ROOT/app/async_effects/worker_deployment_registry.py"

  local inventory
  inventory="$(registry_rows)"
  local worker_count=0
  while IFS='|' read -r worker_kind flag_name service_name; do
    [[ -n "$worker_kind" ]] || continue
    worker_count=$((worker_count + 1))
    grep -q "^[[:space:]]*${service_name}:" "$ROOT/docker-compose.yml" \
      || fail "composeServiceMissing:$service_name"
    grep -q "${flag_name}" "$ROOT/app/core/config.py" \
      || fail "workerFlagMissing:$worker_kind"
    grep -q -- "--worker ${worker_kind}" "$ROOT/docker-compose.yml" \
      || fail "workerActivationMissing:$worker_kind"
  done <<< "$inventory"

  [[ "$worker_count" -gt 0 ]] || fail "workerRegistryEmpty"
  WORKER_COUNT="$worker_count" python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "schemaVersion": "dreamjourney-long-running-worker-image-alignment-v1",
            "mode": "contract",
            "status": "passed",
            "workerCount": int(os.environ["WORKER_COUNT"]),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
PY
}

inspect_worker_state() {
  local service_name="$1"
  local container_id
  container_id="$(docker compose ps -q "$service_name")"
  [[ -n "$container_id" ]] || fail "workerContainerMissing:$service_name"
  docker inspect --format '{{.State.Status}}|{{.State.Restarting}}|{{.RestartCount}}' \
    "$container_id"
}

verify_stable_worker() {
  local service_name="$1"
  local first_state second_state first_restarts second_restarts

  sleep "$VERIFY_DELAY_SECONDS"
  first_state="$(inspect_worker_state "$service_name")"
  sleep "$STABILITY_DELAY_SECONDS"
  second_state="$(inspect_worker_state "$service_name")"

  [[ "$first_state" == running\|false\|* ]] \
    || fail "workerContainerUnstable:$service_name:$first_state"
  [[ "$second_state" == running\|false\|* ]] \
    || fail "workerContainerUnstable:$service_name:$second_state"

  first_restarts="${first_state##*|}"
  second_restarts="${second_state##*|}"
  [[ "$first_restarts" =~ ^[0-9]+$ && "$second_restarts" =~ ^[0-9]+$ ]] \
    || fail "workerRestartCountInvalid:$service_name"
  [[ "$first_restarts" == "0" && "$second_restarts" == "0" ]] \
    || fail "workerRestartedAfterRecreate:$service_name:$first_restarts:$second_restarts"
}

[[ "$MODE" == "--contract-only" || "$MODE" == "live" ]] \
  || fail "unsupportedMode"
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
  docker compose run --rm --no-deps -T --entrypoint python api \
    -m app.async_effects.worker_deployment_registry \
    --format lines --with-enabled-state
)"
[[ -n "$inventory" ]] || fail "workerRegistryEmpty"

enabled_services=()
stopped_disabled_services=()
while IFS='|' read -r worker_kind flag_name service_name enabled; do
  [[ -n "$worker_kind" ]] || continue
  if [[ "$enabled" != "1" ]]; then
    disabled_container_id="$(docker compose ps -q --all "$service_name")"
    if [[ -n "$disabled_container_id" ]]; then
      disabled_state="$(docker inspect --format '{{.State.Status}}' "$disabled_container_id")"
      if [[ "$disabled_state" == "running" || "$disabled_state" == "restarting" ]]; then
        docker compose stop "$service_name"
        stopped_disabled_services+=("$service_name")
      fi
    fi
    continue
  fi

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
      -m app.async_effects.worker_activation --worker "$worker_kind"
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
if payload.get("failureCode") is not None or payload.get("correlationId") is not None:
    raise SystemExit(1)
PY

  docker compose up -d --no-deps --force-recreate "$service_name"
  verify_stable_worker "$service_name"
done <<< "$inventory"

services_csv="$(IFS=,; printf '%s' "${enabled_services[*]-}")"
stopped_csv="$(IFS=,; printf '%s' "${stopped_disabled_services[*]-}")"
ENABLED_SERVICES="$services_csv" STOPPED_SERVICES="$stopped_csv" \
  EXPECTED_HEAD="$expected_head" python3 - <<'PY'
import json
import os

services = [item for item in os.environ.get("ENABLED_SERVICES", "").split(",") if item]
stopped = [item for item in os.environ.get("STOPPED_SERVICES", "").split(",") if item]
print(
    json.dumps(
        {
            "schemaVersion": "dreamjourney-long-running-worker-image-alignment-v1",
            "mode": "live",
            "status": "passed",
            "migrationHead": os.environ["EXPECTED_HEAD"],
            "rebuiltWorkers": services,
            "stoppedDisabledWorkers": stopped,
            "workerCount": len(services),
        },
        sort_keys=True,
    )
)
PY
