# Owner Truth 持久 SearchDocument Projection

日期：2026-07-29
范围：V4 Phase 4C 的 QA-only 私有检索索引基础。

## 已实现

- 新增 owner_truth.search_document_checkpoints 与
  owner_truth.search_documents 的 additive migration 0046。
- 索引只从当前、Owner-confirmed、同 Vault 的 MemoryVersion Projection
  重建；不写入 Source、Candidate、MemoryVersion、KBLite 或 Context Authority。
- 每个索引绑定：
  - vaultId
  - ownerSubjectId
  - authorityEpoch
  - 当前 Memory Projection checkpoint
  - 私有文档 digest
- 读取前重新校验源 Projection 与私有文档 digest。缺失、源变更、索引篡改或
  任何不一致都会返回 rebuilding，不复用旧命中。
- 检索仍是确定性文本 fallback，明确返回
  semanticRankingAvailable=false；没有 embeddings、vector DB、模型调用或
  “已完成语义检索”的声明。

## QA 路由与开关

- `POST /v2/vaults/{vaultId}/memory-search/projection/rebuild`
- `POST /v2/vaults/{vaultId}/memory-search/read`

两条路由均不出现在 OpenAPI，要求已认证 Owner 会话和
X-DreamJourney-QA-Owner-Truth: 1。

| 开关 | 默认值 | 作用 |
| --- | --- | --- |
| OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED | false | 允许显式重建私有 SearchDocument Projection |
| OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED | false | 允许读取命中摘要 |

响应不返回 query、searchText、structuredTerms、Memory 内容、Source 或
Candidate payload；仅返回 citation-shaped hit、类型、敏感级别和计数。

## 重建与失效语义

1. 先重建既有 Memory Projection。
2. 再显式重建 SearchDocument Projection。
3. 读取只接受同一 Owner/Vault/authority epoch 的当前 source checkpoint。
4. 当前 MemoryVersion 变化后，旧 SearchDocument Projection 自动失效为
   rebuilding，必须重建后才可再次检索。

这保证派生索引不是第二事实库，也不会在 Owner 权威发生变化后继续提供旧结果。

## 验证

    PYTHON_BIN=.venv/bin/python \
      scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh

    DATABASE_URL='<admin postgres dsn>' \
      scripts/run-backend-owner-truth-memory-search-projection-postgres-smoke.sh

Postgres smoke 使用独立临时数据库，覆盖 migration head、默认隐藏、持久化重建、
命中无值输出、跨 Owner 拒绝、索引 digest 篡改 fail-closed 和显式重建修复。

## 未完成边界

- 公开人生地图/语义搜索 UI。
- 向量、混合检索、provider embeddings、召回质量评测与 gold corpus。
- 自动异步重建 worker；当前明确采用“源变化后 fail-closed，QA 显式重建”。
- 将检索结果注入公开 Echo 或作为 Persona/Memory Authority。
