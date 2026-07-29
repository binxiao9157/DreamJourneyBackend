# Interview Candidate Proposal Result Status G0

日期：2026-07-30  
范围：Owner Truth 私有访谈候选提案状态的真实结果收敛  
状态：`G0_LOCAL_VERIFIED / QA_ONLY / DEFAULT_OFF / NO_WORKER_PROVIDER_OR_PUBLIC_PROMOTION`

## 解决的问题

已 admit 的访谈批次此前始终显示为 `requested / disabled / notReady`。当同一私有
`conversation` Source 已通过受控 synthetic contract 持久化 `ExtractionResult` 和
pending Candidate 后，该状态会与既有 Candidate review 读模型不一致。

现在状态读取只增加固定、无值的结果标签：

- 无结果：`requested / disabled / notReady`；
- 成功且有 pending Candidate：`succeeded / disabled / reviewReady`；
- 成功但无 Candidate：`succeeded / disabled / noCandidates`；
- 最新结果失败或隔离：`failed|quarantined / disabled / extractionFailed|extractionQuarantined`；
- 若较早的成功结果仍有 pending Candidate，后续失败/隔离不遮蔽可审核基线，保持
  `reviewReady`。

`effectExecution=disabled` 的语义没有改变：通用 Source worker 与 Provider 仍未开启。
状态只承认已经落库的受控结果，不把它表述成 Provider 执行或公开功能。

## 安全边界

- 继续要求现有 Owner QA gate、自身会话和 QA header；未开放公开 UI 或 Echo。
- 先校验 Source 存活性；被 redacted、删除、authority/provenance/hash 不匹配的
  Source 仍优先返回 `invalidated / inactive / blocked / disabled / notReady`。
- 不返回 Source、Effect、ExtractionResult、Candidate、receipt、MemoryVersion ID、文本、
  数量、证据 span 或 Provider 数据。

## 验证

本地已通过：

```bash
.venv/bin/python -m unittest \
  tests.test_owner_truth_interview_candidate_proposal \
  tests.test_owner_truth_interview_candidate_proposal_api \
  tests.test_owner_truth_candidate_extraction \
  tests.test_owner_truth_interview_candidate_review
.venv/bin/python -m compileall -q \
  app/services/owner_truth_interview_candidate_proposal.py \
  tests/test_owner_truth_interview_candidate_proposal.py \
  scripts/backend-owner-truth-conversation-postgres-smoke.py
git diff --check
```

`scripts/backend-owner-truth-conversation-postgres-smoke.py` 已增加真实 Postgres 断言，覆盖
`succeeded -> reviewReady` 和“后续 failed 但保留上一成功审核基线”。当前本机未配置
`DATABASE_URL` 或 `OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL`，所以该 Postgres smoke
尚待专用数据库/部署环境执行，不能记为 G2。

## 非目标

- 不新增 HTTP 写入、泛化异步 worker、Provider 或模型调用；
- 不创建新的 Candidate/DecisionReceipt/MemoryVersion/Projection；
- 不改变公开 Echo、KBLite、iOS UI、部署状态或 release gate。
