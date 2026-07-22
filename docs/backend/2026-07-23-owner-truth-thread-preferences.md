# Owner Truth ConversationThread 偏好 QA 合同

日期：2026-07-23

## 范围

本切片实现 V4 M0-A Slice 3B 的最小、默认关闭的 ThreadPreference 合同，用于把
访谈中的用户边界从单次 Session 状态扩展为当前 Owner 私有 Thread 的持久偏好：

1. `skipOnce` 保持既有 Session-only 语义，不创建 ThreadPreference；
2. `cooldown`（“以后再聊”）由服务端计算 `cooldownUntil`，到期前仍不可重新推荐；
3. `doNotAsk`（“不再问”）持久禁止推荐，且只能走既有显式确认恢复命令；
4. cooldown 到期后仍要求 Owner 的显式恢复，不能因时钟流逝自动重新打开；
5. 推荐规划和调用方提供的候选读取都会对 `cooldown`、`doNotAsk`、`stale` 偏好 fail closed。

它不保存题目、话术、对话正文、模型摘要、Provider 输出或用户自定义 cooldown 时间。
公开 Echo 视觉、公开 API 和推荐文案不变。

## 状态与持久化

迁移 `0040_owner_truth_thread_preferences` 新增两个 Owner Truth 表：

- `owner_truth.thread_preferences`：每个 `(vaultId, threadId)` 的当前偏好快照；
- `owner_truth.thread_preference_receipts`：append-only 命令回执，绑定 Vault、Owner、Thread、
  InterviewSession、authority epoch、前后偏好与服务端计算的 cooldown。

偏好更新和 Session boundary 更新处于同一工作单元；任一校验或写入失败都会一起回滚。
同一 `commandId` 的相同语义重试返回 `deduplicated`，不会再次更新 Session 或偏好。
复用 `commandId` 但更改 Owner、Thread、Session、authority epoch、操作或偏好时会拒绝。

## QA 隐藏接口与开关

既有接口在 QA 开关打开时，会将 `cooldown` 与 `doNotAsk` 写入 ThreadPreference：

```text
POST /v2/vaults/{vaultId}/interview-sessions/{sessionId}/boundary
POST /v2/vaults/{vaultId}/interview-sessions/{sessionId}/restore-do-not-ask
```

新增的 cooldown 恢复接口：

```text
POST /v2/vaults/{vaultId}/interview-sessions/{sessionId}/restore-cooldown
```

全部接口默认不在 OpenAPI 中。ThreadPreference 路径至少需要：

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`；
2. `OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED=true`；
3. 正数 `OWNER_TRUTH_THREAD_COOLDOWN_SECONDS`（默认 `604800`）；
4. Owner 认证会话与 `X-DreamJourney-QA-Owner-Truth: 1`。

默认关闭时，新增 `restore-cooldown` 返回无值的 404；现有 boundary/restore-do-not-ask
保留旧合同，不会意外开启持久 ThreadPreference。

## 推荐与访问边界

`OwnerTruthKnowledgeRecommendationReadService` 在服务器规划和候选读取前读取当前
ThreadPreference：

- `open` 或没有记录：可继续经过既有 Owner、authority、Thread/Session 资格校验；
- `cooldown`、`doNotAsk` 或 `stale`：不进入推荐，调用方指定该 Thread 时统一拒绝；
- cooldown 到期但未显式恢复：仍不可推荐。

因此，时间到期不会绕过用户边界，也不能靠客户端提交的 cooldown 或候选数据越过服务端决策。

## 验证

本地逻辑/API/路由检查：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_thread_preferences \
  tests.test_owner_truth_thread_preference_migration_contract \
  tests.test_owner_truth_knowledge_recommendation_read \
  tests.test_owner_truth_thread_preference_api

PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_route_ownership_registry \
  tests.test_route_authentication \
  tests.test_runtime_capabilities

PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
git diff --check
```

部署后使用隔离 Postgres smoke：

```bash
DATABASE_URL='<admin postgres dsn>' \
  PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-thread-preference-postgres-smoke.sh
```

脚本创建并删除临时数据库，覆盖默认隐藏、服务端 cooldown、幂等重放、跨 Owner 拒绝、
客户端时间注入拒绝、到期前恢复拒绝、到期后显式恢复、`doNotAsk` 显式恢复和 receipt
数量；不会读取或写入线上业务 Vault、档案或对话内容。

## 非目标

- 不开放用户可见的“以后再聊/不再问/恢复”界面；
- 不做自动恢复、自动 Topic 合并、VAD 或 Provider 调用；
- 不把 ThreadPreference 作为跨账号、家庭成员或 Visitor 权限；
- 不代表完整自然输入访谈、双推荐或公开知识地图已经完成。
