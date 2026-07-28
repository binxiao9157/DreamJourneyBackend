# Owner Truth Memory Search Offline Evaluation Baseline

范围：V4 Phase 5A 的 synthetic-only 检索回归基线。

## 覆盖范围

- 当前 `deterministicTextFallback` 的预期 citation 命中、排序和无命中行为。
- 评测结果不返回 query、SearchDocument 私有文本、结构化词、Source、Candidate 或
  Memory 内容。
- gold citation 必须属于同一 Owner/Vault/authority epoch 的 SearchDocument Projection；
  投影外 identifier 会在构造阶段拒绝。
- 指标只包含预期命中、遗漏、意外命中、首位命中和私有输入泄漏；不包含会话时长、
  消息数、点击率、活跃天数或 Persona 依赖。

## 运行

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-memory-search-offline-evaluation-gate.sh
```

该 gate 也包含在 `scripts/verify_backend.sh`。

## 边界

这是当前文本 fallback 的正确性回归，不是语义检索、模型评测、真实用户研究、
cohort 放量或公开搜索 UI 的完成声明。真实 embeddings、向量库、hybrid retrieval、
匿名化真实评测语料和召回质量阈值仍需独立的 Provider/隐私/产品 Gate。
