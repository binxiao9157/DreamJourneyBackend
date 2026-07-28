# Owner Truth 会话结果投影

## 目的

Phase 4C 需要让后续产品 UI 能够解释两件事：本次访谈是否已经形成了
Owner 确认的补充，以及是否还存在可安全继续的线索。本实现先提供一个
默认关闭、只读的 QA 合同；它不改变公开 Echo、不写入任何 Owner Truth
记录，也不是人生地图或语义搜索。

接口：

```text
POST /v2/vaults/{vault_id}/interview-sessions/{session_id}/outcome/read
```

启用条件：

```text
OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED=true
OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_QA_ENABLED=true
Authorization: Bearer <Owner access token>
X-DreamJourney-QA-Owner-Truth: 1
```

正常发布环境保持三个开关均为 `false`。

## 计数边界

`thisSession.confirmedMemoryVersionCount` 不是“模型提取到的候选数量”，也
不是当前 Vault 的全部记忆数量。只有同时满足以下条件的当前
`MemoryVersion` 才会被计入：

1. 它在当前 Owner/Vault 的 Memory Projection 中；
2. 它有匹配的、显式 Owner 知识维度确认回执；
3. 它的 `sourceId + sourceVersion` 可追溯到本会话一个已确认 review batch
   的、仍有效的 admitted conversation Source。

候选、未确认分类、失效 Source、旧 authority epoch、其他会话来源都不计入。
Projection 为 `rebuilding` 或 `unavailable` 时，确认相关计数返回 `null`，
不能把未知误写成 `0`。

`laterContinue.eligibleSavedContinuationCueCount` 只统计当前 `active + open`
会话中仍匹配 session version、authority epoch、确认 MemoryVersion 的显式
saved-continuation cue。历史 cue 不会被该投影重新激活。

## 返回边界

返回内容只包含：

- session/thread opaque ID；
- 现有 session presentation 状态和 `canContinue` / `canContinueLater`；
- review batch、admitted batch、确认 MemoryVersion 的计数；
- 当前确认 read state；
- 合格 saved-continuation cue 的计数。

绝不返回：对话消息、转录、Candidate payload、MemoryVersion 正文、原始
provider 输出、review batch ID、Source ID 或用户/模型生成的主题文本。

## 验证

本地完整 Gate：

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh
```

其中包含：

- `tests/test_owner_truth_interview_session_outcome_read.py`
- `tests/test_owner_truth_interview_session_outcome_read_api.py`
- Thread Summary、知识维度和推荐既有回归。

本次未声称已经完成：公开 UI、语义搜索、人生地图、真实用户会话回顾体验或
线上 Postgres smoke。后者应在后端部署后，结合正式 Owner QA 会话单独验证。
