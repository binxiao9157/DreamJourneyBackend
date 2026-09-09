#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-live}"

fail() {
  printf 'pgvectorImagePreflight=failed reason=%s\n' "$1" >&2
  exit 1
}

contract_only() {
  grep -Fq '${POSTGRES_IMAGE:-pgvector/pgvector:pg16}' "$ROOT/docker-compose.yml" \
    || fail "composeImageContractMissing"
  grep -Fq 'CREATE EXTENSION IF NOT EXISTS vector' \
    "$ROOT/db/migrations/0113_owner_truth_memory_search_hybrid_pgvector.sql" \
    || fail "vectorMigrationContractMissing"
  printf '{"schemaVersion":"dreamjourney-pgvector-image-preflight-v1","mode":"contract","status":"passed"}\n'
}

[[ "$MODE" == "--contract-only" || "$MODE" == "live" ]] \
  || fail "unsupportedMode"
if [[ "$MODE" == "--contract-only" ]]; then
  contract_only
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "dockerUnavailable"
command -v python3 >/dev/null 2>&1 || fail "pythonUnavailable"
[[ -f "$ROOT/.env" ]] || fail "environmentFileMissing"

cd "$ROOT"
docker compose config --quiet || fail "composeConfigurationInvalid"
compose_json="$(docker compose config --format json)" \
  || fail "composeConfigurationUnreadable"
postgres_image="$(COMPOSE_JSON="$compose_json" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["COMPOSE_JSON"])
print(payload["services"]["postgres"]["image"])
PY
)" || fail "postgresImageUnresolved"
[[ -n "$postgres_image" ]] || fail "postgresImageUnresolved"

docker image inspect "$postgres_image" >/dev/null 2>&1 \
  || fail "postgresImageNotPulled"
docker run --rm --entrypoint sh "$postgres_image" -c \
  'sharedir="$(pg_config --sharedir)" && test -f "$sharedir/extension/vector.control"' \
  || fail "pgvectorExtensionUnavailable"

image_id="$(docker image inspect --format '{{.Id}}' "$postgres_image")"
IMAGE_ID="$image_id" python3 - <<'PY'
import hashlib
import json
import os

print(
    json.dumps(
        {
            "schemaVersion": "dreamjourney-pgvector-image-preflight-v1",
            "mode": "live",
            "status": "passed",
            "imageIdHash": hashlib.sha256(os.environ["IMAGE_ID"].encode()).hexdigest(),
            "postgresMajor": 16,
            "pgvectorAvailable": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
PY
