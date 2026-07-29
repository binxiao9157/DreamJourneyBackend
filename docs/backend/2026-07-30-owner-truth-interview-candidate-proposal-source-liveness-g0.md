# Interview Candidate Proposal Source Liveness G0

日期：2026-07-30  
提交：`7c86649 fix(v4): invalidate stale interview proposal status`

## 范围

`GET /v2/vaults/{vaultId}/interview-review-batches/{reviewBatchId}/candidate-proposal/status`
在报告已 admit 的候选提案前，重新校验其私有 `conversation` Source。

- Source 必须仍为 active，且 owner、vault、authority epoch、source kind、content hash 和
  `reviewBatchId` provenance 全部匹配。
- Source 被删除、redacted、替换、跨 scope 或 provenance 不匹配时，接口只返回固定状态：
  `candidateProposal=invalidated`、`source=inactive`、`candidateExtraction=blocked`、
  `effectExecution=disabled`、`candidateReview=notReady`。
- 响应不返回 Source、effect、Candidate、receipt、MemoryVersion 标识或任何私有文本。

## 非目标

- 不执行 Candidate extraction，不新增泛化 Source effect worker，不调用 Provider。
- 不创建 Candidate、DecisionReceipt、MemoryVersion 或 Projection。
- 不改变公开 Echo、KBLite、iOS UI、部署状态或 QA feature gate。

## 验证

```bash
.venv/bin/python -m unittest \
  tests.test_owner_truth_interview_candidate_proposal_api \
  tests.test_owner_truth_interview_candidate_proposal \
  tests.test_owner_truth_interview_candidate_review_api \
  tests.test_owner_truth_interview_candidate_review
PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
git diff --check
```

结果：focused 21 tests 通过；完整 `verify_backend.sh` 通过，包含 1482 个 backend
unit tests 及现有 G0/FastAPI smoke。未运行 Postgres、未部署；因此本记录只构成 scoped local G0
证据。
