# C01：Postgres 恢复 Schema Lineage 收敛

日期：2026-07-23
范围：`WI-MIG-01-02` 的恢复证据 schema lineage 缺口，仅覆盖 G0 合同和 fake/isolated smoke。

## 已修复的行为

旧实现把 backup manifest 的 `schemaHead` 同时作为恢复后完整性审计的预期 head。一个在旧版本生成、但恢复后已成功迁移到当前版本的 backup，会因此被误判为 schema mismatch。

现实现将两个事实分开记录并绑定：

- `backupSchemaHead`：backup manifest 生成时的来源 head；保留兼容字段 `schemaHead`，其含义仍为来源 head。
- `restoredSchemaHead`：隔离 restore 后，`migration-verify.json` 的 `expectedHead/appliedHead`。
- `integrity-evidence.json.expectedSchemaHead`：必须等于 `restoredSchemaHead`，不能回退绑定来源 head。
- `recovery-record.json`：同时保存来源和恢复目标 head，供后续审计追溯。

`restore-evidence.json` 升为 schema version 2；旧 version 1 evidence 因缺少恢复目标 head 会 fail closed，不能被用于新的 recovery record。

备份脚本还会在 dump 前比较数据库 ledger head 与当前代码迁移 head。两者不同会写入 `schemaHeadMismatch` failure receipt，避免生成无法与当前恢复流程对齐的 backup。

归档验证不再使用 `decrypt | pg_restore --list`。后者的 list 模式可能提前关闭输入，配合 `pipefail` 会把上游的 broken pipe 误报为 archive 无效。现在解密到 root-only 临时文件后再检查，并在任一路径清理该文件。

## 已验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_recovery_record \
  tests.test_backup_manifest \
  tests.test_recovery_integrity_audit -v

PYTHONPATH=. .venv/bin/python scripts/db/recovery-postgres-smoke.py
PYTHONPATH=. .venv/bin/python scripts/db/backup-postgres-smoke.py
bash -n scripts/db/backup_postgres.sh scripts/db/restore_postgres.sh \
  scripts/db/run-recovery-deployed-smoke.sh
```

恢复 smoke 特意使用 `0001` 作为 backup source head，并将隔离库迁移到当前代码 head，证明 lineage 不会误报；同时验证把完整性证据继续绑定来源 head 会被拒绝。

## 部署后备份证据

- 后端已部署 `342f0ef`（schema lineage）和 `cd65163`（加密归档验证修复）。
- 部署环境的 migration verify 返回 `status=ready`，`expectedHead=0041`、`appliedHead=0041`，`pendingVersions=[]`。
- `/ready` 返回 HTTP 200，全部组件为 `ready`。
- 已由 systemd 成功执行两次新的加密备份；`run-backup-deployed-smoke.sh` 返回 `verifiedCurrentBackupCount=2`、`encryptedArtifacts=true`、`freshnessGate=true`、`schemaHead=0041`、`automaticDeletion=false`。
- 该证据仅关闭“当前代码 head 下的备份生成与验证”操作链路；没有执行真实 restore、数据切换或 replay。

## 仍未关闭

这不是 G2 生产恢复验收。仍需要：当前真实加密 backup、隔离 Postgres restore、身份根/async authority 审计、可信 replay producer、运维审批和线上演练证据。在这些证据齐全前，任何 recovery record 都不得据此切流。
