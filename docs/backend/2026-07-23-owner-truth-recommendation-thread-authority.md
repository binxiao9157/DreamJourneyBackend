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
- 隔离 Postgres smoke 在部署后验证真实 `conversation_threads` 查询、未知 Thread 拒绝和
  Vault authority epoch 前移后的旧 Thread 拒绝。

部署后使用：

```bash
scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
```

该脚本会创建并删除临时数据库，不读取或写入生产业务数据。
