# Owner Truth KBLite Compatibility Read Envelope

日期：2026-07-19

## 范围

本次为 `WI-S1-01-06` 增加一个默认关闭、仅 QA 使用的 Projection 读取合同：

```text
confirmed standard MemoryVersion
  -> Owner Truth Projection
  -> KBLite compatibility read envelope
  -> isolated iOS QA cache
```

它不切换 legacy KBLite writer，不修改 `/kb/snapshot`、`/context/build`、公开
Archive 或公开 Echo。

## 后端合同

新增隐藏路由：

```text
GET /v2/vaults/{vault_id}/kblite-compatibility/read-envelope
```

它与既有 compatibility summary 使用同一套 Owner session、QA header 和 feature
gate：`OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`、
`X-DreamJourney-QA-Owner-Truth: 1`。未启用时保持 `404
ownerTruthKBLiteCompatibilityUnavailable`。

返回 `owner-truth-kblite-read-envelope-v1`：

- `state=ready` 时才返回 `cacheDisposition=replace`、Projection
  `authorityEpoch`、`projectionCheckpoint`、标准知识 facts 及其 `contentHash`。
- `disabled` 或 `rebuilding` 一律返回 `cacheDisposition=discard` 和空 graph。
- HTTP 响应使用 `Cache-Control: no-store`；客户端若选择本地缓存，必须经过
  自己的 AccountLease 与内容哈希校验，不能依赖 HTTP cache。
- graph 只映射 `memoryKind=knowledge`、`sensitivity=standard`、confirmed 且有
  `content.claim` 的条目。`sensitive/restricted`、experience、emotion 与不支持
  的结构仅作为 value-free filtering reason，不会进入可缓存 facts。
- facts 保留 immutable MemoryVersion/Source citation，但不带 Candidate proposal、
  DecisionReceipt 或 review rationale。

路由登记为 `USER_SESSION`，因此认证中间件会在未分类路由进入业务实现前拒绝。

## iOS 边界

iOS 侧使用独立的 `OwnerTruthKBLiteCompatibilityStore`，不读取也不写 legacy
`kb_graph_<userId>.json`。本地缓存必须同时匹配：

- subject、vault、session ID；
- generation、generation ID、lease authority epoch；
- Projection authority epoch、checkpoint 与 graph SHA-256。

任一账户 A -> B -> A 切换、租约变化、非 ready 响应、解码失败或 hash 不匹配都会
删除本地文件并返回 rebuilding/unavailable，而不是复用旧用户图谱。

该 iOS port 同样是 Debug/UI QA launch argument
`DJEnableOwnerTruthKBLiteCompatibilityQA` 才可调用，尚未接入任何公开 UI 或
Echo 上下文。

## 验证

本地 G0：

```bash
.venv/bin/python -m unittest \
  tests.test_owner_truth_kblite_compatibility \
  tests.test_owner_truth_candidate_review_api \
  tests.test_route_ownership_registry \
  tests.test_route_authentication \
  tests.test_runtime_capabilities \
  tests.test_auth_sessions
.venv/bin/python -m py_compile \
  app/main.py \
  app/services/owner_truth_kblite_compatibility.py \
  scripts/backend-owner-truth-postgres-smoke.py \
  scripts/backend-route-authentication-postgres-smoke.py
git diff --check
```

部署后 G2：

```bash
DATABASE_URL='<deployment postgres admin dsn>' \
  scripts/run-backend-owner-truth-postgres-smoke.sh
DATABASE_URL='<deployment postgres admin dsn>' \
  scripts/run-backend-route-authentication-postgres-smoke.sh
```

第二条 smoke 的 route inventory 应为 `91`；Owner Truth smoke 会验证读 envelope
的 standard-fact content hash、non-ready discard 以及 legacy isolation。

## 未完成项

- 这不是 KBLite cutover，也不是 Context/Echo public reader。
- 未增加自动 Projection rebuild；worker 和公开开关仍保持默认关闭。
- 仍需部署后 G2 Postgres smoke；G1/G3/G4 的真实设备、Provider 与发布证据不由
  本次内部合同闭环声明完成。
