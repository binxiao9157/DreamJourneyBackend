# Owner Truth 正式 Candidate Proposal Admission（G0）

日期：2026-07-30
范围：已确认私有访谈批次进入受控 Source/effect staging 的正式授权边界
状态：`G0_LOCAL_VERIFIED / CAPTURED_FORMAL_DEFAULT_OFF / QA_LANE_RETAINED / NO_PUBLIC_UI_OR_DEPLOYMENT`

## 目的

正式访谈已经可在受捕获的 `echoTextInput` 策略下形成并确认一个私有
`ReviewBatch`。本项只补齐下一步的独立授权：Owner 可在已确认批次上，使用
`ownerTruthCandidateReview` 的捕获策略，将冻结的私有消息窗口写入一个
`conversation` Source 和一个默认关闭的 candidate-extraction effect。

这不是 Candidate 审核、MemoryVersion 激活、公开发布或 Provider 执行的开放。

## 路由与授权

```text
POST /v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-proposal/admit
```

同一路由保留两个明确隔离的 lane：

| Lane | 条件 | Admission ledger evidence |
| --- | --- | --- |
| QA | 现有 `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`、自有会话和 `X-DreamJourney-QA-Owner-Truth: 1` | 空对象 `{}` |
| 正式 | 自有会话、服务端接受的 `ownerTruthCandidateReview` captured release-policy decision | value-minimized `authorization_evidence` |

正式 lane 不接受 `echoTextInput` capture，也不能被单独 QA header 伪装。路由仍
隐藏于 OpenAPI，未新增公开 iOS/Echo 入口；route inventory 数量保持 `141`。

## 存储边界

正式 capture 只写入：

```text
owner_truth.interview_review_batch_candidate_admissions.authorization_evidence
```

保存内容是 schema、feature、策略版本/修订、hash 化的 account generation 和
decision ID、audience/cohort、client build、expiry。它不保存 bearer token、原始
session ID 或原始 decision ID。

证据刻意**不**写进 Source metadata、Source command receipt 或 async effect。
这些对象不是此次 formal authorization 的持久授权根，后续 Candidate 确认与
MemoryVersion activation 仍以自己的正式决策 receipt 为权威。

同一 `commandId`：

- 允许正式 lane 使用新的策略 decision/expiry 幂等重试；
- 禁止 QA-only 与 formal lane 互相重放；
- 禁止换成其他 feature；
- 不会新增第二个 Source/effect。

## 仍然不做的事情

- 不执行 extraction worker、模型或 Provider；
- 不创建/展示 Candidate、DecisionReceipt、MemoryVersion、Projection 或 SearchDocument；
- 不改变公开 Echo、KBLite、三 Tab UI 或 release 默认值；
- 不部署、不标记 G2/G3/G4。

## 本地验证

已通过：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_formal_review_batch_inbox_api \
  tests.test_owner_truth_interview_candidate_proposal \
  tests.test_owner_truth_interview_candidate_proposal_api \
  tests.test_owner_truth_interview_candidate_proposal_authorization_evidence_migration_contract

.venv/bin/python -m py_compile \
  app/main.py \
  app/domain/owner_truth/interview_candidate_proposal.py \
  app/services/owner_truth_interview_candidate_proposal.py
```

覆盖正式 capture 缺失/错误 feature 拒绝、正式成功、最小化响应、capture 只在
admission ledger、Source/effect 不携带 capture、正式新 decision 幂等重试，以及
QA/formal 同 `commandId` 交叉重放拒绝。

`scripts/backend-owner-truth-conversation-postgres-smoke.py` 已补充隔离数据库的
Postgres 持久化断言。当前本机未配置可创建 disposable database 的
`DATABASE_URL`，所以该 smoke 未执行，不能把本项记录为 G2 或部署证据。
