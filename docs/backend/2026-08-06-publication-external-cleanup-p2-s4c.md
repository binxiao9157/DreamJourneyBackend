# P2-S4C：撤回后的异步传播与外部回执

## 目的

本切片把已完成的本地访问阻断与后续外部资源清理解耦。

Owner 撤回、第三方异议或来源权威变化先在生命周期事务中完成以下动作：

- 阻断 Public Projection；
- 撤销 ShareGrant 与 Visitor session；
- 追加脱敏 lifecycle receipt。

之后才允许异步边界为五个外部域创建清理 effect：

- `publicIndex`
- `cache`
- `digitalHumanSession`
- `providerVoice`
- `objectStorage`

这些 effect 的初始状态一律是 `pending`。generic async operation 已受理、outbox 已创建、本地 tombstone 已写入，均不能被解释为外部清理已完成。

## 持久化合同

迁移 `0083_publication_lifecycle_external_cleanup` 新增：

- `publication.lifecycle_external_cleanup_effects`
- `publication.lifecycle_external_cleanup_receipts`

每个域与一个已完成本地 access-deny 的 lifecycle receipt 一一绑定，并关联既有：

- `async_effects.operations`
- `async_effects.provider_effects`

公开/QA 生命周期响应新增 additive 的 `externalCleanup` 字段，但保持 `schemaVersion=publication-lifecycle-v1`。该字段只包含：

```json
{
  "domain": "objectStorage",
  "state": "pending",
  "reasonCode": "publicationExternalCleanupQueued",
  "providerReceiptPresent": false
}
```

它不包含 publication、vault、Owner、effect、Provider 或对象坐标。

## 完成态规则

外部状态可为：`pending`、`partial`、`completed`、`unsupported`。

- `completed` 必须有独立 Provider 回执；
- 回执在本域只保留其 SHA-256 哈希，不保存原始 Provider ID、请求、对象键、URL、内容或凭据；
- 没有回执时只能是 `pending`、`partial` 或 `unsupported`；
- lifecycle effect / receipt 都是 append-only 证据；
- 任何 materialization 或 Provider 失败不会恢复本地撤回后的访问权。

真实 Public Index、缓存、腾讯数智人、火山语音和对象存储清理 adapter 尚未启用。因此当前全部五类 effect 会停留在 `pending`，这是预期状态，不是功能完成声明。

## 补料 worker

`app.async_effects.publication_external_cleanup_materializer_worker` 专门处理“由 authority trigger 产生、但还没有 effect 绑定”的历史 lifecycle receipt。

它只创建既有 async-effect / provider-effect 的脱敏协调记录；不调用外部 Provider，不会产生删除、会话关闭或资产操作。

默认配置：

```dotenv
ASYNC_EFFECT_V1_ENABLED=false
ASYNC_EFFECT_WORKER_ENABLED=false
PUBLICATION_EXTERNAL_CLEANUP_MATERIALIZER_ENABLED=false
```

只有 closed beta 且迁移验证完成后，才可以显式启动：

```bash
docker compose --profile publication-lifecycle-worker up -d \
  publication-external-cleanup-materializer-worker
```

启用前需同时由运维显式设置三个开关为 `true`。不要因为部署本次迁移而默认启动该 profile。

## 验证

静态/单元 gate：

```bash
bash scripts/run-backend-publication-external-cleanup-gate.sh
```

PostgreSQL disposable smoke：

```bash
bash scripts/run-backend-publication-lifecycle-execution-postgres-smoke.sh
```

该 smoke 从 `DATABASE_URL` 派生一个一次性数据库，验证 Owner 撤回、第三方异议、authority trigger、worker 补料、五域 pending receipt、访问即时拒绝和重放幂等；不会写入配置的应用数据库。

## 部署顺序

1. 部署后端代码并执行 migration `0083`。
2. 验证 `/ready` 与 migration head。
3. 在部署容器内运行 lifecycle Postgres disposable smoke。
4. 默认保持 materializer profile 关闭。
5. 只有有明确 Provider adapter、Provider 删除/关闭回执和 closed-beta 批准后，才启用对应 worker，再将单域状态从 `pending` 写为 `partial`、`completed` 或 `unsupported`。

## 部署证据

- 2026-08-06，后端 `main@58e346f` 已部署到 `miao-server`。
- migration `0083` 的 apply/verify 均返回 `status=ready`、`expectedHead=0083`、`appliedHead=0083`、`pendingVersions=[]`。
- `/ready` 返回 `ready`，数据库、schema、认证和 incident 组件均为 `ready`。
- 部署容器内的 `scripts/run-backend-publication-lifecycle-execution-postgres-smoke.sh` 已通过；它使用从 `DATABASE_URL` 派生的一次性数据库，验证撤回、异议、authority trigger、补料、五域 pending 回执和幂等重放，不写入应用业务数据。
- `ASYNC_EFFECT_V1_ENABLED`、`ASYNC_EFFECT_WORKER_ENABLED` 和 `PUBLICATION_EXTERNAL_CLEANUP_MATERIALIZER_ENABLED` 在部署环境均保持 `false`；materializer profile 未启动。
- 初次 disposable smoke 发现 receipt 写入的 SQL 冲突键与迁移约束不一致；修复提交 `58e346f` 已改为 `(effect_id, observation_hash)` 并通过全量后端验证及第二次 deployed smoke。该问题未写入应用业务数据。
