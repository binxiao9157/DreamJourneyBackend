# Owner QA 访谈会话显式结束 G0

日期：2026-07-30
状态：`LOCAL_G0_VERIFIED / QA_ONLY / DEFAULT_OFF / NOT_DEPLOYED`

## 范围

本轮为私有 Owner Truth 访谈补齐一条明确的结束命令：

`POST /v2/vaults/{vault_id}/interview-sessions/{session_id}/end`

请求只允许包含：`commandId`、`threadId`、`expectedThreadVersion` 和
`expectedSessionVersion`。它不接受结束原因、访谈正文、模型输出、Candidate 或
Memory 字段。

命令会原子地将当前私有 `ConversationThread` 和 `InterviewSession` 标记为 `ended`，
保留已有 boundary 作为历史状态，并写入可重放的 command receipt。结束后：

- 同一 `commandId` 且同一 payload 重放幂等；
- 新的追加消息、边界恢复或再次结束均不能绕过 `ended` 状态；
- 当前可恢复会话读取不再返回该会话；
- 线程不再具备推荐上下文资格；
- 若已有未审核 owner turn，既有 QA 自动化以 `sessionExit` 创建一条 review batch；
  同一结束命令重放或并发后只会保留一条 pending batch。

## 发布与数据边界

- 路由使用 `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED` 与
  `X-DreamJourney-QA-Owner-Truth: 1`，默认关闭且 `include_in_schema=False`；
- 它不改变公开 Echo、iOS UI、provider、Source、Candidate、DecisionReceipt 或
  MemoryVersion；
- `0063_owner_truth_interview_session_end` 仅扩展
  `conversation_command_receipts` 的 command type / shape 约束，不写入业务内容。

## 验证

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python scripts/run-backend-owner-truth-interview-end-g0-gate.sh
```

gate 覆盖：领域状态转换、暂停后结束、幂等重放、结束后追加拒绝、跨 Owner 拒绝、
乐观版本冲突、精确 payload、默认隐藏、`sessionExit` review batch、路由 ownership、
迁移约束和 runtime 路由计数。

## 非目标

- 不公开“结束访谈”按钮；
- 不把私有访谈内容直接提升为长期记忆；
- 不部署 migration，也不宣称线上 Postgres 已验证；
- 不替代后续产品层关于访谈恢复、归档或删除的正式决策。
