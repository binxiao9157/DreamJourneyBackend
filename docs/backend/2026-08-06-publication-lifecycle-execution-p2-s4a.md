# P2-S4A 发布撤回与争议冻结的本地访问阻断

状态：`LOCAL_G0_VERIFIED / DEPLOYED_POSTGRES_VERIFIED / DEFAULT_OFF`

本切片实现的是 M2 发布生命周期的第一道安全边界：在 Owner 主动撤回或已确认第三方异议时，先在同一个后端事务内停止新的 Visitor 读取，再记录不可变、脱敏的回执。它没有把 M2 变成公开功能。

## 已实现

- `POST /v2/internal/publication-lifecycle/vaults/{vaultId}/publications/{publicationId}/withdraw`
  - Owner 主动撤回。
  - 将 `publication` 与独立 `publicProjection` 标记为 `withdrawn`。
  - 批量撤销该版本仍活跃的 ShareGrant 和 Visitor session。
- `POST /v2/internal/publication-lifecycle/vaults/{vaultId}/publications/{publicationId}/suspend`
  - 第三方异议进入 `suspended + conflictHold=true`。
  - 本切片没有 restore 路由；不能由后写入者直接恢复。
- 同一 `commandId` 使用相同内容重放时返回同一 receipt，`outcome=deduplicated`；改写 payload、Owner 或 authority epoch 时失败。
- `0082_publication_lifecycle_execution` 增加：
  - `publication.publications.conflict_hold`；
  - append-only `publication.publication_lifecycle_receipts`；
  - 已有 Source/MemoryVersion/Vault authority 变化触发阻断时，同事务撤销活跃 grant/session，并写入 `authorityTrigger` receipt。

## 默认关闭与访问条件

两条路由均不出现在 OpenAPI，且在认证前隐藏。要在内部 QA 环境访问，必须同时满足：

```text
PUBLICATION_AUTHORITY_QA_ENABLED=true
PUBLICATION_VISITOR_ACCESS_QA_ENABLED=true
PUBLICATION_LIFECYCLE_QA_ENABLED=true

X-DreamJourney-QA-Publication: 1
X-DreamJourney-QA-Visitor-Access: 1
X-DreamJourney-QA-Publication-Lifecycle: 1
```

还必须是当前 Vault Owner 的用户会话，并带准确的 `expectedAuthorityEpoch`。普通发布态、未带 header 的请求、匿名请求以及跨 Owner 请求都不能发现或执行该能力。

## 回执边界

响应只返回 publication/version ID、状态、撤销计数和以下脱敏 receipt：

```json
{
  "accessDenyState": "completed",
  "publicIndexCleanupState": "pending",
  "runtimeCleanupState": "notApplicable"
}
```

因此服务端只对“本地访问已拒绝”作出完成声明。Public Index、缓存、CDN、对象存储、外部 Provider、腾讯数智人 session 与任何第三方拷贝均没有被假定已清除。

## 验证

本地已通过：

```bash
.venv/bin/python -m unittest \
  tests.test_publication_lifecycle_api \
  tests.test_publication_lifecycle_execution_migration_contract \
  tests.test_publication_authority_api \
  tests.test_publication_visitor_access_api \
  tests.test_publication_management_read_api \
  tests.test_route_authentication \
  tests.test_route_ownership_registry \
  tests.test_auth_sessions \
  tests.test_runtime_capabilities
```

已在部署环境完成：

- 后端代码已推进至 `main@b473ed5`；功能提交为 `5e7942e`，随后用前向修复更正了 `0082` 的 receipt UUID 语法。
- 生产 PostgreSQL 已执行 `0082_publication_lifecycle_execution`，migration head 已验证为 `0082`。
- `GET /ready` 返回 database、schema、auth 与 incident 均为 `ready`。
- 在 API 容器内执行下述可丢弃数据库 smoke 已通过；它不写入生产业务数据。

部署前/后应运行可丢弃数据库 smoke：

```bash
PYTHON_BIN=.venv/bin/python \
bash scripts/run-backend-publication-lifecycle-execution-postgres-smoke.sh
```

该脚本从 `DATABASE_URL` 创建临时数据库，应用所有迁移，验证撤回、争议冻结、命令幂等、既有 Visitor session 拒绝，以及 Source authority 变化引起的 grant/session 自动撤销；不会写入配置的应用数据库。

## P2-S4B iOS QA consumer follow-on

- 后端 `main@ac7ab7a` 在 owner-only、已脱敏的 publication management summary 中增加 `lifecycleAuthorityEpoch`。它是撤回命令的乐观并发值，不是账户凭据、Visitor credential 或 Provider 标识。
- `main@909fe73` 将该值加入上述 disposable Postgres smoke；部署后 smoke 已再次通过。
- iOS 仅在 owner-management、Visitor 和 lifecycle 三个 QA gate 同时开启时读取该字段并显示撤回控件。撤回成功后会刷新为 publication/projection/grant 均已撤回，并在 Visitor 读取返回 lifecycle denial 时清空内存 scope、projection 和 session。
- 该 consumer 仍是 default-off 的内部 QA 能力，不新增公开 M2 路由、深链或普通发布入口。

## 明确未完成

- Public Index、缓存、CDN、对象存储和外部搜索的实际清理 worker/receipt。
- 绑定了 `publicationId/grantId` 的腾讯数智人 runtime session 释放。当前移动端数智人 session 没有 M2 scope，不能伪造“已关闭”回执。
- 公开 M2 入口、成年人资格 Provider、法务/隐私、Provider 成本与真机 Gate。
