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

记录是 append-only。任何一个绑定条件失效时，历史 receipt 保留，但不会再参与计划。唯一的
受控例外是：同一 Session 从 cue 绑定的 `active + open` 版本 `N` 直接进入 `paused + cooldown`
版本 `N + 1`，并且服务端时钟确认冷却期已经结束；此时可优先恢复同一条 value-free cue，但
不会恢复 Session 状态或写入新的偏好记录。除此以外，以下变化均使 cue 失效：

- Session 版本变化（不含上述唯一的 `N -> N + 1` cooldown 过渡）、结束或边界变为
 `doNotAsk`/`skipOnce`；
- Vault/Thread/Memory authority epoch 漂移；
- MemoryVersion 被替换或不再 current；
- 目标 facet 已被另一条当前 Owner-confirmed 证据覆盖。

该例外只适用于**之前已显式保存**的 cue。单独选择 `cooldown` 不会从会话、对话正文、
模型摘要或 coverage 自动推导/创建 cue；没有仍有效 cue 时，服务端才保留泛化的
`elapsedCooldownContinuation` 回退。

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

## 原子“以后再聊”接口

产品语义中的“以后再聊”不能依赖客户端先保存 cue、再单独设置 cooldown。为避免网络中断或
重试造成半完成状态，隐藏 QA 合同新增：

```text
POST /v2/vaults/{vaultId}/interview-sessions/{sessionId}/defer-with-continuation
```

它使用和保存 cue 相同的严格 value-free 字段：`commandId`、`threadId`、
`expectedSessionVersion`、`memoryVersionId`、`targetDimension`、`missingFacet`。调用方不能选择
边界、传入子命令 ID 或附加文本；服务端固定为 `cooldown`，并从根 `commandId` 派生内部 cue
命令 ID。

该接口额外要求 `OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED=true`，其余 saved continuation cue
Gate 与 Owner QA 认证要求不变。它在同一个 Store unit of work 中：

1. 写入或重放 append-only `SavedContinuationCue`；
2. 将同一 Session 写入 server-owned `cooldown`；
3. 返回 cue 与 cooldown receipt。

任一步失败时整个事务回滚。相同根 `commandId` 的安全重试会重放 cue 与 cooldown receipt，
不会因为 Session 已进入 `paused + cooldown` 而误报过期。该接口默认关闭、不出现在 OpenAPI，
不构成公开 Echo/UI 能力。

## 计划边界

`POST /v2/vaults/{vaultId}/knowledge-recommendations/plan` 仍是 QA-only、只读的服务端计划接口：

- 没有合法 cue 时，保持原有 breadth-only 行为；
- 有合法 cue 时，可额外生成一条 `continuity`，其 `reasonCode` 为
  `explicitOwnerSavedContinuation`；若它通过已到期的直接 cooldown 过渡恢复，则为
  `elapsedCooldownSavedContinuation`；
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

该脚本创建并删除临时数据库，验证原子“以后再聊”默认隐藏、双 QA Gate、Owner 边界、cue 与
cooldown receipt 幂等、自由文本注入拒绝、到期后的显式 continuity 恢复、Session 版本变化后的
自动失效和只读计划；不会读取或写入线上业务 Vault、档案或会话。

## 部署证据

- 后端提交：`adc57cd feat(m0b): add explicit saved continuation cues`；
- 部署环境：线上 API 容器与 Postgres；
- 迁移：`0039_owner_truth_saved_continuation_cues` 已应用，`migrate_db.py --verify` 返回
  `appliedHead=0039`、`expectedHead=0039`、`status=ready`；
- 部署后 smoke：`backend-owner-truth-saved-continuation-postgres-smoke.py` 已通过，覆盖默认隐藏、
  Owner-only、幂等、跨 Owner 拒绝、自由文本拒绝、continuity 计划、Session 版本失效和只读边界；
- 公网 `/ready` 返回 `status=ready`。

### 冷却期恢复补充部署

- 后端提交：`082abf3 fix(m0b): preserve saved cues after elapsed cooldown` 已部署到线上 API 容器；
- 本次无数据库迁移；`migrate_db.py --verify` 返回 `appliedHead=0041`、`expectedHead=0041`、
  `status=ready`；
- 隔离 Postgres smoke 已通过，额外断言 `elapsedCooldownCuePreserved=true`，并同时确认
  `sessionVersionSuppressed=true` 与 `readOnly=true`；
- 公网 `/ready` 返回 `status=ready`。该 smoke 只创建和删除临时数据库，不读取或写入线上业务
  Vault、档案或会话数据。

### 原子“以后再聊”部署

- 后端提交：`f74f18b feat(m0b): atomically defer saved continuations` 已部署到线上 API 容器；
- 本次无数据库迁移；`migrate_db.py --verify` 返回 `appliedHead=0041`、`expectedHead=0041`、
  `status=ready`；
- 部署容器执行 `backend-owner-truth-saved-continuation-postgres-smoke.py` 已通过，包含
  `atomicDefer=true`、默认隐藏、双 QA Gate、Owner-only、cue/cooldown 双 receipt 幂等、跨 Owner
  拒绝、自由文本拒绝、到期恢复和只读边界；
- 部署容器执行 `backend-route-authentication-postgres-smoke.py` 已通过，`routeCount=110`、
  `unclassifiedCount=0`，并验证 public/user/machine 三类认证边界；
- 公网 `/ready` 返回 `status=ready`。两个 smoke 均使用临时或临时测试账号，不读取或写入线上业务
  Vault、档案或会话数据。

## 非目标

- 不做公开“稍后继续”入口、推荐文案或 Echo 注入；
- 不从对话内容、模型摘要、KBLite facts 或 coverage 自动推断用户意图；
- 不新增会话自动恢复、长期通知、Provider 调用或跨账号共享。
