# Owner Truth 显式续聊线索 QA 合同

日期：2026-07-23

## 范围

M0-B 的“接着聊”不能从活跃会话、覆盖缺口、对话摘要或模型猜测中推导。
本切片仅增加一个默认关闭的私有 QA 合同：Owner 明确保存一条续聊线索后，服务端才可能
在同一条仍有效的会话中生成一条 value-free `continuity` 推荐。

它不保存对话正文、问题文本、模型摘要或 Provider 输出，也不接入公开 Echo/UI。

## 绑定条件

一条 `SavedContinuationCue` 必须同时绑定：

1. 当前 active Vault、当前 Owner 与 authority epoch；
2. 一个 `active + open` 的 ConversationThread/InterviewSession；
3. 创建时的 `expectedSessionVersion`；
4. 当前 Owner-confirmed 的一个 MemoryVersion 与相同知识维度；
5. 该维度仍未覆盖的一个 facet。

记录是 append-only。任何一个绑定条件失效时，历史 receipt 保留，但不会再参与计划：

- Session 版本变化、暂停、结束或边界变为 `cooldown`/`doNotAsk`/`skipOnce`；
- Vault/Thread/Memory authority epoch 漂移；
- MemoryVersion 被替换或不再 current；
- 目标 facet 已被另一条当前 Owner-confirmed 证据覆盖。

## 隐藏接口

```text
POST /v2/vaults/{vaultId}/interview-sessions/{sessionId}/saved-continuation-cues
```

接口不出现在 OpenAPI 中，且必须同时满足：

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`；
2. `OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED=true`；
3. `OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED=true`；
4. `OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED=true`；
5. `OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED=true`；
6. Owner 的认证用户会话与 `X-DreamJourney-QA-Owner-Truth: 1`。

请求只接受 `commandId`、`threadId`、`expectedSessionVersion`、`memoryVersionId`、
`targetDimension`、`missingFacet`。任何正文、问题、候选、排序、权限或自定义文本字段都会被拒绝。

同一 `commandId` 的相同语义重试返回 `deduplicated`；一个 Session 最多有一条不可变 cue。

## 计划边界

`POST /v2/vaults/{vaultId}/knowledge-recommendations/plan` 仍是 QA-only、只读的服务端计划接口：

- 没有合法 cue 时，保持原有 breadth-only 行为；
- 有合法 cue 时，可额外生成一条 `continuity`，其 `reasonCode` 为
  `explicitOwnerSavedContinuation`；
- 返回只含 opaque 标识、维度/facet、template ID 与 reason code；
- 客户端 `/knowledge-recommendations/read` 仍拒绝调用方伪造的 `savedContinuation` evidence。

## 验证

本地逻辑与 API：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_knowledge_recommendation_read \
  tests.test_owner_truth_knowledge_recommendation_read_api \
  tests.test_owner_truth_knowledge_recommendations \
  tests.test_owner_truth_saved_continuation_migration_contract
```

部署后隔离 Postgres smoke：

```bash
DATABASE_URL='<admin postgres dsn>' \
  PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-saved-continuation-postgres-smoke.sh
```

该脚本创建并删除临时数据库，验证默认隐藏、Owner 边界、命令幂等、自由文本注入拒绝、
显式 continuity 计划、Session 版本变化后的自动失效和只读计划；不会读取或写入线上业务 Vault、
档案或会话。

## 部署证据

- 后端提交：`adc57cd feat(m0b): add explicit saved continuation cues`；
- 部署环境：线上 API 容器与 Postgres；
- 迁移：`0039_owner_truth_saved_continuation_cues` 已应用，`migrate_db.py --verify` 返回
  `appliedHead=0039`、`expectedHead=0039`、`status=ready`；
- 部署后 smoke：`backend-owner-truth-saved-continuation-postgres-smoke.py` 已通过，覆盖默认隐藏、
  Owner-only、幂等、跨 Owner 拒绝、自由文本拒绝、continuity 计划、Session 版本失效和只读边界；
- 公网 `/ready` 返回 `status=ready`。

## 非目标

- 不做公开“稍后继续”入口、推荐文案或 Echo 注入；
- 不从对话内容、模型摘要、KBLite facts 或 coverage 自动推断用户意图；
- 不新增会话自动恢复、长期通知、Provider 调用或跨账号共享。
