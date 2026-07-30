# 2026-07-30 Postgres 隔离恢复演练（NO_GO）

适用 Work Item：`WI-S0-04-05`

## 结论

已使用当前加密备份完成一次新的真实 Postgres 隔离恢复演练。恢复目标为唯一的
`dj_recovery_*` 数据库，恢复后的迁移校验到当前 schema head `0065`，但最终结论为
`cutoverDecision=NO_GO`。本次结果证明恢复工具链和备份链路可运行，不构成恢复切流、
公开发布或 G2 Gate 通过。

## 执行边界

- 先通过既有 `dreamjourney-db-backup.service` 生成并校验当前 schema 的加密备份；
- 只从该备份恢复到新的隔离数据库；生产 `dreamjourney` 数据库、API 流量、
  recovery mode 和负载均衡均未修改；
- 恢复过程未删除任何旧 `dj_recovery_*` 库、备份或 `.env.backup*`；
- 恢复后生成 manifest、migration、restore、integrity、replay 和 recovery record；
- 仅为 orphan 清单创建 root-only 的本机 HMAC redaction key。该 key 未被输出、提交或
  写入 evidence。

## 已验证事实

| 项目 | 结果 |
| --- | --- |
| 当前加密备份 | 已生成并通过 schema `0065`、checksum、freshness 校验 |
| 隔离目标保护 | 仅允许新 `dj_recovery_*`；生产目标硬拒绝 |
| restore 与 migration verify | 成功，`expectedHead=appliedHead=0065` |
| integrity evidence | 已生成，但状态为 `failed` |
| replay evidence | 已生成，但状态为 `incomplete` |
| recovery record | 已生成，`cutoverDecision=NO_GO` |
| 生产切流 | 未执行 |

## 当前 NO_GO 原因

1. 隔离恢复库发现 `367` 条 legacy direct-user owner orphan。
2. Owner Truth identity root 与 async-effects root authority 仍缺独立可验证来源，因此
   integrity audit 保持 `unverified`。
3. 没有可信的 cutoff 后 replay bundle，replay evidence 返回 `replayBundleMissing`。

为避免把 orphan 误认领给任何账号，已在同一隔离库执行只读 quarantine inventory：

- `status=quarantineRequired`；
- 覆盖 21 个带 `user_id` 的 public 表，`unlocatableTableCount=0`；
- 清单仅包含 HMAC 定位摘要、表级计数和采样状态；
- `automaticMutation=false`、`automaticOwnerClaim=false`、`automaticDelete=false`。

这份清单只用于后续人工处置方案设计，不能作为重绑、删除或恢复流量的执行依据。

## 验证方式

```bash
sudo systemctl start dreamjourney-db-backup.service
sudo /opt/services/dreamjourney/DreamJourneyBackend/.venv/bin/python \
  /opt/services/dreamjourney/DreamJourneyBackend/scripts/db/verify_latest_backup.py \
  /var/backups/dreamjourney/postgres \
  --expected-schema-head 0065 \
  --max-age-hours 36

# 使用新的 dj_recovery_*、root-only backup key 和 expected NO_GO 执行：
sudo -E scripts/db/run-recovery-deployed-smoke.sh
```

运行隔离 orphan inventory 时必须继续使用同一隔离 DSN、`REPEATABLE READ + READ ONLY`
事务和 root-only redaction key；不得对 production DSN 运行该脚本。

## 后续门槛

1. 建立历史 orphan 的人工复核、加密操作映射、审批和回滚方案；不允许自动 owner claim。
2. 让 Owner Truth identity root 与 async-effects authority root 有可验证的权威链。
3. 由服务端 authority/worker 生成可信 replay bundle 和 application evidence。
4. 以上项具备后重新运行独立 G2 演练；只有 integrity 和 replay 同时通过，才可讨论
   read-only/normal 流量恢复审批。

