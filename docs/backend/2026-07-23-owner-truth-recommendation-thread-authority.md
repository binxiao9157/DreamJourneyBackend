# M0-B ConversationThread 权威绑定

日期：2026-07-23

## 本轮范围

隐藏 QA 的知识推荐读取此前只校验候选引用的 `MemoryVersion` 是否为当前 Owner
显式确认的知识维度证据。候选中的 `threadId` 仍由调用请求提供，无法证明它属于
当前 Owner/Vault，也无法证明其 authority epoch 与当前投影一致。

本轮增加只读、无内容的 `OwnerTruthConversationThreadAuthoritySnapshot`，并让
`OwnerTruthKnowledgeRecommendationReadService` 在执行推荐选择前，对每一个
不同的候选 `threadId` 强制验证：

1. Thread 是已持久化的 UUID ConversationThread；
2. Thread 属于当前 active Vault 与当前 Owner；
3. Thread authority epoch 与当前 Vault 及本次知识维度投影一致。

未知、跨 Vault、跨 Owner、非 UUID 或 authority epoch 已过期的 Thread 都统一以
无值的推荐读取无效错误拒绝，不泄露 Thread 是否存在、消息内容或元数据。

## 生命周期资格补充

同一条私有 Thread 在 Owner 明确切换话题后会进入 `paused`。此前权威快照只证明
它曾属于当前 Owner/Vault，仍可能让一个已经暂停的旧话题参与新的推荐读取。

现在快照还包含无内容的 `state`，且推荐读取只接受 `active` Thread：

1. 活跃 Thread 可以参与现有 value-free QA 推荐选择；
2. `paused` 或 `ended` Thread 与未知/越权 Thread 一样，统一拒绝；
3. 暂停必须经过既有 `PauseInterviewForTopicSwitchCommand`，没有为推荐路径增加
   直接状态写入或公开路由。

这只是推荐资格围栏，不实现 `cooldownUntil`、ThreadPreference、自动恢复或完整
主题合并策略；这些仍属于后续 M0-A/M0-B 产品切片。

## 会话资格补充

仅检查 Thread 为 `active` 仍不够：`cooldown`、`doNotAsk` 和 `skipOnce` 都可能保留
同一条历史 Thread，但其关联 InterviewSession 已不应再作为新的推荐上下文。

现在权威快照还绑定唯一的无内容 Session 记录，推荐读取只接受同时满足以下条件的候选
Thread：

1. Thread 为 `active`；
2. 当前 Owner/Vault/authority epoch 下恰好存在一个关联 Session；
3. Session 为 `active` 且 boundary 为 `open`。

`cooldown`、`doNotAsk`、`skipOnce`、暂停/结束 Session、缺失 Session 或多 Session 关联均
fail closed。此处不定义 `cooldownUntil`、自动恢复或新的 Session 生命周期；只是把既有
生命周期的不可推荐状态收进同一条读取资格围栏。

## 保持不变的边界

- 没有新增公开路由、公开 Echo 入口、Provider 调用、Candidate/Memory 写入或数据库迁移；
- `POST /v2/vaults/{vaultId}/knowledge-recommendations/read` 仍是默认关闭的 QA-only 路径；
- 返回值不增加消息、Thread metadata 或推荐问题正文；
- 此变更只补 Phase 4 M0-B 的 G0 授权前置条件，不代表双推荐、知识地图、自动 Thread 合并、
  cooldown 生命周期、G1 UIQA、G2 长期持久化验收或 G4 公开发布已经完成。

## 验证

本地已执行：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_conversation \
  tests.test_owner_truth_knowledge_recommendation_read \
  tests.test_owner_truth_knowledge_recommendation_read_api

PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
git diff --check
```

覆盖点：

- 有效 Owner/Vault/epoch Thread 可参与 value-free 推荐选择；
- 未知 Thread、跨 Owner 和 authority epoch 漂移被拒绝；
- 活跃 Thread 经既有 topic switch 变为 `paused` 后，推荐读取路由拒绝同一候选；
- `cooldown`、`doNotAsk`、`skipOnce` 关联 Session 即使保留 active Thread，也会被推荐读取拒绝；
- 隔离 Postgres smoke 在部署后验证真实 `conversation_threads` 查询、活跃 Thread 选择和
  Thread/Session 不可推荐状态拒绝，且不读取或写入生产业务数据。

部署后使用：

```bash
scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
python scripts/backend-owner-truth-knowledge-dimension-confirmation-postgres-smoke.py
```

该脚本会创建并删除临时数据库，不读取或写入生产业务数据。

## 部署验收记录

本次代码已部署到服务器 `main@aed7db8`，并在 API 容器中完成以下验证：

```bash
python scripts/migrate_db.py --apply --build-id aed7db8
python scripts/migrate_db.py --verify
scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
curl -fsS https://dreamjourney-api.liftora.cn/ready
```

结果：迁移账本头为 `0038` 且无待执行迁移；隔离 Postgres smoke 通过；公网
`/ready` 返回数据库读写、迁移头、认证配置均为 `ready`。本次没有对线上业务
Vault、档案或会话记录执行读取、写入或迁移。

## 生命周期资格部署验收

`main@79987bb` 已部署到 API 容器。本次无数据库迁移，部署后执行：

```bash
python scripts/migrate_db.py --apply --build-id 79987bb
python scripts/migrate_db.py --verify
scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
python scripts/backend-owner-truth-knowledge-dimension-confirmation-postgres-smoke.py
curl -fsS https://dreamjourney-api.liftora.cn/ready
```

结果：schema head 仍为 `0038`、无待执行迁移；两个一次性 Postgres 数据库 smoke
均通过。推荐 smoke 明确输出 `activeThreadSelected=true` 和
`pausedThreadRejected=true`，证明真实路由不会把已暂停的旧会话带入新的推荐选择。
公网 `/ready` 为 `ready`。本次没有读取、写入或迁移线上业务 Vault、档案、会话或
推荐数据。

## 会话资格部署验收

`main@6ad2005` 已部署到 API 容器。本次无数据库迁移，部署后执行：

```bash
python scripts/migrate_db.py --apply --build-id 6ad2005
python scripts/migrate_db.py --verify
scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
python scripts/backend-owner-truth-knowledge-dimension-confirmation-postgres-smoke.py
curl -fsS https://dreamjourney-api.liftora.cn/ready
```

结果：schema head 仍为 `0038`、无待执行迁移，公网 `/ready` 为 `ready`。一次性 Postgres
推荐 smoke 输出 `activeThreadSelected=true`，并分别输出
`pausedThreadRejected=true`、`cooldownSessionRejected=true`、
`doNotAskSessionRejected=true` 和 `skipOnceSessionRejected=true`。这证明推荐读取的真实
持久化查询会同时校验 Thread 与关联 Session 的状态/边界；测试数据库在运行后删除，未读取
或写入线上业务 Vault、档案、会话或推荐数据。
