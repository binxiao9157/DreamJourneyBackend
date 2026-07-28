# Owner Truth Thread Summary Projection

## 范围

本切片补充 V4 Phase 4A 的一个保守读模型：从当前 Owner 已确认的知识维度
证据、当前 Thread 权威快照和显式“以后再聊”线索中构建 `ThreadSummary` 与
跨 Thread 关联。

它不创建第二事实库，不写入 `Source`、`Candidate`、`DecisionReceipt`、
`MemoryVersion` 或 `ConversationThread`，也不新增公开路由、iOS UI、Provider
调用或数据库 migration。

## 关联规则

首版只允许一条可解释、可重建的关联规则：

```text
Thread A -- explicit saved-continuation cue -- current confirmed MemoryVersion X
Thread B -- explicit saved-continuation cue -- current confirmed MemoryVersion X
=> A 与 B 仅作 associatedOnly 展示
```

关联原因固定为 `sharedConfirmedMemoryVersion`。它不是语义模型对话题的猜测，
也不是 destructive merge：

- 不删除或归档任何 Thread；
- 不合并 Source、Candidate、MemoryVersion 或历史视角；
- 不读取或输出消息正文、记忆正文、模型标签或 Provider 文本；
- 没有同一条当前已确认 `MemoryVersion` 证据时保持未关联；
- cue 的 Owner、Vault、authority epoch 不一致时 fail closed；
- cue 指向的 MemoryVersion 不再是当前确认覆盖的一部分时只计为 stale，不参与关联。

该策略满足“主题可自动关联但不越权改写历史”的最小安全边界；真正基于语义的
主题归并、人类可读主题标题、人生地图和语义搜索仍是后续 P1/Phase 4C 工作，
不得由本 Projection 冒充已实现。

## 代码位置

- `app/domain/owner_truth/thread_summary.py`
  - typed summary、anchor、association 和 checkpoint 合同。
- `app/services/owner_truth_thread_summary_read.py`
  - owner-scoped composition seam；当前无 HTTP route。
- `tests/test_owner_truth_thread_summary.py`
  - 关联、stale cue、跨 Owner fail-closed 和 rebuilding coverage 回归。

## 验证

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh
```

本轮结果：78 项单测通过，部署镜像可运行的 dependency-free policy smoke 通过，
`git diff --check` 与 Python compileall 通过。

## Gate 结论

- G0：已覆盖，属于纯后端 read projection 合同。
- G1：未开始；没有公开 UI 或模拟器交互。
- G2：不要求 migration。若需要在部署环境调用该 read service，随下一次后端常规
  发布一起部署即可；本轮未部署。
- G3/G4：不适用；本切片没有 Provider 和真机能力。
