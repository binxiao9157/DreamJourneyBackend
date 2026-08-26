#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-live}"
DEPLOY_OPERATOR="${DJ_DEPLOY_OPERATOR:-ubuntu}"
REPOSITORY_OWNER="${DJ_REPOSITORY_OWNER:-miao}"
DEPLOY_REPOSITORY="${DJ_DEPLOY_REPOSITORY:-/opt/services/dreamjourney/DreamJourneyBackend}"
DEPLOY_BRANCH="${DJ_DEPLOY_BRANCH:-main}"
PRIVATE_CONFIG_BACKUP_ROOT="${DJ_PRIVATE_CONFIG_BACKUP_ROOT:-/var/lib/dreamjourney/private-config-backups}"

fail() {
  printf 'deploymentPreflight=failed reason=%s\n' "$1" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || fail "missingFile:$1"
}

require_directory() {
  [[ -d "$1" ]] || fail "missingDirectory:$1"
}

contract_only() {
  require_directory "$ROOT"
  require_file "$ROOT/docker-compose.yml"
  require_file "$ROOT/scripts/migrate_db.py"
  require_file "$ROOT/scripts/rebuild-enabled-owner-truth-workers-after-migration.sh"
  require_file "$ROOT/scripts/db/verify_latest_backup.py"
  require_file "$ROOT/scripts/db/run-recovery-deployed-smoke.sh"
  require_file "$ROOT/docs/backend/2026-08-09-deployment-account-recovery-runbook.md"
  printf '{"schemaVersion":"dreamjourney-deployment-preflight-v1","mode":"contract","status":"passed"}\n'
}

[[ "$MODE" == "--contract-only" || "$MODE" == "live" ]] || fail "unsupportedMode"
if [[ "$MODE" == "--contract-only" ]]; then
  contract_only
  exit 0
fi

[[ "$(id -un)" == "$DEPLOY_OPERATOR" ]] || fail "operatorMismatch"
[[ "$(id -u)" != "0" ]] || fail "rootLoginForbidden"
require_directory "$DEPLOY_REPOSITORY/.git"
sudo -n test -x "$DEPLOY_REPOSITORY/scripts/rebuild-enabled-owner-truth-workers-after-migration.sh" \
  || fail "workerImageAlignmentScriptUnavailable"

repository_owner="$(stat -c '%U' "$DEPLOY_REPOSITORY")"
[[ "$repository_owner" == "$REPOSITORY_OWNER" ]] || fail "repositoryOwnerMismatch"

sudo -n -iu "$REPOSITORY_OWNER" true >/dev/null || fail "repositoryAccountUnavailable"
sudo -n docker compose version >/dev/null || fail "dockerComposeUnavailable"

branch="$(sudo -n -iu "$REPOSITORY_OWNER" git -C "$DEPLOY_REPOSITORY" branch --show-current)"
[[ "$branch" == "$DEPLOY_BRANCH" ]] || fail "branchMismatch"
sudo -n -iu "$REPOSITORY_OWNER" git -C "$DEPLOY_REPOSITORY" diff --quiet || fail "trackedWorktreeDirty"
sudo -n -iu "$REPOSITORY_OWNER" git -C "$DEPLOY_REPOSITORY" diff --cached --quiet || fail "indexDirty"
[[ -z "$(sudo -n -iu "$REPOSITORY_OWNER" git -C "$DEPLOY_REPOSITORY" status --porcelain --untracked-files=all)" ]] \
  || fail "worktreeNotClean"
sudo -n -iu "$REPOSITORY_OWNER" git -C "$DEPLOY_REPOSITORY" \
  ls-remote --exit-code origin "refs/heads/$DEPLOY_BRANCH" >/dev/null \
  || fail "gitCredentialUnavailable"

sudo -n test -f "$DEPLOY_REPOSITORY/.env" || fail "environmentFileMissing"
env_mode="$(sudo -n stat -c '%a' "$DEPLOY_REPOSITORY/.env")"
env_owner="$(sudo -n stat -c '%U:%G' "$DEPLOY_REPOSITORY/.env")"
[[ "$env_mode" == "600" ]] || fail "environmentFileMode"
[[ "$env_owner" == "root:root" ]] || fail "environmentFileOwner"

legacy_backup_count="$(sudo -n find "$DEPLOY_REPOSITORY" -maxdepth 1 -type f \
  \( -name '.env.backup*' -o -name '.env.bak*' \) -print | wc -l | tr -d ' ')"
[[ "$legacy_backup_count" == "0" ]] || fail "legacyConfigBackupsNotIsolated"

sudo -n test -d "$PRIVATE_CONFIG_BACKUP_ROOT" || fail "privateConfigBackupRootMissing"
backup_root_mode="$(sudo -n stat -c '%a' "$PRIVATE_CONFIG_BACKUP_ROOT")"
backup_root_owner="$(sudo -n stat -c '%U:%G' "$PRIVATE_CONFIG_BACKUP_ROOT")"
[[ "$backup_root_mode" == "700" ]] || fail "privateConfigBackupRootMode"
[[ "$backup_root_owner" == "root:root" ]] || fail "privateConfigBackupRootOwner"
if sudo -n find "$PRIVATE_CONFIG_BACKUP_ROOT" -maxdepth 2 -type f ! -perm 600 -print -quit | grep -q .; then
  fail "privateConfigBackupFileMode"
fi

sudo -n bash -lc "cd '$DEPLOY_REPOSITORY' && docker compose config --quiet" \
  || fail "composeConfigurationInvalid"
sudo -n systemctl is-enabled --quiet dreamjourney-db-backup.timer \
  || fail "backupTimerDisabled"
sudo -n systemctl is-enabled --quiet dreamjourney-db-backup-retention-audit.timer \
  || fail "backupRetentionTimerDisabled"

head_commit="$(sudo -n -iu "$REPOSITORY_OWNER" git -C "$DEPLOY_REPOSITORY" rev-parse --short HEAD)"
printf '{"schemaVersion":"dreamjourney-deployment-preflight-v1","mode":"live","status":"passed","operator":"%s","repositoryOwner":"%s","branch":"%s","head":"%s","configBackups":"isolated","backupTimers":"enabled"}\n' \
  "$DEPLOY_OPERATOR" "$REPOSITORY_OWNER" "$branch" "$head_commit"
