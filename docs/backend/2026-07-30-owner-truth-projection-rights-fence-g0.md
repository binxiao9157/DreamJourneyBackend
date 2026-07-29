# Owner Truth Projection Rights Fence G0

日期：2026-07-30
范围：`WI-S1-01-06` 的 Projection/Context/KBLite 权利失效边界
状态：`LOCAL_G0_VERIFIED / DEFAULT_OFF_INGRESS / G2_PENDING_DEPLOYED_POSTGRES_SMOKE`

## 解决的问题

原有 Projection 会在 `MemoryVersion`、Source 或 authority epoch 变化时失效，但独立的同意变更、用途限制或权利撤回没有进入 Projection checkpoint 的输入。旧 checkpoint 因而可能仍然显示为 `ready`。

本次增加 Vault + authority epoch 维度的不可变 rights revision fence：

- `owner_truth.projection_rights_events` 只保存 `revision`、`active/revoked`、`eventHash` 和 `commandIdHash`。
- 不保存同意正文、身份材料、档案内容或 Provider 数据。
- Projection checkpoint 记录 `rightsRevision` 与 `rightsEventHash`，并把 rights fence 纳入 `sourceHash`。
- revision 变化但尚未重建时，Projection 读返回 `rebuilding` + `rightsRevisionChanged`，不返回条目。
- `revoked` 时读取与重建都 fail closed；重建结果为 `blocked` + `rightsRevoked`。
- Context Shadow 因 Projection 非 ready 自动返回空 selection；KBLite compatibility envelope 自动 `cacheDisposition=discard`。

## 访问与生命周期边界

`OwnerTruthProjectionRightsRevisionCommand` 仅可由同一 Vault Owner 的内部应用服务写入。它要求：

1. 匹配 `authorityEpoch` 与 `expectedRevision`。
2. 事件哈希和命令哈希均为 SHA-256，不传递原始权利材料。
3. revision 递增且事件 append-only。
4. `revoked` 是本切片的终态；重新启用必须由后续独立的专项重新同意流程实现，不能通过普通更新复活。

没有新增 HTTP route、公开 UI、Provider 调用、跨账号读取、旧 KBLite 写入或自动权利同步。当前权利事件 ingress 仍默认关闭；本次实现的是容纳并强制消费已授权事件的内部合同。

## 数据库防线

迁移 `0061_owner_truth_projection_rights_fence`：

- 添加 append-only rights events 表和 owner/epoch/revision trigger。
- 向 `memory_projection_checkpoints` 添加 `rights_revision`、`rights_event_hash`。
- checkpoint trigger 在直接数据库写入时复核当前 Vault、authority epoch 和 active rights event。
- entry trigger 拒绝在 revoke 后新增或更新 Projection entry。

已有 checkpoint 在没有 rights event 时用隐式 `active / revision=0 / eventHash=none` 兼容；首次真实 rights event 后旧 checkpoint 必须重建。

## 验证

本地 Gate：

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python scripts/run-backend-owner-truth-projection-rights-fence-gate.sh
```

覆盖：

- active revision 增加使旧 checkpoint 失效，重建后恢复 ready。
- revoke 后 Projection、Context Shadow、KBLite 缓存均 fail closed。
- 非 Owner 不可写；revoke 后不能由同一命令接口重新激活。
- 迁移结构和触发器合同。

待完成：部署 `0061` 后，在隔离 Postgres 执行数据库 trigger smoke；再由单独、受授权的 consent/data-rights ingress 写入事件。未完成前不得宣称真实权利系统已接通。
