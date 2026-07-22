# DreamJourney Postgres 隔离恢复与 Receipt Replay 运维说明

日期：2026-07-17

适用工作项：`WI-S0-04-05`

## 1. 目的与完成边界

本工具链用于把 `WI-S0-04-04` 产生的已校验 backup 恢复到 `dj_recovery_*` 隔离数据库，迁移到当前 schema head，并生成不含用户正文和直接标识的恢复证据。

恢复成功不等于允许切流。以下条件必须全部满足才会得到 `cutoverDecision=GO`：

1. backup manifest、artifact size 和 checksum 通过校验；
2. 目标数据库不是生产数据库，且 DSN 的数据库名与 `RECOVERY_TARGET_DB` 完全一致；
3. restore 和 versioned migration 成功；证据必须同时保留 backup 的来源 head 与恢复后迁移到的目标 head，完整性审计绑定目标 head；
4. 对 legacy direct-user、Owner Truth Vault scope 和 async operation scope 完成动态
   owner/authority 审计，并保留任何未验证根的 `NO_GO`；
5. cutoff 后 command/outbox/deletion/provider receipt coverage 完整；
6. receipt application evidence 与 backup、cutoff、range、source digest、plan digest 和数量完全一致；
7. provider unknown、pending deletion、receipt conflict 或流量恢复失败均不存在。

当前代码库尚未具备全库 command/outbox/deletion/provider receipt authority。没有可信 replay bundle 和 application evidence 时，工具会保留 `replayBundleMissing` 并输出 `NO_GO`。不得用 KB 单 operation receipt 或人工填写状态替代全库恢复证据。

## 2. 安全边界

- `RECOVERY_TARGET_DB` 必须匹配 `^dj_recovery_[a-z0-9_]{4,48}$`。
- 目标不得等于 `RECOVERY_PRODUCTION_DB`、`postgres`、`template0` 或 `template1`。
- `RECOVERY_DATABASE_URL` 的 path 必须与目标数据库完全一致。
- 默认拒绝未加密 artifact；仅 fake/local smoke 可显式设置 `RECOVERY_ALLOW_UNENCRYPTED=1`。
- 已存在的隔离目标默认拒绝覆盖；只有确认目标可丢弃时才设置 `RECOVERY_ALLOW_DROP_ISOLATED=1`。
- 脚本不切换生产 DSN、不修改负载均衡、不删除生产数据库，也不会合成缺失 receipt。
- 输出目录和所有 evidence 文件权限为 `0700/0600`。

## 3. 本地 G0 合同验证

```bash
cd /srv/dreamjourney-backend
.venv/bin/python -m unittest \
  tests.test_recovery_record \
  tests.test_recovery_integrity_audit \
  tests.test_backup_manifest \
  tests.test_db_migrator -v

scripts/db/run-recovery-postgres-smoke.sh
DATABASE_URL='<isolated-postgres-dsn>' \
  scripts/db/run-recovery-integrity-audit-postgres-smoke.sh
bash -n \
  scripts/db/restore_postgres.sh \
  scripts/db/run-recovery-postgres-smoke.sh \
  scripts/db/run-recovery-deployed-smoke.sh
```

`run-recovery-postgres-smoke.sh` 使用 fake Docker 和假 dump，只证明合同与 fail-closed 行为，不关闭 G2。

`run-recovery-integrity-audit-postgres-smoke.sh` 会创建一个临时
`dj_recovery_audit_*` 数据库，并额外建立一个不在业务迁移名单内、但带
`user_id` 的孤儿 fixture 表。脚本必须发现该表、把它归因到
`orphanOwnerCountsByTable`，并输出 `ownerOrphansPresent` 的 `NO_GO`。该脚本
只允许在可创建临时数据库的隔离 Postgres 环境运行。

### 3.1 Integrity evidence V3

`integrity-evidence.json` 的 V3 版本不再依赖手工维护的 owner 表名单。它通过
`information_schema` 发现并分别审计以下域：

- `publicDirectUserId`：所有 `public` base table 的直接 `user_id`；
- `ownerTruthVaultScope`：所有 `owner_truth` 表相对 Vault 根的 scope；
- `asyncEffectsOperationScope`：所有 `async_effects` 子表相对 operation 根的
  owner/vault/epoch 一致性；
- `explicitExemptions`：只有不携带 owner/resource 值的运行时证据才允许显式豁免，
  当前为 `async_effects.worker_loss_observations`。

V3 必须输出每个域的已检查表、表级计数、未分类表和 root authority 状态。
任何新表不能被分类、表级计数缺失、aggregate 不一致、Vault/operation scope 不一致，
都会 fail closed。V1/V2 evidence 仅作历史可读，均会被标为
`auditCoverageStatus=unverified`，不能促成 `recovery-record.json` 的
`cutoverDecision=GO`。

V3 当前会显式报告 Owner Truth 身份根和 async operation authority root 尚未获得独立
验证；这不是扫描器可以猜测补齐的关系。因此即便各表扫描完整，仍保持 `NO_GO`，直到
对应 identity/authority evidence 有可验证来源。该限制同样不等同于 receipt replay
authority 已完成。

## 4. 生产级隔离 G2 演练

### 4.1 先建立运行时流量围栏

恢复或切流前必须先修改服务器私密 `.env` 并重启 API：

```dotenv
RECOVERY_ACCESS_MODE=maintenance
AUTHORITY_EPOCH=epoch-<current>
```

支持的模式只有：

- `normal`：正常读写；
- `readOnly`：只允许 GET/HEAD/OPTIONS，写请求返回 `503 recoveryWriteBlocked`；
- `signedOut`：业务请求返回 `503 recoveryMaintenance`，iOS 清理后端会话；
- `maintenance`：业务请求全部关闭，仅保留 health/live/ready/runtime。

非法 mode 或空 epoch 会 fail-closed 为 `maintenance`。数据库事务 middleware 在申请连接前执行同一围栏，因此数据库损坏或连接池不可用时，`/config/runtime` 仍可返回恢复状态。

确认围栏后再开始恢复：

```bash
curl -fsS https://<backend-host>/config/runtime \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["recovery"])'
```

不得在命令行或证据包中输出 `BACKEND_API_TOKEN`、数据库密码或恢复密钥。

### 4.2 执行隔离恢复

先选择当前且未过期的真实 manifest，并准备对应加密 key。目标数据库必须使用本次演练唯一名称。

```bash
export RECOVERY_MANIFEST_PATH=/var/backups/dreamjourney/postgres/<backup-id>.manifest.json
export RECOVERY_ENCRYPTION_KEY_FILE=/etc/dreamjourney/backup.key
export RECOVERY_TARGET_DB=dj_recovery_$(date -u +%Y%m%d_%H%M%S)
export RECOVERY_PRODUCTION_DB=dreamjourney
export RECOVERY_DATABASE_URL='postgresql://<restricted-user>:<password>@postgres:5432/'"$RECOVERY_TARGET_DB"
export RECOVERY_OUTPUT_DIR=/var/backups/dreamjourney/recovery/$RECOVERY_TARGET_DB
export RECOVERY_EXPECTED_CUTOVER=NO_GO

sudo -E scripts/db/run-recovery-deployed-smoke.sh
```

在 receipt authority 尚未完成前，`RECOVERY_EXPECTED_CUTOVER` 必须保持 `NO_GO`。未来只有可信 replay producer 已部署并产生以下两个文件后才可改为 `GO`：

```bash
export RECOVERY_REPLAY_BUNDLE_PATH=/secure/recovery/replay-bundle.json
export RECOVERY_REPLAY_APPLICATION_EVIDENCE_PATH=/secure/recovery/replay-application.json
```

这两个文件必须来自服务端 authority/worker，不得手工伪造。

### 4.3 恢复流量

只有 `recovery-record.json`、审批和适用 Gate 都允许时，才能按以下顺序恢复：

1. 先设置 `RECOVERY_ACCESS_MODE=readOnly`，重启并验证核心只读接口；
2. 将 `AUTHORITY_EPOCH` 提升为新的单调值，禁止回退旧 epoch；
3. iOS 重新读取 `/config/runtime`，确认旧 capability 快照已失效；
4. 再设置 `RECOVERY_ACCESS_MODE=normal`，重启并验证核心读写 smoke；
5. 保存 deployment id、epoch、runtime 摘要和 smoke receipt。

任何步骤失败都退回更严格的 `maintenance/readOnly`，不得通过恢复旧写 Authority 解决。

## 5. 证据输出

一次演练会生成：

- `manifest-verification.json`：backup artifact 校验摘要；
- `migration-apply.json` / `migration-verify.json`：versioned migration 证据；
- `restore-evidence.json`：backup、cutoff、目标哈希和恢复时长绑定；其中 `backupSchemaHead` 是 manifest 的来源版本，`restoredSchemaHead` 是 restore 后 migration verify 的目标版本；
- `integrity-evidence.json`：绑定 `restoredSchemaHead` 的 schema、动态 direct-user、Vault/operation scope、hash、
  purged owner 检查；
- `replay-evidence.json`：receipt coverage、range、duplicate/conflict 和 application evidence；
- `recovery-record.json`：最终 RPO/RTO 观测、GO/NO_GO 和所有证据 ID。

`recovery-record.json` 只能表示当次演练的观测值，不构成未实测的 RPO/RTO 承诺。

## 6. 人工复核与清理

1. 比对 `backupId`、`cutoffLSN`、`backupSchemaHead`、`restoredSchemaHead` 和所有 evidence ID。旧 backup 恢复后迁移到当前 head 是允许路径；仅当 migration verify 或 integrity evidence 与 `restoredSchemaHead` 不一致时 fail closed。
2. 在隔离数据库执行抽样只读查询，确认 owner 数量、删除 tombstone 与业务表数量合理。
3. 若结果为 `NO_GO`，保持生产流量不变，记录 blockers 和 owner。
4. 若未来结果为 `GO`，仍需单独的切流审批；本脚本不会自动切流。
5. 证据归档后，人工确认目标名再删除隔离数据库。不得通过通配符批量删除数据库。

## 7. 当前未关闭项

- `WI-S0-05-01` 尚需提供分层 deletion ledger；
- Stage 1 async authority 尚需提供完整 command/outbox/provider receipt；
- iOS 已实现 maintenance/read-only/signed-out runtime 合同、中央请求围栏和 epoch 变化失效逻辑；仍需 G3 设备/发布态验证；
- G2 必须使用真实 Postgres、真实加密 backup 和部署环境执行；
- Provider receipt replay 还受 G3 约束。
- Integrity evidence V3 已覆盖 legacy direct-user、Owner Truth Vault scope 和 async
  operation scope；身份根、async root authority 与可信 replay producer 仍须在各自的
  Gate 中单独收敛，不能由此推导为可切流。
