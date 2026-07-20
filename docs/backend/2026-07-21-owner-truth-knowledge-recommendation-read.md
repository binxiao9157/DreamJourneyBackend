# Owner-Confirmed Knowledge Recommendation Read

Date: 2026-07-21

## 范围

这是 M0-B 的一个默认关闭、仅供 QA 验证的读取适配层。它复用已有的
知识维度覆盖策略和选择器，但在执行选择前强制验证每个候选都引用了当前
Owner 已确认的、同一维度的 `MemoryVersion`。

它不是公开产品推荐功能，且不会接入 Echo、创建问题文本、写入推荐记录、
调用 Provider 或修改任何 Owner Truth 实体。

```text
current MemoryVersion
  + exact Owner confirmation receipt
  + candidate.evidenceKind=confirmedMemory
  + candidate.evidenceRefs belong to the same confirmed dimension
  -> deterministic value-free selection (at most continuity + breadth)
```

## 隐藏 QA 合同

```text
POST /v2/vaults/{vaultId}/knowledge-recommendations/read
```

该路由不出现在 OpenAPI 中，并同时要求：

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`；
2. `OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED=true`；
3. `OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED=true`；
4. 已认证的 Vault Owner 会话；
5. `X-DreamJourney-QA-Owner-Truth: 1` 请求头。

任意一个条件缺失时返回 `404 ownerTruthKnowledgeRecommendationReadUnavailable`。
跨 Owner/Vault 读取返回 `403 ownerTruthKnowledgeRecommendationReadDenied`。

请求体只允许 `candidates` 和可选 `crisisActive`。候选对象禁止携带
`ownerSubjectId`、`vaultId`、问题正文或记忆正文；Owner 和 Vault 只能由
认证会话和路径参数确定。

## 输出与失败关闭

成功响应使用 `Cache-Control: no-store`，仅包含：

- 当前确认维度读取的 checkpoint、计数和 opaque MemoryVersion 引用；
- 被选中的 `candidateId`、slot、dimension、facet、template ID、reason code；
- 被过滤候选的 value-free reason code。

不会返回记忆原文、候选问题正文、用户回答、Provider 输出或 KBLite 正文。

以下情况会拒绝请求，不会静默降级为其他候选或其他 Owner 的数据：

- `evidenceKind` 不是 `confirmedMemory`；
- 引用不是当前 Owner 已确认的 `MemoryVersion`；
- 引用虽已确认但属于不同知识维度；
- 投影处于 rebuilding/unavailable 之外的作用域错误；
- 请求体包含不在白名单中的字段。

投影处于 `rebuilding` 或 `unavailable` 时，响应保留该状态并返回空选择，
不评估候选。

## 验证

本地：

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

Disposable Postgres smoke：

```bash
DATABASE_URL='<admin postgres dsn>' \
  PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-knowledge-dimension-confirmation-postgres-smoke.sh
```

该 smoke 在临时数据库中验证确认回执、默认隐藏、receipt-bound
recommendation read、原文不泄露以及 superseded `MemoryVersion` 自动失效；
不会写入产品数据，也不会修改线上运行时开关。

## Gate 结论

本实现只完成 M0-B 的一个后端 QA 读取边界。公开知识地图、推荐生成、
问题措辞、持久化推荐、Echo 注入和产品 UI 仍不在本次范围内，不能据此声明
为已上线能力。
