# Owner Truth 同 Vault 线程偏好隔离回归

## 范围

本证据补 `WI-S1-01-03 / M0A-31` 的一个已有实现边界：同一 Owner、同一
Vault 下，后续访谈线程不能继承、修改或恢复另一线程的 `cooldown` / `doNotAsk`
偏好。

这不是公开 Echo 功能，也不新增路由、数据表、Provider 调用或 UI。当前
Conversation 服务仍保持既有约束：一个 Vault 同时最多一条 `active` interview
session。测试在第一线程被 `cooldown` 暂停后启动第二线程，验证两条已持久化
线程的偏好隔离。

## 已有生产边界

- `owner_truth.thread_preferences` 以 `UNIQUE (vault_id, thread_id)` 保存偏好。
- 每个 preference receipt 同时绑定 `vault_id`、`thread_id`、`session_id`。
- `0040_owner_truth_thread_preferences.sql` 的触发器验证
  `session.current_thread_id == receipt.thread_id`。
- 服务层在 boundary 与 restore 前再次验证 session 的 thread、version、state 和
  boundary；错配返回 `OwnerTruthThreadPreferenceConflict`，HTTP 合同为
  `409 / ownerTruthThreadPreferenceConflict`。

## 新增回归

### 内存单测

`tests.test_owner_truth_thread_preferences`

`test_same_vault_thread_preferences_do_not_leak_or_accept_cross_thread_sessions`：

1. 线程 A 在同一 Vault 设置 `cooldown`。
2. A 暂停后启动线程 B；B 默认仍允许推荐，且不存在 B 的 preference row。
3. 使用 B 的 session + A 的 thread 发 boundary 被拒绝，且不写 preference/receipt。
4. B 独立设置 `doNotAsk`，A 仍是 `cooldown`。
5. A 的 cooldown 到期后，使用 B 的 session 尝试恢复 A 仍被拒绝；两条当前
   preference 都保持原值。

### Disposable Postgres smoke

`scripts/run-backend-owner-truth-thread-preference-postgres-smoke.sh`

同一 Vault 依次创建 A/B 两条 session，验证：

- A `cooldown` 与 B `doNotAsk` 同时存在且互不影响；
- `sessionB + threadA` 的 boundary 和 restore 都返回 `409`；
- 被拒绝请求不追加 receipt；
- A 恢复后 B 仍保持 `doNotAsk`；
- 最终持久化 receipt 数为 5，均为合法的 thread/session 配对。

该 smoke 在 API 容器内运行，因为服务器的 `DATABASE_URL` 使用 Compose 内网主机
`postgres`。它会创建并删除一个唯一命名的 disposable database，不读取或修改
业务 Vault 数据。

### iOS 回执隔离

`DreamJourneyTests/OwnerTruthContractsTests.swift` 的
`testInterviewNaturalInputUseCaseRejectsSameLeaseReceiptFromDifferentThreadAndSession`
使用同一 `AccountLease` 创建两条本地访谈 use case。若 B 收到 A 的同 Vault
`cooldown` receipt，`threadID/sessionID` 匹配失败，B 进入
`failed / contractMismatch` 并清空本地 receipt；A 状态保持不变。

## Gate 结论

- G0：实现与回归范围已覆盖。
- G1：仍为 QA-only；Release 不公开 boundary 控件。
- G2：2026-07-23，服务器 `main@8a28825` 的 API 容器内已通过 disposable
  Postgres smoke，输出包含 `sameVaultThreadIsolation=true`；重建后 API health
  为 `healthy`，公网 readiness smoke 通过。
- G3/G4：不适用；本切片没有 Provider、公开内容或权利扩张。
