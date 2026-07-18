# Owner Truth 旧数据盘点 Shadow

## 目的

本切片实现 `WI-S1-01-09` 的第一步：对旧 Archive、KBLite、`/memories`
和知识操作回执执行可重跑、无正文的只读 inventory。它不是 backfill，也不
切换任何公开读写路径。

## 默认边界

- 默认关闭，路由不出现在 OpenAPI。
- 仅在 `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`、用户登录态和
  `X-DreamJourney-QA-Owner-Truth: 1` 同时存在时可调用。
- 入口：`POST /v2/vaults/{vaultId}/legacy-migration/inventory`。
- 只读旧表：`archive_items`、`memories`、`kb_snapshots`、`kb_changes`、
  `kb_operation_receipts`。
- Conversation cache 当前没有后端持久化表，报告中固定为
  `conversationCache=unavailable`，不会把 assistant 对话迁为 Owner 记忆。

## 分类与提升边界

| 分类 | 当前处置 |
| --- | --- |
| `proven_confirmed` | 仅在 Owner、Source、终态 Decision receipt 和 revision 证据齐全时，后续 backfill 才可考虑生成 Memory v1。当前 inventory 仍不创建目标。 |
| `needs_review` | 写入 review queue 语义，默认不进入 Context。 |
| `observed_candidate` | Archive/KBLite/知识回执等观察性旧数据，最多进入未来 Candidate 路径。 |
| `quarantine` | owner evidence 缺失、冲突或旧 authority 非 active；不进入 Context。 |
| `do_not_migrate` | Conversation cache 等非 Owner memory 数据，排除。 |

现有 KBLite `kb_operation_receipts` 仅证明旧操作发生，不能替代 Owner Truth
terminal Decision receipt，因此永远不会单独提升为 `proven_confirmed`。

## 持久化合同

迁移 `0023_owner_truth_legacy_migration_inventory` 新增以下独立表：

- `owner_truth.legacy_migration_runs`：inventory hash、计数和无正文 summary；append-only。
- `owner_truth.legacy_migration_entries`：`legacyIdHash`、`recordHash`、五分类、证据状态、原因和 `targetState=notCreated`；append-only。
- `owner_truth.legacy_migration_checkpoints`：按 vault/classifier/domain 保存最新 inventory hash 与计数。

旧 `archive_items`、`memories`、KBLite 表不会被本迁移修改。所有输出只含 hash、
计数、枚举和 opaque run ID；不含旧正文、图片描述、图谱内容、操作结果或原始 legacy ID。

## 验证

本地静态/内存验证：

```bash
STORE_BACKEND=memory PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_legacy_migration \
  tests.test_owner_truth_legacy_migration_service \
  tests.test_owner_truth_legacy_migration_api \
  tests.test_owner_truth_migration_contract \
  tests.test_route_ownership_registry -v
```

独立 PostgreSQL smoke（自动创建并删除临时数据库）：

```bash
DATABASE_URL='<可创建临时数据库的 Postgres DSN>' \
  scripts/run-backend-owner-truth-legacy-migration-postgres-smoke.sh
```

它验证五类保守分类、重复运行幂等、KBLite 内容变更产生新 checkpoint、append-only
约束、无正文落库、以及 inventory 不创建 V4 Source/Candidate/Memory。

## 后续，不在本切片内

1. 真正的 backfill 只能在真实分布、backup/restore、Data/Privacy 抽样批准后进行。
2. promotion 必须为每条记录绑定完整 Owner/Source/terminal Decision/revision
   evidence，且保持默认不进入公开 Context。
3. shadow parity、QA vault 和 Owner cohort cutover 归 `WI-S1-01-09` 后续小切片，
   不能由本 inventory 自动触发。

## 部署证据

2026-07-19（Asia/Shanghai）已部署到后端生产容器。

- 后端实现提交：`cd739fb`；部署 smoke 输出修正：`224e56e`。
- `migrate_db.py --apply` 已应用 `0023`，随后 `--verify` 报告
  `expectedHead=0023`、`status=ready`、无待执行迁移。
- `backend-owner-truth-legacy-migration-postgres-smoke.py` 在部署容器中通过：
  `schemaHead=0023 entries=6`。它使用临时数据库验证可重复盘点、内容变化产生新
  checkpoint、append-only 约束、无正文输出和不创建 V4 authority target。
- 部署后的 route-authentication smoke 通过：`routeCount=89`、公开 runtime 可访问、
  匿名用户路由拒绝、machine/user audience 边界均生效。
- `/ready` 报告 `database`、`schema`、`auth`、`incident` 均为 `ready`。

此部署仍是 QA-only、默认关闭的 inventory 观察能力；没有对 Archive、KBLite、
`/memories`、公开 Echo 或 iOS UI 执行 backfill、promotion 或 authority cutover。

后续 `3d2f54b` 增加了独立的 shadow parity observer；此处的 `routeCount=89` 是
inventory 首次部署时的历史证据。后续部署后的当前 registry 为 `routeCount=90`，
具体的 parity 边界与新证据见
`2026-07-19-owner-truth-legacy-shadow-parity.md`。
