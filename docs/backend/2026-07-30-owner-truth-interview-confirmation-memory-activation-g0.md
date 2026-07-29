# Owner Truth Interview Confirmation Memory Activation (G0)

日期：2026-07-30
范围：`WI-S1-01-06` 的一个默认关闭、仅内部验证的 Owner Truth 子闭环。

## 问题与边界

访谈候选的批量确认必须停在不可变 `DecisionReceipt`，不能因为确认动作本身
自动写入 `MemoryVersion`。此前的批量确认接口已经遵守这一点，但正式链路缺少
后续由 Owner 明确执行的激活边界。

本次新增的边界是：

```text
正式 ReviewBatch
  -> 批量确认（只写 DecisionReceipt）
  -> Owner 显式激活单个已确认 Candidate
  -> canonical Candidate review repository 创建/去重 MemoryVersion
  -> value-free projection rebuild effect intent
```

它不是 Candidate 提取 worker、不是自动审核、不是公开功能，也不将 KBLite 提升为
事实 Authority。

## 新增合同

隐藏路由：

```text
POST /v2/vaults/{vaultId}/interview-review-batches/{reviewBatchId}/confirmation/candidates/{candidateId}/memory-activation
```

请求体严格只有：

```json
{ "commandId": "client-command-id" }
```

客户端不能传入或修改 Candidate 正文、Source、DecisionReceipt、Memory、投影或
Provider 参数。响应只包含固定状态、ReviewBatch/Candidate 标识、是否已创建
MemoryVersion 和是否已登记 projection rebuild；不回传 receipt、MemoryVersion、
Source/effect 标识或私有内容。

## 准入与失败关闭

激活命令必须同时满足：

1. 调用者是当前 Vault Owner。
2. 当前请求有 `ownerTruthCandidateReview` 的正式 captured release-policy
   authorization；旧 QA-only 确认的空 authorization evidence 不能复用。
3. Candidate 的 DecisionReceipt 链接到同一 Vault、同一 ReviewBatch、同一 Owner 的
   正式批量确认根命令。
4. Receipt 的决定是 `accepted` 或 `corrected`；`rejected`、缺失、跨批次、跨 Owner、
   失活或过期状态均拒绝。
5. canonical `activate_memory_version` 在同一 Unit of Work 中继续校验 Candidate、
   Source、Vault、authority epoch 与现行版本。

Postgres 路径对 root/link/receipt 使用事务锁和同一 activation Unit of Work。重复激活
复用 canonical activation 的幂等结果，不产生第二个当前 `MemoryVersion`。

## 对现有路径的约束

- `confirmation/batch-accept` 的行为未变化：仍为 receipt-only，成功后 Memory 数量为零。
- 旧 QA batch、普通公开 Echo、三 Tab、Stitch 视觉、`/context/build` 输入均未改变。
- effect kernel 只记录不含记忆正文的 rebuild intent；本次不启用 Candidate extraction、
  Provider 调用或任何公开 Projection-to-Context cutover。
- 本次不新增数据库迁移。部署时只需要更新应用代码并重启；route authentication
  inventory 预期从 124 变为 125。

## 本地验证（G0）

- 新增正式确认 -> 显式激活 -> 重放去重的 API 覆盖；断言批量确认本身不创建
  MemoryVersion，激活响应不泄露 MemoryVersion/receipt 标识。
- 新增 QA-only receipt 不能通过正式激活路由的负向覆盖。
- disposable formal Postgres smoke 进一步串联正式激活发出的 rebuild intent：在临时库中先证明
  Projection 未重建时 Context 物化 fail-closed，再由默认关闭的 typed projection worker 消费恰好
  一个 job，要求 operation/outbox/job/consumer 全部终态完成；随后只读物化一个当前 confirmed
  Projection citation，并断言 QA-safe summary 不含候选正文或查询正文。
- 更新 route ownership、authentication、runtime capability 与 deployed-smoke 的
  route inventory 断言至 `125`。
- focused Owner Truth / route / session 测试通过。
- `./scripts/verify_backend.sh`、Python compile 和 `git diff --check` 通过。

## 仍需的 Gate

本机未配置专用的 `OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL`，因此本次没有运行
会创建并删除临时数据库的 formal Postgres smoke。该 smoke 只能在明确隔离的管理员
DSN 上运行，不能以线上生产数据库替代：

```bash
DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 \
OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL="$OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL" \
./scripts/run-backend-owner-truth-interview-confirmation-formal-postgres-smoke.sh
```

在该 G2 证据和后续受控 extraction/review worker 之前，本子闭环只能声明为
`INTERNAL_READY / G0_LOCAL`，不能宣称公开上线、Candidate 自动提取、生产 outbox
exactly-once、Provider 处理、真机或产品验收完成。
