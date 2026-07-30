# Owner Truth 正式引导推荐激活（G0）

## 问题与目标

正式引导推荐的展示响应包含策略模板问题。用户点击推荐卡后，客户端不能把该问题作为 Owner 叙述预填或发送到自然输入写入路径。

本项新增默认关闭的正式激活合同：

```text
POST /v2/vaults/{vault_id}/guided-recommendations/activate
```

客户端只可提交：

```json
{
  "commandId": "opaque client idempotency key",
  "recommendationSetId": "sha256 digest",
  "slot": "continuity | breadth"
}
```

服务端重新规划当前 Owner/Vault/session 权威状态，内部派生 candidate 和 session version，并复用既有 append-only 激活 receipt。响应仅表示客户端可等待 Owner 本人输入：

```json
{
  "schemaVersion": "owner-truth-guided-recommendation-activation-response-v1",
  "activation": {
    "status": "created | deduplicated",
    "slot": "continuity | breadth",
    "nextAction": "listen | broaden",
    "inputState": "awaitingOwnerNarrative"
  }
}
```

## 安全与发布边界

- `echoGuidedRecommendations` 仍是默认关闭的 captured release-policy feature。
- 路由要求完整用户会话和对应的 captured policy 决策；QA header 不能绕过该条件。
- 请求严格拒绝 `question`、`text`、candidate/thread/session/evidence/provider 等字段。
- 响应不返回 candidate、evidence、thread、session、reason、维度、问题文本或 Owner 私有内容。
- 激活只记录服务端重算出的选择 receipt；不会创建 ConversationMessage、Candidate、MemoryVersion、Provider 调用或 Echo 文本。
- 同一 `commandId + recommendationSetId + slot` 幂等；跨集合或跨 slot 重用同一 commandId 返回冲突。

## 持久化

迁移 `0068_owner_truth_guided_recommendation_activation_binding` 向现有 receipt 增加可空的 `guided_recommendation_set_id`（SHA-256）。历史 QA receipt 保持可读；只有正式引导激活写入该绑定，以便候选因已接受而不再出现在新 plan 时仍可安全重放。

## 本地验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_knowledge_recommendation_read_api \
  tests.test_owner_truth_knowledge_recommendation_activation \
  tests.test_owner_truth_guided_recommendation_activation_binding_migration_contract \
  tests.test_release_policy \
  tests.test_route_ownership_registry \
  tests.test_route_authentication
```

此项只建立 G0 合同和默认关闭的正式客户端接入条件；未部署、未开启闭测或公开发布，不构成 Owner Truth 发布完成声明。
