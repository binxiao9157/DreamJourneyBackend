# Owner Truth 自然换话题 Shadow（G0）

## 范围

`TopicShiftDetector` 只在成功追加 Owner 自然输入后的既有无正文审计链路中运行。它是确定性、本地、保守的识别器：仅识别用户明确表达的“换个话题 / 先不聊这个 / 聊点别的”等提示，并输出：

- `userChangedTopic: bool`
- `reasonCode: explicitTopicChangeCue | noExplicitTopicChangeCue`

检测器不会保存、返回、记录或输出输入正文；不调用模型或 Provider。

## 默认关闭与效果边界

需要同时显式开启：

```dotenv
OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED=true
OWNER_TRUTH_TOPIC_SHIFT_SHADOW_ENABLED=true
```

正常部署默认均为 `false`。命中后仅把布尔信号交给既有 `InterviewOrchestrator`，因而审计回执可得到 `PAUSE/topicChanged`。它**不会**：

- 修改实际 `InterviewSession` 状态；
- 创建新 Thread、Candidate、MemoryVersion 或 Provider 效果；
- 添加公开 API 或 Echo UI；
- 代替后续有 Gate 的“暂停旧 Thread 后建立新 Thread”产品命令。

该保守 shadow 为后续自然话题识别的误触发评估提供最小证据。语义主题聚类、自动恢复提示和公开切流仍未完成。

## 本地验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_topic_shift_detection \
  tests.test_owner_truth_interview_orchestration \
  tests.test_owner_truth_interview_session_orchestration \
  tests.test_owner_truth_interview_input_api.OwnerTruthInterviewInputAPITests.test_formal_natural_input_topic_shift_shadow_is_independent_and_non_mutating
```

覆盖点：明确换话题命中、连续叙述/歧义表达不误触发、原文不进入 audit 摘要、独立默认关闭、shadow 命中后 Session 仍为 `active`。
