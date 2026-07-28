# Owner Truth SearchDocument Projection v1

## 状态

- 范围：V4 Phase 4C 的 QA-only 检索边界。
- 默认状态：关闭，不影响公开 Echo、KBLite 或现有档案搜索。
- 路由：`POST /v2/vaults/{vault_id}/memory-search/read`，`include_in_schema=False`。
- 开关：`OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED=false`；还要求 Candidate QA、认证 Owner 会话与 `X-DreamJourney-QA-Owner-Truth: 1`。

## 已冻结的最小合同

1. SearchDocument 只能由当前、active 的 Owner Truth `MemoryVersion` Projection 构建。Candidate、Source、旧 KBLite snapshot、AI-only 推断、跨 Vault 和旧 authority epoch 均不能作为输入。
2. 每个 SearchDocument 唯一绑定一个 current `memoryVersionId`，携带当前 `authorityEpoch` 与 Projection checkpoint。Projection 未 ready 时返回 `rebuilding`，不复用旧命中。
3. 首版 QueryPlan 有严格的 query/limit 上限，只在请求内以规范化文本执行；响应、日志合同和 QA 证据不返回原始 query。
4. 首版检索模式固定为 `deterministicTextFallback`，响应显式声明 `semanticRankingAvailable=false`。它不是向量检索或 Provider 语义排序，不得对外宣称已经具备生产语义搜索。
5. 命中只返回 `memoryId`、`memoryVersionId`、`contentHash`、最小 memory metadata、命中原因和排名；不返回 `searchText`、`structuredTerms`、记忆正文、Source、Candidate、转录或 Provider 输出。
6. SearchDocument 是可重建 Projection，不是第二事实库。当前为请求内 ephemeral build；未来持久化 `memory_search_documents`、embedding 与混合排序必须保持相同 Authority / epoch / 删除失效边界。

## 输入与输出

请求体仅允许：

```json
{
  "query": "用户当前查询",
  "limit": 20
}
```

`query` 必填，规范化后最长 256 字符；`limit` 范围 1 到 20。响应带 `Cache-Control: no-store`，并仅返回：

```json
{
  "schemaVersion": "owner-truth-memory-search-read-response-v1",
  "search": {
    "state": "ready",
    "projection": { "documentCount": 1 },
    "queryPlan": {
      "retrievalMode": "deterministicTextFallback",
      "semanticRankingAvailable": false
    },
    "hits": [{ "citation": { "memoryVersionId": "..." } }]
  }
}
```

## 验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_memory_search \
  tests.test_owner_truth_memory_search_read_api -v

PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh
```

覆盖点：当前 confirmed MemoryVersion 命中、正文/Source/query 不泄露、Projection rebuilding 不保留旧命中、跨 Owner 返回 `403`、独立开关默认 `404`、路由认证注册表完整。

## 后续边界

在引入 `memory_search_documents` 持久化、embedding 或混合排序前，需要单独补齐：SearchDocument schema、受测 Projector、删除/authority epoch 失效、QueryPlan stage trace、金标检索语料和 G2 性能证据。不得把现有 iOS KBLite 的本地语义缓存直接提升为 V4 Authority。
