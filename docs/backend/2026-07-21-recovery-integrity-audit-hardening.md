# DreamJourney Recovery Integrity Audit Hardening

日期：2026-07-21
适用工作项：`WI-S0-04-05`

## 目的

恢复完整性检查此前依赖固定 owner 表名单。该方式会在新增认证、收据或业务表时
产生漏审风险：新表即使带直接 `user_id`，也可能未被检测到孤儿 owner 或已 purge
owner 数据复活。

本次将 integrity evidence 升级为 V3。恢复检查在隔离目标库中从
`information_schema` 动态发现 legacy `public`、`owner_truth` 和 `async_effects`
范围，并对每张表产生可归因、无用户正文的 scope 审计计数。

## V3 合同

V3 evidence 必须同时提供：

- `publicDirectUserId`：动态发现的 `public.*.user_id`、孤儿 owner 和 purge owner
  表级计数；
- `ownerTruthVaultScope`：Vault 根缺失、`owner_subject_id` 不一致和未分类表；
- `asyncEffectsOperationScope`：operation 根缺失、owner/vault/epoch 不一致和未分类表；
- `explicitExemptions`：当前唯一允许的 value-free
  `async_effects.worker_loss_observations`；
- 与 legacy aggregate count 一致的 public 表级计数。

任何表集合为空、排序不稳定、表级计数缺失或 aggregate 不一致都会 fail closed。
发现孤儿 owner、Vault/operation scope 违规或 purge owner 复活时，恢复记录保留
`NO_GO` 并记录对应 blocker。

V1/V2 evidence 仍可解析以保留历史证据，但会成为
`integrityAuditCoverageUnverified`，不能支持 GO 决策。V3 也会明确标注当前尚未
具备独立 identity root 和 async root authority 证明，不能因扫描覆盖完整而签发 GO。

## 验证

本地合同测试：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_recovery_record \
  tests.test_recovery_integrity_audit
```

隔离 Postgres smoke：

```bash
DATABASE_URL='<isolated-postgres-dsn>' \
  scripts/db/run-recovery-integrity-audit-postgres-smoke.sh
```

该 smoke 创建临时数据库和一个迁移之外的 direct-`user_id` fixture 表，再写入一个
没有对应 `users` 记录的 user ID；再注入一个缺失 Vault 的 Owner Truth run 和一个
与 operation scope 不一致的 async outbox。通过条件是三个 fixture 都被归因到各自的
表级证据、产生相应 NO_GO blocker，并且 value-free worker observation 出现在显式
豁免清单。脚本会清理临时数据库。

## 不在本次范围

- 不修改生产数据、生产 schema 或切流行为；
- 不对间接 owner 关系作自动推断；
- 不生成、签发或信任 replay bundle/application evidence；
- 不将 V3 integrity coverage 解释为 recovery GO。

可信 replay producer 和全库 replay authority 仍是独立的后续 Gate。
