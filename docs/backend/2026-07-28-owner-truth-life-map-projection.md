# Owner Truth 人生地图 Projection（Phase 4C）

日期：2026-07-28

## 本轮范围

新增默认关闭、只读的 QA 合同：

```text
POST /v2/vaults/{vaultId}/life-map/read
```

它把同一 Owner/Vault/authority epoch 下的两个现有 Projection 组合为一份可重建的人生地图骨架：

1. `OwnerTruthKnowledgeDimensionReadService` 的六个稳定维度覆盖；
2. 已确认 `MemoryVersion` 绑定的 Thread anchors；
3. 仅由共享当前确认锚点生成的、可逆的 Thread association。

输出只包含：

- 六个稳定维度的证据数、已覆盖 facet 数、缺口数和锚定 Thread 数；
- 当前 Thread/session 状态、关联维度键和关联数量；
- 可逆关联的 opaque ID、Thread ID 和原因码；
- 当前 authority/checkpoint/policy 元数据与 stale cue 过滤计数。

不输出：

- `MemoryVersion`、Source、Candidate、审核回执或证据引用 ID；
- 记忆正文、对话转录、Thread 标题、模型主题、Provider 输出；
- 完成百分比、推断经历或任何写入动作。

## 安全边界

路由必须同时满足：

```text
OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED=true
OWNER_TRUTH_LIFE_MAP_READ_QA_ENABLED=true
认证 Vault Owner 会话
X-DreamJourney-QA-Owner-Truth: 1
```

默认关闭。非 Owner、范围错配、失效投影和不支持 payload 均 fail-closed；响应带
`Cache-Control: no-store`。地图不合并、归档或修改 Thread，也不改变 Source、Candidate、
DecisionReceipt、MemoryVersion 或公开 Echo。

## 验证

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_life_map \
  tests.test_owner_truth_life_map_read_api \
  tests.test_route_ownership_registry -v
PYTHON_BIN=.venv/bin/python scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh
git diff --check
```

专项覆盖：

- 已确认覆盖与两个 Thread 的可逆关联；
- stale cue 被过滤且不会留下过期关联；
- rebuilding 状态不返回旧地图；
- QA 开关关闭、额外 payload、跨账号访问被拒绝；
- 输出不含私有正文、`memoryVersionId` 或 `sourceId`。

## 明确未完成

- 面向公开产品的人生地图入口或 UI；
- 对实际记忆内容的浏览、搜索结果详情和纠错路径；
- 语义搜索的 V4 Owner-confirmed 索引。现有 KBLite 本地语义搜索不能反向成为 Owner Truth 事实 Authority；
- 真实 Postgres 部署 smoke、G1/G4 产品验收。

因此，这个合同是 Phase 4C 的可验证数据骨架，不是“人生地图已发布”或“语义搜索已完成”的结论。
