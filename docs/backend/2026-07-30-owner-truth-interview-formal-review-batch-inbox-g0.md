# Owner Truth 正式待确认 ReviewBatch 收件箱（G0）

日期：2026-07-30  
范围：M0-A 私有引导访谈的待确认批次发现与确认  
状态：`G0_LOCAL_VERIFIED / CAPTURED_FORMAL_DEFAULT_OFF / NO_PUBLIC_UI_OR_DEPLOYMENT`

## 目的

`OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED=true` 时，正式的
`echoTextInput` 已可在五轮叙述或有效暂停边界后原子地形成待确认
`ReviewBatch`。此前正式调用方没有安全、无正文的方式发现该批次，也无法
在不进入 QA-only Candidate 流程的情况下确认它。

本变更只补齐该私有操作闭环：同一 Owner 可发现自己仍待确认的批次，并确认
其中一个冻结边界。它不是 Candidate、Source、MemoryVersion 或 Provider
工作流的开放。

## 接口与权限

### 待确认收件箱

`GET /v2/vaults/{vault_id}/interview-review-batches/pending`

- 必须是已认证的 Vault Owner；
- 必须携带已捕获、服务端接受的 `echoTextInput` release-policy 决策；
- `x-dreamjourney-qa-owner-truth: 1` **不能**绕过上述正式策略；
- 路由不出现在 OpenAPI；同时进入 route-ownership/authentication inventory，
  当前受审计业务路由总数为 `141`；
- 响应仅返回确认所需的操作句柄：`reviewBatchId`、`threadId`、`sessionId`、
  `reviewBatchVersion`、`sessionVersion`、`trigger` 和已冻结 turn 数。

响应绝不包含访谈正文、Candidate、Source、MemoryVersion、Provider 状态或
Candidate 提案内容。

### 确认

现有的：

`POST /v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/acknowledgement`

保留原来的 QA-only 通道；同时增加受捕获 `echoTextInput` 决策保护的正式
通道。确认仍只将批次从 `pendingAcknowledgement` 推进为 `acknowledged`：

- 不创建 Source；
- 不创建 Candidate；
- 不生成 DecisionReceipt 或 MemoryVersion；
- 不调用任何 AI/数字人/语音 Provider；
- 同一 `commandId` 仍可幂等重放；
- 请求未携带有效捕获策略时，返回 `403 release_policy_denied`。

确认后的 Candidate 提案、Owner 决策和 Projection 仍由后续
`ownerTruthCandidateReview` 独立 Gate 处理，不能由本接口越权触发。

## 存储与可见性

收件箱查询仅返回满足全部条件的批次：

1. Vault、会话、线程和批次属于当前 Owner；
2. authority epoch 与当前 Vault 一致；
3. 批次状态为 `pendingAcknowledgement`；
4. 会话的 `pendingReviewBatchId` 指向该批次；
5. 会话与线程的绑定关系仍一致。

因此已确认、过期 epoch、其他 Owner、断开的会话/线程或已经被替换的批次都
不会进入该收件箱。

## 本地验证

已通过：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_formal_review_batch_inbox_api \
  tests.test_owner_truth_interview_input_api \
  tests.test_owner_truth_interview_review_batch_automation \
  tests.test_owner_truth_interview_review_batch_automation_api \
  tests.test_owner_truth_interview_review_batch_acknowledgement_api \
  tests.test_route_ownership_registry \
  tests.test_route_authentication \
  tests.test_auth_sessions \
  tests.test_runtime_capabilities

PYTHONPATH=. .venv/bin/python -m py_compile \
  app/main.py \
  app/services/owner_truth_conversation.py \
  app/services/owner_truth_interview_review_batch_inbox.py

PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
git diff --check
```

覆盖内容：正式策略缺失拒绝、QA header 不可读取正式收件箱、同 Owner 读取、
确认、确认重放、跨 Owner 拒绝，以及不存在 Candidate/Memory 侧效应。

当提供可创建隔离数据库的 `DATABASE_URL` 时，以下 smoke 还会验证 Postgres
实现的待确认查询在确认前返回当前边界、确认后清空：

```bash
scripts/run-backend-owner-truth-postgres-smoke.sh
```

## 尚未完成

- 未推送、未部署、未运行线上 Postgres smoke；
- 未增加 iOS 或公开 UI；
- 未开放 Candidate admission、Owner 决策、Projection 或 Echo 上下文切换；
- 公共发布前仍须经过独立 product/release Gate。
