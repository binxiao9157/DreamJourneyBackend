# Owner Truth Projection Recovery 可执行状态防线

日期：2026-07-30

## 目的

正式 `MemoryVersion` 尚未出现在兼容 Projection 中，并不表示它仍会自动恢复。
如果对应异步 effect 已取消、阻断、失败或不再可执行，把它展示为 `rebuilding`
会形成错误承诺。

本次只收紧隐藏 QA 的恢复发现读路径：

`GET /v2/vaults/{vault_id}/interview-memory-projection-recovery-inbox`

公开 Archive、Echo、Projection Worker、重试行为和 iOS 视觉均不改变。

## 可见条件

一项正式激活的当前 `MemoryVersion` 只有同时满足以下条件，才会以固定
`state=rebuilding` 出现在 inbox：

1. 当前 Projection 中确实缺少该当前版本。
2. 找到与该版本精确绑定的 `ownerTruth.memoryVersion.activated` operation：同一
   Owner、Vault、`memoryVersion` ID/版本、兼容 Projection purpose、authority epoch 和
   content hash。
3. 对应 `ownerTruth.memoryProjection.rebuild` job 仍为 `pending`、`retryWait` 或
   `leased`，且没有取消请求。
4. operation 仍处于 `accepted`。

`cancelled`、`blocked`、`failed`、`unknown`、已完成或缺少精确 effect 的版本一律不
进入 inbox。该读路径不返回 job ID、operation ID、错误、MemoryVersion ID、Candidate
正文、Source、receipt、worker 或 retry action。

## 内存语义实现

`InMemoryEffectKernelRepository` 现在保留最小 intent 指纹和生命周期状态，仅向
Owner Truth semantic double 暴露布尔型“是否仍可执行”判断。API 回归覆盖：

- pending/retryWait/leased 仍可显示恢复中；
- cancelled/blocked 不会伪装为恢复中；
- Projection 已重建后 inbox 为空；
- 既有 owner/vault/正式策略边界保持 fail closed。

## 验证

- 聚焦 async-effect、formal recovery API、batch query、Projection worker 和 formal
  Postgres smoke 静态测试：46/46 通过。
- `python -m compileall -q app tests scripts`：通过。
- `git diff --check`：通过。
- `scripts/verify_backend.sh`：通过，1,619 个单测及现有 Gate。

## 部署边界

本次仅完成本地 G0 证据，未推送、未部署、未执行真实 Provider 或真机验证。上线前仍需在
隔离 Postgres 环境执行既有 formal confirmation smoke，确认实际 operation/job 状态的
查询条件已随部署生效。
