# Owner Truth 正式候选确认待办入口 G0

日期：2026-07-30
范围：V4 M0-A 私人引导访谈的正式候选确认发现合同

## 目标

既有的正式确认路由需要客户端已经持有 `reviewBatchId`。本次新增仅供
Vault Owner 发现待处理确认批次的只读入口：

```text
GET /v2/vaults/{vaultId}/interview-candidate-confirmations
```

它不改变 Candidate 生成、决定或 MemoryVersion 激活的权限边界。

## 合同与边界

- 路由为 `USER_SESSION`，认证/所有权策略为
  `ownerTruthInterviewCandidateConfirmationInboxRead`。
- 使用与单批次正式确认读取相同的 captured
  `ownerTruthCandidateReview` ReleasePolicy；QA 请求头不能作为绕过手段。
- 响应仅包含不透明 `reviewBatchId`、`readiness`、标准批量候选计数和必须逐项处理的候选计数。
- 响应不含 Candidate ID、Candidate 文本、Source ID、消息、admission、receipt 或内部 authority 值。
- 仓储只返回当前 Owner、活动 Vault、活动 conversation Source、所有 authority epoch 匹配且
  已 `acknowledged` 的 admission。
- 已无待处理候选的批次会被过滤，不能作为历史决定的待办残留。
- 客户端仍须对一个选定的批次调用既有
  `GET /v2/vaults/{vaultId}/interview-review-batches/{reviewBatchId}/confirmation`
  才能读取候选内容；批量接受、逐项决定和 MemoryVersion 激活仍保持既有独立路由。

## 验证

本地 G0 已通过：

```bash
STORE_BACKEND=memory PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_confirmation_formal_postgres_smoke \
  tests.test_owner_truth_interview_candidate_review_api \
  tests.test_route_ownership_registry \
  tests.test_route_authentication \
  tests.test_runtime_capabilities \
  tests.test_auth_sessions
.venv/bin/python -m compileall -q app tests
git diff --check
```

断言覆盖：ReleasePolicy 拒绝 QA header-only 请求、Owner 成功发现多个批次、跨 Owner 拒绝、响应
value-free、路由 inventory 为 `133`、以及正式 Postgres smoke 源码包含内容最小化与确认后待办清理断言。

## 尚未关闭的 Gate

G2 尚未执行。部署或声明 Postgres 证据前，必须使用显式的可丢弃管理员 DSN 运行：

```bash
DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 \
OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL='<isolated-admin-dsn>' \
scripts/run-backend-owner-truth-interview-confirmation-formal-postgres-smoke.sh
```

该脚本创建并删除临时数据库，不读取或写入线上业务数据。它会验证正式待办入口、确认后待办过滤、
授权、原子决定、回放、并发、MemoryVersion 激活和投影链路。G2 完成前，本功能不得被标记为部署
或公开发布完成。
