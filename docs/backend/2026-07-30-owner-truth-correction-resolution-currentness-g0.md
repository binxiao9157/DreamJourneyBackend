# Owner Truth Correction Resolution Currentness G0

日期：2026-07-30
范围：QA-only Owner Truth correction resolver 的来源存活性与终态当前性
状态：`G0_LOCAL_VERIFIED / DEFAULT_OFF / DEPLOYMENT_AND_ISOLATED_POSTGRES_G2_PENDING`

## 解决的问题

Correction Request 创建时已经绑定 Answer/Citation、当前 MemoryVersion、原始 Source 和私有 correction Source。此前 Resolution 阶段只复核了 MemoryVersion 是否 current：若请求 pending 后任一 Source 被 redacted/deleted，或特权数据库写入试图为已 superseded 的版本写入 `rejected` 终态，终态边界没有完整重验。

本轮将 Resolution 收敛为同一条 current active Source chain：

- 原始 Source 必须仍属于 Owner、`active`、authority epoch 与 Vault 一致，并且 `source_version` 与 cited MemoryVersion 一致；
- correction Source 必须仍属于 Owner、`active`，且 authority epoch 与 Vault 一致；
- cited MemoryVersion 必须仍是同一 Memory 的 current active version；
- 任一条件失效，服务返回 stale，Candidate 与 correction request 不被消费为终态；
- 直接数据库写入也不能把已 superseded 的 predecessor 写成 `rejected`。

## 实现

- `app/services/owner_truth_correction_request.py`：Resolution 前重验原始 Source、correction Source 与 cited version，并在读取链路上加共享锁。
- `db/migrations/0058_owner_truth_correction_resolution_currentness.sql`：替换数据库 trigger 校验函数；将原始/校正 Source 存活性和 predecessor currentness 放在任何终态分支之前校验。
- `scripts/backend-owner-truth-postgres-smoke.py`：加入直接插入 stale `rejected` resolution 必须被 trigger 拒绝的回归路径。
- 单元与迁移合同测试覆盖两类来源在 request 创建后失效的服务层拒绝，以及迁移约束存在性。

## 本地验证

已通过：

```bash
.venv/bin/python -m unittest \
  tests.test_owner_truth_correction_request \
  tests.test_owner_truth_correction_resolution_currentness_migration_contract \
  tests.test_owner_truth_migration_contract

PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
git diff --check
```

全量 backend 验证通过：1,487 个测试、G0 gates、FastAPI smoke 与 diff 检查均通过。日志中的预期 policy 拒绝和 FastAPI `on_event` 弃用警告不影响结果。

## 部署边界

本机没有配置 `DATABASE_URL` 或 `OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL`，因此没有声称 PostgreSQL trigger 已被实际执行。部署 migration `0058` 后，必须运行：

```bash
scripts/run-backend-owner-truth-postgres-smoke.sh
```

在隔离 PostgreSQL 和部署环境完成这条 smoke 前，本项新的数据库 G2 仍为 pending。

## 非目标

本轮不新增公开校正入口、不改变 iOS UI、不接入 Provider、不执行生产数据迁移或权限策略切换。
