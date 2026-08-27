#!/usr/bin/env bash
set -euo pipefail

# Compatibility entrypoint for existing operator notes and automation.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/scripts/rebuild-enabled-workers-after-migration.sh" "$@"
